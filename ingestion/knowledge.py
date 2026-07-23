import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from prefect.concurrency.sync import concurrency
from pydantic import BaseModel

import config
from db import get_postgres_session
from models import Artifact, Transformation
from neo4j_client import get_neo4j_session


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


def _chat(
    client: OpenRouter,
    model: str,
    messages: list[dict[str, str]],
    llm_params: dict[str, Any],
    response_format: dict[str, Any] | None = None,
) -> str | None:
    """One chat completion under the LLM concurrency limit; None if the model returned no text.

    Every LLM call in this module goes through here, so the concurrency gate can't be
    forgotten. Args are assembled into a kwargs dict — besides being tidy, it sidesteps the
    SDK's message-type overloads (which reject plain str-keyed dicts under strict typing,
    though they work fine at runtime, as transformations.py relies on).
    """
    kwargs: dict[str, Any] = {"model": model, "messages": messages, **llm_params}
    if response_format is not None:
        kwargs["response_format"] = response_format
    with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
        result = client.chat.send(**kwargs)
    content = result.choices[0].message.content
    return content if isinstance(content, str) else None


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
    content = _chat(
        client,
        model,
        build_extraction_messages(prompt, entity_types, text),
        llm_params,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "knowledge_extraction", "schema": KnowledgeExtraction.model_json_schema()},
        },
    )
    if content is None:
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


# Lucene special characters (plus the two-char operators) that must be backslash-escaped
# before a raw name is dropped into a full-text query string.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


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


def fulltext_candidate_query(org_id: str, entity_type: str, query_text: str, limit: int) -> tuple[str, dict[str, Any]]:
    # Full-text match over name + aliases (the `entity_name` index), then filtered to the
    # org + type — queryNodes searches ALL orgs, so this WHERE clause is the org boundary.
    query = (
        "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score "
        "WHERE e.org_id = $org_id AND e.type = $type "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary "
        "ORDER BY score DESC LIMIT $limit"
    )
    params: dict[str, Any] = {
        "q": escape_lucene(query_text),
        "org_id": org_id,
        "type": entity_type,
        "limit": limit,
    }
    return query, params


def _gather_candidates(session: Session, org_id: str, entity: ExtractedEntity, limit: int) -> list[dict[str, Any]]:
    """Existing entities that might be the same as `entity`: exact/alias matches, plus
    full-text matches that catch name variants ("Barack Obama" vs "President Obama"). A
    malformed full-text query just contributes nothing rather than failing resolution."""
    exact_query, exact_params = candidate_query(org_id, entity.type, normalize_name(entity.name), limit)
    candidates = [dict(record) for record in session.run(exact_query, exact_params)]

    ft_query, ft_params = fulltext_candidate_query(org_id, entity.type, entity.name, limit)
    try:
        fulltext = [dict(record) for record in session.run(ft_query, ft_params)]
    except Exception:
        fulltext = []

    seen = {c["id"] for c in candidates}
    for c in fulltext:
        if c["id"] not in seen:
            candidates.append(c)
            seen.add(c["id"])
    return candidates[:limit]


def build_resolution_messages(entity: ExtractedEntity, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    listed = "\n".join(f'- id={c["id"]}: {c["name"]} — {c["summary"]}' for c in candidates)
    system = (
        "You resolve whether a newly mentioned entity is the SAME as one of the existing "
        "entities. Reply with ONLY the matching id, or the word NEW if none match."
    )
    user = f"New entity: {entity.name} ({entity.type}) — {entity.description}\n\nExisting candidates:\n{listed}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def resolve_entity(
    session: Session,
    client: OpenRouter,
    model: str,
    org_id: str,
    entity: ExtractedEntity,
    llm_params: dict[str, Any],
) -> str | None:
    """The id of the existing entity `entity` refers to, or None if it's new."""
    candidates = _gather_candidates(session, org_id, entity, config.KNOWLEDGE_RESOLUTION_CANDIDATES)
    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0]["id"])

    content = _chat(client, model, build_resolution_messages(entity, candidates), llm_params)
    answer = content.strip() if content else "NEW"
    valid_ids = {str(c["id"]) for c in candidates}
    return answer if answer in valid_ids else None  # ignore a hallucinated / "NEW" answer


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
    content = _chat(client, model, messages, llm_params)
    return content.strip() if content else existing


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


def _current_summary(session: Session, org_id: str, entity_id: str) -> str:
    record = session.run(
        "MATCH (e:Entity {id: $id, org_id: $org_id}) RETURN e.summary AS summary",
        {"id": entity_id, "org_id": org_id},
    ).single()
    return record["summary"] if record else ""


class KnowledgeTransformOutput(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int
    source_artifact_id: str

    def to_model(self) -> tuple[str, str]:
        return self.model_dump_json(), "application/json"


@dataclass(frozen=True)
class _Context:
    """The per-run config, resolved once from Postgres and threaded into the graph work."""

    model: str
    prompt: str
    entity_types: list[str]
    llm_params: dict[str, Any]
    org_id: str
    artifact_id: str
    text: str


def _load_context(transformation_id: str, artifact_id: str) -> _Context:
    with get_postgres_session() as session:
        transformation = session.get(Transformation, transformation_id)
        if transformation is None:
            raise ValueError(f"Transformation {transformation_id} not found")
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        if artifact.org_id is None:
            raise ValueError(f"Artifact {artifact_id} has no org")

        params = dict(transformation.params or {})
        entity_types = params.pop("entity_types", [])
        return _Context(
            model=transformation.model,
            prompt=transformation.prompt,
            entity_types=entity_types,
            llm_params=params,  # whatever's left are LLM knobs (temperature, ...)
            org_id=str(artifact.org_id),  # str: the Neo4j driver rejects raw uuid.UUID params
            artifact_id=artifact_id,
            text=artifact.data,
        )


def _ingest(client: OpenRouter, ctx: _Context, extraction: KnowledgeExtraction) -> tuple[int, int, int]:
    """Write the extraction into the org's graph. Returns (created, merged, relationships)."""
    created = merged = relationships = 0
    allowed = {t.lower() for t in ctx.entity_types}
    name_to_id: dict[str, str] = {}

    with get_neo4j_session() as neo:
        for entity in extraction.entities:
            if entity.type.lower() not in allowed:
                continue  # the LLM strayed outside the org's configured entity_types

            existing_id = resolve_entity(neo, client, ctx.model, ctx.org_id, entity, ctx.llm_params)
            if existing_id is None:
                entity_id, summary = str(uuid.uuid4()), entity.description
                created += 1
            else:
                entity_id = existing_id
                current = _current_summary(neo, ctx.org_id, entity_id)
                summary = merge_summary(client, ctx.model, current, entity.description, ctx.llm_params)
                merged += 1

            upsert_entity(neo, ctx.org_id, entity_id, entity, summary)
            write_provenance(neo, ctx.org_id, entity_id, ctx.artifact_id)
            name_to_id[normalize_name(entity.name)] = entity_id

        for rel in extraction.relationships:
            source_id = name_to_id.get(normalize_name(rel.source_name))
            target_id = name_to_id.get(normalize_name(rel.target_name))
            if source_id and target_id:
                write_relationship(neo, ctx.org_id, source_id, target_id, rel.type, ctx.artifact_id)
                relationships += 1

    return created, merged, relationships


def _persist_output(ctx: _Context, created: int, merged: int, relationships: int) -> str:
    output = KnowledgeTransformOutput(
        entities_created=created,
        entities_merged=merged,
        relationships_created=relationships,
        source_artifact_id=ctx.artifact_id,
    )
    data, content_type = output.to_model()
    with get_postgres_session() as session:
        out = Artifact(
            org_id=ctx.org_id,
            ref_table_name=Artifact.__tablename__,
            ref_table_id=ctx.artifact_id,
            type=content_type,
            data=data,
        )
        session.add(out)
        session.flush()
        out_id = out.id
        session.commit()
    return out_id


def run_knowledge_transform(artifact_id: str, transformation_id: str) -> str:
    # str(): run_transform_pipeline chains a prior transform's raw output id (a uuid.UUID,
    # despite the Mapped[str] annotation) into this call, and Neo4j rejects UUID params.
    ctx = _load_context(transformation_id, str(artifact_id))
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client:
        extraction = extract_knowledge(client, ctx.model, ctx.prompt, ctx.entity_types, ctx.text, ctx.llm_params)
        created, merged, relationships = _ingest(client, ctx, extraction)
    return _persist_output(ctx, created, merged, relationships)
