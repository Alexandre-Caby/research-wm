"""GitHub source: repo discovery plus a single-tarball fetch-filter-persist pass.

A repository is never cloned. One GET resolves its pinned commit, one GET streams
the tarball to Tier 1, `tarfile` walks it in place, and the archive is deleted in
the same pass -- so ingesting N repositories costs 2N API calls, not one per file.
"""

import hashlib
import os
import sqlite3
import tarfile
import time

import requests

from core import config
from core.log import get_logger

logger = get_logger(__name__)

GITHUB_API = "https://api.github.com"
PER_PAGE = 100


class FetchError(Exception):
    """A repository could not be resolved or fetched -- distinct from "fetched, but empty"."""


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


def _github_get(url: str, params: dict | None = None, max_retries: int = 5) -> requests.Response | None:
    response = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=_github_headers(), params=params, timeout=30)
        except requests.RequestException as e:
            logger.warning("Network error on GitHub request (%d/%d): %s. Retrying in 5s...",
                            attempt, max_retries, e)
            time.sleep(5)
            continue
        if response.status_code == 200:
            return response
        if response.status_code in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0":
            reset_at = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait_s = max(reset_at - int(time.time()), 1) + 2
            logger.info("GitHub rate limit reached, waiting %ss...", wait_s)
            time.sleep(min(wait_s, 900))
            continue
        return response
    return response


def normalize_repo(payload: dict) -> dict:
    license_info = payload.get("license") or {}
    return {
        "repo_id": f"gh_{payload['id']}",
        "full_name": payload["full_name"],
        "org": payload["full_name"].split("/", 1)[0],
        "name": payload["name"],
        "html_url": payload["html_url"],
        "default_branch": payload.get("default_branch") or "main",
        "licence": license_info.get("spdx_id"),
        "stars": payload.get("stargazers_count", 0),
        "pushed_at": payload.get("pushed_at"),
        "topics": ",".join(payload.get("topics") or []),
        "is_fork": bool(payload.get("fork", False)),
    }


def discover_org(org: str, cursor: dict) -> tuple[list[dict], dict | None]:
    """Page an org's repos, falling back to the user-account endpoint on 404.

    `cursor["kind"]` remembers which endpoint answered so later pages skip the
    org-first probe -- user accounts like `danijar` 404 on every `/orgs/...` page.
    """
    page = cursor.get("page", 1)
    kind = cursor.get("kind", "orgs")
    params = {"type": "public", "sort": "pushed", "per_page": PER_PAGE, "page": page}

    response = _github_get(f"{GITHUB_API}/{kind}/{org}/repos", params=params)
    if response is not None and response.status_code == 404 and kind == "orgs":
        kind = "users"
        response = _github_get(f"{GITHUB_API}/{kind}/{org}/repos", params=params)

    if response is None or response.status_code != 200:
        logger.warning("Org discovery failed for %s (page %d): %s", org, page,
                        response.status_code if response is not None else "no response")
        return [], None

    repos = response.json()
    if not isinstance(repos, list) or not repos:
        return [], None

    next_cursor = {"page": page + 1, "kind": kind} if len(repos) == PER_PAGE else None
    return [normalize_repo(r) for r in repos], next_cursor


def discover_search(query: str, cursor: dict) -> tuple[list[dict], dict | None]:
    """Page `/search/repositories` by stars. GitHub caps search at 1000 results."""
    page = cursor.get("page", 1)
    response = _github_get(
        f"{GITHUB_API}/search/repositories",
        params={"q": query, "sort": "stars", "order": "desc", "per_page": PER_PAGE, "page": page},
    )
    if response is None or response.status_code != 200:
        logger.warning("Search discovery failed for %r (page %d): %s", query, page,
                        response.status_code if response is not None else "no response")
        return [], None

    items = response.json().get("items", [])
    if not items:
        return [], None

    at_cap = page * PER_PAGE >= 1000
    next_cursor = None if at_cap or len(items) < PER_PAGE else {"page": page + 1}
    return [normalize_repo(r) for r in items], next_cursor


def resolve_repo(full_name: str) -> dict | None:
    response = _github_get(f"{GITHUB_API}/repos/{full_name}")
    if response is None or response.status_code != 200:
        logger.warning("Could not resolve %s: %s", full_name,
                        response.status_code if response is not None else "no response")
        return None
    return normalize_repo(response.json())


def _resolve_commit_sha(full_name: str, branch: str) -> str | None:
    response = _github_get(f"{GITHUB_API}/repos/{full_name}/commits/{branch}")
    if response is None or response.status_code != 200:
        return None
    return response.json().get("sha")


def _download_tarball(full_name: str, sha: str, org: str, name: str) -> str | None:
    url = f"{GITHUB_API}/repos/{full_name}/tarball/{sha}"
    tar_path = os.path.join(config.RAW_DIR, f"{org}__{name}.tar.gz")
    try:
        with requests.get(url, headers=_github_headers(), stream=True, timeout=180) as response:
            if response.status_code != 200:
                logger.warning("Tarball download failed for %s: HTTP %s", full_name, response.status_code)
                return None
            total = 0
            with open(tar_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    total += len(chunk)
                    if total > config.MAX_TARBALL_BYTES:
                        logger.warning("Tarball for %s exceeds %d bytes, aborting.",
                                        full_name, config.MAX_TARBALL_BYTES)
                        f.close()
                        os.remove(tar_path)
                        return None
                    f.write(chunk)
    except requests.RequestException as e:
        logger.warning("Network error downloading tarball for %s: %s", full_name, e)
        if os.path.exists(tar_path):
            os.remove(tar_path)
        return None
    return tar_path


def _is_excluded(relative: str) -> bool:
    dirname = os.path.dirname(relative)
    padded = f"/{dirname}/"
    if any(f"/{excluded}/" in padded for excluded in config.EXCLUDE_DIRS):
        return True
    basename = os.path.basename(relative)
    return any(pattern in basename for pattern in config.EXCLUDE_FILE_PATTERNS)


def _process_member(tar: tarfile.TarFile, member: tarfile.TarInfo, code_root: str) -> tuple[str, str] | None:
    if not member.isfile():
        return None

    parts = member.name.replace("\\", "/").split("/", 1)
    if len(parts) < 2 or not parts[1]:
        return None
    relative = parts[1]

    # Tar-traversal guard (the CVE-2007-4559 class): an archive member can smuggle
    # ".." or an absolute path and write outside code_root on naive extraction.
    if relative.startswith("/") or ".." in relative.split("/"):
        logger.warning("Skipping path-escaping tar member: %s", member.name)
        return None

    ext = os.path.splitext(relative)[1].lower()
    if ext in config.WEIGHT_EXTENSIONS or ext not in config.CODE_EXTENSIONS:
        return None
    if _is_excluded(relative):
        return None
    if member.size > config.MAX_TEXT_FILE_BYTES:
        return None

    dest_path = os.path.join(code_root, relative)
    dest_abs = os.path.abspath(dest_path)
    root_abs = os.path.abspath(code_root)
    if dest_abs != root_abs and not dest_abs.startswith(root_abs + os.sep):
        logger.warning("Skipping tar member resolving outside code_root: %s", member.name)
        return None

    extracted = tar.extractfile(member)
    if extracted is None:
        return None
    content = extracted.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(text)
    return config.rel_path(dest_path), hashlib.sha256(content).hexdigest()


def fetch_and_filter(conn: sqlite3.Connection, repo_meta: dict) -> tuple[str | None, list[tuple[str, str]], int]:
    """Resolve the pinned commit, stream+filter the tarball, delete it, return the survivors.

    Mutates `repo_meta["commit_sha"]` in place: the caller persists it to the
    registry, and the fixed 3-tuple return has no slot for it.
    """
    full_name = repo_meta["full_name"]
    org, name = repo_meta["org"], repo_meta["name"]
    branch = repo_meta["default_branch"]

    sha = _resolve_commit_sha(full_name, branch)
    if sha is None:
        raise FetchError(f"could not resolve commit for {full_name}@{branch}")
    repo_meta["commit_sha"] = sha

    tar_path = _download_tarball(full_name, sha, org, name)
    if tar_path is None:
        raise FetchError(f"tarball download failed for {full_name}@{sha}")

    code_root = os.path.join(config.CODE_DIR, f"{org}__{name}")
    manifest: list[tuple[str, str]] = []
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar:
                result = _process_member(tar, member, code_root)
                if result is not None:
                    manifest.append(result)
    except tarfile.TarError as e:
        os.remove(tar_path)
        raise FetchError(f"corrupted tarball for {full_name}: {e}") from e
    os.remove(tar_path)

    if not manifest:
        return None, [], 0
    return config.rel_path(code_root), manifest, len(manifest)
