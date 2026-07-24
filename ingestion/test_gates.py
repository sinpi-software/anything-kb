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


from gates import evaluate_gate  # noqa: E402


def _gate(op: str, value: object) -> dict[str, object]:
    return {"source": "sc", "field": "score", "op": op, "value": value}


def test_gate_passes_numeric() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), '{"score": 7}')
    assert ok is True


def test_gate_fails_numeric() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), '{"score": 3}')
    assert ok is False
    assert "score" in reason


def test_gate_fails_when_source_missing() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), None)
    assert ok is False and "no output" in reason


def test_gate_fails_on_non_json() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), "not json at all")
    assert ok is False


def test_gate_fails_on_missing_field() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), '{"other": 1}')
    assert ok is False


def test_gate_type_mismatch_fails_not_raises() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), '{"score": "high"}')
    assert ok is False and "mismatch" in reason


def test_gate_contains_and_in() -> None:
    assert evaluate_gate({"source": "c", "field": "categories", "op": "contains", "value": "tech"},
                         '{"categories": ["tech", "science"]}')[0] is True
    assert evaluate_gate({"source": "c", "field": "cat", "op": "in", "value": ["a", "b"]},
                         '{"cat": "a"}')[0] is True


def test_gate_non_string_field_fails_not_raises() -> None:
    ok, _ = evaluate_gate({"source": "s", "field": ["x"], "op": "eq", "value": 1}, '{"score": 7}')
    assert ok is False
