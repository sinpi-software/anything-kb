import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

from sqlalchemy import inspect

from db import get_postgres_session
from models import TransformRunStatus


def test_skipped_status_exists() -> None:
    assert TransformRunStatus.SKIPPED.value == "skipped"


def test_transformation_has_name_and_gate_columns() -> None:
    with get_postgres_session() as session:
        assert session.bind is not None
        cols = {c["name"] for c in inspect(session.bind).get_columns("transformations")}
    assert {"name", "gate"} <= cols
