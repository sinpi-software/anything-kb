import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text as sqlalchemy_text

# These tests need the docker Neo4j. Skip if the driver can't connect.
neo4j = pytest.importorskip("neo4j")

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")
os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import knowledge as knowledge_mod  # noqa: E402
from db import get_postgres_session  # noqa: E402
from knowledge import (  # noqa: E402
    ExtractedEntity,
    ExtractedRelationship,
    KnowledgeExtraction,
    MergeResult,
    TypeConsolidation,
    TypeDecision,
    build_extraction_messages,
    candidate_query,
    escape_lucene,
    fulltext_candidate_query,
    merge_content,
    normalize_name,
    upsert_entity,
    write_provenance,
    write_relationship,
)
from neo4j_client import bootstrap_schema, get_neo4j_session  # noqa: E402


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as session:
            session.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqlalchemy_text("SELECT 1"))
        return True
    except Exception:
        return False


requires_neo4j_and_postgres = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)


@requires_neo4j
def test_bootstrap_is_idempotent() -> None:
    bootstrap_schema()
    bootstrap_schema()  # second call must not raise
    with get_neo4j_session() as session:
        names = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}
        assert "entity_id" in names


def test_extraction_parses_from_json() -> None:
    payload = (
        '{"entities": [{"name": "Ada Lovelace", "type": "Person", "description": "Mathematician", '
        '"aliases": ["Ada"]}], "relationships": [{"source_name": "Ada Lovelace", '
        '"target_name": "Analytical Engine", "type": "WORKED_ON"}]}'
    )
    result = KnowledgeExtraction.model_validate_json(payload)
    assert result.entities[0] == ExtractedEntity(
        name="Ada Lovelace", type="Person", description="Mathematician", aliases=["Ada"]
    )
    assert result.relationships[0].type == "WORKED_ON"


def test_extraction_defaults_aliases_empty() -> None:
    e = ExtractedEntity(name="X", type="Thing", description="d")
    assert e.aliases == []


def test_build_extraction_messages_open_mode_includes_interests_and_invites_new_types() -> None:
    msgs = build_extraction_messages(
        [{"name": "Person", "description": "a named human"}],
        [{"name": "Affected by", "description": ""}],
        "Some article",
        interests="US civic politics",
        discover=True,
    )
    joined = " ".join(m["content"] for m in msgs)
    assert "US civic politics" in joined
    assert "Person" in joined and "a named human" in joined
    assert "new" in joined.lower()  # invites coining new types


def test_build_extraction_messages_guided_mode_forbids_new_types() -> None:
    msgs = build_extraction_messages([{"name": "Person", "description": ""}], [], "x", interests="i", discover=False)
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "do not invent" in joined


def test_normalize_name() -> None:
    assert normalize_name("  Barack   Obama ") == "barack obama"


def test_candidate_query_is_knowledge_base_and_type_scoped() -> None:
    query, params = candidate_query("knowledge_base-1", "Person", "ada lovelace", 5)
    assert "knowledge_base_id" in query
    assert params["knowledge_base_id"] == "knowledge_base-1"
    assert params["type"] == "Person"
    assert params["name_normalized"] == "ada lovelace"
    assert params["limit"] == 5


def test_fulltext_candidate_query_is_knowledge_base_and_type_scoped() -> None:
    query, params = fulltext_candidate_query("knowledge_base-1", "Person", "ada lovelace", 5)
    assert "knowledge_base_id" in query
    assert "type" in query
    assert params["knowledge_base_id"] == "knowledge_base-1"
    assert params["type"] == "Person"
    assert params["q"] == "ada lovelace"
    assert params["limit"] == 5


def test_escape_lucene_escapes_special_characters() -> None:
    assert escape_lucene("a+b:c") == "a\\+b\\:c"
    assert escape_lucene('(quoted "phrase")') == '\\(quoted \\"phrase\\"\\)'


def test_strict_schema_closes_objects_and_requires_all_keys() -> None:
    schema = knowledge_mod._strict_schema(KnowledgeExtraction.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"entities", "relationships"}
    entity = schema["$defs"]["ExtractedEntity"]
    assert entity["additionalProperties"] is False
    assert set(entity["required"]) == {"name", "type", "description", "aliases"}
    assert "default" not in entity["properties"]["aliases"]


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeChatResult:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeChat:
    """Stands in for OpenRouter's `client.chat`; records whether `.send` was invoked
    so tests can assert the single-/zero-candidate paths never reach the LLM."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.send_called = False

    def send(self, **kwargs: Any) -> _FakeChatResult:
        self.send_called = True
        return _FakeChatResult(self.answer)


class _FakeResolutionClient:
    """Stands in for OpenRouter, exposing only the `.chat.send` surface resolution uses."""

    def __init__(self, answer: str) -> None:
        self.chat = _FakeChat(answer)


class _FakeNeoSession:
    """Stands in for a Neo4j Session; `.run` returns pre-baked candidate records
    regardless of the query/params passed, so no real Neo4j connection is needed."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def run(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.records


def _resolution_entity() -> ExtractedEntity:
    return ExtractedEntity(name="Ada", type="Person", description="Mathematician")


def _resolve_batch(session: Any, client: Any) -> list[str | None]:
    return knowledge_mod.resolve_entities_batch(
        session, client, "model", "knowledge_base-1", [_resolution_entity()], {}
    )


def test_resolve_batch_multi_candidate_valid_answer_returns_id() -> None:
    session = _FakeNeoSession(
        [{"id": "a", "name": "Ada", "summary": "s1"}, {"id": "b", "name": "Ada Byron", "summary": "s2"}]
    )
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "a"}]}')
    assert _resolve_batch(session, client) == ["a"]
    assert client.chat.send_called


def test_resolve_batch_multi_candidate_hallucinated_answer_returns_none() -> None:
    session = _FakeNeoSession(
        [{"id": "a", "name": "Ada", "summary": "s1"}, {"id": "b", "name": "Ada Byron", "summary": "s2"}]
    )
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "not-a-real-id"}]}')
    assert _resolve_batch(session, client) == [None]
    assert client.chat.send_called


def test_resolve_batch_single_candidate_skips_llm() -> None:
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "s1"}])
    client = _FakeResolutionClient("unused")
    assert _resolve_batch(session, client) == ["a"]
    assert not client.chat.send_called


def test_resolve_batch_no_candidates_skips_llm() -> None:
    session = _FakeNeoSession([])
    client = _FakeResolutionClient("unused")
    assert _resolve_batch(session, client) == [None]
    assert not client.chat.send_called


def test_consolidate_types_maps_synonym_and_mints_new(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = TypeConsolidation(
        decisions=[
            TypeDecision(candidate="backed", decision="existing", canonical="Sponsors"),
            TypeDecision(candidate="Endorses", decision="new", name="Endorses", description="publicly endorses"),
            TypeDecision(candidate="mentioned", decision="drop"),
        ]
    ).model_dump_json()
    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: payload)
    out = knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]  # consolidate_types forwards client to a monkeypatched _chat
        model="m",
        kind="relationship",
        candidates=["backed", "Endorses", "mentioned"],
        vocab=[{"name": "Sponsors", "description": "introduces legislation", "pinned": True}],
        interests="civic",
        llm_params={},
    )
    assert out[knowledge_mod._norm_type("backed")]["canonical"] == "Sponsors"
    assert out[knowledge_mod._norm_type("Endorses")]["name"] == "Endorses"
    assert out[knowledge_mod._norm_type("mentioned")]["decision"] == "drop"


def _cleanup(knowledge_base_id: str) -> None:
    with get_neo4j_session() as session:
        session.run(
            "MATCH (n) WHERE n.knowledge_base_id = $knowledge_base_id DETACH DELETE n",
            {"knowledge_base_id": knowledge_base_id},
        )


@requires_neo4j
def test_upsert_and_relationship_roundtrip() -> None:
    bootstrap_schema()
    knowledge_base = f"test-{uuid.uuid4()}"
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with get_neo4j_session() as session:
            upsert_entity(
                session, knowledge_base, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A"
            )
            upsert_entity(
                session, knowledge_base, b_id, ExtractedEntity(name="Engine", type="Thing", description="d"), "sum B"
            )
            # Re-upsert A with a new summary — must update, not duplicate.
            upsert_entity(
                session, knowledge_base, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A v2"
            )
            write_relationship(session, knowledge_base, a_id, b_id, "WORKED_ON", "art-1")
            write_provenance(session, knowledge_base, a_id, "art-1")

            count = session.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN count(e) AS c", {"o": knowledge_base}
            ).single(True)["c"]
            assert count == 2  # no duplicate
            summ = session.run("MATCH (e:Entity {id: $id}) RETURN e.summary AS s", {"id": a_id}).single(True)["s"]
            assert summ == "sum A v2"
            rels = session.run(
                "MATCH (:Entity {knowledge_base_id: $o})-[r:RELATED]->() RETURN r.type AS t", {"o": knowledge_base}
            ).single(True)["t"]
            assert rels == "WORKED_ON"
    finally:
        _cleanup(knowledge_base)


@requires_neo4j
def test_knowledge_base_isolation() -> None:
    bootstrap_schema()
    knowledge_base_a, knowledge_base_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    try:
        with get_neo4j_session() as session:
            ada = ExtractedEntity(name="Ada", type="Person", description="d")
            upsert_entity(session, knowledge_base_a, str(uuid.uuid4()), ada, "A")
            upsert_entity(session, knowledge_base_b, str(uuid.uuid4()), ada, "B")
            a_count = session.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN count(e) AS c", {"o": knowledge_base_a}
            ).single(True)["c"]
            assert a_count == 1  # knowledge_base_b's identically-named entity is invisible to knowledge_base_a
    finally:
        _cleanup(knowledge_base_a)
        _cleanup(knowledge_base_b)


@requires_neo4j
def test_fulltext_candidate_query_matches_name_variants_and_is_knowledge_base_scoped() -> None:
    """Proves the `entity_name` full-text index (from bootstrap_schema) is actually wired
    into candidate lookup: two differently-named-but-related entities in the same knowledge_base both
    surface for a shared-token query, while a same-token entity in a different knowledge_base does not."""
    bootstrap_schema()
    knowledge_base_a, knowledge_base_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    try:
        with get_neo4j_session() as session:
            upsert_entity(
                session,
                knowledge_base_a,
                str(uuid.uuid4()),
                ExtractedEntity(name="Barack Obama", type="Person", description="d"),
                "s1",
            )
            upsert_entity(
                session,
                knowledge_base_a,
                str(uuid.uuid4()),
                ExtractedEntity(name="President Obama", type="Person", description="d"),
                "s2",
            )
            upsert_entity(
                session,
                knowledge_base_b,
                str(uuid.uuid4()),
                ExtractedEntity(name="Obama Fried Chicken", type="Person", description="d"),
                "s3",
            )
            query, params = fulltext_candidate_query(knowledge_base_a, "Person", "Obama", 5)
            names = {r["name"] for r in session.run(query, params)}
            assert names == {"Barack Obama", "President Obama"}  # both knowledge_base_a variants found
    finally:
        _cleanup(knowledge_base_a)
        _cleanup(knowledge_base_b)


@requires_neo4j_and_postgres
def test_merge_content_constrains_types_and_records_job_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="Person", description="Mathematician"),
            ExtractedEntity(name="Engine", type="Thing", description="A machine"),
            ExtractedEntity(name="Rover", type="Robot", description="Off-list entity type"),
        ],
        relationships=[
            ExtractedRelationship(source_name="Ada", target_name="Engine", type="WORKED_ON"),
            # Off-list relationship type — must be dropped.
            ExtractedRelationship(source_name="Ada", target_name="Engine", type="HATES"),
        ],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(
            knowledge_base_id,
            "Ada worked on the Engine.",
            [{"name": "Person", "description": ""}, {"name": "Thing", "description": ""}],
            [{"name": "WORKED_ON", "description": ""}],
            job_id,
        )
        assert isinstance(result, MergeResult)
        assert result.entities_created == 2  # Rover filtered
        assert result.relationships_created == 1  # HATES filtered
        with get_neo4j_session() as neo:
            ecount = neo.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN count(e) AS c", {"o": knowledge_base_id}
            ).single(True)["c"]
            assert ecount == 2
            rel = neo.run(
                "MATCH (:Entity {knowledge_base_id: $o})-[r:RELATED]->() RETURN r.type AS t, r.source_job_id AS j",
                {"o": knowledge_base_id},
            ).single(True)
            assert rel["t"] == "WORKED_ON"
            assert rel["j"] == job_id
            src = neo.run(
                "MATCH (:Entity {knowledge_base_id: $o})-[:MENTIONED_IN]->(s:Source) RETURN s.job_id AS j LIMIT 1",
                {"o": knowledge_base_id},
            ).single(True)
            assert src["j"] == job_id
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})


@requires_neo4j_and_postgres
def test_merge_content_consolidates_novel_relationship_type_and_records_new_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="person", description="d"),
        ],
        relationships=[
            ExtractedRelationship(source_name="Ada", target_name="Ada", type="AFFECTED_BY"),
            # Novel relationship type — no fast-path match, resolved via consolidate_types.
            ExtractedRelationship(source_name="Ada", target_name="Ada", type="endorsed"),
        ],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")
    monkeypatch.setattr(
        knowledge_mod,
        "consolidate_types",
        lambda *a, **k: {
            knowledge_mod._norm_type("endorsed"): {
                "decision": "new",
                "name": "Endorses",
                "description": "backs publicly",
            }
        },
    )

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(
            knowledge_base_id,
            "Ada endorsed Ada and is affected by Ada.",
            [{"name": "Person", "description": ""}],
            [{"name": "Affected by", "description": ""}],
            job_id,
            discover=True,
        )
        assert result.relationships_created == 2
        assert result.new_relationship_types == [{"name": "Endorses", "description": "backs publicly"}]
        with get_neo4j_session() as neo:
            types = {
                r["t"]
                for r in neo.run(
                    "MATCH (:Entity {knowledge_base_id: $o})-[r:RELATED]->() RETURN r.type AS t",
                    {"o": knowledge_base_id},
                )
            }
            assert types == {"Affected by", "Endorses"}
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})


@requires_neo4j_and_postgres
def test_merge_content_normalizes_type_casing_and_stores_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_schema()
    # The model emits UPPER_SNAKE / lowercase even though the config uses sentence case.
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="person", description="d"),
            ExtractedEntity(name="Bill 5", type="LEGISLATION", description="d"),
        ],
        relationships=[
            ExtractedRelationship(source_name="Ada", target_name="Bill 5", type="AFFECTED_BY"),
        ],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(
            knowledge_base_id,
            "Ada is affected by Bill 5.",
            [{"name": "Person", "description": ""}, {"name": "Legislation", "description": ""}],
            [{"name": "Affected by", "description": ""}],
            job_id,
        )
        assert result.relationships_created == 1  # matched despite the model's UPPER_SNAKE casing
        with get_neo4j_session() as neo:
            rel = neo.run(
                "MATCH (:Entity {knowledge_base_id: $o})-[r:RELATED]->() RETURN r.type AS t", {"o": knowledge_base_id}
            ).single(True)
            assert rel["t"] == "Affected by"  # stored as the configured name, not the model's casing
            entity_type = neo.run(
                "MATCH (e:Entity {knowledge_base_id: $o, name: 'Ada'}) RETURN e.type AS t", {"o": knowledge_base_id}
            ).single(True)
            assert entity_type["t"] == "Person"  # entity type normalized to configured casing too
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})


@requires_neo4j_and_postgres
def test_merge_content_new_type_colliding_with_existing_canon_uses_canonical_casing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """consolidate_types can mint a "new" type whose name normalizes to a type already in canon
    (e.g. a differently-cased duplicate). The stored type must be the existing canonical name,
    never the raw minted name, and no duplicate entry should be added to new_entity_types."""
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            # "Human" is unmatched against the configured "Person" type, so it goes through
            # consolidate_types rather than the fast path.
            ExtractedEntity(name="Alice", type="Human", description="d"),
        ],
        relationships=[],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")
    monkeypatch.setattr(
        knowledge_mod,
        "consolidate_types",
        lambda *a, **k: {
            # Mints "PERSON", which normalizes the same as the already-configured "Person".
            knowledge_mod._norm_type("Human"): {
                "decision": "new",
                "name": "PERSON",
                "description": "a human being",
            }
        },
    )

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(
            knowledge_base_id,
            "Alice is a human.",
            [{"name": "Person", "description": ""}],
            [],
            job_id,
            discover=True,
        )
        assert result.entities_created == 1
        assert result.new_entity_types == []  # no duplicate "PERSON" type appended
        with get_neo4j_session() as neo:
            entity_type = neo.run(
                "MATCH (e:Entity {knowledge_base_id: $o, name: 'Alice'}) RETURN e.type AS t", {"o": knowledge_base_id}
            ).single(True)
            assert entity_type["t"] == "Person"  # canonical stored casing, not the raw minted "PERSON"
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})
