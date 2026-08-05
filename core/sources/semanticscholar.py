"""Semantic Scholar discovery source: unauthenticated, aggressively rate-limited.

No API key in this project, so a 429 is expected under normal use, not an
error worth retrying — the caller just gets an empty page back and moves on.
"""
import time

import requests

from core.log import get_logger

logger = get_logger(__name__)

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
REQUEST_TIMEOUT = 20
FIELDS = "title,abstract,year,venue,externalIds,openAccessPdf"
INTER_CALL_DELAY = 1.0


def _record_from_paper(paper: dict) -> dict | None:
    if not paper.get("title"):
        return None
    external_ids = paper.get("externalIds") or {}
    doi = external_ids.get("DOI")
    arxiv_id = external_ids.get("ArXiv")
    oa_pdf = (paper.get("openAccessPdf") or {}).get("url")

    return {
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": paper["title"],
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "abstract": paper.get("abstract") or "",
        "landing_url": f"https://www.semanticscholar.org/paper/{paper['paperId']}"
        if paper.get("paperId") else None,
        "ids": {"doi": doi, "arxiv_id": arxiv_id, "oa_url": oa_pdf},
    }


def discover(block: str, query: str, cursor: dict, per_page: int = 25) -> tuple[list[dict], dict]:
    offset = cursor.get("offset", 0)
    params = {
        "query": query,
        "fields": FIELDS,
        "offset": offset,
        "limit": per_page,
    }

    time.sleep(INTER_CALL_DELAY)
    try:
        with requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT) as res:
            if res.status_code == 429:
                logger.warning("Semantic Scholar rate-limited for block %s; skipping this page.", block)
                return [], cursor
            res.raise_for_status()
            payload = res.json()
    except requests.RequestException as e:
        logger.error("Semantic Scholar discover failed for block %s: %s", block, e)
        return [], cursor

    papers = payload.get("data") or []
    records = [r for p in papers if (r := _record_from_paper(p)) is not None]

    total = payload.get("total", 0)
    next_offset = payload.get("next")
    if next_offset is None:
        next_offset = offset + per_page if offset + per_page < total else 0
    next_cursor = {"offset": next_offset}

    return records, next_cursor
