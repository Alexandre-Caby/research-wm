"""Paper discovery orchestrator: discover (multi-source) -> dedup -> resolve -> fetch -> register."""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import html2text
import requests
from bs4 import BeautifulSoup

from core import config
from core import registry as R
from core.log import get_logger
from core.sources import arxiv, openalex, resolvers, semanticscholar
from core.sources.resolvers import HEADERS

logger = get_logger(__name__)

SOURCES = {"openalex": openalex, "arxiv": arxiv, "semanticscholar": semanticscholar}

INGEST_WORKERS = int(os.environ.get("INGEST_WORKERS", "8"))

REQUEST_TIMEOUT = 20
_h2t = html2text.HTML2Text()
_h2t.ignore_links = False


def download_pdf(url: str, dest_stub: str) -> str | None:
    try:
        res = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        try:
            if res.status_code != 200 or res.content[:5] != b"%PDF-":
                return None
            path = os.path.join(config.RAW_DIR, f"{dest_stub}.pdf")
            with open(path, "wb") as f:
                f.write(res.content)
            return path
        finally:
            res.close()
    except requests.RequestException as e:
        logger.warning("Failed PDF fetch from %s: %s", url, e)
        return None


def scrape_html_to_md(url: str, dest_stub: str) -> str | None:
    try:
        res = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        try:
            if res.status_code != 200:
                return None
            soup = BeautifulSoup(res.text, "html.parser")
            for el in soup(["script", "style", "nav", "footer", "header"]):
                el.decompose()
            body = soup.find("article") or soup.find("main") or soup.body
            if not body:
                return None
            markdown_text = _h2t.handle(str(body))
            if len(markdown_text.strip()) <= 300:
                return None
            path = os.path.join(config.PAPERS_DIR, f"{dest_stub}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(markdown_text)
            return path
        finally:
            res.close()
    except requests.RequestException as e:
        logger.warning("Failed HTML scrape from %s: %s", url, e)
        return None


def _default_cursor(source_name: str) -> dict:
    if source_name == "arxiv":
        return {"start": 0}
    if source_name == "semanticscholar":
        return {"offset": 0}
    return {"next_page": 1}


def _paper_id(source: str, record: dict, doi: str | None) -> str | None:
    if doi:
        normalized = R.normalize_title(doi)
    elif record.get("arxiv_id"):
        normalized = R.normalize_title(record["arxiv_id"])
    else:
        basis = f'{record.get("landing_url") or ""}|{record.get("abstract") or ""}|{record.get("title") or ""}'
        if not basis.strip("|"):
            return None
        normalized = R.content_hash(basis)[:16]
    return f"{source}_{normalized}"


def _try_fetch(candidates: list[tuple[str, str]], dest_stub: str) -> tuple[str | None, str | None, str | None]:
    for name, url in candidates:
        pdf_path = download_pdf(url, dest_stub)
        if pdf_path:
            return "pdf", pdf_path, name
        md_path = scrape_html_to_md(url, dest_stub)
        if md_path:
            return "html", md_path, name
    return None, None, None


def _resolve_and_fetch_task(paper_id: str, record: dict):
    """Runs in a worker thread: network-only, touches no DB connection."""
    candidates = resolvers.resolve(record)
    kind, path, used = _try_fetch(candidates, paper_id)
    return paper_id, kind, path, used


def _apply_fetch_result(conn, paper_id: str, kind: str | None, path: str | None, used: str | None) -> None:
    if kind == "pdf":
        R.update_status(conn, R.PAPER, paper_id, R.FETCHED, raw_path=config.rel_path(path), source_used=used)
    elif kind == "html":
        R.update_status(
            conn, R.PAPER, paper_id, R.EXTRACTED,
            md_path=config.rel_path(path), extraction_engine="html", source_used=used,
        )
    else:
        R.update_status(conn, R.PAPER, paper_id, R.PAYWALLED)


def run_ingest(sources: tuple[str, ...] = ("openalex", "arxiv", "semanticscholar"),
               limit: int | None = None, per_page: int = 25) -> None:
    R.init_db(config.DB_PATH)
    conn = R.connect(config.DB_PATH)
    known_dois, known_titles, known_statuses = R.get_known_identifiers(conn)
    logger.info("Ingest: %d DOIs and %d titles pre-loaded in memory for fast skip.",
                len(known_dois), len(known_titles))

    registered = 0

    for source_name in sources:
        module = SOURCES[source_name]
        for block, query in config.KEYWORD_BLOCKS.items():
            if limit is not None and registered >= limit:
                break

            cursor = R.get_cursor(conn, source_name, block, default=_default_cursor(source_name))
            if cursor.get("exhausted"):
                continue

            try:
                records, next_cursor = module.discover(block, query, cursor, per_page)
            except Exception as e:
                logger.warning("discover crashed for %s/%s: %s", source_name, block, e)
                continue

            page_consumed = True
            with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as executor:
                futures = {}

                for record in records:
                    if limit is not None and registered >= limit:
                        page_consumed = False
                        break

                    try:
                        doi = R.canonical_doi(record.get("doi"))
                        title = record.get("title")
                        norm_title = R.normalize_title(title) if title else None

                        existing_id = (doi and known_dois.get(doi)) or (norm_title and known_titles.get(norm_title))
                        if existing_id:
                            if R.get_status(conn, R.PAPER, existing_id) == R.PAYWALLED:
                                fut = executor.submit(_resolve_and_fetch_task, existing_id, record)
                                futures[fut] = existing_id
                            continue

                        paper_id = _paper_id(source_name, record, doi)
                        if paper_id is None:
                            logger.warning("Skipping record with no doi/arxiv_id/content: %r", record.get("title"))
                            continue

                        R.upsert_paper(
                            conn, paper_id,
                            arxiv_id=record.get("arxiv_id"), doi=doi, title=title,
                            year=record.get("year"), venue=record.get("venue"),
                            abstract=record.get("abstract"),
                            source=source_name, query_block=block,
                            landing_url=record.get("landing_url"), status=R.DISCOVERED,
                        )
                        registered += 1

                        if doi:
                            known_dois[doi] = paper_id
                        if norm_title:
                            known_titles[norm_title] = paper_id

                        fut = executor.submit(_resolve_and_fetch_task, paper_id, record)
                        futures[fut] = paper_id
                    except Exception as e:
                        logger.warning("Failed to register record %r: %s", record.get("title"), e)

                for future in as_completed(futures):
                    paper_id = futures[future]
                    try:
                        _, kind, path, used = future.result()
                    except Exception as e:
                        logger.warning("resolve/fetch crashed for %s: %s", paper_id, e)
                        kind, path, used = None, None, None
                    try:
                        _apply_fetch_result(conn, paper_id, kind, path, used)
                    except Exception as e:
                        logger.warning("Failed to apply fetch result for %s: %s", paper_id, e)

            if page_consumed:
                R.save_cursor(conn, source_name, block, next_cursor)

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="openalex,arxiv,semanticscholar")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-page", type=int, default=25)
    args = parser.parse_args()
    run_ingest(
        sources=tuple(s.strip() for s in args.source.split(",") if s.strip()),
        limit=args.limit, per_page=args.per_page,
    )
