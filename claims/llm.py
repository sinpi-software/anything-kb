"""The one place that talks to OpenRouter, and the one place tests patch.

This calls the HTTP API directly rather than using the `openrouter` SDK. The SDK's
ChatAssistantMessage component has no `annotations` field and its BaseModel does not
set extra="allow", so pydantic discards the web plugin's citations during
unmarshalling. verify.py's grounding filter needs exactly those citations, so the raw
JSON is the only workable source.

Structured output uses json_schema mode, the same way ingestion/knowledge.py and
neonews/write.py do.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from prefect.concurrency.sync import concurrency
from pydantic import BaseModel

import config as config  # re-exported: tests read llm.config.WEB_MAX_RESULTS


class LLMError(Exception):
    """OpenRouter rejected the request, or returned nothing usable."""


class LLMResult(BaseModel):
    content: str
    # The URLs OpenRouter itself reports having visited. Empty when the model was not
    # web-enabled, or when the provider returned no annotations at all — which is why
    # `had_annotations` is tracked separately: "cited nothing" and "we cannot tell what
    # was cited" must not be conflated by the grounding filter.
    citation_urls: frozenset[str]
    had_annotations: bool


# Keywords OpenAI's strict structured-output mode rejects. Dropping them costs
# nothing: pydantic re-validates the parsed response against the same Field
# constraints, so `ge`/`le` (etc.) are still enforced where it counts.
_UNSUPPORTED_KEYWORDS = (
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
)


def strict_schema(node: Any) -> Any:
    """OpenAI structured outputs require additionalProperties:false and every key
    required on each object; pydantic's model_json_schema() emits neither. It also
    rejects numeric/string/array constraint keywords (minimum, maxLength, ...) on
    properties, which pydantic happily emits for any Field(ge=..., le=...) etc."""
    if isinstance(node, dict):
        for keyword in _UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)
        if node.get("type") == "object" and node.get("properties"):
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            strict_schema(value)
    return node


def _post(url: str, **kwargs: Any) -> httpx.Response:
    """The single HTTP call, isolated so tests patch one function."""
    return httpx.post(url, **kwargs)


def _citation_urls(message: dict[str, Any]) -> tuple[frozenset[str], bool]:
    annotations = message.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        return frozenset(), False
    urls = {
        url
        for annotation in annotations
        if isinstance(annotation, dict) and annotation.get("type") == "url_citation"
        if isinstance(uc := annotation.get("url_citation"), dict) and isinstance(url := uc.get("url"), str) and url
    }
    return frozenset(urls), True


def complete(
    model: str,
    system: str,
    user: str,
    schema_name: str,
    schema: type[BaseModel],
    web: bool = False,
) -> LLMResult:
    """One structured-output call. `web=True` attaches OpenRouter's web-search plugin.

    Bounded by a Prefect global concurrency limit acquired with strict=False, so an
    absent limit is a no-op rather than an error (neonews/write.py does the same).
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": strict_schema(schema.model_json_schema())},
        },
    }
    if web:
        body["plugins"] = [{"id": "web", "max_results": config.WEB_MAX_RESULTS}]

    api_key = os.environ.get(config.OPENROUTER_API_KEY_ENV, "").strip()
    if not api_key:
        # An empty value, not just an absent one: .env.sample sets this key to the empty
        # string as a placeholder, so os.environ[...] would sail past a KeyError and the
        # call would fail as an opaque 401 instead.
        raise LLMError(f"{config.OPENROUTER_API_KEY_ENV} is unset or empty")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with concurrency(config.LLM_CONCURRENCY_LIMIT, strict=False):
        response = _post(config.OPENROUTER_URL, json=body, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise LLMError(f"OpenRouter returned {response.status_code}: {response.text[:500]}")

    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError(f"OpenRouter returned no choices: {str(payload)[:500]}")
    # Named here rather than left to surface as a downstream JSONDecodeError: a
    # response cut off at the token limit is truncated JSON, and the parse failure
    # four layers away gives no hint of why — it can reach a human reader as a bare
    # "could not be checked: Unterminated string starting at line 1 column ...".
    finish_reason = choices[0].get("finish_reason")
    if finish_reason == "length":
        raise LLMError(f"{model} response truncated at the token limit (finish_reason=length)")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError(f"{model} returned no content")
    citation_urls, had_annotations = _citation_urls(message)
    return LLMResult(content=content, citation_urls=citation_urls, had_annotations=had_annotations)
