import os
import re
import threading
import uuid
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from pydantic import BaseModel

import config
from neo4j_client import get_neo4j_session

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


def build_extraction_messages(
    entity_types: list[str], relationship_types: list[str], text: str
) -> list[dict[str, str]]:
    system = (
        f"Extract only entities of these types: {', '.join(entity_types)}. "
        "For each entity, write a thorough, self-contained description capturing everything this "
        "article says about it (who/what it is, key facts, context) — a rich paragraph, not a label. "
        f"Also extract relationships between them, using only these relationship types: "
        f"{', '.join(relationship_types)}. Use the exact type strings given; do not invent new ones."
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
    entity_types: list[str],
    relationship_types: list[str],
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
    messages = build_extraction_messages(entity_types, relationship_types, text)
    content = _chat(client, model, messages, llm_params, schema)
    if content is None:
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)


def candidate_query(
    knowledge_base_id: str, entity_type: str, name_normalized: str, limit: int
) -> tuple[str, dict[str, Any]]:
    query = (
        "MATCH (e:Entity {knowledge_base_id: $knowledge_base_id, type: $type}) "
        "WHERE e.name_normalized = $name_normalized OR $name_normalized IN e.aliases "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary LIMIT $limit"
    )
    return query, {
        "knowledge_base_id": knowledge_base_id,
        "type": entity_type,
        "name_normalized": name_normalized,
        "limit": limit,
    }


def fulltext_candidate_query(
    knowledge_base_id: str, entity_type: str, query_text: str, limit: int
) -> tuple[str, dict[str, Any]]:
    # queryNodes spans every knowledge_base, so this knowledge_base_id/type filter is the tenant boundary.
    query = (
        "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score "
        "WHERE e.knowledge_base_id = $knowledge_base_id AND e.type = $type "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary ORDER BY score DESC LIMIT $limit"
    )
    return query, {
        "q": escape_lucene(query_text),
        "knowledge_base_id": knowledge_base_id,
        "type": entity_type,
        "limit": limit,
    }


def _gather_candidates(
    session: Session, knowledge_base_id: str, entity: ExtractedEntity, limit: int
) -> list[dict[str, Any]]:
    candidates = [
        dict(r)
        for r in session.run(*candidate_query(knowledge_base_id, entity.type, normalize_name(entity.name), limit))
    ]
    seen = {c["id"] for c in candidates}
    try:  # full-text catches name variants; a bad query just adds nothing
        for r in session.run(*fulltext_candidate_query(knowledge_base_id, entity.type, entity.name, limit)):
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
    knowledge_base_id: str,
    entities: list[ExtractedEntity],
    llm_params: dict[str, Any],
) -> list[str | None]:
    """For each entity, the id of the existing entity it refers to, or None if new. Entities with
    0 or 1 candidates need no LLM; the ambiguous rest are resolved in a single batched call."""
    limit = config.KNOWLEDGE_RESOLUTION_CANDIDATES
    candidates = [_gather_candidates(session, knowledge_base_id, e, limit) for e in entities]
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


def upsert_entity(
    session: Session, knowledge_base_id: str, entity_id: str, entity: ExtractedEntity, summary: str
) -> None:
    session.run(
        "MERGE (e:Entity {id: $id}) "
        "ON CREATE SET e.knowledge_base_id = $knowledge_base_id, e.type = $type, e.created_at = datetime() "
        "SET e.name = $name, e.name_normalized = $nn, e.summary = $summary, "
        "e.aliases = $aliases, e.updated_at = datetime()",
        {
            "id": entity_id,
            "knowledge_base_id": knowledge_base_id,
            "type": entity.type,
            "name": entity.name,
            "nn": normalize_name(entity.name),
            "summary": summary,
            "aliases": [normalize_name(a) for a in entity.aliases],
        },
    )


def write_relationship(
    session: Session, knowledge_base_id: str, source_id: str, target_id: str, rel_type: str, job_id: str
) -> None:
    session.run(
        "MATCH (a:Entity {id: $source_id, knowledge_base_id: $knowledge_base_id}), "
        "(b:Entity {id: $target_id, knowledge_base_id: $knowledge_base_id}) "
        "MERGE (a)-[r:RELATED {type: $rel_type, knowledge_base_id: $knowledge_base_id}]->(b) "
        "ON CREATE SET r.source_job_id = $job_id, r.created_at = datetime()",
        {
            "source_id": source_id,
            "target_id": target_id,
            "knowledge_base_id": knowledge_base_id,
            "rel_type": rel_type,
            "job_id": job_id,
        },
    )


def write_provenance(session: Session, knowledge_base_id: str, entity_id: str, job_id: str) -> None:
    session.run(
        "MERGE (s:Source {knowledge_base_id: $knowledge_base_id, job_id: $job_id}) "
        "WITH s MATCH (e:Entity {id: $entity_id, knowledge_base_id: $knowledge_base_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(s)",
        {"knowledge_base_id": knowledge_base_id, "job_id": job_id, "entity_id": entity_id},
    )


class MergeResult(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int


def merge_content(
    knowledge_base_id: str,
    content: str,
    entity_types: list[str],
    relationship_types: list[str],
    job_id: str,
) -> MergeResult:
    allowed_entities = {t.lower() for t in entity_types}
    allowed_rels = {t.upper() for t in relationship_types}
    llm_params: dict[str, Any] = {}
    created = merged = rels = 0
    name_to_id: dict[str, str] = {}
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client, get_neo4j_session() as neo:
        extraction = extract_knowledge(client, config.LLM_MODEL, entity_types, relationship_types, content, llm_params)
        entities = [e for e in extraction.entities if e.type.lower() in allowed_entities]
        resolved_ids = resolve_entities_batch(neo, client, config.LLM_MODEL, knowledge_base_id, entities, llm_params)
        for entity, existing_id in zip(entities, resolved_ids, strict=True):
            if existing_id is None:
                entity_id, summary = str(uuid.uuid4()), entity.description
                created += 1
            else:
                entity_id = existing_id
                row = neo.run(
                    "MATCH (e:Entity {id: $id, knowledge_base_id: $knowledge_base_id}) RETURN e.summary AS s",
                    {"id": entity_id, "knowledge_base_id": knowledge_base_id},
                ).single()
                existing_summary = row["s"] if row else ""
                summary = merge_summary(client, config.LLM_MODEL, existing_summary, entity.description, llm_params)
                merged += 1
            upsert_entity(neo, knowledge_base_id, entity_id, entity, summary)
            write_provenance(neo, knowledge_base_id, entity_id, job_id)
            name_to_id[normalize_name(entity.name)] = entity_id

        for rel in extraction.relationships:
            if rel.type.upper() not in allowed_rels:
                continue
            src = name_to_id.get(normalize_name(rel.source_name))
            tgt = name_to_id.get(normalize_name(rel.target_name))
            if src and tgt:
                write_relationship(neo, knowledge_base_id, src, tgt, rel.type, job_id)
                rels += 1
    return MergeResult(entities_created=created, entities_merged=merged, relationships_created=rels)
