"""
ingestion/repo_indexer.py

Repository-Aware RAG — Phase 1

Indexes the ENTIRE repository into ChromaDB once.
After this runs, the retriever can find related files
across the whole codebase, not just PR files.

Usage:
    # One-time index (or re-index after major changes)
    indexer = RepoIndexer()
    indexer.index_repo("Pratiksha-p3/fault_management")

    # Check status
    indexer.status("Pratiksha-p3/fault_management")

    # Re-index only changed files (fast, incremental)
    indexer.sync_repo("Pratiksha-p3/fault_management")
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from embeddings.embed import Embedder
from ingestion.chunker import Chunker
from ingestion.parser import Parser
from vectordb.chroma_store import ChromaStore
from config import cfg

# Separate collection for repo-wide index
# (PR files go into "code_review", repo index goes into "repo_index")
REPO_COLLECTION = "repo_index"

# Track which file SHAs are indexed to avoid re-embedding unchanged files
INDEX_CACHE_PATH = Path("./vectordb/repo_index_cache.json")

EXTENSION_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
    ".java": "java", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".swift": "swift",
    ".kt": "kotlin", ".sh": "bash", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".sql": "sql", ".tf": "terraform", ".md": "markdown",
}

SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", "dist", "build",
    "vendor", "venv", ".venv", "env", "migrations", ".github",
}

SKIP_EXTENSIONS = {
    ".lock", ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".zip", ".tar",
    ".gz", ".exe", ".dll", ".bin", ".so", ".dylib",
}


@dataclass
class RepoFile:
    """A file fetched from GitHub with its content."""
    filename: str
    language: str
    full_content: str
    status: str = "existing"
    additions: int = 0
    deletions: int = 0
    patch: str = ""


class RepoIndexer:
    """
    Fetches and indexes an entire GitHub repository into ChromaDB.

    Design decisions (as a senior AIML engineer):
    - Separate ChromaDB collection from PR reviews — repo index is
      long-lived, PR reviews are ephemeral per-PR.
    - SHA-based incremental updates — only re-embed files that actually
      changed, not the whole repo every time.
    - Metadata includes: filename, language, function names, imports —
      so retrieval can filter by language AND find callers/callees.
    - Chunk size slightly larger (800 tokens) than PR review chunks (512)
      because we want more context per retrieved chunk when doing
      cross-file reference lookups.
    """

    def __init__(self):
        self.embedder  = Embedder()
        self.chunker   = Chunker(chunk_size=800, chunk_overlap=150)
        self.parser    = Parser()
        self._cache    = self._load_cache()
        self._store    = None
        self._gh_token = os.getenv("GITHUB_TOKEN", "")

    # ─────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────

    def index_repo(
        self,
        repo: str,
        branch: str = "",
        force: bool = False,
    ) -> int:
        """
        Index the entire repository.
        Returns number of files indexed.

        Args:
            repo:   "owner/reponame"
            branch: branch to index (default: repo's default branch)
            force:  re-index everything even if SHA unchanged
        """
        print(f"\n[repo-indexer] Indexing repository: {repo}")
        col = self._get_collection()

        # Get file tree from GitHub
        branch    = branch or self._get_default_branch(repo)
        all_files = self._get_file_tree(repo, branch)

        print(f"[repo-indexer] Found {len(all_files)} indexable files in {repo}")

        repo_cache = self._cache.get(repo, {})
        to_index   = []
        skipped    = 0

        for file_info in all_files:
            path = file_info["path"]
            sha  = file_info["sha"]

            if not force and repo_cache.get(path) == sha:
                skipped += 1
                continue

            to_index.append(file_info)

        print(
            f"[repo-indexer] {len(to_index)} files to index  "
            f"({skipped} unchanged, skipped)"
        )

        if not to_index:
            print("[repo-indexer] Already up to date!")
            return 0

        indexed = 0
        for i, file_info in enumerate(to_index):
            path     = file_info["path"]
            sha      = file_info["sha"]
            language = EXTENSION_MAP.get(Path(path).suffix.lower(), "unknown")

            content = self._fetch_file_content(repo, path, branch)
            if not content.strip():
                continue

            # Convert to RepoFile so existing parser/chunker works on it
            repo_file = RepoFile(
                filename     = path,
                language     = language,
                full_content = content,
            )

            # Parse → chunk → embed → store
            sections = self.parser.parse(repo_file)
            chunks   = self.chunker.chunk_sections(sections)

            if not chunks:
                continue

            embedded = self.embedder.embed_chunks(chunks)

            # Delete old chunks for this file before upserting new ones
            self._delete_file_chunks(col, path)
            self._upsert_with_metadata(col, embedded, repo, sha)

            # Update cache
            repo_cache[path] = sha
            indexed += 1

            if (i + 1) % 10 == 0 or (i + 1) == len(to_index):
                print(
                    f"  [{i+1}/{len(to_index)}] Indexed: {path}"
                )
                # Save cache every 10 files so progress isn't lost on crash
                self._cache[repo] = repo_cache
                self._save_cache()

            time.sleep(0.05)  # Gentle rate limiting

        self._cache[repo] = repo_cache
        self._save_cache()

        total = col.count()
        print(
            f"\n[repo-indexer] Done! "
            f"{indexed} files indexed. "
            f"Total vectors: {total}"
        )
        return indexed

    def sync_repo(self, repo: str, branch: str = "") -> int:
        """
        Incremental sync — only re-index files changed since last index.
        Fast: only fetches and embeds what's new or modified.
        """
        print(f"[repo-indexer] Syncing {repo}...")
        return self.index_repo(repo, branch=branch, force=False)

    def status(self, repo: str) -> dict:
        """Check how many files are indexed for a repo."""
        col        = self._get_collection()
        repo_cache = self._cache.get(repo, {})
        return {
            "repo":           repo,
            "files_indexed":  len(repo_cache),
            "total_vectors":  col.count(),
            "collection":     REPO_COLLECTION,
        }

    # ─────────────────────────────────────────────────────
    # GITHUB API
    # ─────────────────────────────────────────────────────

    def _get_default_branch(self, repo: str) -> str:
        data = self._gh_get(f"/repos/{repo}")
        return data.get("default_branch", "main")

    def _get_file_tree(self, repo: str, branch: str) -> list[dict]:
        data  = self._gh_get(f"/repos/{repo}/git/trees/{branch}?recursive=1")
        items = data.get("tree", [])
        return [
            {"path": item["path"], "sha": item["sha"]}
            for item in items
            if item["type"] == "blob"
            and self._should_index(item["path"])
        ]

    def _fetch_file_content(self, repo: str, path: str, branch: str) -> str:
        import base64
        try:
            data    = self._gh_get(f"/repos/{repo}/contents/{path}?ref={branch}")
            encoded = data.get("content", "")
            return base64.b64decode(encoded).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  [repo-indexer] Could not fetch {path}: {e}")
            return ""

    def _gh_get(self, path: str) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self._gh_token:
            headers["Authorization"] = f"token {self._gh_token}"
        url  = f"https://api.github.com{path}"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 403:
            raise Exception(
                "GitHub rate limited. Add GITHUB_TOKEN to .env for higher limits."
            )
        resp.raise_for_status()
        return resp.json()

    def _should_index(self, path: str) -> bool:
        parts = Path(path).parts
        for part in parts[:-1]:
            if part in SKIP_DIRS or part.startswith("."):
                return False
        suffix = Path(path).suffix.lower()
        if suffix in SKIP_EXTENSIONS:
            return False
        return suffix in EXTENSION_MAP

    # ─────────────────────────────────────────────────────
    # CHROMADB
    # ─────────────────────────────────────────────────────

    def _get_collection(self):
        if self._store is not None:
            return self._store

        import chromadb
        client = chromadb.PersistentClient(path=cfg.chroma_dir)
        self._store = client.get_or_create_collection(
            name     = REPO_COLLECTION,
            metadata = {"hnsw:space": "cosine"},
        )
        print(
            f"[repo-indexer] Collection '{REPO_COLLECTION}' "
            f"({self._store.count()} vectors)"
        )
        return self._store

    def _upsert_with_metadata(self, col, embedded_chunks, repo: str, file_sha: str):
        """
        Upsert chunks with enriched metadata.
        Adds: repo, file_sha, imports, function_names
        so the retriever can filter and rank more intelligently.
        """
        ids        = []
        embeddings = []
        documents  = []
        metadatas  = []

        for ec in embedded_chunks:
            c = ec.chunk
            ids.append(f"{repo}::{c.chunk_id}")
            embeddings.append(ec.vector)
            documents.append(c.content)
            metadatas.append({
                "filename":       c.filename,
                "language":       c.language,
                "section_name":   c.section_name,
                "section_type":   c.section_type,
                "start_line":     c.start_line,
                "end_line":       c.end_line,
                "chunk_index":    c.chunk_index,
                "token_estimate": c.token_estimate,
                "repo":           repo,
                "file_sha":       file_sha,
                # Extra metadata for smarter retrieval
                "imports":        self._extract_imports(c.content),
                "func_names":     self._extract_function_names(c.content),
            })

        col.upsert(
            ids        = ids,
            embeddings = embeddings,
            documents  = documents,
            metadatas  = metadatas,
        )

    def _delete_file_chunks(self, col, filename: str):
        try:
            col.delete(where={"filename": {"$eq": filename}})
        except Exception:
            pass

    # ─────────────────────────────────────────────────────
    # METADATA EXTRACTION
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_imports(content: str) -> str:
        """
        Extract import statements from a code chunk.
        Stored as metadata so retrieval can find files that
        import the same modules as the changed file.
        """
        import re
        imports = re.findall(
            r"^(?:import|from)\s+(\S+)",
            content,
            re.MULTILINE,
        )
        return ",".join(imports[:10])  # top 10

    @staticmethod
    def _extract_function_names(content: str) -> str:
        """
        Extract function/class names from a code chunk.
        Stored as metadata so retrieval can find callers of
        a changed function across the entire codebase.
        """
        import re
        # Python
        py_names = re.findall(
            r"^\s*(?:def|class|async def)\s+(\w+)",
            content,
            re.MULTILINE,
        )
        # JS/TS
        js_names = re.findall(
            r"(?:function|class|const|let)\s+(\w+)",
            content,
        )
        names = list(dict.fromkeys(py_names + js_names))  # dedupe, preserve order
        return ",".join(names[:10])

    # ─────────────────────────────────────────────────────
    # CACHE
    # ─────────────────────────────────────────────────────

    def _load_cache(self) -> dict:
        if INDEX_CACHE_PATH.exists():
            try:
                return json.loads(INDEX_CACHE_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        INDEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_CACHE_PATH.write_text(json.dumps(self._cache, indent=2))