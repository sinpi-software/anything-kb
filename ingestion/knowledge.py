import os
import re
import uuid
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


class KnowledgeTransformOutput(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int
    source_artifact_id: str

    def to_model(self) -> tuple[str, str]:
        return self.model_dump_json(), "application/json"


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
    # Every LLM call goes through here, so the concurrency gate can't be forgotten.
    kwargs: dict[str, Any] = {"model": model, "messages": messages, **llm_params}
    if response_format is not None:
        kwargs["response_format"] = response_format
    with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
        result = client.chat.send(**kwargs)
    content = result.choices[0].message.content
    return content if isinstance(content, str) else None


def build_extraction_messages(prompt: str, entity_types: list[str], text: str) -> list[dict[str, str]]:
    system = (
        f"{prompt}\n\nExtract only entities of these types: {', '.join(entity_types)}. "
        "Also extract relationships between them, each with a concise UPPER_SNAKE_CASE type."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


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
        "json_schema": {"name": "knowledge", "schema": KnowledgeExtraction.model_json_schema()},
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


def resolve_entity(
    session: Session,
    client: OpenRouter,
    model: str,
    org_id: str,
    entity: ExtractedEntity,
    llm_params: dict[str, Any],
) -> str | None:
    limit = config.KNOWLEDGE_RESOLUTION_CANDIDATES
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
    candidates = candidates[:limit]

    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0]["id"])

    listed = "\n".join(f"- id={c['id']}: {c['name']} — {c['summary']}" for c in candidates)
    answer = (
        _chat(
            client,
            model,
            [
                {
                    "role": "system",
                    "content": "Is the new entity the SAME as an existing one? Reply ONLY its id, or NEW.",
                },
                {"role": "user", "content": f"New: {entity.name} ({entity.type}) — {entity.description}\n\n{listed}"},
            ],
            llm_params,
        )
        or "NEW"
    ).strip()
    return answer if answer in {str(c["id"]) for c in candidates} else None


def merge_summary(client: OpenRouter, model: str, existing: str, new: str, llm_params: dict[str, Any]) -> str:
    messages = [
        {
            "role": "system",
            "content": "Merge the new info into the existing summary; keep it accurate and concise. "
            "Return only the summary.",
        },
        {"role": "user", "content": f"Existing:\n{existing}\n\nNew:\n{new}"},
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


def run_knowledge_transform(artifact_id: str, transformation_id: str) -> str:
    artifact_id = str(artifact_id)  # pipeline may pass a uuid.UUID; Neo4j needs str params
    with get_postgres_session() as session:
        transformation = session.get(Transformation, transformation_id)
        if transformation is None:
            raise ValueError(f"Transformation {transformation_id} not found")
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.org_id is None:
            raise ValueError(f"Artifact {artifact_id} missing or has no org")
        model, prompt, text = transformation.model, transformation.prompt, artifact.data
        params = dict(transformation.params or {})
        entity_types = params.pop("entity_types", [])  # the rest are LLM knobs
        org_id = str(artifact.org_id)

    allowed = {t.lower() for t in entity_types}
    created = merged = rels = 0
    name_to_id: dict[str, str] = {}
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client, get_neo4j_session() as neo:
        extraction = extract_knowledge(client, model, prompt, entity_types, text, params)
        for entity in extraction.entities:
            if entity.type.lower() not in allowed:
                continue
            entity_id = resolve_entity(neo, client, model, org_id, entity, params)
            if entity_id is None:
                entity_id, summary = str(uuid.uuid4()), entity.description
                created += 1
            else:
                row = neo.run(
                    "MATCH (e:Entity {id: $id, org_id: $org_id}) RETURN e.summary AS s",
                    {"id": entity_id, "org_id": org_id},
                ).single()
                summary = merge_summary(client, model, row["s"] if row else "", entity.description, params)
                merged += 1
            upsert_entity(neo, org_id, entity_id, entity, summary)
            write_provenance(neo, org_id, entity_id, artifact_id)
            name_to_id[normalize_name(entity.name)] = entity_id

        for rel in extraction.relationships:
            src = name_to_id.get(normalize_name(rel.source_name))
            tgt = name_to_id.get(normalize_name(rel.target_name))
            if src and tgt:
                write_relationship(neo, org_id, src, tgt, rel.type, artifact_id)
                rels += 1

    output = KnowledgeTransformOutput(
        entities_created=created, entities_merged=merged, relationships_created=rels, source_artifact_id=artifact_id
    )
    data, content_type = output.to_model()
    with get_postgres_session() as session:
        out = Artifact(
            org_id=org_id, ref_table_name=Artifact.__tablename__, ref_table_id=artifact_id, type=content_type, data=data
        )
        session.add(out)
        session.flush()
        out_id = out.id
        session.commit()
    return out_id
