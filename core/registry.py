"""SQLite control plane: entity lifecycle state shared by every source and worker."""

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

DISCOVERED = "discovered"
FETCHED = "fetched"
EXTRACTED = "extracted"
CHUNKED = "chunked"
EMBEDDED = "embedded"
PAYWALLED = "paywalled"
EMPTY = "empty"
DUPLICATE = "duplicate"
FAILED = "failed"

PAPER = "paper"
REPO = "repo"

BUSY_TIMEOUT_MS = 30_000


_DOI_URL_PREFIXES = ("https://doi.org/", "http://dx.doi.org/")


def canonical_doi(doi: str | None) -> str | None:
    if not doi or doi == "N/A":
        return None
    s = str(doi).strip()
    for prefix in _DOI_URL_PREFIXES:
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    if s.lower().startswith("doi:"):
        s = s[4:].strip()
    s = s.strip().lower()
    return s or None


def connect(db_path: str | os.PathLike) -> sqlite3.Connection:
    db_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    # Ingestion runs a process pool against one lake; the 5s default surfaces as
    # "database is locked" and kills a worker mid-entity instead of waiting out
    # a peer's commit.
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.row_factory = sqlite3.Row
    conn.create_function("canonical_doi", 1, canonical_doi)
    return conn


def init_db(db_path: str | os.PathLike) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS papers (
                    paper_id TEXT PRIMARY KEY,
                    arxiv_id TEXT,
                    doi TEXT,
                    title TEXT,
                    year INTEGER,
                    venue TEXT,
                    abstract TEXT,
                    source TEXT,
                    query_block TEXT,
                    landing_url TEXT,
                    status TEXT,
                    raw_path TEXT,
                    md_path TEXT,
                    chunks_path TEXT,
                    source_used TEXT,
                    extraction_engine TEXT,
                    extraction_quality REAL,
                    num_chunks INTEGER,
                    content_hash TEXT,
                    error TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS repos (
                    repo_id TEXT PRIMARY KEY,
                    full_name TEXT,
                    org TEXT,
                    name TEXT,
                    html_url TEXT,
                    default_branch TEXT,
                    commit_sha TEXT,
                    pin_policy TEXT,
                    licence TEXT,
                    stars INTEGER,
                    pushed_at TEXT,
                    topics TEXT,
                    is_fork INTEGER,
                    source TEXT,
                    query_block TEXT,
                    status TEXT,
                    raw_path TEXT,
                    code_dir TEXT,
                    chunks_path TEXT,
                    num_files INTEGER,
                    num_chunks INTEGER,
                    content_hash TEXT,
                    error TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    repo_id TEXT,
                    paper_id TEXT,
                    org TEXT,
                    repo_full_name TEXT,
                    catalog_status TEXT,
                    note TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cursors (
                    source TEXT,
                    block TEXT,
                    state TEXT,
                    PRIMARY KEY (source, block)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_content_hash ON papers(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title)")
            # Unique, not just indexed: dedup is otherwise a check-then-act race
            # between concurrent discovery workers, which would both pass the
            # "unknown" check and insert the same repository twice.
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_full_name ON repos(full_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_content_hash ON repos(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_status ON repos(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_models_repo_full_name ON models(repo_full_name)")
    finally:
        conn.close()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_title(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def manifest_hash(entries: list[tuple[str, str]]) -> str | None:
    """Hash a repository's (path, blob sha) manifest so a re-upload is caught."""
    if not entries:
        return None
    joined = "\n".join(f"{path}\t{blob}" for path, blob in sorted(entries))
    return content_hash(joined)


def get_known_identifiers(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """DOIs, normalized titles, and statuses, for an O(1) skip before any network call."""
    rows = conn.execute("SELECT paper_id, doi, title, status FROM papers").fetchall()
    known_dois, known_titles, known_statuses = {}, {}, {}
    for r in rows:
        pid = r["paper_id"]
        known_statuses[pid] = r["status"]
        c_doi = canonical_doi(r["doi"])
        if c_doi:
            known_dois[c_doi] = pid
        if r["title"]:
            norm = normalize_title(r["title"])
            if norm:
                known_titles[norm] = pid
    return known_dois, known_titles, known_statuses


def get_known_repos(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    rows = conn.execute("SELECT repo_id, full_name, status FROM repos").fetchall()
    by_id = {r["repo_id"]: r["status"] for r in rows}
    by_full_name = {
        r["full_name"].lower(): r["repo_id"] for r in rows if r["full_name"]
    }
    return by_id, by_full_name


_PAPER_FIELDS = (
    "arxiv_id", "doi", "title", "year", "venue", "abstract", "source",
    "query_block", "landing_url", "content_hash",
)
_REPO_FIELDS = (
    "full_name", "org", "name", "html_url", "default_branch", "commit_sha",
    "pin_policy", "licence", "stars", "pushed_at", "topics", "is_fork",
    "source", "query_block", "content_hash",
)
_PAPER_STATUS_FIELDS = (
    "raw_path", "md_path", "chunks_path", "source_used", "extraction_engine",
    "extraction_quality", "num_chunks", "content_hash", "error",
)
_REPO_STATUS_FIELDS = (
    "raw_path", "code_dir", "chunks_path", "commit_sha", "licence", "stars",
    "pushed_at", "num_files", "num_chunks", "content_hash", "error",
)
_KIND_TABLES = {
    PAPER: ("papers", "paper_id", _PAPER_FIELDS, _PAPER_STATUS_FIELDS),
    REPO: ("repos", "repo_id", _REPO_FIELDS, _REPO_STATUS_FIELDS),
}


def hash_seen(conn: sqlite3.Connection, kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    table, pk, _, _ = _KIND_TABLES[kind]
    row = conn.execute(
        f"SELECT {pk} AS eid FROM {table} WHERE content_hash = ?", (value,)
    ).fetchone()
    return row["eid"] if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PATH_COLUMNS = ("raw_path", "md_path", "chunks_path", "code_dir")

_COALESCED_COLUMNS = frozenset({"content_hash"})


def _reject_unportable_paths(fields: dict) -> None:
    """Enforce C3 at the write boundary. Checks traversal as well as absoluteness:
    `1_raw_data/../../etc/x` is relative, so an isabs check alone would pass it through."""
    for column in _PATH_COLUMNS:
        value = fields.get(column)
        if not value:
            continue
        if os.path.isabs(value):
            raise ValueError(
                f"{column} must be storage-relative (see config.rel_path), "
                f"got absolute: {value!r}"
            )
        normalized = os.path.normpath(value)
        if normalized == os.pardir or normalized.startswith(os.pardir + os.sep):
            raise ValueError(
                f"{column} escapes the lake (see config.rel_path): {value!r}"
            )


def _insert_or_update(
    conn: sqlite3.Connection, table: str, pk: str, pk_value: str,
    allowed: tuple, fields: dict, extra: dict | None = None,
) -> None:
    """Insert a row or refresh it in place, always preserving `created_at`."""
    now = _now()
    cols = [pk, "created_at", "updated_at"]
    values = [pk_value, now, now]
    for column, value in (extra or {}).items():
        cols.append(column)
        values.append(value)
    for key in allowed:
        if key in fields:
            cols.append(key)
            values.append(fields[key])

    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(
        f"{c}=COALESCE(excluded.{c}, {table}.{c})" if c in _COALESCED_COLUMNS
        else f"{c}=excluded.{c}"
        for c in cols if c not in (pk, "created_at")
    )
    with conn:
        conn.execute(
            f"""INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})
            ON CONFLICT({pk}) DO UPDATE SET {updates}""",
            values,
        )


def _upsert(conn: sqlite3.Connection, kind: str, entity_id: str, status: str, fields: dict) -> None:
    """Insert or refresh an entity row, preserving `created_at`.

    `status` is written unconditionally, so upserting a known entity resets it to
    the caller's default and regresses anything already further along the FSM.
    Discovery stages must therefore fast-skip entities already in
    `get_known_identifiers()` / `get_known_repos()` before upserting; this
    matches the sibling pipelines, where the skip lives in the ingest stage.
    """
    _reject_unportable_paths(fields)
    table, pk, allowed, _ = _KIND_TABLES[kind]
    _insert_or_update(conn, table, pk, entity_id, allowed, fields, {"status": status})


def upsert_paper(conn: sqlite3.Connection, paper_id: str, *, status: str = DISCOVERED, **fields) -> None:
    _upsert(conn, PAPER, paper_id, status, fields)


def upsert_repo(conn: sqlite3.Connection, repo_id: str, *, status: str = DISCOVERED, **fields) -> None:
    _upsert(conn, REPO, repo_id, status, fields)


def update_status(conn: sqlite3.Connection, kind: str, entity_id: str, status: str, **fields) -> None:
    _reject_unportable_paths(fields)
    table, pk, _, allowed = _KIND_TABLES[kind]
    cols = ["status = ?", "updated_at = ?"]
    values = [status, _now()]
    for key in allowed:
        if key in fields:
            cols.append(f"{key} = ?")
            values.append(fields[key])
    values.append(entity_id)
    with conn:
        conn.execute(f"UPDATE {table} SET {', '.join(cols)} WHERE {pk} = ?", values)


def clear_raw_path(conn: sqlite3.Connection, kind: str, entity_id: str) -> None:
    """Drop the Tier 1 pointer once its staging file is deleted.

    Without this the registry claims a file the purge already removed.
    """
    table, pk, _, _ = _KIND_TABLES[kind]
    with conn:
        conn.execute(
            f"UPDATE {table} SET raw_path = NULL, updated_at = ? WHERE {pk} = ?",
            (_now(), entity_id),
        )


def checkpoint_wal(conn: sqlite3.Connection) -> None:
    """Fold the -wal file back into the .db file (C3).

    The lake is copied to the server as a directory tree; a copy taken while an
    un-checkpointed -wal exists loses every committed row still sitting in it.
    """
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def get_by_status(conn: sqlite3.Connection, kind: str, status: str) -> list[sqlite3.Row]:
    table, _, _, _ = _KIND_TABLES[kind]
    return conn.execute(f"SELECT * FROM {table} WHERE status = ?", (status,)).fetchall()


def get_status(conn: sqlite3.Connection, kind: str, entity_id: str) -> str | None:
    table, pk, _, _ = _KIND_TABLES[kind]
    row = conn.execute(
        f"SELECT status FROM {table} WHERE {pk} = ?", (entity_id,)
    ).fetchone()
    return row["status"] if row else None


_MODEL_FIELDS = ("display_name", "repo_id", "paper_id", "org", "repo_full_name",
                 "catalog_status", "note")


def upsert_model(conn: sqlite3.Connection, model_id: str, **fields) -> None:
    _insert_or_update(conn, "models", "model_id", model_id, _MODEL_FIELDS, fields)


def seed_catalog(conn: sqlite3.Connection) -> int:
    from core import catalog

    entries = (*catalog.CATALOG_SEED, *catalog.WATCH_SEED)
    for entry in entries:
        upsert_model(conn, entry["model_id"], **{
            k: v for k, v in entry.items() if k != "model_id"
        })
    return len(entries)


def get_cursor(conn: sqlite3.Connection, source: str, block: str, default: dict | None = None) -> dict:
    row = conn.execute(
        "SELECT state FROM cursors WHERE source = ? AND block = ?", (source, block)
    ).fetchone()
    if row is None or row["state"] is None:
        return default or {}
    return json.loads(row["state"])


def save_cursor(conn: sqlite3.Connection, source: str, block: str, state: dict) -> None:
    with conn:
        conn.execute(
            """INSERT INTO cursors (source, block, state) VALUES (?, ?, ?)
            ON CONFLICT(source, block) DO UPDATE SET state=excluded.state""",
            (source, block, json.dumps(state)),
        )
