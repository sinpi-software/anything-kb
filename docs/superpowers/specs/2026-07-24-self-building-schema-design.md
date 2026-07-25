# Self-Building Schema — Design

**Goal:** Stop requiring users to pre-define entity/relationship types. Instead, the user writes one prose statement of what they care about, and the schema (the vocabulary of entity and relationship types) **builds and consolidates itself** from ingested content — killing setup friction while preserving a coherent, queryable graph.

**Context:** This is sub-project **A** of a larger north star — the engine as a *self-building Neo4j wiki* (entities = articles, relationships = links, sources = citations, schema = the category system). Sub-projects **B** (wiki-grade article synthesis) and **C** (per-entity page view) are **out of scope here** and get their own specs. See `knowledge-graph-engine-direction` memory.

**Architecture in one line:** the existing per-org **entity resolution** (merge "Ada Lovelace" across documents) gets a symmetric sibling — **type resolution** (merge "sponsored" / "backed" / "introduced" into one canonical `Sponsors`). Extraction goes from "only these types allowed" to "reuse the vocabulary so far, coin a new type when genuinely new"; a consolidation pass then folds novel types into a canonical, growing vocabulary.

**Tech stack (unchanged):** FastAPI + Strawberry GraphQL, SQLAlchemy/Alembic (Postgres), neo4j driver, OpenRouter, React Router 8 frontend.

## Global Constraints

- The knowledge base's type list (`entity_types` / `relationship_types`) remains the source of truth for the vocabulary; it is now **auto-maintained** by the worker, not just user-edited.
- The stored type on every node/edge is always the **canonical configured name** (already true as of the casing-robust change) — display consistency must never regress.
- A knowledge base with **"discover new types" off** must behave exactly like today (constrain to configured types, drop novel ones) — this is the backward-compatible "guided" mode.
- No new hard dependency (no embedding service in v1 — consolidation uses the LLM already in the loop).
- Existing data is untouched: the current curated types seed the vocabulary; the current relevance prompt seeds the interests.

---

## 1. The interests field (merge relevance + focus)

Today `KnowledgeBaseConfig` has `relevance_prompt` (the document-level admit/reject gate). It now becomes **`interests`** — one prose statement of "what I care about" that does double duty:

- **Gate** (unchanged mechanism): `judge_relevance(interests, content)` in `worker.py` decides whether a document enters at all.
- **Lens** (new): the same text is injected into the extraction prompt to focus *what's worth extracting* from an admitted document — replacing the schema's old focusing role.

**Data model:**
- Rename column `relevance_prompt` → `interests` (Alembic migration; value carried over verbatim — existing prompts already read as "what I care about").
- Add `discover_types: bool` (default **true**; existing KBs set **true** to opt into the new behavior they asked for).
- Extend each type object from `{name, description}` to `{name, description, pinned?: bool, banned?: bool}` (JSONB; absent flags default false). A **banned** entry is a blocklist: the name is a *tombstone*, not an active type.

**API/schema:** `ConfigRequest`/`ConfigResponse` rename `relevance_prompt` → `interests`, `TypeDef` gains optional `pinned`/`banned`, config gains `discover_types`. Breaking shape change; no external clients.

## 2. Open, vocabulary-hinted extraction

`build_extraction_messages` changes from a hard allow-list to a soft, growable one. When `discover_types` is **on**, the system prompt becomes roughly:

> Here is what the user cares about: `{interests}`.
> Here is the vocabulary of types discovered so far (reuse these names exactly when a fact fits one):
> `{rendered entity types with descriptions}` / `{rendered relationship types with descriptions}`
> When you find something genuinely new that matches the user's interests and no existing type fits, coin a concise new type name and use it. Do not force-fit; do not invent types for incidental mentions.

When `discover_types` is **off**, the prompt keeps today's wording ("extract only these types … do not invent new ones") and the pipeline drops anything off-vocabulary — i.e. current behavior.

The structured-output schema (`KnowledgeExtraction`) is **unchanged**: `type` is already a free string, so "open" is purely a prompt change. Novelty is detected and resolved *after* extraction, in §3.

## 3. Type consolidation (the crux)

After extraction, before writing to Neo4j, resolve every extracted type to a canonical vocabulary entry. Mirrors `resolve_entities_batch`.

**Algorithm (per job, entity types and relationship types handled separately):**
1. Collect the distinct extracted type names.
2. **Fast path** — normalize each (`_norm_type`, already exists) and match against the current vocabulary. Exact/normalized hits map straight to the canonical name (today's behavior). Banned normalized names → drop the fact.
3. **Novel candidates** (no normalized match) — if `discover_types` is off, drop them. If on, batch *all* novel candidates from this job into **one** LLM call:
   - Input: the candidate names + the full current vocabulary (names + descriptions; **pinned** ones flagged as preferred/stable) + the interests statement.
   - Output (structured): for each candidate, either `{decision: "existing", canonical: <vocab name>}` (it's a synonym) or `{decision: "new", name: <clean name>, description: <one line>}` (genuinely novel), or `{decision: "drop"}` (incidental / not aligned with interests).
4. Apply the mapping: facts get the canonical name; `"new"` results are appended to the vocabulary (§4); `"drop"` results are discarded.

**Cost:** zero extra LLM calls on jobs whose types all hit the fast path (the common case after warmup). At most **one** extra call per job otherwise, regardless of how many novel candidates.

**Consolidation-quality controls (the main risk):**
- **Pinned** types are presented as authoritative and are never renamed/merged away.
- The prompt is explicit that near-synonyms should merge but genuinely distinct relations (e.g. `Funds` vs `Sponsors`) must stay separate.
- A "recently added / recently merged types" audit list is surfaced in the UI (§5) so bad calls are catchable. (Auto-undo is out of scope for v1.)

## 4. Growing the vocabulary

New canonical types from §3 are appended to `KnowledgeBaseConfig.entity_types` / `relationship_types` with their auto-written description, `pinned=false`, `banned=false`.

- **Concurrency:** the worker re-reads the config row inside its transaction, appends only names whose normalized form is absent, and commits — so concurrent user edits and parallel workers can't create duplicates. Dedup key is `_norm_type(name)`.
- **Banned names** are never re-added (checked against the tombstone set).
- Vocabulary growth is idempotent: re-ingesting the same content adds no new types.

## 5. Config API + UI

`/app/config` becomes the schema's live view and curation surface.

- **Interests**: the relevance-prompt textarea is relabeled "What I care about" and its help text explains it both gates and focuses.
- **Discover toggle**: a switch — "Automatically discover new types" (on by default). Off ⇒ locked/guided schema.
- **Vocabulary**: the existing name+description rows, now auto-growing, each gaining two controls: **pin** (protect from auto-merge) and **ban** (remove and blocklist). Rename and delete already exist. A small "added recently" marker on fresh types.
- **Deferred to a later iteration:** an explicit **merge** action (combine two existing types and repoint existing graph edges) — it needs edge-repointing in Neo4j; not required for v1 since consolidation prevents most duplicates at write time.

Cookie route (`routes_settings.py`) and Bearer route (`routes_config.py`) both updated for the new field names/flags; both continue to sanitize input.

## 6. End-to-end worker flow

`worker.process_job` reads `interests`, the vocabulary, `discover_types`, and the pinned/banned sets, then:
1. `judge_relevance(interests, content)` — unchanged gate.
2. `extract_knowledge(...)` with the open, vocabulary-hinted prompt (§2).
3. `merge_content(...)` — entity resolution (existing) **plus** type consolidation (§3): fast-path match, one batched LLM call for novel candidates, canonicalize, grow vocabulary (§4), write nodes/edges with canonical names.

## Error handling

- Consolidation LLM failure ⇒ retry the consolidation call; on repeated failure, **fall back to the fast path only** — known (already-canonical) types are kept and written, novel candidates are deferred (dropped this run), and the job is marked done rather than losing the whole document. (Confirmed.)
- A candidate that the LLM maps to a non-existent canonical name ⇒ treated as `new`.
- Vocabulary write contention ⇒ resolved by the normalized-dedup append (§4).

## Testing

- **Unit:** `_norm_type` fast-path matching (exists); interests rendering; the open-vs-guided prompt branch; append-dedup by normalized name; banned-name drop.
- **Consolidation:** with the LLM mocked (as `test_knowledge` already mocks `extract_knowledge`), assert synonyms map to canonical, genuinely-new types are appended with descriptions, pinned types are never renamed, `discover_types=off` drops novel candidates.
- **Migration:** `relevance_prompt`→`interests` carries the value; `discover_types` defaults true; flags default false; existing type objects still valid.
- **Round-trip:** config API get/put for interests + flags + toggle.

## Rollout / migration

One Alembic migration: rename `relevance_prompt`→`interests`, add `discover_types` (default true), and (no column change needed for the JSONB type objects — new optional flags are additive within the existing JSONB). The user's existing KB keeps working immediately: its curated vocabulary seeds discovery, its relevance prompt becomes its interests, its graph is untouched.

## Out of scope (explicit)

- Wiki-grade article synthesis (sub-project **B**) and the per-entity page view (sub-project **C**).
- Embedding-based consolidation (LLM-only in v1; embeddings are a later optimization).
- UI **merge** with graph-edge repointing (deferred; consolidation prevents most duplicates at write time).
- Auto-undo of bad merges (v1 gives a visible audit list only).

## Resolved decisions

1. **Consolidation-LLM failure ⇒ fast-path fallback:** keep known types, defer novel candidates, mark the job done. Never fail the whole document over consolidation.
2. **`discover_types` defaults on**, including for the existing KB.
3. **Ban = blocklist future only.** Banning a type prevents it from being (re)added and drops future facts that resolve to it; existing nodes/edges of that type are left untouched.
