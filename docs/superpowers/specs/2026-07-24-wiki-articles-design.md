# Wiki-Grade Articles — Design

**Goal:** Upgrade each entity's content from a single merged paragraph into a **structured, living wiki article** (a lead + `## sections` in markdown) with **page-level citations**, synthesized incrementally as sources arrive. Expose the article + references over GraphQL so the (later) wiki page view can render them.

**Context:** Sub-project **B** of the self-building-wiki north star (entities = articles, relationships = links, sources = citations). **A** (self-building schema) shipped. **C** (per-entity page view) is a separate later spec — B stops at *producing and exposing* the content; C renders the page. See `knowledge-graph-engine-direction` memory and `docs/superpowers/specs/2026-07-24-self-building-schema-design.md`.

**Architecture in one line:** `merge_summary` already grows an incremental "encyclopedia article" in the Entity node's `summary` string. B evolves it into `synthesize_article` — same one-LLM-call-per-touched-entity-per-ingest, but it returns a **structured markdown article + a short abstract**; the node stores both; `Source` nodes gain a label+date so a page-level **References** list is derivable; GraphQL exposes `article` + `references`.

**Tech stack (unchanged):** FastAPI + Strawberry GraphQL, neo4j driver, OpenRouter, SQLAlchemy/Postgres.

## Global Constraints

- **Tenancy:** every read/write stays `knowledge_base_id`-scoped. B must not weaken isolation.
- **Cost parity:** article synthesis remains exactly **one LLM call per touched entity per ingest** (the same shape as today's `merge_summary`) — the structured `{abstract, article}` come from that single call.
- **No ingest-API change:** citations use the freeform `metadata.source` label already accepted at `POST /content` (+ the ingest date). The `/content` contract does not change.
- **References are page-level, not inline:** the markdown article body contains NO reference markers or a References section — references are derived from the entity's `Source` nodes and rendered separately.
- **`summary` stays concise:** it becomes a short abstract (≤ ~2 sentences) so entity-resolution prompts, candidate lists, and graph tooltips stay small; the full body lives in the new `article` field.
- **Backward compatible:** existing entities keep working — a backfill sets `article` from the current `summary` and re-derives a short `summary`; existing `Source` nodes get labels backfilled where their job metadata has one.

## 1. Structured article synthesis

Replace `merge_summary(client, model, existing, new, llm_params) -> str` with `synthesize_article(client, model, existing_article, new_info, llm_params) -> ArticleResult` where `ArticleResult` is a Pydantic model with structured output:

```
class ArticleResult(BaseModel):
    abstract: str   # one to two sentences; the lead, for lists/resolution/tooltips
    article: str    # full markdown: a lead paragraph, then `## sections` as material warrants
```

- Structured-output call (same `_chat` + `_strict_schema` pattern as extraction/resolution). One call.
- Prompt: "You maintain an encyclopedia article about an entity. Integrate the new information into the existing article as a living document: a lead paragraph followed by `## Section` headings as the material warrants. Keep all existing facts, add the new ones, note contradictions. Also produce a one-to-two-sentence abstract. **Do not add a References or Sources section and do not add inline citations** — sources are tracked separately." Return `{abstract, article}`.
- On a first mention `existing_article` is `""` (synthesize from the extracted description).
- On LLM failure/empty: fall back to keeping the existing article and deriving the abstract from it (never lose content) — mirror today's `(_chat(...) or existing)` guard.

## 2. Node storage: `article` + `summary`-as-abstract

The `Entity` node gains an `article` property; `summary` is repurposed as the short abstract.

- `upsert_entity` writes both `e.article = $article` and `e.summary = $abstract`.
- In `merge_content`, the per-entity flow changes from fetch-`summary`/`merge_summary` to fetch-`article`/`synthesize_article`:
  - New entity: `synthesize_article(client, model, "", entity.description, llm_params)` → store `article`, `summary=abstract`.
  - Existing entity: fetch `e.article` (not `e.summary`), `synthesize_article(existing_article, entity.description)` → store both.
- Entity-resolution candidate queries and the resolver keep using `summary` (now the concise abstract) — no change needed there beyond it being shorter.

## 3. Source enrichment for references

`Source` nodes currently hold `{knowledge_base_id, job_id}`. Add `label` + `date`.

- `write_provenance(session, knowledge_base_id, entity_id, job_id, *, label: str, date: str)` sets `s.label` and `s.date` on the `MERGE (s:Source {knowledge_base_id, job_id})`.
- The worker already has the job; it passes the source label (`job_metadata.get("source")` → a string, or `""`) and date (the job's `created_at` ISO string) into `merge_content`, which forwards them to `write_provenance`. `merge_content` gains keyword-only `source_label: str = ""`, `source_date: str = ""` (defaults keep existing callers/tests valid).

## 4. GraphQL read exposure

Extend the Strawberry `Node` type:
- `article: str | None` — resolves from the node's `article` property (query_node/query_nodes RETURN it alongside the existing fields).
- `references: list[Reference]` — a field resolver that runs a knowledge-base-scoped query `MATCH (e:Entity {id, knowledge_base_id})-[:MENTIONED_IN]->(s:Source) RETURN s.label AS label, s.date AS date ORDER BY s.date` and returns `Reference{label: str, date: str}` objects.

`graph_read` gains `query_references(knowledge_base_id, entity_id)` and adds `article` to `_NODE_RETURN`. Both Bearer and cookie GraphQL routers get this for free (same schema).

## 5. Backfill

A one-off script (run once against prod, and available for dev):
- **Entities:** for every `Entity` with an `article` unset, `SET e.article = e.summary` (the current body becomes the article), then derive a short abstract from it and `SET e.summary = <abstract>`. The abstract derivation is a cheap non-LLM heuristic (first sentence/paragraph, truncated to ~240 chars) to avoid an LLM pass over the whole graph — a good-enough abstract that future ingests refine.
- **Sources:** for every `Source` missing a `label`, look up its `job_id` in Postgres `ingest_jobs.metadata->>'source'` and `created_at`; set `s.label` and `s.date`. Sources whose job is gone or label-less get `label=""` (reference shows date only).

## Error handling

- Synthesis LLM failure ⇒ keep the existing article, derive the abstract from it, continue — never fail a document over article synthesis (consistent with today's `merge_summary` fallback and the relevance/extraction conventions).
- A `Source` with no label ⇒ reference renders with date only (empty label is valid).

## Testing

- **Unit:** `synthesize_article` with a mocked `_chat` returning `{abstract, article}` → asserts both stored; the fallback path (mock returns None) keeps the existing article and a derived abstract.
- **merge_content:** with the LLM chain mocked, a new entity stores `article` (markdown) + a concise `summary`; a second ingest merges into the existing `article` (fetch-article, not summary). Source label+date land on the `Source` node.
- **GraphQL:** `article` and `references` resolve on a `Node`, scoped to the knowledge base (a reference for another KB's source must not appear).
- **Backfill:** entity with only `summary` → gets `article` = old summary + a short `summary`; a `Source` with a matching job → gets label+date.

## Out of scope (explicit)

- The per-entity **page view** UI (sub-project **C**) — B only exposes `article` + `references` over GraphQL.
- **Inline** [n] citations / per-claim attribution — page-level references only.
- **Structured source fields** (title/url/published_at) on the ingest API — citations use the existing freeform `metadata.source` label.
- Periodic full re-synthesis / article de-bloat — incremental merge only for v1.

## Resolved decisions

1. Citations are **page-level References** (derived from `Source` nodes), never inline; the article body stays clean prose.
2. Sources are cited by the **freeform `metadata.source` label** already sent + the ingest date — no `/content` API change.
3. The node splits into a short `summary`/**abstract** (kept concise for resolution/lists/tooltips) and a full **`article`** markdown body; both come from one structured synthesis call.
4. B **exposes** `article` + `references` over GraphQL and stops there; the page rendering is **C**.
5. Backfill derives the abstract with a **non-LLM heuristic** (no costly graph-wide LLM pass); future ingests refine it.
