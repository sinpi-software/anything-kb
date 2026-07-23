# Knowledge Transform — Design

**Status:** approved design
**Date:** 2026-07-23

## Goal

A new `KNOWLEDGE` transform type for the ingestion pipeline. Given a markdown
artifact (an ingested article) and an org's configuration (a prompt + a set of
expected entity types), it builds and incrementally maintains a per-org
**knowledge graph in Neo4j** — an "LLM wiki" (per
<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>) stored in a
graph instead of a filesystem. Knowledge **compounds**: entity "pages" (node
summaries) are revised as new sources arrive.

## Core decisions (locked)

1. **Graph model:** typed entity nodes **and** relationships between them (a real
   knowledge graph).
2. **Per-org isolation:** **property-scoped** — every node and relationship carries
   `org_id`; every query is filtered by it. One shared Neo4j (Community edition,
   `neo4j:5`, already in the compose stack). Isolation is logical, enforced in
   query code, not physical. (Database-per-org would need Enterprise.)
3. **Merge semantics:** **compound / LLM-merge** — a re-seen entity's summary is
   revised by the LLM from (existing summary + new context); knowledge accumulates.
4. **Entity resolution:** **LLM-assisted** — candidate lookup in the org's graph,
   then one LLM "same entity or new?" judgment before merging.
5. **Relationship types:** **open / LLM-inferred** — the LLM proposes edge-type
   labels freely from context.

## Sub-decisions (defaults chosen)

- **Node labels:** a single `:Entity` label with a `type` property (not dynamic
  `:Person`/`:Place` labels). Simpler, no APOC, queryable by `type`.
- **Relationship storage:** generic `(:Entity)-[:RELATED { type, ... }]->(:Entity)`,
  the inferred label held in the `type` property (open vocabulary without APOC).
  *Future option:* add the APOC plugin for native dynamic-typed edges.
- **Resolution candidates:** normalized-name + Neo4j full-text match (no embeddings
  for MVP). *Future option:* a vector index for embedding-similarity candidates.

## Config (reuses the `Transformation` row)

```
type   = "knowledge"
model  = "<llm model id>"
prompt = extraction guidance (the org's schema — what to look for, how to describe)
params = { "entity_types": ["Person","Place","Thing","Idea","Topic","Story","Author"] }
```

`validate_transform_config` is extended: a knowledge transform requires a model, a
prompt, and a non-empty `entity_types` list in `params`.

## Neo4j graph schema (property-scoped)

- **Entity node:** `:Entity { id, org_id, type, name, name_normalized, summary,
  aliases: [string], created_at, updated_at }`. `id` is a UUID assigned on create;
  resolution decides whether a mention maps to an existing node or a new one.
- **Source node:** `:Source { org_id, artifact_id }` — one per ingested markdown
  artifact.
- **Provenance edge:** `(:Entity)-[:MENTIONED_IN]->(:Source)`.
- **Semantic edge:** `(:Entity)-[:RELATED { type, org_id, source_artifact_id }]->(:Entity)`,
  where `type` is the LLM-inferred label.
- **Constraints / indexes (idempotent bootstrap):** unique constraint on
  `(:Entity org_id, id)`; a full-text index over `Entity.name` and `Entity.aliases`
  used for candidate lookup. All read/write queries include an `org_id` predicate.

## Flow (per markdown artifact)

The transform resolves `org_id` from the input artifact (artifacts already carry
`org_id`), loads the transformation config, then:

1. **Extract** (1 LLM call). Structured output validated by a pydantic
   `LLMKnowledgeExtraction`:
   ```
   { "entities": [ { "name", "type", "description", "aliases"? } ],
     "relationships": [ { "source_name", "target_name", "type" } ] }
   ```
   Constrained to the configured `entity_types`. Uses the existing OpenRouter client
   under the `llm` global concurrency limit.
2. **Resolve** each extracted entity against the org's graph:
   - Candidate lookup scoped by `org_id` (+ `type`): normalized-name equality and
     full-text match on name/aliases → top-K candidates.
   - If candidates exist, one LLM judgment ("which candidate is the same entity, or
     none?"). Batch judgments where practical.
   - Result: a matched existing node, or a new node (UUID assigned).
3. **Compound-merge summaries:**
   - New node → `summary` = extracted description.
   - Matched node → LLM revises `summary` from (existing summary + new description);
     update `summary`, `updated_at`, and union `aliases`. Skip the merge call when
     the extraction adds nothing material.
4. **Relationships + provenance:** resolve both endpoints (from step 2), then MERGE
   `:RELATED { type }` edges tagged with `org_id` + `source_artifact_id`; MERGE the
   `:Source` node and `(:Entity)-[:MENTIONED_IN]->(:Source)` edges.
5. **Output artifact:** JSON `{ entities_created, entities_merged,
   relationships_created, source_artifact_id }` — observable and chainable, produced
   via the same `to_model()` pattern as the other transform outputs.

All graph writes for one article are idempotent (MERGE-based), so a retried run does
not duplicate nodes or edges.

## Plumbing

- `TransformationType.KNOWLEDGE = "knowledge"`.
- New `@task llm_knowledge_transform(artifact_id, transformation_id) -> str`,
  registered in `DISPATCH`. `run_transform_pipeline` fold logic is unchanged (it
  dispatches by `type`).
- **Neo4j client:** add the `neo4j` Python driver to ingestion deps. `neo4j_client.py`
  exposes a cached driver built from `INGESTION_NEO4J_URI` / `_USER` / `_PASSWORD`
  and a session helper. Ingestion runs on the host, so `bolt://localhost:7687` is
  correct. An idempotent schema bootstrap (constraints + full-text index) runs at
  startup, alongside `ensure_concurrency_limits`.

## Tradeoff (accepted)

The chosen quality options (LLM-assisted resolution + compound-merge) mean several
LLM calls per article: 1 extraction + N resolution judgments + M summary merges. All
run under the existing `llm` concurrency limit. Mitigations in scope: batch
resolution judgments; skip summary merges that add nothing. This cost is inherent to
a compounding wiki and is the deliberate trade for quality.

## Error handling

Each `TransformRun` records status. An LLM or Neo4j failure marks the run `FAILED`
with the error message; idempotent MERGE writes make retries safe.

## Testing

- **Unit (pure functions):** extraction-schema parsing, resolution-prompt building,
  Cypher query builders, `validate_transform_config` for knowledge.
- **Integration (docker Neo4j):** ingest an article → nodes/edges created;
  re-ingest → no duplicates, summary merged; relationships formed; and **two orgs do
  not collide** (org isolation).

## Out of scope (explicit)

- Enterprise database-per-org isolation.
- Embedding/vector-index entity resolution (name/full-text only for now).
- APOC / native dynamic-typed relationships (generic `:RELATED{type}` for now).
- A UI for browsing the graph (Neo4j Browser at :7474 suffices for now).
- Deleting/GC of stale entities.
