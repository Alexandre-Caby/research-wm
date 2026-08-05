"""Creates the registry schema and seeds the curated catalog into the lake."""

import os

from core import config
from core import registry as R
from core.log import get_logger

logger = get_logger(__name__)


def _warn_placeholder_credentials() -> None:
    """Surface unset credentials now rather than as throttling mid-ingest.

    OpenAlex and Unpaywall demote a placeholder contact out of the polite pool,
    and the anonymous GitHub limit is 60 requests/hour. Both fail as slowness,
    not as errors, so nothing downstream would tell you why a run crawled.
    """
    if config.EMAIL_CONTACT == config.DEFAULT_CONTACT_EMAIL:
        logger.warning(
            "WM_CONTACT_EMAIL unset: discovery APIs will throttle the placeholder "
            "contact %s.", config.DEFAULT_CONTACT_EMAIL,
        )
    if not config.GITHUB_TOKEN:
        logger.warning(
            "GITHUB_TOKEN unset: repository discovery is capped at the anonymous "
            "rate limit of 60 requests/hour."
        )


def run_bootstrap(db_path: str | os.PathLike | None = None) -> int:
    """Initialize the four tables and seed the catalog. Returns the models seeded."""
    _warn_placeholder_credentials()
    db_path = db_path or config.DB_PATH
    R.init_db(db_path)
    conn = R.connect(db_path)
    try:
        seeded = R.seed_catalog(conn)
    finally:
        conn.close()
    logger.info("Bootstrap complete: schema ready at %s, %d catalog models seeded.",
                db_path, seeded)
    return seeded


if __name__ == "__main__":
    print(f"- Catalog models seeded: {run_bootstrap()}")
