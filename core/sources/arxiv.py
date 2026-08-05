"""arXiv discovery source: Atom API, no auth, preprints with a direct PDF link.

The most important source for this field — most world-model work is published
here first, often months before (or instead of) a venue.
"""
import re
import warnings

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from core import config
from core.log import get_logger

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = get_logger(__name__)

API_URL = "http://export.arxiv.org/api/query"
REQUEST_TIMEOUT = 20

_VERSION_SUFFIX_RE = re.compile(r"v\d+$")
_CAT_QUERY = " OR ".join(f"cat:{c}" for c in config.ARXIV_CATEGORIES)


def _strip_version(arxiv_id: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", arxiv_id)


def _pdf_url(entry) -> str | None:
    for link in entry.find_all("link"):
        if link.get("title") == "pdf":
            return link.get("href")
    return None


def _record_from_entry(entry) -> dict | None:
    id_url = entry.find("id")
    if not id_url or not id_url.text:
        return None
    raw_id = id_url.text.strip().rsplit("/", 1)[-1]
    arxiv_id = _strip_version(raw_id)

    title_tag = entry.find("title")
    title = title_tag.text.strip().replace("\n", " ") if title_tag else "Untitled Work"
    summary_tag = entry.find("summary")
    abstract = summary_tag.text.strip().replace("\n", " ") if summary_tag else ""
    published_tag = entry.find("published")
    year = int(published_tag.text[:4]) if published_tag and published_tag.text else None
    primary_category = entry.find("arxiv:primary_category")
    venue = primary_category.get("term") if primary_category else None

    pdf_url = _pdf_url(entry)
    landing_url = f"https://arxiv.org/abs/{arxiv_id}"

    return {
        "arxiv_id": arxiv_id,
        "doi": None,
        "title": title,
        "year": year,
        "venue": venue,
        "abstract": abstract,
        "landing_url": landing_url,
        "ids": {"arxiv_id": arxiv_id, "oa_url": pdf_url, "doi": None},
    }


def discover(block: str, query: str, cursor: dict, per_page: int = 25) -> tuple[list[dict], dict]:
    start = cursor.get("start", 0)
    search_query = f"all:({query}) AND ({_CAT_QUERY})"
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": per_page,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    try:
        with requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT) as res:
            res.raise_for_status()
            xml = res.text
    except requests.RequestException as e:
        logger.error("arXiv discover failed for block %s: %s", block, e)
        return [], cursor

    soup = BeautifulSoup(xml, "html.parser")
    entries = soup.find_all("entry")
    records = []
    for entry in entries:
        try:
            record = _record_from_entry(entry)
        except Exception as e:
            logger.warning("Failed to parse arXiv entry for block %s: %s", block, e)
            continue
        if record:
            records.append(record)

    total_tag = soup.find("opensearch:totalresults")
    total_results = int(total_tag.text) if total_tag and total_tag.text else 0
    next_start = start + per_page if start + per_page < total_results else 0
    next_cursor = {"start": next_start}

    return records, next_cursor
