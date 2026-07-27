import os
from typing import Any

import pytest
from sqlalchemy import delete, select

from db import get_postgres_session
from models import Document
from submit import submit_url


@pytest.fixture
def cleanup() -> Any:
    created: list[str] = []
    yield created
    with get_postgres_session() as session:
        for document_id in created:
            session.execute(delete(Document).where(Document.id == document_id))
        session.commit()


def test_submit_url_creates_a_document(cleanup: list[str]) -> None:
    url = f"https://example.com/{os.urandom(4).hex()}"
    result = submit_url(url)
    cleanup.append(result["document_id"])
    assert result["created"] is True
    with get_postgres_session() as session:
        document = session.get(Document, result["document_id"])
        assert document is not None
        assert document.url == url
        assert document.extracted_at is None


def test_submit_url_is_idempotent_across_equivalent_urls(cleanup: list[str]) -> None:
    slug = os.urandom(4).hex()
    first = submit_url(f"https://example.com/{slug}")
    cleanup.append(first["document_id"])
    second = submit_url(f"https://Example.com/{slug}/?utm_source=twitter#top")
    assert second["created"] is False
    assert second["document_id"] == first["document_id"]
    with get_postgres_session() as session:
        matches = session.scalars(
            select(Document).where(Document.canonical_url == f"https://example.com/{slug}")
        ).all()
        assert len(matches) == 1
