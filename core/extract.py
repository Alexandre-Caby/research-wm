"""PDF-to-markdown extraction for papers. Repositories skip this stage entirely --
they arrive as plain text and go straight from FETCHED to EXTRACTED elsewhere.

fitz only: no vision-model fallback. If quality is below threshold the fitz text
is kept anyway and the low score is recorded, so a paper never blocks the pipeline
waiting on a heavyweight dependency this project does not carry.
"""
import argparse
import os
import sqlite3
from statistics import median
from concurrent.futures import ProcessPoolExecutor, as_completed

import fitz  # PyMuPDF

from core import config
from core import registry as R
from core.log import get_logger

logger = get_logger(__name__)

BOILERPLATE_MIN_PAGES = 4


def extract_with_fitz(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    pages_blocks = []
    text_counts: dict[str, int] = {}

    for page in doc:
        page_dict = page.get_text("dict")
        blocks_out = []

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue

            line_texts = []
            max_size = 0.0
            for line in lines:
                spans = line.get("spans", [])
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if not line_text:
                    continue
                line_texts.append(line_text)
                sizes = [s.get("size", 0) for s in spans]
                if sizes:
                    max_size = max(max_size, max(sizes))

            if not line_texts:
                continue

            paragraph = line_texts[0]
            for lt in line_texts[1:]:
                if paragraph.endswith('-') and lt[:1].islower():
                    paragraph = paragraph[:-1] + lt
                else:
                    paragraph += " " + lt

            bbox = block["bbox"]
            blocks_out.append({"text": paragraph, "size": max_size, "y": bbox[1], "x": bbox[0]})
            text_counts[paragraph.lower().strip()] = text_counts.get(paragraph.lower().strip(), 0) + 1

        pages_blocks.append(blocks_out)

    doc.close()

    boilerplate = set()
    if total_pages >= BOILERPLATE_MIN_PAGES:
        threshold = max(2, int(total_pages * 0.4))
        boilerplate = {t for t, c in text_counts.items() if c >= threshold}

    all_sizes = [b["size"] for page in pages_blocks for b in page if b["size"] > 0]
    body_size = median(all_sizes) if all_sizes else 10.0

    markdown_lines = []
    bibliography_reached = False

    for page_blocks in pages_blocks:
        if bibliography_reached:
            break
        page_blocks.sort(key=lambda b: (round(b["y"]), b["x"]))

        for b in page_blocks:
            text = b["text"]
            norm = text.lower().strip()

            if norm in boilerplate:
                continue

            if any(text.startswith(x) for x in ["References", "REFERENCES", "Bibliography", "BIBLIOGRAPHY"]):
                bibliography_reached = True
                break

            if b["size"] >= body_size * 1.15 and len(text) < 120:
                markdown_lines.append(f"\n## {text}\n")
            else:
                markdown_lines.append(text)

    return "\n\n".join(markdown_lines)


def assess_quality(text: str) -> tuple[float, str]:
    if not text or len(text.strip()) < 300:
        return 0.0, "too_short"

    length = len(text)
    alnum_ratio = sum(c.isalnum() or c.isspace() for c in text) / length
    lines = [l for l in text.split("\n") if l.strip()]
    avg_line_len = sum(len(l) for l in lines) / max(len(lines), 1)
    short_line_ratio = sum(1 for l in lines if len(l) < 15) / max(len(lines), 1)

    score = 1.0
    reasons = []
    if alnum_ratio < 0.85:
        score -= 0.4
        reasons.append("low_alnum_ratio")
    if avg_line_len < 25:
        score -= 0.3
        reasons.append("short_avg_line")
    if short_line_ratio > 0.4:
        score -= 0.3
        reasons.append("fragmented_lines")

    return max(score, 0.0), (",".join(reasons) if reasons else "ok")


def build_front_matter(paper_row: dict, quality: float) -> str:
    title = str(paper_row.get("title") or "Unknown").replace('"', "'")
    doi = paper_row.get("doi") or "N/A"
    year = paper_row.get("year") if paper_row.get("year") is not None else "N/A"
    block = paper_row.get("query_block") or "N/A"

    return (
        "---\n"
        f'title: "{title}"\n'
        f'doi: "{doi}"\n'
        f"year: {year}\n"
        f'source_block: "{block}"\n'
        f'extraction_engine: "fitz"\n'
        f"extraction_quality: {round(quality, 2)}\n"
        "---\n\n"
    )


def _fitz_only(pdf_path: str) -> tuple[str, float, str]:
    """Runs in a worker process: isolates a crashing PDF from the main run."""
    try:
        text = extract_with_fitz(pdf_path)
    except Exception as e:
        return "", 0.0, f"fitz_error:{e}"
    score, reason = assess_quality(text)
    return text, score, reason


def run_extract(limit: int | None = None, workers: int | None = None) -> None:
    conn = R.connect(config.DB_PATH)
    papers = [p for p in R.get_by_status(conn, R.PAPER, R.FETCHED) if p["raw_path"]]

    if limit:
        papers = papers[:limit]
    if not papers:
        logger.info("No fetched PDFs to extract.")
        return

    logger.info("Extracting %d paper(s)...", len(papers))

    futures = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for paper in papers:
            paper_id = paper["paper_id"]
            try:
                raw_abs = config.abs_path(paper["raw_path"])
            except ValueError as e:
                logger.warning("Unportable raw_path for %s: %s", paper_id, e)
                R.update_status(conn, R.PAPER, paper_id, R.FAILED, error=str(e))
                continue
            if not raw_abs or not os.path.exists(raw_abs):
                R.update_status(conn, R.PAPER, paper_id, R.FAILED, error="raw_path missing")
                continue
            futures[executor.submit(_fitz_only, raw_abs)] = paper

        for future in as_completed(futures):
            paper = futures[future]
            paper_id = paper["paper_id"]

            try:
                text, score, reason = future.result()
            except Exception as e:
                text, score, reason = "", 0.0, f"worker_error:{e}"

            _finalize_paper(conn, paper, text, score, reason)


def _finalize_paper(
    conn: sqlite3.Connection, paper: sqlite3.Row, text: str, score: float, reason: str,
) -> None:
    paper_id = paper["paper_id"]

    if len(text.strip()) <= 300:
        R.update_status(
            conn, R.PAPER, paper_id, R.FAILED,
            extraction_quality=round(score, 2), error=reason,
        )
        logger.info("Extraction failed (%s): %s", reason, paper_id)
        return

    content_hash = R.content_hash(text)
    dup_id = R.hash_seen(conn, R.PAPER, content_hash)
    if dup_id and dup_id != paper_id:
        R.update_status(conn, R.PAPER, paper_id, R.DUPLICATE, content_hash=content_hash)
        logger.info("Duplicate content (matches %s): %s", dup_id, paper_id)
        return

    md_path = os.path.join(config.PAPERS_DIR, f"{paper_id}.md")
    front_matter = build_front_matter(dict(paper), score)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(front_matter + text)

    R.update_status(
        conn, R.PAPER, paper_id, R.EXTRACTED,
        md_path=config.rel_path(md_path),
        extraction_quality=round(score, 2),
        content_hash=content_hash,
    )
    if score < config.QUALITY_THRESHOLD:
        logger.info("Low quality extraction kept (%s, score=%.2f): %s", reason, score, paper_id)
    else:
        logger.info("Extracted (score=%.2f): %s", score, paper_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract fetched PDFs into markdown.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    run_extract(limit=args.limit, workers=args.workers)
