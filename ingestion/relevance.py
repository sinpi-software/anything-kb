import os

from openrouter import OpenRouter
from pydantic import BaseModel, ValidationError

import config
from knowledge import _chat, _strict_schema


class RelevanceResult(BaseModel):
    relevant: bool
    reason: str


class RelevanceError(RuntimeError):
    """The relevance check could not be completed (empty/unparseable LLM output).
    Raised so the worker retries or marks the job failed, rather than silently
    treating undecidable content as irrelevant and dropping it as `skipped`."""


def build_relevance_messages(interests: str, content: str) -> list[dict[str, str]]:
    system = (
        f"{interests}\n\nDecide whether the following content is relevant under that instruction. "
        "Return relevant=true or relevant=false and a brief reason."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def judge_relevance(interests: str, content: str) -> RelevanceResult:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "relevance",
            "strict": True,
            "schema": _strict_schema(RelevanceResult.model_json_schema()),
        },
    }
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client:
        out = _chat(client, config.LLM_MODEL, build_relevance_messages(interests, content), {}, schema)
    if out is None:
        raise RelevanceError("relevance check failed: empty LLM response")
    try:
        return RelevanceResult.model_validate_json(out)
    except ValidationError as exc:
        raise RelevanceError("relevance check failed: unparseable LLM response") from exc
