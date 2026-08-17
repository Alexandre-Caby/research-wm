"""Batch-encodes chunked papers and repos into LanceDB with streaming checkpoints.

Chunk records already carry every field the store needs (chunk.py stamps title,
source, doi, repo metadata etc. at chunk time), so this stage only adds the
vector and upserts -- no per-kind row-mapping step required.
"""
import argparse
import json
import os
import sqlite3

from core import config
from core import registry as R
from core.store import LanceStore
from core.log import get_logger

logger = get_logger(__name__)

MAX_CHUNKS_PER_BATCH = int(os.environ.get("EMBED_MAX_CHUNKS_PER_BATCH", "1500"))


def _pending_entities(conn: sqlite3.Connection) -> list[tuple[str, sqlite3.Row]]:
    entities = [(R.PAPER, row) for row in R.get_by_status(conn, R.PAPER, R.CHUNKED)]
    entities += [(R.REPO, row) for row in R.get_by_status(conn, R.REPO, R.CHUNKED)]
    return entities


def _entity_id(kind: str, row: sqlite3.Row) -> str:
    return row["paper_id"] if kind == R.PAPER else row["repo_id"]


def run_embed(
    text_encoder=None, limit: int | None = None, batch_size: int = 32,
    purge_chunks_json: bool = True, max_chunks_per_batch: int = MAX_CHUNKS_PER_BATCH,
) -> None:
    if text_encoder is None:
        if os.environ.get("EMBED_BACKEND") == "ollama":
            from core.encoders import OllamaTextEncoder
            text_encoder = OllamaTextEncoder()
        else:
            from core.encoders import TextEncoder
            text_encoder = TextEncoder()

    conn = R.connect(config.DB_PATH)
    entities = _pending_entities(conn)

    if limit is not None:
        entities = entities[:limit]
    if not entities:
        logger.info("No chunked entities to embed.")
        return

    store = LanceStore(lance_dir=config.LANCE_DIR, dim=config.TEXT_DIM)
    logger.info(
        "Embedding %d entities, capped at %d chunks/batch (encoder batch_size=%d)...",
        len(entities), max_chunks_per_batch, batch_size,
    )

    batch_chunks: list[dict] = []
    embedded: list[tuple[str, str]] = []
    json_paths_to_purge: list[str] = []

    def flush() -> None:
        nonlocal batch_chunks, embedded, json_paths_to_purge
        if not batch_chunks:
            return

        vectors = text_encoder.encode([c["text"] for c in batch_chunks], batch_size=batch_size)
        for chunk_row, vector in zip(batch_chunks, vectors):
            chunk_row["vector"] = vector

        store.upsert(batch_chunks)

        for kind, entity_id in embedded:
            R.update_status(conn, kind, entity_id, R.EMBEDDED)
            logger.info("Embedded: %s", entity_id)

        if purge_chunks_json:
            for json_p in json_paths_to_purge:
                try:
                    if os.path.exists(json_p):
                        os.remove(json_p)
                except OSError as e:
                    logger.warning("Failed to purge chunk json %s: %s", json_p, e)

        batch_chunks, embedded, json_paths_to_purge = [], [], []

    for kind, row in entities:
        entity_id = _entity_id(kind, row)
        try:
            chunks_path_rel = row["chunks_path"]
            if not chunks_path_rel:
                R.update_status(conn, kind, entity_id, R.EMPTY, error="chunks_path missing")
                continue

            chunks_path = config.abs_path(chunks_path_rel)
            if not os.path.exists(chunks_path):
                R.update_status(conn, kind, entity_id, R.EMPTY, error="chunks_path missing")
                continue

            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
        except Exception as e:
            logger.warning("Failed to load chunks for %s: %s", entity_id, e)
            R.update_status(conn, kind, entity_id, R.FAILED, error=str(e))
            continue

        if not chunks:
            R.update_status(conn, kind, entity_id, R.EMPTY, error="no chunks in file")
            continue

        if batch_chunks and len(batch_chunks) + len(chunks) > max_chunks_per_batch:
            flush()

        batch_chunks.extend(chunks)
        embedded.append((kind, entity_id))
        json_paths_to_purge.append(chunks_path)

        if len(batch_chunks) >= max_chunks_per_batch:
            flush()

    flush()  # final partial batch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embed chunked papers and repos into LanceDB.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    run_embed(limit=args.limit, batch_size=args.batch_size)
