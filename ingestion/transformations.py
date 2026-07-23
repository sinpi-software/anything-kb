import os
from collections.abc import Callable
from typing import Any

from openrouter import OpenRouter
from prefect import flow, task
from prefect.concurrency.sync import concurrency
from prefect.logging import get_run_logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update

import config
from db import get_postgres_session
from models import Artifact, Transformation, TransformationType, TransformRun, TransformRunStatus


class LLMParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None


class _TransformOutput(BaseModel):
    def to_model(self) -> tuple[str, str]:
        """(data, content_type) for the produced artifact; JSON unless a subclass overrides."""
        return self.model_dump_json(), "application/json"


class LLMScoreTransformOutput(_TransformOutput):
    score: int
    rationale: str


class LLMSummarizeTransformOutput(_TransformOutput):
    summary: str

    def to_model(self) -> tuple[str, str]:
        # A summary is prose — store it as markdown so the next chain step reads plain text.
        return self.summary, "text/markdown"


class LLMClassifyTransformOutput(_TransformOutput):
    categories: list[str]


def _run_llm_transform(
    artifact_id: str, transformation_id: str, output_type: type[_TransformOutput], schema_name: str
) -> str:
    logger = get_run_logger()
    logger.info("Running %s on artifact %s (transformation %s)", schema_name, artifact_id, transformation_id)

    with get_postgres_session() as session:
        transformation = session.get(Transformation, transformation_id)
        if transformation is None:
            raise ValueError(f"Transformation {transformation_id} not found")
        model = transformation.model
        prompt = transformation.prompt
        params = transformation.params or {}

        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        message = artifact.data
        org_id = artifact.org_id
        logger.info("Sending %d chars to %s", len(message), model)

        with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client:
            with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
                result = client.chat.send(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": message},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": schema_name, "schema": output_type.model_json_schema()},
                    },
                    **params,
                )
            logger.info("LLM usage for artifact %s: %s", artifact_id, result.usage)

            content = result.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("LLM returned no text content")

            output = output_type.model_validate_json(content)
            data, content_type = output.to_model()
            out_artifact = Artifact(
                org_id=org_id,
                ref_table_name=Artifact.__tablename__,
                ref_table_id=artifact_id,
                type=content_type,
                data=data,
            )
            session.add(out_artifact)
            session.flush()
            out_artifact_id = out_artifact.id

        session.commit()

    logger.info("%s produced artifact %s from %s", schema_name, out_artifact_id, artifact_id)
    return out_artifact_id


@task
def llm_score_transform(artifact_id: str, transformation_id: str) -> str:
    return _run_llm_transform(artifact_id, transformation_id, LLMScoreTransformOutput, "score")


@task
def llm_summarize_transform(artifact_id: str, transformation_id: str) -> str:
    return _run_llm_transform(artifact_id, transformation_id, LLMSummarizeTransformOutput, "summarize")


@task
def llm_classify_transform(artifact_id: str, transformation_id: str) -> str:
    return _run_llm_transform(artifact_id, transformation_id, LLMClassifyTransformOutput, "classify")


# Maps a transformation's `type` to the task that runs it.
DISPATCH: dict[str, Callable[[str, str], str]] = {
    TransformationType.SCORE.value: llm_score_transform,
    TransformationType.SUMMARIZE.value: llm_summarize_transform,
    TransformationType.CLASSIFY.value: llm_classify_transform,
}


def validate_transform_config(
    transform_type: str, model: str | None, prompt: str, params: dict[str, Any] | None
) -> None:
    # Every current type is an LLM transform, so all require a model, a prompt, and valid params.
    if transform_type not in DISPATCH:
        raise ValueError(f"Unknown transform type {transform_type!r}")
    if not model:
        raise ValueError(f"{transform_type} transform requires a model")
    if not prompt:
        raise ValueError(f"{transform_type} transform requires a prompt")
    LLMParams.model_validate(params or {})


def _mark_run(run_id: str, status: TransformRunStatus, **fields: Any) -> None:
    with get_postgres_session() as session:
        session.execute(update(TransformRun).where(TransformRun.id == run_id).values(status=status.value, **fields))
        session.commit()


@flow
def run_transform_pipeline(input_artifact_id: str) -> None:
    """Its own flow — triggered by the markdown-created event, not run inline by ingestion."""
    logger = get_run_logger()

    with get_postgres_session() as session:
        artifact = session.get(Artifact, input_artifact_id)
        if artifact is None:
            logger.warning("Missing artifact %s; nothing to transform", input_artifact_id)
            return
        org_id = artifact.org_id
        if org_id is None:
            logger.warning("Artifact %s has no org; skipping transforms", input_artifact_id)
            return
        transforms = session.query(Transformation).filter_by(org_id=org_id).order_by(Transformation.position).all()
        pipeline = [(t.id, t.type) for t in transforms]

    if not pipeline:
        logger.info("No transforms configured for org %s", org_id)
        return

    logger.info("Running %d transform(s) for org %s on artifact %s", len(pipeline), org_id, input_artifact_id)

    current_input = input_artifact_id
    for transformation_id, transform_type in pipeline:
        handler = DISPATCH.get(transform_type)
        if handler is None:
            logger.warning("No handler for transform type %r; skipping", transform_type)
            continue

        with get_postgres_session() as session:
            run = TransformRun(
                transformation_id=transformation_id,
                input_artifact_id=current_input,
                status=TransformRunStatus.RUNNING.value,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            session.commit()

        try:
            output_artifact_id = handler(current_input, transformation_id)
        except Exception as exc:
            _mark_run(run_id, TransformRunStatus.FAILED, error_message=str(exc))
            logger.warning("Transform %s failed on artifact %s: %s", transformation_id, current_input, exc)
            break

        _mark_run(run_id, TransformRunStatus.COMPLETED, output_artifact_id=output_artifact_id)
        logger.info("Transform %s produced artifact %s", transformation_id, output_artifact_id)
        current_input = output_artifact_id
