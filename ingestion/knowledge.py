from typing import Any

from openrouter import OpenRouter
from pydantic import BaseModel


class ExtractedEntity(BaseModel):
    name: str
    type: str
    description: str
    aliases: list[str] = []


class ExtractedRelationship(BaseModel):
    source_name: str
    target_name: str
    type: str


class KnowledgeExtraction(BaseModel):
    entities: list[ExtractedEntity] = []
    relationships: list[ExtractedRelationship] = []


def build_extraction_messages(prompt: str, entity_types: list[str], text: str) -> list[dict[str, str]]:
    system = (
        f"{prompt}\n\n"
        f"Extract only entities of these types: {', '.join(entity_types)}. "
        "Also extract relationships between the extracted entities; choose a concise "
        "UPPER_SNAKE_CASE relationship type that best fits the context."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def extract_knowledge(
    client: OpenRouter,
    model: str,
    prompt: str,
    entity_types: list[str],
    text: str,
    llm_params: dict[str, Any],
) -> KnowledgeExtraction:
    # The SDK's overloads want its own TypedDict/BaseModel message types; plain
    # str-keyed dicts work fine at runtime (see transformations.py, which sends
    # the same shape) but only type-check there because that call site never
    # gives `client` an explicit `OpenRouter` annotation, so mypy treats it as
    # Any and skips the overload check. Here `client` is explicitly typed per
    # this module's interface, which surfaces the mismatch.
    result = client.chat.send(  # type: ignore[call-overload]
        model=model,
        messages=build_extraction_messages(prompt, entity_types, text),
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "knowledge_extraction", "schema": KnowledgeExtraction.model_json_schema()},
        },
        **llm_params,
    )
    content = result.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)
