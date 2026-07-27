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

import config  # noqa: E402
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
    # New types must be durable categories, never temporal/quantity ones (blocks the coined `TimeWindow` type).
    assert "durable, individually-referenceable" in joined
    assert "time windows, durations" in joined


def test_build_extraction_messages_guided_mode_forbids_new_types() -> None:
    msgs = build_extraction_messages([{"name": "Person", "description": ""}], [], "x", interests="i", discover=False)
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "do not invent" in joined


@pytest.mark.parametrize("discover", [True, False])
def test_build_extraction_messages_bars_vague_phrase_entities(discover: bool) -> None:
    msgs = build_extraction_messages([{"name": "Person", "description": ""}], [], "x", interests="i", discover=discover)
    joined = " ".join(m["content"] for m in msgs).lower()
    # Both modes must instruct the model to skip descriptive noun-phrases like
    # "a two-year legal battle" instead of minting them as their own nodes.
    assert "concrete, specific" in joined
    assert "descriptive phrases" in joined


@pytest.mark.parametrize("discover", [True, False])
def test_build_extraction_messages_pushes_relationship_completeness(discover: bool) -> None:
    msgs = build_extraction_messages([{"name": "Person", "description": ""}], [], "x", interests="i", discover=discover)
    joined = " ".join(m["content"] for m in msgs).lower()
    # Both modes must push the model to connect + anchor entities to recurring shared hubs so the
    # graph coheres — domain-agnostic, NOT hardcoded to geography — and keep endpoints matching names.
    assert "connect and anchor" in joined
    assert "recurring anchors" in joined
    assert "companies and technologies" in joined  # a non-geographic domain example is present
    assert "name of an entity in your entities list" in joined


@pytest.mark.parametrize("discover", [True, False])
def test_build_extraction_messages_demands_a_self_contained_description(discover: bool) -> None:
    """For most entities the description IS the article, permanently — synthesize_article runs
    only on merge, and in a real ingest 178 of 193 entities were named by exactly one document.
    Those averaged 169 characters against 5,613 for the one entity mentioned nine times.

    The instruction this replaces was a single trailing clause, and compliance was erratic: it
    produced "Founder of the Trail Blazers in 1970." and "Town in Oregon where Randy Stapilus
    resides" as readily as a real paragraph. Both are defined by their relation to something else
    in the same document, which is exactly what a standalone entry must not do."""
    msgs = build_extraction_messages(
        [{"name": "Person", "description": ""}], [], "x", interests="i", discover=discover
    )
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "stands on its own" in joined
    assert "role in this text" in joined  # names the failure mode, not just the goal


@pytest.mark.parametrize("discover", [True, False])
def test_build_extraction_messages_demands_a_specific_relationship_type(discover: bool) -> None:
    """The completeness rule pushes hard for coverage — "connect every entity" — and says nothing
    about how precise the relationship type must be. With a catch-all named "Related to" in the
    vocabulary, the cheapest way to satisfy coverage is to label every edge with it, which is what
    the model did: 216 of 241 edges in a real ingest, including "person -[Related to]-> the
    department prosecuting them". Coverage must be asked for together with specificity."""
    msgs = build_extraction_messages(
        [{"name": "Person", "description": ""}],
        [{"name": "Related to", "description": ""}],
        "x",
        interests="i",
        discover=discover,
    )
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "most specific" in joined
    assert "related to" in joined  # the catch-all is named as a last resort, not a default


def test_build_extraction_messages_open_mode_invites_new_relationship_types() -> None:
    """Type discovery worked for entities and never once for relationships across two full ingests:
    the entity vocabulary grew from 4 types to 9 while the relationship vocabulary stayed at its
    original 3. The permission to coin a type is phrased only in entity terms — "durable,
    individually-referenceable things (people, organizations, places, works, events)" — which a
    relationship can never satisfy, so the model correctly declined. `consolidate_types` was
    therefore never called for relationships and its permissive gate never got a say."""
    msgs = build_extraction_messages(
        [{"name": "Person", "description": ""}],
        [{"name": "Related to", "description": ""}],
        "x",
        interests="i",
        discover=True,
    )
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "new relationship type" in joined


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


def test_derive_abstract_takes_first_sentence_truncated() -> None:
    from knowledge import _derive_abstract

    assert _derive_abstract("Ada was a mathematician. She wrote the first algorithm.") == "Ada was a mathematician."
    assert len(_derive_abstract("x " * 400)) <= 240


def test_synthesize_article_returns_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge import ArticleResult, synthesize_article

    payload = ArticleResult(abstract="Ada, a mathematician.", article="Ada Lovelace…\n\n## Work\n…").model_dump_json()
    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: payload)
    out = synthesize_article(client=None, model="m", existing_article="old", new_info="new", llm_params={})  # type: ignore[arg-type]
    assert out.abstract == "Ada, a mathematician." and out.article.startswith("Ada Lovelace")


def test_synthesize_article_falls_back_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge import synthesize_article

    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: None)
    out = synthesize_article(
        client=None,  # type: ignore[arg-type]
        model="m",
        existing_article="Existing body. More.",
        new_info="new",
        llm_params={},
    )
    assert out.article == "Existing body. More." and out.abstract == "Existing body."


def test_synthesize_article_falls_back_on_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """_chat can return "" (empty but non-None); this must fall back like None, not
    reach model_validate_json with an empty payload."""
    from knowledge import synthesize_article

    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: "")
    out = synthesize_article(
        client=None,  # type: ignore[arg-type]
        model="m",
        existing_article="Existing body. More.",
        new_info="new",
        llm_params={},
    )
    assert out.article == "Existing body. More." and out.abstract == "Existing body."


def test_synthesize_article_falls_back_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-JSON/invalid LLM response must not raise ValidationError out of synthesize_article
    (which would otherwise propagate through merge_content and fail the whole ingest)."""
    from knowledge import synthesize_article

    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: "not json")
    out = synthesize_article(
        client=None,  # type: ignore[arg-type]
        model="m",
        existing_article="Existing body. More.",
        new_info="new",
        llm_params={},
    )
    assert out.article == "Existing body. More." and out.abstract == "Existing body."


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


def test_resolve_batch_single_candidate_is_verified_and_can_merge() -> None:
    # A single candidate no longer auto-merges; the resolver is consulted and may confirm it.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "s1"}])
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "a"}]}')
    assert _resolve_batch(session, client) == ["a"]
    assert client.chat.send_called


def test_resolve_batch_single_candidate_different_subject_stays_new() -> None:
    # The bug this fixes: a single (weak) candidate that is a DIFFERENT subject must NOT merge.
    # The resolver answers NEW, which is not among the candidate ids, so the entity becomes new.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "an unrelated thing"}])
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "NEW"}]}')
    assert _resolve_batch(session, client) == [None]
    assert client.chat.send_called


def test_resolve_batch_resolver_empty_leaves_candidate_new() -> None:
    # If the resolver returns nothing, a candidate-having entity is created new — never wrongly merged.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "s1"}])
    client = _FakeResolutionClient("")
    assert _resolve_batch(session, client) == [None]
    assert client.chat.send_called


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


def test_consolidate_types_entity_uses_wiki_criterion_and_renders_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat(client: Any, model: str, messages: list[dict[str, str]], llm_params: Any, schema: Any) -> str:
        captured["messages"] = messages
        return TypeConsolidation(decisions=[TypeDecision(candidate="TimeWindow", decision="drop")]).model_dump_json()

    monkeypatch.setattr(knowledge_mod, "_chat", fake_chat)
    out = knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]
        model="m",
        kind="entity",
        candidates=["TimeWindow"],
        vocab=[],
        interests="local news",
        llm_params={},
        examples={"TimeWindow": "7:00 a.m.-3:30 p.m."},
    )
    system = captured["messages"][0]["content"]
    user = captured["messages"][1]["content"]
    assert "wiki page" in system
    assert "7:00 a.m.-3:30 p.m." in user  # example instance rendered next to the candidate
    assert out[knowledge_mod._norm_type("TimeWindow")]["decision"] == "drop"


def test_consolidate_types_maps_echoed_example_suffix_back_to_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real models echo the candidate WITH the ` (e.g. "…")` suffix we render. The result
    must still be keyed by the bare candidate name, or merge_content drops every novel type."""

    def fake_chat(client: Any, model: str, messages: list[dict[str, str]], llm_params: Any, schema: Any) -> str:
        return TypeConsolidation(
            decisions=[
                TypeDecision(candidate='Event (e.g. "Airplane crash")', decision="new", name="Event", description="d"),
                TypeDecision(
                    candidate='Organization (e.g. "FAA")', decision="new", name="Organization", description="d"
                ),
            ]
        ).model_dump_json()

    monkeypatch.setattr(knowledge_mod, "_chat", fake_chat)
    out = knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]
        model="m",
        kind="entity",
        candidates=["Event", "Organization"],
        vocab=[],
        interests="civic",
        llm_params={},
        examples={"Event": "Airplane crash", "Organization": "FAA"},
    )
    # Keyed by the bare candidate norm, despite the echoed example suffix.
    assert out[knowledge_mod._norm_type("Event")]["name"] == "Event"
    assert out[knowledge_mod._norm_type("Organization")]["name"] == "Organization"


def test_consolidate_types_relationship_keeps_distinctness_criterion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat(client: Any, model: str, messages: list[dict[str, str]], llm_params: Any, schema: Any) -> str:
        captured["messages"] = messages
        return TypeConsolidation(
            decisions=[TypeDecision(candidate="backed", decision="existing", canonical="Sponsors")]
        ).model_dump_json()

    monkeypatch.setattr(knowledge_mod, "_chat", fake_chat)
    knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]
        model="m",
        kind="relationship",
        candidates=["backed"],
        vocab=[{"name": "Sponsors", "description": ""}],
        interests="civic",
        llm_params={},
    )
    system = captured["messages"][0]["content"]
    assert "Funds vs Sponsors" in system  # existing distinctness guidance retained
    assert "wiki page" not in system  # durability test is entity-only
    assert "sentence case" in system  # new relationship types must be named in sentence case


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
                session,
                knowledge_base,
                a_id,
                ExtractedEntity(name="Ada", type="Person", description="d"),
                "sum A",
                "art A",
            )
            upsert_entity(
                session,
                knowledge_base,
                b_id,
                ExtractedEntity(name="Engine", type="Thing", description="d"),
                "sum B",
                "art B",
            )
            # Re-upsert A with a new summary/article — must update, not duplicate.
            upsert_entity(
                session,
                knowledge_base,
                a_id,
                ExtractedEntity(name="Ada", type="Person", description="d"),
                "sum A v2",
                "art A v2",
            )
            write_relationship(session, knowledge_base, a_id, b_id, "WORKED_ON", "art-1")
            write_provenance(session, knowledge_base, a_id, "art-1")

            count = session.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN count(e) AS c", {"o": knowledge_base}
            ).single(True)["c"]
            assert count == 2  # no duplicate
            row = session.run("MATCH (e:Entity {id: $id}) RETURN e.summary AS s, e.article AS a", {"id": a_id}).single(
                True
            )
            assert row["s"] == "sum A v2"
            assert row["a"] == "art A v2"
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
            upsert_entity(session, knowledge_base_a, str(uuid.uuid4()), ada, "A", "art A")
            upsert_entity(session, knowledge_base_b, str(uuid.uuid4()), ada, "B", "art B")
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
                "a1",
            )
            upsert_entity(
                session,
                knowledge_base_a,
                str(uuid.uuid4()),
                ExtractedEntity(name="President Obama", type="Person", description="d"),
                "s2",
                "a2",
            )
            upsert_entity(
                session,
                knowledge_base_b,
                str(uuid.uuid4()),
                ExtractedEntity(name="Obama Fried Chicken", type="Person", description="d"),
                "s3",
                "a3",
            )
            query, params = fulltext_candidate_query(knowledge_base_a, "Person", "Obama", 5)
            names = {r["name"] for r in session.run(query, params)}
            assert names == {"Barack Obama", "President Obama"}  # both knowledge_base_a variants found
    finally:
        _cleanup(knowledge_base_a)
        _cleanup(knowledge_base_b)


def _echo_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub article synthesis to echo the description straight back.

    merge_content now synthesizes a NEW entity's article as well as a merged one, so tests that
    exercise something else entirely — type constraints, provenance, casing — would otherwise need
    a real LLM client where they previously needed none. Echoing keeps their existing assertions
    about article content true and unchanged.
    """
    monkeypatch.setattr(
        knowledge_mod,
        "synthesize_article",
        lambda client, model, existing, new_info, params: knowledge_mod.ArticleResult(
            article=new_info, abstract=knowledge_mod._derive_abstract(new_info)
        ),
    )


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
    _echo_synthesis(monkeypatch)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))

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
    _echo_synthesis(monkeypatch)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
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
    _echo_synthesis(monkeypatch)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))

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
    _echo_synthesis(monkeypatch)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
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


@requires_neo4j_and_postgres
def test_merge_content_synthesizes_a_new_entitys_article_from_its_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new entity's article is synthesized too, not stored as the raw description.

    This deliberately inverts the earlier contract, which asserted synthesize_article must NOT be
    called for a new entity — the description was stored verbatim to save one LLM call. The cost of
    that saving turned out to be the whole knowledge base: article richness tracked merge count
    exactly, and since 178 of 193 entities in a real ingest were named by exactly one document,
    92% of the graph was a ~169-character stub that nothing would ever enrich.

    Passing "" as the existing article routes a first sighting through the same living-document
    prompt a merge uses, so a new entity gets a structured article rather than a fragment.
    """
    bootstrap_schema()
    description = "Ada was a mathematician. She wrote the first algorithm."
    extraction = KnowledgeExtraction(
        entities=[ExtractedEntity(name="Ada", type="Person", description=description)],
        relationships=[],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))

    seen: dict[str, Any] = {}

    def _capture(client: Any, model: str, existing_article: str, new_info: str, params: Any) -> Any:
        seen["existing_article"] = existing_article
        seen["new_info"] = new_info
        return knowledge_mod.ArticleResult(
            article="## Life\nAda Lovelace was a mathematician…", abstract="A mathematician."
        )

    monkeypatch.setattr(knowledge_mod, "synthesize_article", _capture)

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
            description,
            [{"name": "Person", "description": ""}],
            [],
            job_id,
            source_label="newsletter",
            source_published_at="2026-07-22",
        )
        assert result.entities_created == 1
        # Synthesis ran, and it ran with no existing article — a first sighting, not a merge.
        assert seen["existing_article"] == ""
        assert seen["new_info"] == description
        with get_neo4j_session() as neo:
            row = neo.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN e.article AS a, e.summary AS s",
                {"o": knowledge_base_id},
            ).single(True)
            assert row["a"] == "## Life\nAda Lovelace was a mathematician…"
            assert row["s"] == "A mathematician."
            source = neo.run(
                "MATCH (s:Source {knowledge_base_id: $o, job_id: $j}) "
                "RETURN s.label AS l, toString(s.published_at) AS d",
                {"o": knowledge_base_id, "j": job_id},
            ).single(True)
            assert source["l"] == "newsletter"
            assert source["d"].startswith("2026-07-22")  # stored as a native datetime
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})


@requires_neo4j_and_postgres
def test_merge_content_dedupes_an_entity_repeated_within_one_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One article naming the same entity twice must produce one node, not two.

    `resolve_entities_batch` compares each extracted entity only against what Neo4j
    already holds, so on a first ingest neither occurrence sees the other, both come
    back None, and each mints its own UUID. A real Hacker News catalogue article hit
    this six times over for a single product, and every surplus copy was an isolated
    node carrying only its own mention's relationships.

    The second occurrence takes the merge path, so its description folds into the
    first's article rather than being discarded.
    """
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Atmos Embassy", type="Product", description="A torsion-pendulum clock."),
            # Same entity, named again with different spacing and casing — normalize_name
            # must see through both.
            ExtractedEntity(name="atmos  embassy", type="Product", description="Runs on temperature change."),
        ],
        relationships=[],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(
        knowledge_mod,
        "synthesize_article",
        lambda *a, **k: knowledge_mod.ArticleResult(article="both mentions", abstract="both mentions"),
    )

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    try:
        result = merge_content(
            knowledge_base_id,
            "irrelevant — extraction is stubbed",
            [{"name": "Product", "description": ""}],
            [],
            str(uuid.uuid4()),
        )
        assert result.entities_created == 1, "the repeat must not create a second node"
        with get_neo4j_session() as neo:
            count = neo.run(
                "MATCH (e:Entity {knowledge_base_id: $o}) RETURN count(e) AS c",
                {"o": knowledge_base_id},
            ).single(True)["c"]
        assert count == 1
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})


@requires_neo4j_and_postgres
def test_merge_content_existing_entity_synthesizes_article_and_stores_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When resolution finds an existing entity, merge_content must read its stored article, pass it
    to synthesize_article as the existing article, and persist the returned article + abstract."""
    from knowledge import ArticleResult

    bootstrap_schema()
    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    try:
        with get_neo4j_session() as neo:
            upsert_entity(
                neo,
                knowledge_base_id,
                entity_id,
                ExtractedEntity(name="Ada", type="Person", description="d"),
                "old summary",
                "Old article body.",
            )

        extraction = KnowledgeExtraction(
            entities=[ExtractedEntity(name="Ada", type="Person", description="New fact about Ada.")],
            relationships=[],
        )
        monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
        monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [entity_id])

        seen_existing_articles: list[str] = []

        def _fake_synthesize(client: Any, model: str, existing_article: str, new_info: str, llm_params: Any) -> Any:
            seen_existing_articles.append(existing_article)
            return ArticleResult(abstract="New abstract.", article="Merged article body.")

        monkeypatch.setattr(knowledge_mod, "synthesize_article", _fake_synthesize)

        class _NullClient:
            def __enter__(self) -> "_NullClient":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

        result = merge_content(
            knowledge_base_id,
            "New fact about Ada.",
            [{"name": "Person", "description": ""}],
            [],
            job_id,
        )
        assert result.entities_merged == 1
        assert seen_existing_articles == ["Old article body."]
        with get_neo4j_session() as neo:
            row = neo.run("MATCH (e:Entity {id: $id}) RETURN e.article AS a, e.summary AS s", {"id": entity_id}).single(
                True
            )
            assert row["a"] == "Merged article body."
            assert row["s"] == "New abstract."
    finally:
        _cleanup(knowledge_base_id)


@requires_neo4j_and_postgres
def test_merge_content_passes_examples_and_gate_model_and_drops_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="7:00 a.m.-3:30 p.m.", type="TimeWindow", description="a work window"),
            ExtractedEntity(name="Harry Morgan Bridge", type="Bridge", description="a bridge"),
        ],
        relationships=[],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    _echo_synthesis(monkeypatch)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))

    captured: dict[str, Any] = {}

    def fake_consolidate(
        client: Any,
        model: str,
        kind: str,
        candidates: Any,
        vocab: Any,
        interests: str,
        llm_params: Any,
        examples: Any = None,
    ) -> dict[str, dict[str, str]]:
        captured[kind] = {"model": model, "examples": examples}
        return {
            knowledge_mod._norm_type("TimeWindow"): {"decision": "drop"},
            knowledge_mod._norm_type("Bridge"): {"decision": "new", "name": "Bridge", "description": "a bridge"},
        }

    monkeypatch.setattr(knowledge_mod, "consolidate_types", fake_consolidate)

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
            "Bridge work 7:00 a.m.-3:30 p.m.",
            [{"name": "Person", "description": ""}],  # neither type is pre-known -> both go through the gate
            [],
            job_id,
            discover=True,
        )
        # Example instance for the fragment type was threaded into the gate, on the gate model.
        assert captured["entity"]["examples"]["TimeWindow"] == "7:00 a.m.-3:30 p.m."
        assert captured["entity"]["model"] == config.TYPE_GATE_MODEL
        # TimeWindow dropped, Bridge admitted -> exactly one entity written.
        assert result.entities_created == 1
        with get_neo4j_session() as neo:
            types = {
                r["t"]
                for r in neo.run(
                    "MATCH (e:Entity {knowledge_base_id: $o}) RETURN e.type AS t",
                    {"o": knowledge_base_id},
                )
            }
        assert types == {"Bridge"}
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})
