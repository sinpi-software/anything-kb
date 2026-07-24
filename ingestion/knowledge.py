import re
import threading
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from pydantic import BaseModel

import config

# Bounds concurrent OpenRouter calls in place of the old Prefect concurrency pool.
_llm_semaphore = threading.Semaphore(config.LLM_CONCURRENCY)


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


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def _chat(
    client: OpenRouter,
    model: str,
    messages: list[dict[str, str]],
    llm_params: dict[str, Any],
    response_format: dict[str, Any] | None = None,
) -> str | None:
    # Every LLM call goes through here, so the concurrency gate and request timeout
    # can't be forgotten (an untimed reasoning-model call can hang the flow run forever).
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "timeout_ms": config.LLM_TIMEOUT_MS, **llm_params}
    if response_format is not None:
        kwargs["response_format"] = response_format
    with _llm_semaphore:
        result = client.chat.send(**kwargs)
    content = result.choices[0].message.content
    return content if isinstance(content, str) else None


def build_extraction_messages(prompt: str, entity_types: list[str], text: str) -> list[dict[str, str]]:
    system = (
        f"{prompt}\n\nExtract only entities of these types: {', '.join(entity_types)}. "
        "For each entity, write a thorough, self-contained description capturing everything this "
        "article says about it (who/what it is, key facts, context) — a rich paragraph, not a label. "
        "Also extract relationships between them, each with a concise UPPER_SNAKE_CASE type."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def _strict_schema(node: Any) -> Any:
    # OpenAI structured outputs require additionalProperties:false and every key required
    # on each object; pydantic's model_json_schema() emits neither.
    if isinstance(node, dict):
        node.pop("default", None)
        if node.get("type") == "object" and node.get("properties"):
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            _strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            _strict_schema(value)
    return node


def extract_knowledge(
    client: OpenRouter,
    model: str,
    prompt: str,
    entity_types: list[str],
    text: str,
    llm_params: dict[str, Any],
) -> KnowledgeExtraction:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "knowledge",
            "strict": True,
            "schema": _strict_schema(KnowledgeExtraction.model_json_schema()),
        },
    }
    content = _chat(client, model, build_extraction_messages(prompt, entity_types, text), llm_params, schema)
    if content is None:
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)


def candidate_query(org_id: str, entity_type: str, name_normalized: str, limit: int) -> tuple[str, dict[str, Any]]:
    query = (
        "MATCH (e:Entity {org_id: $org_id, type: $type}) "
        "WHERE e.name_normalized = $name_normalized OR $name_normalized IN e.aliases "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary LIMIT $limit"
    )
    return query, {"org_id": org_id, "type": entity_type, "name_normalized": name_normalized, "limit": limit}


def fulltext_candidate_query(org_id: str, entity_type: str, query_text: str, limit: int) -> tuple[str, dict[str, Any]]:
    # queryNodes spans every org, so this org_id/type filter is the tenant boundary.
    query = (
        "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score "
        "WHERE e.org_id = $org_id AND e.type = $type "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary ORDER BY score DESC LIMIT $limit"
    )
    return query, {"q": escape_lucene(query_text), "org_id": org_id, "type": entity_type, "limit": limit}


def _gather_candidates(session: Session, org_id: str, entity: ExtractedEntity, limit: int) -> list[dict[str, Any]]:
    candidates = [
        dict(r) for r in session.run(*candidate_query(org_id, entity.type, normalize_name(entity.name), limit))
    ]
    seen = {c["id"] for c in candidates}
    try:  # full-text catches name variants; a bad query just adds nothing
        for r in session.run(*fulltext_candidate_query(org_id, entity.type, entity.name, limit)):
            if r["id"] not in seen:
                candidates.append(dict(r))
                seen.add(r["id"])
    except Exception:
        pass
    return candidates[:limit]


class _Resolution(BaseModel):
    index: int
    id: str  # a candidate id, or "NEW"


class _BatchResolution(BaseModel):
    resolutions: list[_Resolution] = []


def resolve_entities_batch(
    session: Session,
    client: OpenRouter,
    model: str,
    org_id: str,
    entities: list[ExtractedEntity],
    llm_params: dict[str, Any],
) -> list[str | None]:
    """For each entity, the id of the existing entity it refers to, or None if new. Entities with
    0 or 1 candidates need no LLM; the ambiguous rest are resolved in a single batched call."""
    limit = config.KNOWLEDGE_RESOLUTION_CANDIDATES
    candidates = [_gather_candidates(session, org_id, e, limit) for e in entities]
    resolved: list[str | None] = []
    ambiguous: list[int] = []
    for i, cands in enumerate(candidates):
        if len(cands) == 1:
            resolved.append(str(cands[0]["id"]))
        else:
            resolved.append(None)  # 0 candidates -> new; 2+ -> decided by the batch call below
            if cands:
                ambiguous.append(i)

    if ambiguous:
        blocks = [
            f"[{i}] {entities[i].name} ({entities[i].type}) — {entities[i].description}\n"
            + "\n".join(f"    - id={c['id']}: {c['name']} — {c['summary']}" for c in candidates[i])
            for i in ambiguous
        ]
        messages = [
            {
                "role": "system",
                "content": "For each numbered new entity, decide whether it is the SAME as one of its listed "
                "candidates. Return that candidate's id, or NEW if none match.",
            },
            {"role": "user", "content": "\n\n".join(blocks)},
        ]
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "resolutions",
                "strict": True,
                "schema": _strict_schema(_BatchResolution.model_json_schema()),
            },
        }
        content = _chat(client, model, messages, llm_params, schema)
        if content:
            valid = {i: {str(c["id"]) for c in candidates[i]} for i in ambiguous}
            for r in _BatchResolution.model_validate_json(content).resolutions:
                if r.index in valid and r.id in valid[r.index]:  # ignore hallucinated ids / "NEW"
                    resolved[r.index] = r.id
    return resolved


def merge_summary(client: OpenRouter, model: str, existing: str, new: str, llm_params: dict[str, Any]) -> str:
    messages = [
        {
            "role": "system",
            "content": "You maintain an encyclopedia article about an entity. Integrate the new "
            "information into the existing article, growing it into a comprehensive, well-organized "
            "entry (use multiple paragraphs/sections as the material warrants). Keep all existing "
            "facts, add the new ones, and note any contradictions. Return only the article text.",
        },
        {"role": "user", "content": f"Existing article:\n{existing}\n\nNew source:\n{new}"},
    ]
    return (_chat(client, model, messages, llm_params) or existing).strip()


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
        "WITH s MATCH (e:Entity {id: $entity_id, org_id: $org_id}) MERGE (e)-[:MENTIONED_IN]->(s)",
        {"org_id": org_id, "artifact_id": artifact_id, "entity_id": entity_id},
    )
