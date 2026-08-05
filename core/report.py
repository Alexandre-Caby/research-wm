"""QC stats over the registry: entity counts by kind, status, source and block."""

import sqlite3

from core import config
from core import registry as R
from core.log import get_logger

logger = get_logger(__name__)


def _counts(conn: sqlite3.Connection, table: str, column: str) -> dict:
    # Interpolation is safe: table and column are hard-coded at call sites only.
    rows = conn.execute(
        f"SELECT {column}, COUNT(*) AS n FROM {table} GROUP BY {column}"
    ).fetchall()
    return {row[column]: row["n"] for row in rows}


def _scalar(conn: sqlite3.Connection, table: str, expr: str) -> int:
    return conn.execute(f"SELECT {expr} AS n FROM {table}").fetchone()["n"]


def run_report(conn: sqlite3.Connection | None = None) -> dict:
    # Only close a connection we opened ourselves; a caller-supplied conn
    # is the caller's to close.
    owns_conn = conn is None
    if conn is None:
        conn = R.connect(config.DB_PATH)

    try:
        out = {
            "papers_total": _scalar(conn, "papers", "COUNT(*)"),
            "repos_total": _scalar(conn, "repos", "COUNT(*)"),
            "papers_by_status": _counts(conn, "papers", "status"),
            "repos_by_status": _counts(conn, "repos", "status"),
            "papers_by_source": _counts(conn, "papers", "source"),
            "repos_by_source": _counts(conn, "repos", "source"),
            "papers_by_query_block": _counts(conn, "papers", "query_block"),
            "models_by_catalog_status": _counts(conn, "models", "catalog_status"),
            "num_chunks_total": (
                _scalar(conn, "papers", "COALESCE(SUM(num_chunks), 0)")
                + _scalar(conn, "repos", "COALESCE(SUM(num_chunks), 0)")
            ),
        }
    finally:
        if owns_conn:
            conn.close()

    logger.info(
        "QC report: papers=%d repos=%d chunks=%d papers_by_status=%s repos_by_status=%s",
        out["papers_total"], out["repos_total"], out["num_chunks_total"],
        out["papers_by_status"], out["repos_by_status"],
    )
    return out


if __name__ == "__main__":
    conn = R.connect(config.DB_PATH)
    try:
        report = run_report(conn)
    finally:
        conn.close()
    for key, value in report.items():
        if isinstance(value, dict):
            print(key)
            for sub_key, count in value.items():
                print(f"  {str(sub_key):<24} {count}")
        else:
            print(f"{key:<24} {value}")
