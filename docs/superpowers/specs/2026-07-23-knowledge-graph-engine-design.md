# Knowledge Graph Engine — Design

**Status:** approved design (pending spec review)
**Date:** 2026-07-23

> **Supersedes** the transform-pipeline and transform-gates designs. This is a
> deliberate, drastic scope reduction: the product is no longer a configurable
> multi-step transform pipeline over RSS. It is a single-purpose engine — content
> in, a relevance-filtered typed knowledge graph out, read over GraphQL. The
> newsletter/OSINT products become *external consumers* of the GraphQL API, built
> separately and later.

## Goal

A multi-tenant engine that does exactly four things:

1. Accept content via an authenticated `POST` (async).
2. Judge each item's relevance against a per-org **relevance prompt**.
3. If relevant, extract entities + relationships — constrained to the org's
   configured **entity types** and **relationship types** — and merge them into
   that org's Neo4j graph.
4. Expose the graph read-only over an authenticated **GraphQL** API.

Nothing else. No transform chains, no gates, no RSS, no scoring/summarize/classify.

## Architecture

```
POST /content ──▶ ingest_jobs(pending) ──▶ 202 {job_id}
                          │
                    worker loop (separate process, claims jobs)
                          │
              ┌───────────┴───────────┐
        relevant?  ── no ──▶ job: skipped (+reason)
              │ yes
        extract (types from org config) ──▶ merge into Neo4j (org-scoped)
              │
        job: done
                          
GraphQL /graphql (org-scoped) ──▶ reads Neo4j
```

- **Async ingest, Postgres-backed queue.** `POST /content` inserts an `ingest_jobs`
  row (`status=pending`) and returns `202 {job_id}`. A separate **worker** process
  claims pending rows with `SELECT ... FOR UPDATE SKIP LOCKED`, processes them, and
  updates status. Durable across restarts; no orchestration engine.
- **Postgres** holds orgs, per-org config, API keys, and the job queue. **Neo4j**
  holds the graph. Every node/edge/query is scoped by `org_id` (unchanged isolation
  model from the knowledge work).
- **Tech:** FastAPI + uvicorn (HTTP), Strawberry (GraphQL), SQLAlchemy (Postgres),
  the neo4j driver, OpenRouter (LLM). **Prefect is removed.**

## Data model

### Postgres

- `orgs` — id, name (kept, trimmed of anything transform-related).
- `org_configs` (1:1 with org) — `org_id` (FK), `relevance_prompt TEXT`,
  `entity_types TEXT[]`, `relationship_types TEXT[]`, `updated_at`.
- `api_keys` — id, `org_id` (FK), `key_hash`, `created_at`, `revoked_at NULL`. The
  key is shown once on creation; only its hash is stored.
- `ingest_jobs` — id, `org_id` (FK), `content TEXT`, `metadata JSONB` (source, url,
  author, published_at, external_id — all optional), `status` (`pending | processing
  | done | skipped | failed`), `relevance_reason TEXT NULL`, `error TEXT NULL`,
  `attempts INT`, `created_at`, `processed_at NULL`.

### Neo4j (unchanged shape from the knowledge feature)

- Entity nodes: `org_id`, `type` (one of the org's configured entity types),
  `name`, `name_normalized`, `summary`, provenance.
- `RELATED` edges: `type` (one of the org's configured relationship types),
  `org_id`, provenance.
- **Provenance** now references the originating `ingest_jobs.id` + its source
  metadata, instead of an Artifact id.

## API surface (all authenticated by API key → org)

Auth: `Authorization: Bearer <api_key>`; the key hashes to an `api_keys` row that
resolves the `org_id`. Every request is scoped to that org. No cross-org access.

- **`POST /content`** — body `{ text: string, metadata?: {...} }`. Validates, inserts
  an `ingest_jobs` row (`pending`), returns `202 { job_id }`.
- **`GET /content/{job_id}`** — returns the job's status + relevance_reason/error, so
  a client can poll what happened to its submission. (Necessary for an async API to
  be usable.)
- **`PUT /config`** — body `{ relevance_prompt, entity_types[], relationship_types[] }`.
  Upserts the org's `org_configs` row. *(Not in the original 4-point list, but the
  engine cannot function without a way to set config — included as a necessary
  addition.)*
- **`POST /graphql`** — the generic read schema below, org-scoped.

## Relevance filter

One LLM call per job. Input: the job `content` + the org's `relevance_prompt`.
Structured output `{ relevant: boolean, reason: string }`. Not relevant → the job is
marked `skipped` (with the reason) and never touches the graph. Binary, not graded —
no numeric threshold, no ops. This *is* the old "newsworthiness" idea, reduced to a
single prompt.

## Extraction + merge

Reuse the existing knowledge extraction/resolution/compound-merge/graph-write logic,
with two changes:
- Entity extraction is constrained to the org's `entity_types` (already the pattern).
- Relationship extraction is constrained to the org's `relationship_types` (new — the
  current code lets the LLM infer open relationship labels; now they must come from
  the configured set).
- Provenance is the `ingest_jobs.id` + source metadata.

## GraphQL read schema (generic, one schema for all orgs)

```graphql
type Query {
  nodes(type: String, search: String, limit: Int = 50): [Node!]!
  node(id: ID!): Node
}
type Node {
  id: ID!
  type: String!
  name: String!
  summary: String
  edges(type: String): [Edge!]!
}
type Edge { type: String!, target: Node! }
```

Entity/relationship *types* are string data, not GraphQL types — so a config change
never requires a schema regeneration. `search` uses the existing Neo4j full-text
index. Every resolver filters by the caller's `org_id`.

## Worker

A standalone loop (its own process/entrypoint):
1. Claim a batch of `pending` jobs (`FOR UPDATE SKIP LOCKED`), mark `processing`.
2. Load the org's config. Run the relevance filter.
   - Not relevant → `skipped` (+reason).
   - Relevant → extract (constrained to configured types) → merge into Neo4j → `done`.
3. On exception → `failed` (+error), increment `attempts`. (Simple bounded retry; a
   job over the attempt cap stays `failed`.)
LLM concurrency stays bounded (a small worker pool / semaphore), replacing the
Prefect concurrency limits.

## Demolition — what gets deleted

**This is the sign-off list.** Under the reduced scope, the following leave the
codebase:

- **Ingestion service (`ingestion/`):**
  - `transformations.py`, `gates.py`, `rss_feeds.py`, `test_gates.py`,
    `test_transformations.py`.
  - Models: `Transformation`, `TransformRun`, `Artifact`, `RssFeed`, `RssFeedItem`
    (and their tables, via a migration).
  - All Prefect usage: `serve`, deployments, event triggers, concurrency pools;
    the `prefect` dependency.
  - `main.py` becomes: start the API (uvicorn). `worker.py` is the new worker
    entrypoint.
  - `seed.py` rewritten to seed an org + config + an API key.
  - `config.py` trimmed to what remains.
- **Frontend (`app/`):** the `/desk/:org_id/transformations` editor and its
  schema/service/routes/tests have no role in an API-only product. **Open decision
  (below):** delete the whole `app/` React frontend, or keep it dormant for a future
  config/graph-viewer UI.

**Kept and adapted:** `knowledge.py` (now the core, not one transform of four),
`neo4j_client.py`, `db.py`, the Neo4j graph shape, org-scoping, OpenRouter usage.

## Testing

- **Relevance filter:** relevant/irrelevant decisions (mocked LLM); malformed LLM
  output fails safe.
- **Worker:** job lifecycle — `pending → done`, `pending → skipped` (irrelevant),
  `pending → failed` (extraction error); `SKIP LOCKED` claim doesn't double-process.
- **Extraction:** entities/relationships constrained to the org's configured types;
  provenance references the job.
- **GraphQL:** `nodes`/`node`/`edges` resolvers over a seeded graph; every query is
  org-scoped (an org never sees another's nodes).
- **Auth:** a valid key resolves the right org; a missing/invalid/revoked key is
  rejected; all endpoints enforce org scope.
- **Ingest endpoint:** `POST /content` returns `202` + a job row; `GET /content/{id}`
  reflects status.

## Open decisions for spec review

1. **Frontend fate:** delete `app/` entirely, or keep it dormant for a later
   config/graph-viewer UI? (No UI is in the reduced scope.)
2. **Dropping tables** (`artifacts`, `rss_*`, `transformations`, `transform_runs`) is
   destructive — fine in dev, but confirm there's nothing to preserve.
3. **Provenance detail:** job id + source metadata on nodes/edges — enough, or do you
   want the raw submitted content retained long-term (it lives in `ingest_jobs`)?

## Out of scope (v1)

- The consuming products (newsletter/newsroom, OSINT) — separate, later, built
  against the GraphQL API.
- Non-POST source adapters (RSS/email/Discord/crawlers) — external producers that
  POST to `/content`, not part of this service.
- Typed/generated per-org GraphQL schema (generic schema chosen).
- Graded relevance, multi-condition rules, transform chains, gates — deliberately gone.
- A config/graph UI (unless the frontend-fate decision keeps `app/`).
