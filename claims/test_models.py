import os

from db import get_postgres_session
from models import Claim, Document


def test_document_defaults_are_applied() -> None:
    with get_postgres_session() as session:
        document = Document(url="https://example.com/a", canonical_url=f"https://example.com/{os.urandom(4).hex()}")
        session.add(document)
        session.commit()
        session.refresh(document)
        assert document.attempts == 0
        assert document.extracted_at is None
        assert document.reported_at is None
        session.delete(document)
        session.commit()


def test_claim_defaults_to_unselected_and_unjudged() -> None:
    with get_postgres_session() as session:
        document = Document(url="https://example.com/b", canonical_url=f"https://example.com/{os.urandom(4).hex()}")
        session.add(document)
        session.flush()
        claim = Claim(document_id=document.id, text="A thing happened.", claim_type="empirical", checkworthiness=0.9)
        session.add(claim)
        session.commit()
        session.refresh(claim)
        assert claim.selected_for_verification is False
        assert claim.verdict is None
        assert claim.attempts == 0
        session.delete(claim)
        session.delete(document)
        session.commit()
