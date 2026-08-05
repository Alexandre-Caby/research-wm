"""Orchestrates GitHub repository ingestion: org scan, topic search, single repo.

Discovery never spends a network call on a repo already in the registry --
`R.get_known_repos` is checked before any GitHub API request. `--limit` is a
global budget across the whole invocation, spent as sources are walked in
order, so an anonymous 60-req/hour run can be capped predictably.
"""

import argparse
import sqlite3

from core import catalog
from core import config
from core import registry as R
from core.log import get_logger
from core.sources import github

logger = get_logger(__name__)

# HY-World's releases move past any pinned commit fast enough that a sha pin
# would go stale between runs; every other catalog repo pins for reproducibility.
_HEAD_PINNED = frozenset({"Tencent-Hunyuan/HY-World"})


def _pin_policy(full_name: str) -> str:
    return "head" if full_name in _HEAD_PINNED else "sha"


def _ingest_repo(conn: sqlite3.Connection, repo_meta: dict, source: str, query_block: str | None) -> str:
    """Fetch, filter and persist one repo. Returns the terminal status it landed in."""
    repo_id = repo_meta["repo_id"]
    full_name = repo_meta["full_name"]

    R.upsert_repo(
        conn, repo_id, status=R.DISCOVERED,
        full_name=full_name, org=repo_meta["org"], name=repo_meta["name"],
        html_url=repo_meta["html_url"], default_branch=repo_meta["default_branch"],
        pin_policy=_pin_policy(full_name), licence=repo_meta["licence"],
        stars=repo_meta["stars"], pushed_at=repo_meta["pushed_at"],
        topics=repo_meta["topics"], is_fork=repo_meta["is_fork"],
        source=source, query_block=query_block,
    )

    try:
        code_dir, manifest, num_files = github.fetch_and_filter(conn, repo_meta)
    except github.FetchError as e:
        logger.warning("Fetch failed for %s: %s", full_name, e)
        R.update_status(conn, R.REPO, repo_id, R.FAILED, error=str(e))
        return R.FAILED
    except Exception as e:
        # A single malformed repo (e.g. a tarball tarfile can't stream, an
        # unexpected payload shape) must never take the whole run down with it.
        logger.exception("Unexpected error ingesting %s", full_name)
        R.update_status(conn, R.REPO, repo_id, R.FAILED, error=str(e))
        return R.FAILED

    commit_sha = repo_meta.get("commit_sha")

    if not manifest:
        R.update_status(conn, R.REPO, repo_id, R.EMPTY, commit_sha=commit_sha)
        logger.info("EMPTY %s: no text file survived filtering.", full_name)
        return R.EMPTY

    manifest_digest = R.manifest_hash(manifest)
    duplicate_of = R.hash_seen(conn, R.REPO, manifest_digest)
    if duplicate_of and duplicate_of != repo_id:
        R.update_status(conn, R.REPO, repo_id, R.DUPLICATE, commit_sha=commit_sha,
                         content_hash=manifest_digest, error=f"duplicate of {duplicate_of}")
        logger.info("DUPLICATE %s: manifest matches %s.", full_name, duplicate_of)
        return R.DUPLICATE

    R.update_status(conn, R.REPO, repo_id, R.EXTRACTED, code_dir=code_dir, num_files=num_files,
                     content_hash=manifest_digest, commit_sha=commit_sha)
    logger.info("EXTRACTED %s: %d file(s) -> %s", full_name, num_files, code_dir)
    return R.EXTRACTED


def _should_skip(full_name: str, by_full_name: dict[str, str]) -> bool:
    return full_name.lower() in by_full_name or full_name in catalog.EXCLUDED_REPOS


def _run_discovery_block(
    conn: sqlite3.Connection, by_full_name: dict[str, str], source: str, query_block: str | None,
    cursor_source: str, cursor_block: str, discover, limit: int | None,
) -> int:
    """Drive one discover_* generator-like paginator to exhaustion or `limit`."""
    cursor = R.get_cursor(conn, cursor_source, cursor_block, default={})
    processed = 0
    while limit is None or processed < limit:
        repos, next_cursor = discover(cursor)
        if not repos:
            R.save_cursor(conn, cursor_source, cursor_block, {})
            break
        for repo_meta in repos:
            if limit is not None and processed >= limit:
                break
            full_name = repo_meta["full_name"]
            if _should_skip(full_name, by_full_name):
                continue
            _ingest_repo(conn, repo_meta, source, query_block)
            by_full_name[full_name.lower()] = repo_meta["repo_id"]
            processed += 1
        cursor = next_cursor or {}
        R.save_cursor(conn, cursor_source, cursor_block, cursor)
        if next_cursor is None:
            break
    return processed


def run_org_mode(conn: sqlite3.Connection, limit: int | None) -> int:
    _, by_full_name = R.get_known_repos(conn)
    total, remaining = 0, limit
    for org in catalog.SCAN_ORGS:
        if remaining is not None and remaining <= 0:
            break
        n = _run_discovery_block(
            conn, by_full_name, source=f"github_org:{org}", query_block=None,
            cursor_source="github_org", cursor_block=org,
            discover=lambda c, org=org: github.discover_org(org, c),
            limit=remaining,
        )
        total += n
        if remaining is not None:
            remaining -= n
    logger.info("org mode: %d repo(s) ingested across %d org(s).", total, len(catalog.SCAN_ORGS))
    return total


def run_search_mode(conn: sqlite3.Connection, limit: int | None) -> int:
    _, by_full_name = R.get_known_repos(conn)
    total, remaining = 0, limit
    for query in config.GITHUB_TOPIC_QUERIES:
        if remaining is not None and remaining <= 0:
            break
        n = _run_discovery_block(
            conn, by_full_name, source="github_search", query_block=query,
            cursor_source="github_search", cursor_block=query,
            discover=lambda c, query=query: github.discover_search(query, c),
            limit=remaining,
        )
        total += n
        if remaining is not None:
            remaining -= n
    logger.info("search mode: %d repo(s) ingested across %d topic quer(y/ies).",
                total, len(config.GITHUB_TOPIC_QUERIES))
    return total


def run_repo_mode(conn: sqlite3.Connection, full_name: str) -> int:
    _, by_full_name = R.get_known_repos(conn)
    if _should_skip(full_name, by_full_name):
        logger.info("Skip %s: already known or excluded.", full_name)
        return 0
    repo_meta = github.resolve_repo(full_name)
    if repo_meta is None:
        logger.warning("Could not resolve %s.", full_name)
        return 0
    _ingest_repo(conn, repo_meta, source="github_repo", query_block=None)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest GitHub repositories into Tier 3 code storage.")
    parser.add_argument("--mode", choices=("org", "search", "repo"), required=True)
    parser.add_argument("--limit", type=int, default=None, help="Global cap on new repos ingested this run.")
    parser.add_argument("--repo", default=None, help="org/name, required for --mode repo")
    args = parser.parse_args()

    if args.mode == "repo" and not args.repo:
        parser.error("--mode repo requires --repo org/name")

    R.init_db(config.DB_PATH)
    conn = R.connect(config.DB_PATH)
    try:
        if args.mode == "org":
            run_org_mode(conn, args.limit)
        elif args.mode == "search":
            run_search_mode(conn, args.limit)
        else:
            run_repo_mode(conn, args.repo)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
