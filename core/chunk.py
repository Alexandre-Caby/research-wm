"""Splits extracted papers and repository source trees into a single chunk schema.

Papers: front-matter markdown -> `##`-section split -> overlapping word windows.
Code: `.py` files split at top-level def/class boundaries so a chunk is a whole
symbol; every other extension gets a sliding line window. Both kinds converge on
the same chunk dict, written to config.VECTOR_DIR for embed.py to pick up.
"""
import argparse
import json
import os
import re
import sqlite3

from core import config
from core import registry as R
from core.log import get_logger

logger = get_logger(__name__)

_FRONT_MATTER_RE = re.compile(r'^---\n(.*?)\n---\n(.*)$', re.DOTALL)
_SECTION_SPLIT_RE = re.compile(r'\n##\s+(.+?)\n')
_PY_BOUNDARY_RE = re.compile(r'^(\s*)(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)')

_CHUNK_FIELDS = (
    "chunk_id", "kind", "entity_id", "model_id", "title", "source", "query_block",
    "section", "section_index", "chunk_index", "word_count", "text", "doi", "year",
    "extraction_quality", "repo_full_name", "file_path", "symbol", "symbol_kind",
    "start_line", "end_line", "language", "commit_sha", "licence",
)
_INT_FIELDS = {"section_index", "chunk_index", "word_count", "year", "start_line", "end_line"}
_FLOAT_FIELDS = {"extraction_quality"}


def _new_chunk(**overrides) -> dict:
    row = {f: (0 if f in _INT_FIELDS else 0.0 if f in _FLOAT_FIELDS else "") for f in _CHUNK_FIELDS}
    row.update(overrides)
    return row


def parse_front_matter(content: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_block, body = match.groups()
    metadata = {}
    for line in yaml_block.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip().strip('"')
    return metadata, body


def split_into_sections(body: str) -> list[tuple[str, str]]:
    parts = _SECTION_SPLIT_RE.split(body)
    sections = []

    if parts and parts[0].strip():
        sections.append(("Introduction", parts[0].strip()))

    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            sections.append((heading, text))

    if not sections:
        sections = [("Document", body.strip())]

    return sections


def chunk_words(
    text: str, size: int = config.CHUNK_SIZE_WORDS, overlap: int = config.CHUNK_OVERLAP_WORDS,
) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text.strip()] if text.strip() else []

    chunks = []
    start = 0
    while start < len(words):
        end = start + size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += size - overlap

    if len(chunks) > 1 and len(chunks[-1].split()) < config.MIN_TRAILING_CHUNK_WORDS:
        chunks[-2] = chunks[-2] + " " + chunks[-1]
        chunks.pop()

    return chunks


def build_paper_chunks(body: str, paper_row: sqlite3.Row) -> list[dict]:
    sections = split_into_sections(body)
    chunks = []

    for sec_idx, (heading, text) in enumerate(sections):
        for c_idx, piece in enumerate(chunk_words(text)):
            chunks.append(_new_chunk(
                chunk_id=f"{paper_row['paper_id']}__s{sec_idx}_c{c_idx}",
                kind="paper",
                entity_id=paper_row["paper_id"],
                title=paper_row["title"] or "",
                source=paper_row["source"] or "",
                query_block=paper_row["query_block"] or "",
                section=heading,
                section_index=sec_idx,
                chunk_index=c_idx,
                word_count=len(piece.split()),
                text=piece,
                doi=paper_row["doi"] or "",
                year=paper_row["year"] or 0,
                extraction_quality=paper_row["extraction_quality"] or 0.0,
            ))

    return chunks


def _python_symbol_segments(lines: list[str]) -> list[dict]:
    boundaries = []
    for i, line in enumerate(lines):
        m = _PY_BOUNDARY_RE.match(line)
        if m:
            indent, keyword, name = m.groups()
            kind = "class" if keyword == "class" else ("method" if indent else "function")
            boundaries.append((i, name, kind))

    if not boundaries:
        return []

    segments = []
    if boundaries[0][0] > 0:
        segments.append({"start": 0, "end": boundaries[0][0], "symbol": "module", "symbol_kind": "module"})

    for idx, (line_no, name, kind) in enumerate(boundaries):
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        segments.append({"start": line_no, "end": end, "symbol": name, "symbol_kind": kind})

    return segments


def _merged(dest: dict, src: dict, *, dest_first: bool) -> dict:
    """Combine two segments, joining their symbol names so the merged chunk's
    header still names everything inside it (a plain end-extension would keep
    dest's old symbol label while silently absorbing src's code under it)."""
    order = (dest, src) if dest_first else (src, dest)
    return {
        "start": min(dest["start"], src["start"]),
        "end": max(dest["end"], src["end"]),
        "symbol": "+".join(s["symbol"] for s in order),
        "symbol_kind": "merged",
    }


def _merge_short_segments(segments: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for seg in segments:
        length = seg["end"] - seg["start"]
        if merged and length < config.CODE_CHUNK_MIN_LINES:
            merged[-1] = _merged(merged[-1], seg, dest_first=True)
        else:
            merged.append(seg)

    # A too-short leading segment (e.g. a two-line preamble) has no predecessor
    # to absorb it, so fold it into the segment that follows instead.
    if len(merged) > 1 and (merged[0]["end"] - merged[0]["start"]) < config.CODE_CHUNK_MIN_LINES:
        merged[1] = _merged(merged[1], merged[0], dest_first=True)
        merged.pop(0)

    return merged


def _sliding_window_segments(num_lines: int, size: int) -> list[tuple[int, int]]:
    if num_lines <= 0:
        return []
    if num_lines <= size:
        return [(0, num_lines)]

    segments = []
    start = 0
    while start < num_lines:
        end = min(start + size, num_lines)
        segments.append((start, end))
        if end >= num_lines:
            break
        start = end

    if len(segments) > 1 and (segments[-1][1] - segments[-1][0]) < config.CODE_CHUNK_MIN_LINES:
        segments[-2] = (segments[-2][0], segments[-1][1])
        segments.pop()

    return segments


def build_code_chunks(text: str, rel_file_path: str, repo_row: sqlite3.Row) -> list[dict]:
    lines = text.splitlines()
    if not lines:
        return []

    language = os.path.splitext(rel_file_path)[1].lstrip(".") or "text"

    segments: list[dict] = []
    if rel_file_path.endswith(".py"):
        segments = _merge_short_segments(_python_symbol_segments(lines))
    if not segments:
        segments = [
            {"start": s, "end": e, "symbol": f"L{s + 1}-{e}", "symbol_kind": "window"}
            for s, e in _sliding_window_segments(len(lines), config.CODE_CHUNK_MAX_LINES)
        ]

    full_name = repo_row["full_name"] or ""
    chunks = []
    for idx, seg in enumerate(segments):
        body = "\n".join(lines[seg["start"]:seg["end"]])
        header = f"# {full_name} / {rel_file_path} / {seg['symbol']}\n"
        piece = header + body
        chunks.append(_new_chunk(
            chunk_id=f"{repo_row['repo_id']}__{rel_file_path}__{idx}",
            kind="code",
            entity_id=repo_row["repo_id"],
            title=full_name,
            source=repo_row["source"] or "",
            query_block=repo_row["query_block"] or "",
            chunk_index=idx,
            word_count=len(piece.split()),
            text=piece,
            repo_full_name=full_name,
            file_path=rel_file_path,
            symbol=seg["symbol"],
            symbol_kind=seg["symbol_kind"],
            start_line=seg["start"] + 1,
            end_line=seg["end"],
            language=language,
            commit_sha=repo_row["commit_sha"] or "",
            licence=repo_row["licence"] or "",
        ))

    return chunks


def _iter_code_files(code_dir: str):
    for root, dirs, files in os.walk(code_dir):
        dirs[:] = [d for d in dirs if d not in config.EXCLUDE_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1] not in config.CODE_EXTENSIONS:
                continue
            if any(pat in fname for pat in config.EXCLUDE_FILE_PATTERNS):
                continue
            full = os.path.join(root, fname)
            try:
                if os.path.getsize(full) > config.MAX_TEXT_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield full


def _chunk_repo_tree(code_dir: str, repo_row: sqlite3.Row) -> list[dict]:
    chunks = []
    for file_abs in _iter_code_files(code_dir):
        rel_file = os.path.relpath(file_abs, code_dir)
        try:
            with open(file_abs, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            logger.warning("Skipping unreadable file %s: %s", file_abs, e)
            continue
        chunks.extend(build_code_chunks(text, rel_file, repo_row))
    return chunks


def _write_chunks(conn: sqlite3.Connection, kind: str, entity_id: str, chunks: list[dict]) -> None:
    if not chunks:
        R.update_status(conn, kind, entity_id, R.EMPTY, error="no chunks produced")
        return

    chunks_path = os.path.join(config.VECTOR_DIR, f"{entity_id}.json")
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    R.update_status(
        conn, kind, entity_id, R.CHUNKED,
        chunks_path=config.rel_path(chunks_path), num_chunks=len(chunks),
    )
    logger.info("chunked %s (%d chunks)", entity_id, len(chunks))


def _chunk_papers(conn: sqlite3.Connection, limit: int | None) -> None:
    rows = R.get_by_status(conn, R.PAPER, R.EXTRACTED)
    if limit is not None:
        rows = rows[:limit]

    for row in rows:
        paper_id = row["paper_id"]
        try:
            md_abs = config.abs_path(row["md_path"]) if row["md_path"] else None
            if not md_abs or not os.path.exists(md_abs):
                R.update_status(conn, R.PAPER, paper_id, R.EMPTY, error="md_path missing")
                continue

            with open(md_abs, "r", encoding="utf-8") as f:
                content = f.read()

            _, body = parse_front_matter(content)
            if not body.strip():
                R.update_status(conn, R.PAPER, paper_id, R.EMPTY, error="empty body")
                continue

            _write_chunks(conn, R.PAPER, paper_id, build_paper_chunks(body, row))
        except Exception as e:
            logger.warning("Failed to chunk paper %s: %s", paper_id, e)
            R.update_status(conn, R.PAPER, paper_id, R.FAILED, error=str(e))


def _chunk_repos(conn: sqlite3.Connection, limit: int | None) -> None:
    rows = R.get_by_status(conn, R.REPO, R.EXTRACTED)
    if limit is not None:
        rows = rows[:limit]

    for row in rows:
        repo_id = row["repo_id"]
        try:
            code_dir_abs = config.abs_path(row["code_dir"]) if row["code_dir"] else None
            if not code_dir_abs or not os.path.isdir(code_dir_abs):
                R.update_status(conn, R.REPO, repo_id, R.EMPTY, error="code_dir missing")
                continue

            _write_chunks(conn, R.REPO, repo_id, _chunk_repo_tree(code_dir_abs, row))
        except Exception as e:
            logger.warning("Failed to chunk repo %s: %s", repo_id, e)
            R.update_status(conn, R.REPO, repo_id, R.FAILED, error=str(e))


def run_chunk(limit: int | None = None, kind: str = "all") -> None:
    conn = R.connect(config.DB_PATH)
    if kind in ("all", "paper"):
        _chunk_papers(conn, limit)
    if kind in ("all", "code"):
        _chunk_repos(conn, limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chunk extracted papers and repositories.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--kind", choices=("paper", "code", "all"), default="all")
    args = parser.parse_args()
    run_chunk(limit=args.limit, kind=args.kind)
