"""
index_repo.py

CLI to index your entire GitHub repository into ChromaDB.
Run this ONCE before reviewing PRs for the first time.
After that, run with --sync to update only changed files.

Usage:
    # First time — index entire repo
    python index_repo.py --repo Pratiksha-p3/fault_management

    # After pushing new code — sync only changed files (fast)
    python index_repo.py --repo Pratiksha-p3/fault_management --sync

    # Check how many files are indexed
    python index_repo.py --repo Pratiksha-p3/fault_management --status

    # Force re-index everything (e.g. after major refactor)
    python index_repo.py --repo Pratiksha-p3/fault_management --force
"""
import argparse
import sys
from ingestion.repo_indexer import RepoIndexer


def main():
    parser = argparse.ArgumentParser(
        description="Index a GitHub repo into ChromaDB for RAG-based code review"
    )
    parser.add_argument(
        "--repo", required=True,
        help="GitHub repo: owner/reponame  e.g. Pratiksha-p3/fault_management"
    )
    parser.add_argument(
        "--branch", default="",
        help="Branch to index (default: repo default branch)"
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="Only re-index changed files (fast, incremental)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-index ALL files even if unchanged"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show indexing status without indexing"
    )
    args = parser.parse_args()

    indexer = RepoIndexer()

    if args.status:
        status = indexer.status(args.repo)
        print(f"\n[status] Repository: {status['repo']}")
        print(f"[status] Files indexed: {status['files_indexed']}")
        print(f"[status] Total vectors: {status['total_vectors']}")
        print(f"[status] Collection: {status['collection']}")
        return

    if args.sync:
        n = indexer.sync_repo(args.repo, branch=args.branch)
        print(f"\n✅ Sync complete — {n} files updated")
    else:
        n = indexer.index_repo(
            args.repo,
            branch=args.branch,
            force=args.force,
        )
        print(f"\n✅ Indexing complete — {n} files indexed")
        print(f"\nNext steps:")
        print(f"  Review a PR with full repo context:")
        print(f"  python app.py --repo {args.repo} --pr <number>")


if __name__ == "__main__":
    main()