import logging
import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import inspect
from sqlalchemy import text as sqlalchemy_text

import transformations
from db import get_postgres_session
from models import Artifact, Org, Transformation, TransformationType, TransformRun, TransformRunStatus


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


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqlalchemy_text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def _run_gate_case(monkeypatch: pytest.MonkeyPatch, score: int, threshold: int, expect_second_called: bool) -> None:
    """Seeds a throwaway Org + source Artifact + two Transformations (the 2nd gated on the
    1st's name), monkeypatches DISPATCH so both handlers are fakes (no real LLM calls), and
    runs the real `run_transform_pipeline` to prove the gate actually halts the pipeline."""
    # run_transform_pipeline calls get_run_logger(), which raises outside a live flow/task
    # run context; stub it so we can call the flow's plain function (`.fn`) directly and
    # skip spinning up Prefect's orchestration engine entirely.
    monkeypatch.setattr(transformations, "get_run_logger", lambda: logging.getLogger("test_gates"))

    second_calls: list[tuple[str, str]] = []
    org_id: str | None = None
    artifact_id: str | None = None
    t1_id: str | None = None
    t2_id: str | None = None
    source_output_id: str | None = None
    downstream_output_id: str | None = None

    def source_handler(input_id: str, transformation_id: str) -> str:
        with get_postgres_session() as session:
            out = Artifact(
                org_id=org_id,
                ref_table_name=Artifact.__tablename__,
                ref_table_id=input_id,
                type="application/json",
                data=f'{{"score": {score}}}',
            )
            session.add(out)
            session.flush()
            out_id = out.id
            session.commit()
        return out_id

    def downstream_handler(input_id: str, transformation_id: str) -> str:
        second_calls.append((input_id, transformation_id))
        with get_postgres_session() as session:
            out = Artifact(
                org_id=org_id,
                ref_table_name=Artifact.__tablename__,
                ref_table_id=input_id,
                type="text/markdown",
                data="downstream ran",
            )
            session.add(out)
            session.flush()
            out_id = out.id
            session.commit()
        return out_id

    monkeypatch.setitem(transformations.DISPATCH, TransformationType.SCORE.value, source_handler)
    monkeypatch.setitem(transformations.DISPATCH, TransformationType.SUMMARIZE.value, downstream_handler)

    try:
        with get_postgres_session() as session:
            org = Org(name=f"gate-e2e-{uuid.uuid4()}")
            session.add(org)
            session.flush()
            org_id = org.id

            artifact = Artifact(
                org_id=org_id,
                ref_table_name=Artifact.__tablename__,
                ref_table_id=uuid.uuid4(),
                type="text/markdown",
                data="source markdown",
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id

            t1 = Transformation(
                org_id=org_id,
                name="sc",
                type=TransformationType.SCORE.value,
                model="test/model",
                prompt="score it",
                position=0,
            )
            session.add(t1)
            session.flush()
            t1_id = t1.id

            t2 = Transformation(
                org_id=org_id,
                name="downstream",
                type=TransformationType.SUMMARIZE.value,
                model="test/model",
                prompt="summarize",
                position=1,
                gate={"source": "sc", "field": "score", "op": "gte", "value": threshold},
            )
            session.add(t2)
            session.flush()
            t2_id = t2.id
            session.commit()

        transformations.run_transform_pipeline.fn(artifact_id)

        assert bool(second_calls) is expect_second_called

        with get_postgres_session() as session:
            source_runs = session.query(TransformRun).filter_by(transformation_id=t1_id).all()
            assert any(r.status == TransformRunStatus.COMPLETED.value for r in source_runs)
            source_output_id = next(r.output_artifact_id for r in source_runs if r.output_artifact_id)

            second_runs = session.query(TransformRun).filter_by(transformation_id=t2_id).all()
            if expect_second_called:
                assert any(r.status == TransformRunStatus.COMPLETED.value for r in second_runs)
                assert not any(r.status == TransformRunStatus.SKIPPED.value for r in second_runs)
                downstream_output_id = next(r.output_artifact_id for r in second_runs if r.output_artifact_id)
            else:
                assert any(r.status == TransformRunStatus.SKIPPED.value for r in second_runs)
                assert not any(r.status == TransformRunStatus.COMPLETED.value for r in second_runs)
    finally:
        with get_postgres_session() as session:
            transformation_ids = [i for i in (t1_id, t2_id) if i is not None]
            if transformation_ids:
                session.query(TransformRun).filter(
                    TransformRun.transformation_id.in_(transformation_ids)
                ).delete(synchronize_session=False)
            for artifact_ident in (downstream_output_id, source_output_id, artifact_id):
                if artifact_ident is None:
                    continue
                artifact_row = session.get(Artifact, artifact_ident)
                if artifact_row is not None:
                    session.delete(artifact_row)
            for transformation_ident in (t2_id, t1_id):
                if transformation_ident is None:
                    continue
                transformation_row = session.get(Transformation, transformation_ident)
                if transformation_row is not None:
                    session.delete(transformation_row)
            if org_id is not None:
                org_row = session.get(Org, org_id)
                if org_row is not None:
                    session.delete(org_row)
            session.commit()


@requires_postgres
def test_failing_gate_halts_pipeline_and_records_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_gate_case(monkeypatch, score=3, threshold=5, expect_second_called=False)


@requires_postgres
def test_passing_gate_lets_pipeline_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_gate_case(monkeypatch, score=7, threshold=5, expect_second_called=True)
