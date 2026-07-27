"""`submit-url`: put a link into the pipeline.

The only flow that takes a parameter. Everything downstream is a sweep, so this does
nothing but insert a row — fetching, extraction and verification happen on their own
schedules and can each fail and retry without losing the submission.
"""

from __future__ import annotations

import sys
from typing import Any

from prefect import flow, get_run_logger
from sqlalchemy import select

from db import get_postgres_session
from fetch import canonicalize_url
from models import Document


@flow(name="submit-url")
def submit_url(url: str) -> dict[str, Any]:
    logger = get_run_logger()
    canonical = canonicalize_url(url)
    with get_postgres_session() as session:
        existing = session.scalars(select(Document).where(Document.canonical_url == canonical)).first()
        if existing is not None:
            logger.info("submit: %s already submitted as %s", canonical, existing.id)
            return {"document_id": str(existing.id), "created": False}
        document = Document(url=url, canonical_url=canonical)
        session.add(document)
        session.commit()
        session.refresh(document)
        logger.info("submit: %s → %s", canonical, document.id)
        return {"document_id": str(document.id), "created": True}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: uv run python submit.py <url>")
    print(submit_url(sys.argv[1]))
