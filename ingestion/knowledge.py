import re
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from pydantic import BaseModel

import config


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


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def candidate_query(org_id: str, entity_type: str, name_normalized: str, limit: int) -> tuple[str, dict[str, Any]]:
    # Exact/alias match within the org + type; scoped by org_id.
    query = (
        "MATCH (e:Entity {org_id: $org_id, type: $type}) "
        "WHERE e.name_normalized = $name_normalized OR $name_normalized IN e.aliases "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary "
        "LIMIT $limit"
    )
    params: dict[str, Any] = {
        "org_id": org_id,
        "type": entity_type,
        "name_normalized": name_normalized,
        "limit": limit,
    }
    return query, params


def build_resolution_messages(entity: ExtractedEntity, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    listed = "\n".join(f'- id={c["id"]}: {c["name"]} — {c["summary"]}' for c in candidates)
    system = (
        "You resolve whether a newly mentioned entity is the SAME as one of the existing "
        "entities. Reply with ONLY the matching id, or the word NEW if none match."
    )
    user = (
        f"New entity: {entity.name} ({entity.type}) — {entity.description}\n\n"
        f"Existing candidates:\n{listed}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def resolve_entity(
    session: Session,
    client: OpenRouter,
    model: str,
    org_id: str,
    entity: ExtractedEntity,
    llm_params: dict[str, Any],
) -> str | None:
    query, params = candidate_query(
        org_id, entity.type, normalize_name(entity.name), config.KNOWLEDGE_RESOLUTION_CANDIDATES
    )
    candidates = [dict(record) for record in session.run(query, params)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0]["id"])
    # Same plain str-keyed dict shape as extract_knowledge above; mypy resolves this call
    # (no response_format kwarg) to a single overload and flags the messages arg directly,
    # rather than the ambiguous-overload error extract_knowledge triggers.
    result = client.chat.send(
        model=model,
        messages=build_resolution_messages(entity, candidates),  # type: ignore[arg-type]
        **llm_params,
    )
    content = result.choices[0].message.content
    answer = content.strip() if isinstance(content, str) else "NEW"
    valid_ids = {str(c["id"]) for c in candidates}
    return answer if answer in valid_ids else None


def merge_summary(client: OpenRouter, model: str, existing: str, new: str, llm_params: dict[str, Any]) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You maintain an encyclopedia entry. Merge the new information into the existing "
                "summary, keeping it accurate and concise. Return only the revised summary."
            ),
        },
        {"role": "user", "content": f"Existing summary:\n{existing}\n\nNew information:\n{new}"},
    ]
    # Same plain str-keyed dict shape as resolve_entity above; mypy resolves this call
    # (no response_format kwarg) to a single overload and flags the messages arg directly.
    result = client.chat.send(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        **llm_params,
    )
    content = result.choices[0].message.content
    return content.strip() if isinstance(content, str) else existing


def upsert_entity(session: Session, org_id: str, entity_id: str, entity: ExtractedEntity, summary: str) -> None:
    session.run(
        "MERGE (e:Entity {id: $id}) "
        "ON CREATE SET e.org_id = $org_id, e.type = $type, e.created_at = datetime() "
        "SET e.name = $name, e.name_normalized = $nn, e.summary = $summary, "
        "e.aliases = $aliases, e.updated_at = datetime()",
        {
            "id": entity_id,
            "org_id": org_id,
            "type": entity.type,
            "name": entity.name,
            "nn": normalize_name(entity.name),
            "summary": summary,
            "aliases": [normalize_name(a) for a in entity.aliases],
        },
    )


def write_relationship(
    session: Session, org_id: str, source_id: str, target_id: str, rel_type: str, artifact_id: str
) -> None:
    session.run(
        "MATCH (a:Entity {id: $source_id, org_id: $org_id}), (b:Entity {id: $target_id, org_id: $org_id}) "
        "MERGE (a)-[r:RELATED {type: $rel_type, org_id: $org_id}]->(b) "
        "ON CREATE SET r.source_artifact_id = $artifact_id, r.created_at = datetime()",
        {
            "source_id": source_id,
            "target_id": target_id,
            "org_id": org_id,
            "rel_type": rel_type,
            "artifact_id": artifact_id,
        },
    )


def write_provenance(session: Session, org_id: str, entity_id: str, artifact_id: str) -> None:
    session.run(
        "MERGE (s:Source {org_id: $org_id, artifact_id: $artifact_id}) "
        "WITH s MATCH (e:Entity {id: $entity_id, org_id: $org_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(s)",
        {"org_id": org_id, "artifact_id": artifact_id, "entity_id": entity_id},
    )
