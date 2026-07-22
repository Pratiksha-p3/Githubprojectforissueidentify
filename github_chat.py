"""
github_chat.py

Chat about YOUR GitHub repo code using RAG.

Commands:
  python github_chat.py --load   --repo owner/reponame   # First time load
  python github_chat.py --update --repo owner/reponame   # Sync new/changed files
  python github_chat.py --chat   --repo owner/reponame   # Ask questions
  python github_chat.py --all    --repo owner/reponame   # Load + chat in one go
  python github_chat.py --mock                           # Test without GitHub
"""
from __future__ import annotations

import os, sys, argparse, base64, json, hashlib
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "llama-3.3-70b-versatile")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

EXTENSION_MAP = {
    ".py":"python",".js":"javascript",".ts":"typescript",
    ".tsx":"typescript",".jsx":"javascript",".go":"go",
    ".java":"java",".rs":"rust",".rb":"ruby",".php":"php",
    ".cs":"csharp",".cpp":"cpp",".c":"c",".swift":"swift",
    ".kt":"kotlin",".sh":"bash",".yaml":"yaml",".yml":"yaml",
    ".json":"json",".sql":"sql",".tf":"terraform",".md":"markdown",
}
SKIP_DIRS = {"node_modules","__pycache__",".git","dist","build","vendor","venv",".venv","env","migrations"}
SKIP_EXT  = {".lock",".pyc",".png",".jpg",".jpeg",".gif",".svg",".ico",
             ".woff",".woff2",".ttf",".pdf",".zip",".tar",".gz",".exe",".dll"}

# ── where we track which files/SHAs are already in RAG ──────────────────────
INDEX_PATH = Path("./vectordb/repo_index.json")


# ─────────────────────────────────────────────────────────────────────────────
# GITHUB LOADER
# ─────────────────────────────────────────────────────────────────────────────

class GitHubLoader:
    BASE = "https://api.github.com"

    def __init__(self, token=""):
        self.token = token or GITHUB_TOKEN
        self.headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"
        else:
            print("[loader] WARNING: No GITHUB_TOKEN set. Rate limit = 60 req/hr.")
            print("[loader] Add GITHUB_TOKEN=your_token to .env for private repos & higher limits.")

    def get_default_branch(self, repo):
        info = self._get(f"/repos/{repo}")
        return info.get("default_branch", "main")

    def get_all_files(self, repo, branch=""):
        """Return list of {path, sha} for every code file in the repo."""
        if not branch:
            branch = self.get_default_branch(repo)
        print(f"[loader] Reading file tree from branch: {branch}")
        tree = self._get(f"/repos/{repo}/git/trees/{branch}?recursive=1")
        items = tree.get("tree", [])
        return [
            {"path": i["path"], "sha": i["sha"]}
            for i in items
            if i["type"] == "blob" and self._should_include(i["path"])
        ]

    def get_file_content(self, repo, path, branch):
        try:
            data = self._get(f"/repos/{repo}/contents/{path}?ref={branch}")
            if isinstance(data, list):
                return ""
            return base64.b64decode(data.get("content","")).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  [skip] {path}: {e}")
            return ""

    def _get(self, path):
        url = f"{self.BASE}{path}" if path.startswith("/") else path
        r = requests.get(url, headers=self.headers, timeout=30)
        if r.status_code == 403:
            raise Exception("Rate limited or private repo. Add GITHUB_TOKEN to .env")
        r.raise_for_status()
        return r.json()

    def _should_include(self, path):
        parts = Path(path).parts
        for part in parts[:-1]:
            if part in SKIP_DIRS or part.startswith("."):
                return False
        suffix = Path(path).suffix.lower()
        return suffix in EXTENSION_MAP and suffix not in SKIP_EXT


# ─────────────────────────────────────────────────────────────────────────────
# INDEX  (tracks file SHA so we only re-embed changed files)
# ─────────────────────────────────────────────────────────────────────────────

def load_index(repo):
    if INDEX_PATH.exists():
        data = json.loads(INDEX_PATH.read_text())
        return data.get(repo, {})
    return {}

def save_index(repo, index):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_data = {}
    if INDEX_PATH.exists():
        all_data = json.loads(INDEX_PATH.read_text())
    all_data[repo] = index
    INDEX_PATH.write_text(json.dumps(all_data, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# CHUNKER
# ─────────────────────────────────────────────────────────────────────────────

def chunk_file(filename, language, content, chunk_lines=60, overlap=10):
    lines  = content.splitlines()
    chunks = []
    step   = max(1, chunk_lines - overlap)
    for i in range(0, len(lines), step):
        batch = lines[i : i + chunk_lines]
        text  = "\n".join(batch).strip()
        if not text:
            continue
        cid = hashlib.sha256(f"{filename}:{i}:{text}".encode()).hexdigest()[:16]
        chunks.append({
            "id":         cid,
            "filename":   filename,
            "language":   language,
            "content":    text,
            "start_line": i + 1,
            "end_line":   i + len(batch),
        })
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDER  (sentence-transformers, free & local)
# ─────────────────────────────────────────────────────────────────────────────

_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer
        print("[embed] Loading local model (first time ~90MB download)...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("[embed] Ready!")
        return _embed_model
    except ImportError:
        print("ERROR: pip install sentence-transformers")
        sys.exit(1)

def embed_texts(texts):
    model = get_embed_model()
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


# ─────────────────────────────────────────────────────────────────────────────
# CHROMADB
# ─────────────────────────────────────────────────────────────────────────────

_chroma_collections = {}

def get_collection(repo):
    if repo in _chroma_collections:
        return _chroma_collections[repo]
    try:
        import chromadb, re
        # Strip any URL prefix so "https://github.com/owner/repo" → "owner/repo"
        repo_clean = re.sub(r'https?://[^/]+/', '', repo).strip("/")
        # Keep only alphanumeric, underscores, hyphens
        safe = "repo_" + re.sub(r'[^a-z0-9_-]', '_', repo_clean.lower())[:55]
        # Collapse multiple underscores and strip trailing underscores
        safe = re.sub(r'_+', '_', safe).strip('_')
        client = chromadb.PersistentClient(path="./vectordb/chroma_data")
        col    = client.get_or_create_collection(name=safe, metadata={"hnsw:space":"cosine"})
        print(f"[chroma] Collection '{safe}' — {col.count()} vectors")
        _chroma_collections[repo] = col
        return col
    except ImportError:
        print("ERROR: pip install chromadb")
        sys.exit(1)

def upsert_chunks(col, chunks):
    if not chunks:
        return
    texts = [
        f"File: {c['filename']}\nLanguage: {c['language']}\n"
        f"Lines {c['start_line']}-{c['end_line']}\n\n{c['content']}"
        for c in chunks
    ]
    vectors = embed_texts(texts)
    col.upsert(
        ids        = [c["id"] for c in chunks],
        embeddings = vectors,
        documents  = [c["content"] for c in chunks],
        metadatas  = [{
            "filename":   c["filename"],
            "language":   c["language"],
            "start_line": c["start_line"],
            "end_line":   c["end_line"],
        } for c in chunks],
    )

def delete_file_chunks(col, filename):
    try:
        col.delete(where={"filename": {"$eq": filename}})
    except Exception:
        pass

def search_rag(question, col, top_k=6):
    if col.count() == 0:
        return []
    q_vec = embed_texts([question])[0]
    res   = col.query(
        query_embeddings=[q_vec],
        n_results=min(top_k, col.count()),
        include=["documents","metadatas","distances"],
    )
    results = []
    for i in range(len(res["ids"][0])):
        score = max(0.0, 1.0 - res["distances"][0][i])
        if score < 0.15:
            continue
        meta = res["metadatas"][0][i]
        results.append({
            "filename":   meta.get("filename",""),
            "language":   meta.get("language",""),
            "start_line": meta.get("start_line",0),
            "end_line":   meta.get("end_line",0),
            "content":    res["documents"][0][i],
            "score":      round(score, 3),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# LOAD / UPDATE REPO INTO RAG
# ─────────────────────────────────────────────────────────────────────────────

def load_repo(repo, branch="", token="", force=False):
    loader  = GitHubLoader(token=token)
    col     = get_collection(repo)
    index   = {} if force else load_index(repo)

    if not branch:
        branch = loader.get_default_branch(repo)

    all_files   = loader.get_all_files(repo, branch)
    new_files   = []
    changed     = []
    unchanged   = 0

    for f in all_files:
        path = f["path"]
        sha  = f["sha"]
        if path not in index:
            new_files.append(f)
        elif index[path] != sha:
            changed.append(f)
        else:
            unchanged += 1

    print(f"\n[sync] {len(all_files)} total files in repo")
    print(f"[sync] {len(new_files)} new  |  {len(changed)} changed  |  {unchanged} unchanged")

    to_process = new_files + changed
    if not to_process:
        print("[sync] Everything is up to date! No embedding needed.")
        return col

    print(f"[sync] Embedding {len(to_process)} files...\n")

    for i, f in enumerate(to_process):
        path     = f["path"]
        sha      = f["sha"]
        language = EXTENSION_MAP.get(Path(path).suffix.lower(), "unknown")

        content  = loader.get_file_content(repo, path, branch)
        if not content.strip():
            continue

        # Delete old chunks for this file (in case it changed)
        delete_file_chunks(col, path)

        # Chunk + embed
        chunks = chunk_file(path, language, content)
        upsert_chunks(col, chunks)

        # Update index
        index[path] = sha

        print(f"  [{i+1}/{len(to_process)}] {path} → {len(chunks)} chunks")

    save_index(repo, index)
    print(f"\n[sync] Done! Total vectors in RAG: {col.count()}")
    return col


# ─────────────────────────────────────────────────────────────────────────────
# GROQ ANSWER
# ─────────────────────────────────────────────────────────────────────────────

def ask_groq(question, context_chunks, history):
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)

    context = ""
    for i, c in enumerate(context_chunks, 1):
        context += (
            f"\n{'─'*50}\n"
            f"File: {c['filename']}  |  Lines {c['start_line']}-{c['end_line']}"
            f"  |  Relevance: {c['score']}\n"
            f"{'─'*50}\n"
            f"{c['content']}\n"
        )

    system = f"""\
You are a senior software engineer. You answer questions about a GitHub codebase.

RELEVANT CODE FROM THE REPOSITORY:
{context if context else "No relevant code found for this query."}

RULES:
- Answer based on the actual code shown above.
- Quote function names, line numbers, or code snippets when relevant.
- If asked about a bug, explain exactly what is wrong and provide fixed code.
- If asked what a function does, explain it clearly with examples.
- If asked about security issues, explain the vulnerability and provide secure code.
- If the retrieved code does not answer the question, say so clearly.
- Keep answers clear and practical.
"""

    messages = [
        {"role": "system", "content": system},
        *history[-8:],
        {"role": "user", "content": question},
    ]

    resp = client.chat.completions.create(
        model=REVIEW_MODEL,
        temperature=0.2,
        max_tokens=1500,
        messages=messages,
    )
    return resp.choices[0].message.content.strip()


# ─────────────────────────────────────────────────────────────────────────────
# CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(col, repo):
    history = []

    print("\n" + "═"*60)
    print("  GitHub Code Chatbot  —  Ask about your repo")
    print("═"*60)
    print(f"  Repo    : {repo}")
    print(f"  Vectors : {col.count()} code chunks indexed")
    print("═"*60)
    print("  Example questions:")
    print("    > What does the login function do?")
    print("    > Are there any security vulnerabilities?")
    print("    > How is the password hashed?")
    print("    > Show me all API endpoints")
    print("    > What changed in the new file I added?")
    print("    > How do I fix the SQL injection bug?")
    print("    > Where is the database connection defined?")
    print("  Type 'quit' to exit  |  'update' to sync new code")
    print("═"*60 + "\n")

    while True:
        try:
            question = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not question:
            continue

        if question.lower() in ("quit", "exit", "q", "bye"):
            print("Goodbye!")
            break

        if question.lower() == "update":
            print("[sync] Pulling latest code from GitHub...")
            load_repo(repo)
            print("[sync] Done! Continuing chat...\n")
            continue

        # Search RAG
        chunks = search_rag(question, col)

        if chunks:
            print(f"\n  [RAG] {len(chunks)} relevant chunks found:")
            for c in chunks:
                print(f"    • {c['filename']}  L{c['start_line']}-{c['end_line']}  score={c['score']}")
        else:
            print("\n  [RAG] No matching code found — answering from general knowledge.")

        # Get answer
        try:
            answer = ask_groq(question, chunks, history)
            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant",  "content": answer})
            print(f"\nAssistant:\n{answer}\n")
        except Exception as e:
            print(f"\n[ERROR] {e}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA
# ─────────────────────────────────────────────────────────────────────────────

MOCK_FILES = [
    {"filename":"src/auth/login.py","language":"python","content":
"""import sqlite3, os
SECRET_KEY = 'hardcoded-secret-abc123'

def login(username, password):
    conn = sqlite3.connect('users.db')
    query = f"SELECT * FROM users WHERE name='{username}'"
    result = conn.execute(query)
    return result.fetchone()

def logout(user_id):
    pass

def is_admin(user):
    return user.get('role') == 'admin'
"""},
    {"filename":"src/utils/hash.py","language":"python","content":
"""import hashlib

def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed
"""},
    {"filename":"src/api/users.py","language":"python","content":
"""from flask import Flask, request, jsonify
from src.auth.login import login
from src.utils.hash import hash_password

app = Flask(__name__)

@app.route('/api/users', methods=['GET'])
def get_users():
    # No authentication check!
    users = db.query("SELECT * FROM users")
    return jsonify(users)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data['username']
    password = hash_password(data['password'])
    db.execute(f"INSERT INTO users VALUES ('{username}', '{password}')")
    return jsonify({"status": "ok"})
"""},
    {"filename":"src/config.py","language":"python","content":
"""import os
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")
DEBUG = True
SECRET_KEY = "super-secret-hardcoded-key-123"
ALLOWED_HOSTS = ["*"]
"""},
]

def load_mock(repo="mock/demo"):
    col   = get_collection(repo)
    index = load_index(repo)
    if col.count() > 0 and index:
        print(f"[mock] Already loaded ({col.count()} vectors).")
        return col
    print("[mock] Loading mock repo data...")
    for f in MOCK_FILES:
        chunks = chunk_file(f["filename"], f["language"], f["content"])
        upsert_chunks(col, chunks)
        index[f["filename"]] = hashlib.md5(f["content"].encode()).hexdigest()
    save_index(repo, index)
    print(f"[mock] Done! {col.count()} vectors stored.")
    return col


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Chat about your GitHub repo code")
    parser.add_argument("--mock",   action="store_true", help="Use mock data (no GitHub needed)")
    parser.add_argument("--load",   action="store_true", help="Load repo into RAG")
    parser.add_argument("--update", action="store_true", help="Sync only new/changed files")
    parser.add_argument("--chat",   action="store_true", help="Start chat session")
    parser.add_argument("--all",    action="store_true", help="Load + chat in one command")
    parser.add_argument("--repo",   type=str, default="", help="GitHub repo: owner/reponame")
    parser.add_argument("--branch", type=str, default="", help="Branch (default: auto-detect)")
    parser.add_argument("--token",  type=str, default="", help="GitHub personal access token")
    parser.add_argument("--force",  action="store_true", help="Re-embed all files (ignore index)")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set in .env")
        sys.exit(1)

    # ── Mock ──────────────────────────────────────────────
    if args.mock:
        col = load_mock()
        chat_loop(col, "mock/demo")
        return

    # ── Real GitHub ───────────────────────────────────────
    if not args.repo:
        print("Usage:")
        print("  python github_chat.py --mock")
        print("  python github_chat.py --all    --repo owner/reponame")
        print("  python github_chat.py --load   --repo owner/reponame")
        print("  python github_chat.py --update --repo owner/reponame")
        print("  python github_chat.py --chat   --repo owner/reponame")
        print("\nFor private repos, add to .env:  GITHUB_TOKEN=ghp_your_token")
        sys.exit(1)

    # Accept full URL or just owner/repo
    import re as _re
    raw_repo = args.repo.strip().rstrip("/")
    # Strip https://github.com/ prefix if present
    repo = _re.sub(r'https?://github\.com/', '', raw_repo).strip("/")
    # Also handle git@ style
    repo = _re.sub(r'^git@github\.com:', '', repo).replace('.git', '')
    print(f"[info] Using repo: {repo}")

    token = args.token or GITHUB_TOKEN
    col   = get_collection(repo)

    if args.load or args.all or (not args.chat and col.count() == 0):
        col = load_repo(repo, branch=args.branch, token=token, force=args.force)

    elif args.update:
        col = load_repo(repo, branch=args.branch, token=token, force=False)
        if not args.chat:
            return

    if args.chat or args.all or col.count() > 0:
        chat_loop(col, repo)
    else:
        print(f"No data for '{repo}'. Run with --load first.")

if __name__ == "__main__":
    main()