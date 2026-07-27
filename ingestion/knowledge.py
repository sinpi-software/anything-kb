import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from pydantic import BaseModel, ValidationError

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


def _render_types(types: list[dict[str, str]]) -> str:
    """One type per line as `- name: description`, so the extractor knows what each means."""
    lines = []
    for t in types:
        name = t["name"]
        description = (t.get("description") or "").strip()
        lines.append(f"- {name}: {description}" if description else f"- {name}")
    return "\n".join(lines)


# For most entities the description IS the article, permanently: synthesize_article runs only when
# an entity is merged, and in a real ingest 178 of 193 entities were named by exactly one document.
# Those averaged 169 characters against 5,613 for the one entity mentioned nine times. The single
# trailing clause this replaces ("a rich paragraph, not a label") was followed erratically — it also
# produced "Founder of the Trail Blazers in 1970." and "Town in Oregon where Randy Stapilus resides",
# both defined by their relation to something else in the same document. State the standard the way
# _ENTITY_QUALITY does, naming the failure mode rather than only the goal.
_DESCRIPTION_QUALITY = (
    "For each entity write a description that stands on its own as an encyclopedia entry: identify "
    "what the subject IS, for a reader who has never seen this document. Lead with the subject's own "
    "identity rather than its role in this text — 'a US senator from Vermont and two-time "
    "presidential candidate', never 'presidential candidate who held a rally at the Moda Center'. "
    "Never define a subject solely by its relation to another entity mentioned here. Use what you "
    "reliably know about a well-known subject to establish that identity, then add what this "
    "document specifically contributes; if you do not know the subject, say only what the document "
    "supports rather than inventing detail. Several sentences at minimum — a bare label or a single "
    "relative clause is not acceptable."
)

# Keeps vague noun-phrases (e.g. "a two-year legal battle") out of the graph as their own nodes.
_ENTITY_QUALITY = (
    "Only extract entities that are concrete, specific, and individually significant — a distinct, "
    "named person, organization, place, product, work, law, or named event. Do NOT extract vague "
    "descriptive phrases (e.g. 'a two-year legal battle'), durations, quantities, dates, generic "
    "concepts, or a phrase that merely restates or is a fragment of another entity — fold such detail "
    "into the relevant entity's description rather than creating a node for it."
)

# A graph coheres when entities are anchored to the recurring higher-level "hub" entities that many
# documents share. What those hubs ARE is domain-specific (places for local news, companies and
# technologies for tech, institutions and fields for research) — so state the general principle and
# let the model infer the right anchors from the content, rather than hardcoding one domain.
_RELATIONSHIP_COMPLETENESS = (
    "Connect and anchor every entity so the graph forms one connected whole, not one island per "
    "document. Emit each relationship the text supports so no entity is left unconnected, and tie every "
    "entity to the broader, longer-lived entities that give it context — the organization it belongs "
    "to, the place it sits in, the person behind it, the field or work it is part of. Build the graph's "
    "backbone from the RECURRING anchors of this material: the higher-level, slowly-changing entities "
    "that many different documents mention in common (for regional news these are places — a town "
    "within its county within its state; for technology they are companies and technologies; for "
    "research they are institutions and fields). Create each such anchor as its own separate, "
    "consistently-named node — never fold a hierarchy into a compound name like "
    "'Kelso, Cowlitz County, Washington' — and add it from your own knowledge when the text omits it, "
    "so separate documents connect through the anchors they share. Each relationship's source and "
    "target MUST be written exactly as the name of an entity in your entities list (same spelling).\n\n"
    # Coverage alone is satisfied most cheaply by labelling every edge with the catch-all, which is
    # exactly what happened: 216 of 241 edges in a real ingest were "Related to", including
    # "person -[Related to]-> the department prosecuting them". Ask for precision in the same breath.
    "Give each relationship the MOST SPECIFIC type the text supports, and say what actually holds "
    "between the two entities: prefer 'Charged by', 'Detained at', 'Acquired', 'Funds' over a vague "
    "link. Use 'Related to' only when the text genuinely supports no more specific relation — it is a "
    "last resort, not a default. Direction matters: the source is the actor and the target is what "
    "it acts on, so a maker of a product is 'Google -[Created]-> Pixel', never the reverse."
)


def build_extraction_messages(
    entity_types: list[dict[str, str]],
    relationship_types: list[dict[str, str]],
    text: str,
    *,
    interests: str,
    discover: bool,
) -> list[dict[str, str]]:
    lens = f"The user cares about: {interests}\n\n" if interests.strip() else ""
    if discover:
        system = (
            f"{lens}"
            "Extract entities and relationships that match the user's interests.\n"
            "Vocabulary discovered so far — reuse these exact names when a fact fits one:\n"
            f"Entity types:\n{_render_types(entity_types)}\n"
            f"Relationship types:\n{_render_types(relationship_types)}\n\n"
            "When you find something genuinely new that matches the user's interests and no existing "
            "type fits, coin a concise new type name and use it. Do not force-fit and do not create "
            "types for incidental mentions. Only coin an ENTITY type for a category of durable, "
            "individually-referenceable things (people, organizations, places, works, events) — never "
            "for time windows, durations, dates, quantities, measurements, or descriptive attributes.\n"
            # That durability rule reads as entity-only guidance, and a relationship can never be a
            # "durable, individually-referenceable thing" — so the model declined to coin relationship
            # types at all. Across two full ingests the entity vocabulary grew from 4 types to 9 while
            # the relationship vocabulary never moved off its original 3. Say it explicitly.
            "Coin a new RELATIONSHIP type on the same terms: when the relation the text states is "
            "genuinely distinct from every existing one, name it in sentence case ('Charged by', "
            "'Detained at', 'Acquired') rather than falling back on a vague existing type.\n\n"
            f"{_ENTITY_QUALITY}\n\n"
            f"{_DESCRIPTION_QUALITY}\n\n"
            f"{_RELATIONSHIP_COMPLETENESS}"
        )
    else:
        system = (
            f"{lens}"
            f"Extract only entities of these types:\n{_render_types(entity_types)}\n\n"
            f"{_ENTITY_QUALITY}\n\n"
            f"{_DESCRIPTION_QUALITY}\n\n"
            f"Also extract relationships, using only these relationship types:\n{_render_types(relationship_types)}\n\n"
            "Use the exact type names given; do not invent new ones.\n\n"
            f"{_RELATIONSHIP_COMPLETENESS}"
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
    entity_types: list[dict[str, str]],
    relationship_types: list[dict[str, str]],
    text: str,
    llm_params: dict[str, Any],
    interests: str = "",
    discover: bool = False,
) -> KnowledgeExtraction:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "knowledge",
            "strict": True,
            "schema": _strict_schema(KnowledgeExtraction.model_json_schema()),
        },
    }
    messages = build_extraction_messages(entity_types, relationship_types, text, interests=interests, discover=discover)
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
    """For each entity, the id of the existing entity it refers to, or None if new. Entities with no
    candidate are new without an LLM call; every entity with a candidate is verified in a single
    conservative batched call — a merge is never made without that check."""
    limit = config.KNOWLEDGE_RESOLUTION_CANDIDATES
    candidates = [_gather_candidates(session, knowledge_base_id, e, limit) for e in entities]
    resolved: list[str | None] = []
    to_resolve: list[int] = []
    for i, cands in enumerate(candidates):
        resolved.append(None)  # default new; a candidate merge must be LLM-verified below
        if cands:
            to_resolve.append(i)

    if to_resolve:
        blocks = [
            f"[{i}] {entities[i].name} ({entities[i].type}) — {entities[i].description}\n"
            + "\n".join(f"    - id={c['id']}: {c['name']} — {c['summary']}" for c in candidates[i])
            for i in to_resolve
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "For each numbered entity below, decide whether it is UNMISTAKABLY the same "
                    "real-world entity as one of its listed candidates — the same specific person, "
                    "place, organization, work, or event, not merely the same type or a related "
                    "topic. Return that candidate's id ONLY when you are sure they are the same "
                    "thing. If they are different things that share a name or category, or you are "
                    "unsure, return NEW. Creating a new node is always safe; fusing two different "
                    "subjects is not."
                ),
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
            valid = {i: {str(c["id"]) for c in candidates[i]} for i in to_resolve}
            for r in _BatchResolution.model_validate_json(content).resolutions:
                if r.index in valid and r.id in valid[r.index]:  # ignore hallucinated ids / "NEW"
                    resolved[r.index] = r.id
    return resolved


class ArticleResult(BaseModel):
    abstract: str
    article: str


def _derive_abstract(text: str) -> str:
    """A cheap (no-LLM) short abstract: the first sentence, capped at 240 chars."""
    text = text.strip()
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    return head[:240]


def synthesize_article(
    client: OpenRouter,
    model: str,
    entity_name: str,
    entity_type: str,
    existing_article: str,
    new_info: str,
    llm_params: dict[str, Any],
) -> ArticleResult:
    """The living article for one entity, named explicitly.

    The name and type are not decoration. Without them this function saw only description text, so
    a description that mentioned another entity more prominently than its own subject hijacked the
    article — "Presidential candidate who held a rally at Moda Center." produced an article about
    Moda Center, stored on the Bernie Sanders node.
    """
    messages = [
        {
            "role": "system",
            "content": (
                f"You maintain an encyclopedia article about one entity as a living document. The "
                f"subject is: {entity_name} ({entity_type}). Write about that subject and no other, "
                "however prominently another entity features in the material below.\n\n"
                "Structure the article as:\n"
                # "Encyclopedia article" implies a lead strongly enough that the model writes one
                # whether or not it is asked to, and with nothing forbidding overlap it then restated
                # that lead as Background: the real United States entry opened with "a federal
                # republic located in North America. It comprises 50 states, a federal district…"
                # and repeated it almost verbatim one heading later.
                "a single opening sentence identifying the subject, then the sections below. Do not "
                "repeat the opening sentence in any section — each section adds what the lead does "
                "not already say.\n"
                "`## Background` — what you reliably know about this subject from established "
                "knowledge, written only when you can positively identify it. If the name is "
                "ambiguous, or you are not confident this is a subject you know, omit this section "
                "entirely rather than guessing. Never invent specifics.\n"
                "`## From sources` — what the ingested material states, and only that.\n\n"
                # The References list on an entity page accounts for the ingested sources. Anything the
                # model supplies from training has no such provenance, so it is kept under its own
                # heading rather than blended, letting a reader see which claims a source backs.
                "Keep the two separate: the sources shown alongside this article account for the "
                "second section only. Integrate new information into the existing article, keeping "
                "all existing facts and noting contradictions. Do NOT add a References or Sources "
                "section and do NOT add inline citations. Also produce a one-to-two-sentence abstract."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Subject: {entity_name} ({entity_type})\n\n"
                f"Existing article:\n{existing_article}\n\nNew source:\n{new_info}"
            ),
        },
    ]
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "article", "strict": True, "schema": _strict_schema(ArticleResult.model_json_schema())},
    }
    # Falling back to existing_article alone would blank a first sighting, where it is "" — a
    # synthesis failure would then throw the description away entirely, leaving the entity worse
    # off than if synthesis had never run.
    fallback = existing_article or new_info
    out = _chat(client, model, messages, llm_params, schema)
    if not out:
        return ArticleResult(abstract=_derive_abstract(fallback), article=fallback)
    try:
        return ArticleResult.model_validate_json(out)
    except ValidationError:
        return ArticleResult(abstract=_derive_abstract(fallback), article=fallback)


def upsert_entity(
    session: Session, knowledge_base_id: str, entity_id: str, entity: ExtractedEntity, summary: str, article: str
) -> None:
    session.run(
        "MERGE (e:Entity {id: $id}) "
        "ON CREATE SET e.knowledge_base_id = $knowledge_base_id, e.type = $type, e.created_at = datetime() "
        "SET e.name = $name, e.name_normalized = $nn, e.summary = $summary, e.article = $article, "
        "e.aliases = $aliases, e.updated_at = datetime()",
        {
            "id": entity_id,
            "knowledge_base_id": knowledge_base_id,
            "type": entity.type,
            "name": entity.name,
            "nn": normalize_name(entity.name),
            "summary": summary,
            "article": article,
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


def _iso_or_empty(value: str) -> str:
    """Normalize a date string to ISO-8601, or '' if empty/unparseable — so the Cypher
    datetime() call only ever receives a valid temporal literal and a bad producer date
    can't crash ingestion."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def write_provenance(
    session: Session,
    knowledge_base_id: str,
    entity_id: str,
    job_id: str,
    *,
    label: str = "",
    published_at: str = "",
    ingested_at: str = "",
) -> None:
    # published_at = when the news happened (the article's date); ingested_at = when we saw it.
    # Both stored as native datetimes so discovery queries can do duration math (null when absent).
    session.run(
        "MERGE (s:Source {knowledge_base_id: $knowledge_base_id, job_id: $job_id}) "
        "SET s.label = $label, "
        "s.published_at = CASE WHEN $published_at = '' THEN null ELSE datetime($published_at) END, "
        "s.ingested_at = CASE WHEN $ingested_at = '' THEN null ELSE datetime($ingested_at) END "
        "WITH s MATCH (e:Entity {id: $entity_id, knowledge_base_id: $knowledge_base_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(s)",
        {
            "knowledge_base_id": knowledge_base_id,
            "job_id": job_id,
            "entity_id": entity_id,
            "label": label,
            "published_at": _iso_or_empty(published_at),
            "ingested_at": _iso_or_empty(ingested_at),
        },
    )


class MergeResult(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int
    new_entity_types: list[dict[str, str]] = []
    new_relationship_types: list[dict[str, str]] = []


def _norm_type(name: str) -> str:
    """Fold a type name to a case/spacing-insensitive key, so 'Affected by',
    'AFFECTED_BY', and 'affected-by' all match the same configured type."""
    return "".join(ch for ch in name.upper() if ch.isalnum())


class TypeDecision(BaseModel):
    candidate: str
    decision: str  # "existing" | "new" | "drop"
    canonical: str = ""  # set when decision == "existing"
    name: str = ""  # cleaned name when decision == "new"
    description: str = ""  # one-line description when decision == "new"


class TypeConsolidation(BaseModel):
    decisions: list[TypeDecision] = []


def consolidate_types(
    client: OpenRouter,
    model: str,
    kind: str,
    candidates: list[str],
    vocab: list[dict[str, Any]],
    interests: str,
    llm_params: dict[str, Any],
    examples: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Resolve novel candidate type names to existing/new/drop against the current vocabulary."""
    if not candidates:
        return {}
    vocab_lines = "\n".join(
        f"- {t['name']}{' (pinned/authoritative)' if t.get('pinned') else ''}: {t.get('description') or ''}"
        for t in vocab
    )
    if kind == "entity":
        criterion = (
            "Choose 'new' ONLY if each instance of the type would merit its own standalone wiki "
            "page — a durable, individually-notable subject (a person, organization, place, work, "
            "law, lasting event). Choose 'drop' when instances are passing details, circumstances, "
            "measurements, time windows, or attributes of some other subject rather than subjects in "
            "their own right — even if topically relevant. Merge near-synonyms onto an existing type."
        )
    else:
        criterion = (
            "Choose 'new' when genuinely distinct AND aligned with the user's interests; 'drop' when "
            "incidental or not aligned. Merge near-synonyms; keep genuinely distinct relations separate "
            "(e.g. Funds vs Sponsors). Name a new type in sentence case — capitalize only the first "
            "word (e.g. 'Located in', 'Works at'), never UPPER_CASE or with underscores."
        )
    system = (
        f"You maintain a controlled vocabulary of {kind} types for a knowledge graph.\n"
        f"The user cares about: {interests}\n\n"
        f"Existing {kind} types (reuse the exact name when a candidate means the same thing; "
        f"pinned types are authoritative and must not be renamed):\n{vocab_lines or '(none yet)'}\n\n"
        "For each candidate below decide: 'existing' (a synonym of an existing type — give its exact "
        "canonical name), 'new' (give a clean name and a one-line description), or 'drop'.\n"
        f"{criterion}"
    )
    ex = examples or {}
    user = "Candidates:\n" + "\n".join(f"- {c}" + (f' (e.g. "{ex[c]}")' if c in ex else "") for c in candidates)
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "type_consolidation",
            "strict": True,
            "schema": _strict_schema(TypeConsolidation.model_json_schema()),
        },
    }
    out = _chat(
        client, model, [{"role": "system", "content": system}, {"role": "user", "content": user}], llm_params, schema
    )
    if out is None:
        raise ValueError("type consolidation returned no content")
    result = TypeConsolidation.model_validate_json(out)
    # The model echoes each candidate back, often including the ` (e.g. "…")` example
    # suffix we render. Key by the ORIGINAL candidate so callers can look decisions up
    # by the bare type name; match the echo to the original by longest normalized prefix.
    originals = {_norm_type(c): c for c in candidates}
    decisions: dict[str, dict[str, str]] = {}
    for d in result.decisions:
        echoed = _norm_type(d.candidate)
        key = (
            echoed
            if echoed in originals
            else max((n for n in originals if echoed.startswith(n)), key=len, default=echoed)
        )
        decisions[key] = d.model_dump(exclude={"candidate"})
    return decisions


def merge_content(
    knowledge_base_id: str,
    content: str,
    entity_types: list[dict[str, str]],
    relationship_types: list[dict[str, str]],
    job_id: str,
    *,
    interests: str = "",
    discover: bool = False,
    source_label: str = "",
    source_published_at: str = "",
    source_ingested_at: str = "",
) -> MergeResult:
    active_entities = [t for t in entity_types if not t.get("banned")]
    active_rels = [t for t in relationship_types if not t.get("banned")]
    # canon holds ACTIVE (non-banned) types only; banned is a disjoint drop-set. Order in resolve()
    # is safe because the two sets never overlap.
    entity_canon = {_norm_type(t["name"]): t["name"] for t in active_entities}
    rel_canon = {_norm_type(t["name"]): t["name"] for t in active_rels}
    banned_ent = {_norm_type(t["name"]) for t in entity_types if t.get("banned")}
    banned_rel = {_norm_type(t["name"]) for t in relationship_types if t.get("banned")}
    new_entity_types: list[dict[str, str]] = []
    new_relationship_types: list[dict[str, str]] = []
    llm_params: dict[str, Any] = {}
    created = merged = rels = 0
    name_to_id: dict[str, str] = {}
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client, get_neo4j_session() as neo:
        extraction = extract_knowledge(
            client,
            config.EXTRACTION_MODEL,
            entity_types,
            relationship_types,
            content,
            llm_params,
            interests=interests,
            discover=discover,
        )

        def resolve_kind(
            kind: str,
            extracted_types: set[str],
            canon: dict[str, str],
            banned: set[str],
            new_out: list[dict[str, str]],
            vocab: list[dict[str, str]],
            examples: dict[str, str],
        ) -> Callable[[str], str | None]:
            unmatched = sorted(
                {t for t in extracted_types if _norm_type(t) not in canon and _norm_type(t) not in banned}
            )
            decisions: dict[str, dict[str, str]] = {}
            if discover and unmatched:
                try:
                    decisions = consolidate_types(
                        client, config.TYPE_GATE_MODEL, kind, unmatched, vocab, interests, llm_params, examples=examples
                    )
                except Exception:
                    decisions = {}  # fast-path fallback: known types kept, novel deferred

            def resolve(t: str) -> str | None:
                key = _norm_type(t)
                if key in canon:
                    return canon[key]
                if key in banned:
                    return None
                d = decisions.get(key)
                if not d or d["decision"] == "drop":
                    return None
                if d["decision"] == "existing" and _norm_type(d["canonical"]) in canon:
                    return canon[_norm_type(d["canonical"])]
                if d["decision"] == "new" and _norm_type(d["name"]) not in banned:
                    name = d["name"]
                    if _norm_type(name) not in canon:
                        canon[_norm_type(name)] = name
                        new_out.append({"name": name, "description": d.get("description", "")})
                    return canon[_norm_type(name)]
                return None

            return resolve

        entity_examples: dict[str, str] = {}
        for e in extraction.entities:
            entity_examples.setdefault(e.type, e.name)
        resolve_entities = resolve_kind(
            "entity",
            {e.type for e in extraction.entities},
            entity_canon,
            banned_ent,
            new_entity_types,
            active_entities,
            entity_examples,
        )
        entities = []
        for e in extraction.entities:
            canonical = resolve_entities(e.type)
            if canonical is not None:
                e.type = canonical  # normalize to the configured casing before storing
                entities.append(e)
        resolved_ids = resolve_entities_batch(
            neo, client, config.RESOLUTION_MODEL, knowledge_base_id, entities, llm_params
        )
        # `resolve_entities_batch` compares each entity only against what Neo4j already holds,
        # so two occurrences of one name in the same extraction never see each other: both come
        # back None and each would mint its own node. Remember what this batch has already
        # assigned, so a repeat merges into that node instead. Keyed by type as well as name —
        # "Apple" the Organization and "Apple" the Product are not the same subject.
        assigned_in_batch: dict[tuple[str, str], str] = {}
        for entity, existing_id in zip(entities, resolved_ids, strict=True):
            batch_key = (normalize_name(entity.name), entity.type)
            if existing_id is None:
                existing_id = assigned_in_batch.get(batch_key)
            if existing_id is None:
                entity_id = str(uuid.uuid4())
                # A first sighting is synthesized too, with nothing to merge into. Storing the raw
                # description instead saved one LLM call and cost the knowledge base its substance:
                # article richness tracked merge count exactly, and because most entities are named
                # by a single document, the great majority of the graph stayed a short stub that
                # nothing would ever revisit — and never gained a `## Background` section at all.
                result = synthesize_article(
                    client, config.LLM_MODEL, entity.name, entity.type, "", entity.description, llm_params
                )
                article, summary = result.article, result.abstract
                created += 1
            else:
                entity_id = existing_id
                row = neo.run(
                    "MATCH (e:Entity {id: $id, knowledge_base_id: $knowledge_base_id}) RETURN e.article AS a",
                    {"id": entity_id, "knowledge_base_id": knowledge_base_id},
                ).single()
                existing_article = row["a"] if row and row["a"] else ""
                result = synthesize_article(
                    client, config.LLM_MODEL, entity.name, entity.type, existing_article, entity.description, llm_params
                )
                article, summary = result.article, result.abstract
                merged += 1
            upsert_entity(neo, knowledge_base_id, entity_id, entity, summary, article)
            write_provenance(
                neo,
                knowledge_base_id,
                entity_id,
                job_id,
                label=source_label,
                published_at=source_published_at,
                ingested_at=source_ingested_at,
            )
            assigned_in_batch[batch_key] = entity_id
            name_to_id[normalize_name(entity.name)] = entity_id

        rel_examples: dict[str, str] = {}
        for r in extraction.relationships:
            rel_examples.setdefault(r.type, f"{r.source_name} -> {r.target_name}")
        resolve_rels = resolve_kind(
            "relationship",
            {r.type for r in extraction.relationships},
            rel_canon,
            banned_rel,
            new_relationship_types,
            active_rels,
            rel_examples,
        )
        for rel in extraction.relationships:
            canonical = resolve_rels(rel.type)
            if canonical is None:
                continue
            src = name_to_id.get(normalize_name(rel.source_name))
            tgt = name_to_id.get(normalize_name(rel.target_name))
            if src and tgt:
                write_relationship(neo, knowledge_base_id, src, tgt, canonical, job_id)
                rels += 1
    return MergeResult(
        entities_created=created,
        entities_merged=merged,
        relationships_created=rels,
        new_entity_types=new_entity_types,
        new_relationship_types=new_relationship_types,
    )
