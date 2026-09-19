#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Git-native retrieval-augmented generation (RAG) indexing and search engine.

Indexes commit histories, diff statistics, git notes (`ai-memory`), and
markdown documentation in `.agents/memory/` into a local SQLite full-text search
(FTS5 with BM25 ranking) database, allowing AI agents to query past decisions
and automatically inject relevant engineering context into sessions.
"""

import argparse
import datetime
import json
import re
import sqlite3
import subprocess
from pathlib import Path


def get_repo_root() -> Path:
    """Find the root directory path of the current Git repository.

    Returns:
        Path object representing the repository top-level root, or current
        working directory if not in a git repository.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        return Path(res.stdout.strip())
    except Exception:
        return Path.cwd()


def get_current_head(repo_root: Path) -> str:
    """Retrieve the current HEAD commit hash of the repository.

    Args:
        repo_root: Path to the root of the Git repository.

    Returns:
        40-character commit hash string, or empty string on failure.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        return res.stdout.strip()
    except Exception:
        return ""


def get_db_path(repo_root: Path) -> Path:
    """Return the filesystem path to the SQLite FTS5 index database.

    Args:
        repo_root: Path to the root of the Git repository.

    Returns:
        Path to `.agents/.git_rag.db`.
    """
    agents_dir = repo_root / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    return agents_dir / ".git_rag.db"


def init_db(db_path: Path, rebuild: bool = False) -> sqlite3.Connection:
    """Initialize SQLite database with schema and FTS5 search virtual tables.

    Args:
        db_path: Filesystem path to the SQLite database file.
        rebuild: Whether to delete existing database and rebuild schema from scratch.

    Returns:
        Active sqlite3.Connection object.
    """
    conn = sqlite3.connect(str(db_path))
    if rebuild:
        try:
            with conn:
                conn.execute("DROP TABLE IF EXISTS chunks_fts")
                conn.execute("DROP TABLE IF EXISTS chunks")
                conn.execute("DROP TABLE IF EXISTS files")
                conn.execute("DROP TABLE IF EXISTS meta")
                conn.execute("VACUUM")
        except Exception:
            pass
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source_type TEXT,
                ref TEXT,
                title TEXT,
                content TEXT,
                metadata TEXT,
                timestamp TEXT
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                id UNINDEXED,
                title,
                content,
                source_type UNINDEXED,
                tokenize='porter unicode61'
            )
        """)
    return conn


def extract_git_commits(repo_root: Path, depth: int = 50):
    """Extract recent commits, messages, diff summaries, and AI notes.

    Args:
        repo_root: Root directory of the Git repository.
        depth: Number of recent commits to inspect.

    Returns:
        List of dictionaries containing document metadata and text content.
    """
    commits = []
    try:
        log_res = subprocess.run(
            ["git", "log", f"-n{depth}", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        if log_res.returncode != 0 or not log_res.stdout.strip():
            return commits

        lines = log_res.stdout.strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            commit_hash, author, date, subject = parts[0], parts[1], parts[2], parts[3]

            stat_res = subprocess.run(
                ["git", "show", "--stat", "--oneline", commit_hash],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            diff_stat = stat_res.stdout.strip() if stat_res.returncode == 0 else ""

            note_res = subprocess.run(
                ["git", "notes", "--ref=ai-memory", "show", commit_hash],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            ai_note = note_res.stdout.strip() if note_res.returncode == 0 else ""

            content_blocks = [
                f"Commit: {commit_hash}",
                f"Author: {author}",
                f"Date: {date}",
                f"Message: {subject}",
                f"Summary:\n{diff_stat}"
            ]
            if ai_note:
                content_blocks.append(f"AI Note:\n{ai_note}")

            commits.append({
                "id": f"commit:{commit_hash}",
                "source_type": "git_commit",
                "ref": commit_hash,
                "title": f"[{date}] Commit: {subject}",
                "content": "\n\n".join(content_blocks),
                "metadata": json.dumps({"hash": commit_hash, "author": author, "has_note": bool(ai_note)}),
                "timestamp": date
            })
    except Exception as e:
        print(f"[Warning] Failed extracting commits: {e}", file=sys.stderr)

    return commits


def extract_markdown_memories(repo_root: Path):
    """Extract structured sections from markdown files in .agents/memory/*.md.

    Args:
        repo_root: Root directory of the Git repository.

    Returns:
        List of dictionaries containing memory titles, sections, and timestamps.
    """
    memories = []
    memory_dir = repo_root / ".agents" / "memory"
    if not memory_dir.exists():
        return memories

    md_files = list(memory_dir.glob("*.md"))
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            sections = re.split(r"\n(?=##\s+)", text)
            for idx, sec in enumerate(sections):
                sec = sec.strip()
                if not sec or sec.startswith("# "):
                    continue
                header_match = re.match(r"^##\s+(.+)$", sec, flags=re.MULTILINE)
                title = header_match.group(1).strip() if header_match else f"{md_file.name} - Section {idx+1}"
                
                date_match = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", title)
                timestamp = date_match.group(1) if date_match else datetime.date.today().isoformat()

                memories.append({
                    "id": f"doc:{md_file.stem}:{idx}",
                    "source_type": "memory_doc",
                    "ref": f".agents/memory/{md_file.name}",
                    "title": f"[{md_file.stem}] {title}",
                    "content": sec,
                    "metadata": json.dumps({"file": str(md_file.name), "section": idx}),
                    "timestamp": timestamp
                })
        except Exception as e:
            print(f"[Warning] Error reading {md_file}: {e}", file=sys.stderr)

    return memories


def index_all(repo_root: Path, depth: int = 50, rebuild: bool = False):
    """Build or update the SQLite FTS5 full-text index from commits and markdown files.

    Args:
        repo_root: Root directory of the Git repository.
        depth: Number of recent commits to index.
        rebuild: Whether to recreate database from scratch.

    Returns:
        Total number of indexed documents.
    """
    db_path = get_db_path(repo_root)
    conn = init_db(db_path, rebuild=rebuild)

    items = []
    items.extend(extract_git_commits(repo_root, depth=depth))
    items.extend(extract_markdown_memories(repo_root))

    current_head = get_current_head(repo_root)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with conn:
        for item in items:
            conn.execute("""
                INSERT OR REPLACE INTO documents (id, source_type, ref, title, content, metadata, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (item["id"], item["source_type"], item["ref"], item["title"], item["content"], item["metadata"], item["timestamp"]))

            conn.execute("DELETE FROM documents_fts WHERE id = ?", (item["id"],))
            conn.execute("""
                INSERT INTO documents_fts (id, title, content, source_type)
                VALUES (?, ?, ?, ?)
            """, (item["id"], item["title"], item["content"], item["source_type"]))

        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_head', ?)", (current_head,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_time', ?)", (now_iso,))

    conn.close()
    return len(items)


def ensure_fresh_index(repo_root: Path):
    """Verify index freshness against current HEAD commit and auto-sync if outdated.

    Args:
        repo_root: Root directory of the Git repository.
    """
    db_path = get_db_path(repo_root)
    if not db_path.exists():
        index_all(repo_root)
        return

    current_head = get_current_head(repo_root)
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT value FROM meta WHERE key = 'last_indexed_head'")
        row = cursor.fetchone()
        cached_head = row[0] if row else ""
        conn.close()

        if current_head and cached_head != current_head:
            index_all(repo_root)
    except Exception:
        index_all(repo_root)


def sanitize_fts_query(query: str) -> str:
    """Sanitize and tokenize query string for SQLite FTS5 syntax.

    Args:
        query: Raw query string.

    Returns:
        FTS5 formatted query string with prefix wildcard matches.
    """
    clean = re.sub(r'[^\w\s\-\.]', ' ', query).strip()
    tokens = clean.split()
    if not tokens:
        return ""
    return " OR ".join([f'"{t}"*' if not t.endswith('*') else f'"{t}"' for t in tokens if len(t) > 1])


def query_index(repo_root: Path, query_str: str, limit: int = 5):
    """Perform BM25-ranked full-text search against indexed memory and commits.

    Falls back to SQL LIKE substring search if FTS5 match parsing fails.

    Args:
        repo_root: Root directory of the Git repository.
        query_str: Search keywords.
        limit: Maximum number of search results to return.

    Returns:
        List of result dictionaries sorted by relevance score.
    """
    ensure_fresh_index(repo_root)

    db_path = get_db_path(repo_root)
    conn = sqlite3.connect(str(db_path))
    fts_query = sanitize_fts_query(query_str)
    
    if not fts_query:
        return []

    results = []
    try:
        cursor = conn.execute("""
            SELECT d.id, d.source_type, d.ref, d.title, d.content, d.timestamp,
                   bm25(documents_fts) as rank
            FROM documents_fts f
            JOIN documents d ON f.id = d.id
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit))

        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "source_type": row[1],
                "ref": row[2],
                "title": row[3],
                "content": row[4],
                "timestamp": row[5],
                "score": round(-row[6], 4)
            })
    except sqlite3.OperationalError:
        safe_q = query_str.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = conn.execute("""
            SELECT id, source_type, ref, title, content, timestamp, 0.0
            FROM documents
            WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
            LIMIT ?
        """, (f"%{safe_q}%", f"%{safe_q}%", limit))
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "source_type": row[1],
                "ref": row[2],
                "title": row[3],
                "content": row[4],
                "timestamp": row[5],
                "score": 1.0
            })
    finally:
        conn.close()

    return results


def record_memory(repo_root: Path, title: str, content: str, target: str = "markdown", filename: str = "decisions.md", commit_hash: str = "HEAD"):
    """Record an engineering decision or context item to markdown memory or git notes.

    Args:
        repo_root: Root directory of the Git repository.
        title: Short title summarizing the decision or insight.
        content: Detailed explanation or engineering rationale.
        target: Storage destination ('markdown' or 'note').
        filename: Destination file within `.agents/memory/` for markdown target.
        commit_hash: Git commit hash to attach note to if target is 'note'.
    """
    today = datetime.date.today().isoformat()
    if target == "markdown":
        mem_dir = repo_root / ".agents" / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        target_file = mem_dir / filename
        
        entry = f"\n\n## [{today}] {title}\n{content.strip()}\n"
        if target_file.exists():
            with open(target_file, "a", encoding="utf-8", newline="\n") as f:
                f.write(entry)
        else:
            with open(target_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"# {filename.replace('.md', '').capitalize()}\n" + entry)
        print(f"[Success] Recorded memory to {target_file}")
    elif target == "note":
        note_text = f"[{today}] {title}\n{content.strip()}"
        res = subprocess.run(
            ["git", "notes", "--ref=ai-memory", "add", "-f", "-m", note_text, commit_hash],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        if res.returncode == 0:
            print(f"[Success] Attached git note to {commit_hash}")
        else:
            print(f"[Error] Failed attaching note: {res.stderr}", file=sys.stderr)

    index_all(repo_root)


def install_git_hooks(repo_root: Path):
    """Install post-commit and post-merge Git hooks for background auto-indexing.

    Args:
        repo_root: Root directory of the Git repository.
    """
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.exists():
        print(f"[Error] Git hooks directory not found at {hooks_dir}", file=sys.stderr)
        return

    hook_content = '#!/bin/sh\nrepo_root=$(git rev-parse --show-toplevel)\npython -B "$repo_root/scripts/git_rag.py" index >/dev/null 2>&1 &\n'
    
    post_commit = hooks_dir / "post-commit"
    post_merge = hooks_dir / "post-merge"

    with open(post_commit, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_content)

    with open(post_merge, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_content)

    print(f"[Success] Installed Git hooks in {hooks_dir}:")
    print(f"  - {post_commit.name}")
    print(f"  - {post_merge.name}")


def main():
    """CLI entry point for Git RAG AI memory search, indexing, and recording."""
    parser = argparse.ArgumentParser(description="Git RAG AI Memory Engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # index
    idx_parser = subparsers.add_parser("index", help="Index git commits and memory files")
    idx_parser.add_argument("--depth", type=int, default=50, help="Number of recent commits to index")
    idx_parser.add_argument("--rebuild", action="store_true", help="Rebuild database from scratch")

    # query
    query_parser = subparsers.add_parser("query", help="Query the memory index")
    query_parser.add_argument("query", type=str, help="Search query string")
    query_parser.add_argument("--limit", type=int, default=5, help="Maximum number of results")
    query_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    # record
    rec_parser = subparsers.add_parser("record", help="Record a new memory or note")
    rec_parser.add_argument("title", type=str, help="Title / summary of memory")
    rec_parser.add_argument("content", type=str, help="Detailed content or rationale")
    rec_parser.add_argument("--target", choices=["markdown", "note"], default="markdown", help="Storage target")
    rec_parser.add_argument("--file", type=str, default="decisions.md", help="Markdown filename inside .agents/memory/")
    rec_parser.add_argument("--commit", type=str, default="HEAD", help="Commit hash for git notes")

    # inject
    inj_parser = subparsers.add_parser("inject", help="Generate prompt injection context block")
    inj_parser.add_argument("--query", type=str, default="", help="Optional query to filter relevant memories")
    inj_parser.add_argument("--limit", type=int, default=3, help="Max context items to inject")

    # install-hooks
    subparsers.add_parser("install-hooks", help="Install post-commit and post-merge Git hooks")

    args = parser.parse_args()
    repo_root = get_repo_root()

    if args.command == "index":
        count = index_all(repo_root, depth=args.depth, rebuild=args.rebuild)
        print(f"[Index Completed] Successfully indexed {count} items into .agents/.git_rag.db")

    elif args.command == "query":
        results = query_index(repo_root, args.query, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            if not results:
                print("No matching memories or commits found.")
            for i, r in enumerate(results, 1):
                print(f"\n--- Match {i} (Score: {r['score']}) [{r['source_type']}] ---")
                print(f"Title: {r['title']}")
                print(f"Ref: {r['ref']} | Date: {r['timestamp']}")
                print("Content:")
                print(r['content'])

    elif args.command == "record":
        record_memory(repo_root, args.title, args.content, target=args.target, filename=args.file, commit_hash=args.commit)

    elif args.command == "inject":
        if args.query:
            results = query_index(repo_root, args.query, limit=args.limit)
        else:
            results = query_index(repo_root, "decision learning context rule", limit=args.limit)
        
        import xml.sax.saxutils as saxutils
        print("<git_rag_memory>")
        for r in results:
            print(f"  <memory_item source=\"{saxutils.escape(r['source_type'])}\" ref=\"{saxutils.escape(r['ref'])}\" date=\"{saxutils.escape(r['timestamp'])}\">")
            print(f"    <title>{saxutils.escape(r['title'])}</title>")
            print(f"    <content>\n{saxutils.escape(r['content'])}\n    </content>")
            print("  </memory_item>")
        print("</git_rag_memory>")

    elif args.command == "install-hooks":
        install_git_hooks(repo_root)


if __name__ == "__main__":
    main()
