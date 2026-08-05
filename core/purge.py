"""Deletes Tier 1 staging artifacts once their extraction has been validated."""
import argparse
import os
import sqlite3

from core import config
from core import registry as R
from core.log import get_logger

logger = get_logger(__name__)

_VALIDATED_STATUSES = (R.EXTRACTED, R.CHUNKED, R.EMBEDDED)


def _candidates(
    conn: sqlite3.Connection, min_quality: float | None
) -> tuple[list[tuple[str, str, str]], list[tuple[str, float]]]:
    raw_dir = os.path.abspath(config.RAW_DIR)
    to_purge, kept = [], []
    for kind in (R.PAPER, R.REPO):
        for status in _VALIDATED_STATUSES:
            for row in R.get_by_status(conn, kind, status):
                entity_id = row["paper_id"] if kind == R.PAPER else row["repo_id"]
                stored = row["raw_path"]
                if not stored:
                    continue
                try:
                    path = config.abs_path(stored)
                except ValueError as e:
                    # Predates the write-boundary guard, or bypassed it; skip rather than abort the run.
                    logger.warning("Skipping unportable raw_path on %s: %s", entity_id, e)
                    continue
                if not os.path.exists(path):
                    continue
                # Tier 1 only: tiers 2, 3 and 4 are permanent.
                if os.path.dirname(os.path.abspath(path)) != raw_dir:
                    continue

                quality = row["extraction_quality"] if kind == R.PAPER else None
                if min_quality is not None and quality is not None and quality < min_quality:
                    kept.append((path, quality))
                    continue
                to_purge.append((kind, entity_id, path))
    return to_purge, kept


def run_purge(
    dry_run: bool = False, assume_yes: bool = False, min_quality: float | None = None
) -> int:
    conn = R.connect(config.DB_PATH)
    try:
        to_purge, kept = _candidates(conn, min_quality)

        for path, quality in kept:
            logger.info("Skip (quality %.2f < %.2f, keep raw for reprocessing): %s",
                        quality, min_quality, path)

        if not to_purge:
            logger.info("Nothing to purge.")
            return 0

        if dry_run:
            total = 0
            for _, _, path in to_purge:
                size = os.path.getsize(path)
                total += size
                logger.info("[DRY RUN] Would purge: %s (%.1f KB)", path, size / 1024)
            logger.info("[DRY RUN] %d files, %.1f MB total.", len(to_purge),
                        total / (1024 * 1024))
            return len(to_purge)

        if not assume_yes:
            confirm = input(
                f"Purge {len(to_purge)} staging file(s) from {config.RAW_DIR}? "
                "This cannot be undone. (yes/no): "
            )
            if confirm.strip().lower() != "yes":
                logger.info("Purge cancelled.")
                return 0

        purged, freed = 0, 0
        for kind, entity_id, path in to_purge:
            try:
                size = os.path.getsize(path)
                os.remove(path)
                R.clear_raw_path(conn, kind, entity_id)
                purged += 1
                freed += size
                logger.info("Purged: %s", path)
            except OSError as e:
                logger.warning("Failed to purge %s: %s", path, e)

        logger.info("Purge complete: %d files removed, %.1f MB freed.", purged,
                    freed / (1024 * 1024))
        return purged
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Purge Tier 1 staging files once their extraction is validated."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview deletions without removing any file.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt.")
    parser.add_argument("--min-quality", type=float, default=None,
                        help="Keep the raw file if extraction_quality is below this threshold.")
    args = parser.parse_args()
    run_purge(dry_run=args.dry_run, assume_yes=args.yes, min_quality=args.min_quality)
