# Transform Gates + Fan-out Pipeline — Design

**Status:** approved design
**Date:** 2026-07-23

## Goal

Let a transformation declare a **gate** — a condition on an earlier transform's
output — that must pass for it to run. A failed gate **skips that transform and
halts the pipeline** (nothing at a later position runs). To make gates reference
prior outputs coherently, the pipeline changes from a linear pipe to **fan-out**:
every transform reads the source article, and outputs are addressable by a
per-transform **name**.

## Decisions (locked)

1. **Gate condition:** a prior transform's output (by name).
2. **Input model:** fan-out — every transform reads the source article; outputs are
   recorded by name. (Also fixes today's bug where `knowledge`, at position 2,
   received the `score` output as its input instead of the article.)
3. **Reference by name** (unique per org) — position is reorder-fragile, uuid is
   ugly to configure. There may be more than one `score` transform, so type-based
   references are insufficient.
4. **Storage:** one migration adds two first-class columns to `Transformation`:
   `name` and `gate`.
5. **On gate fail:** skip the transform (record a `SKIPPED` run) **and halt the
   pipeline** — a closed gate stops all later-position transforms. Position order is
   therefore a sequence of checkpoints.
6. **Frontend:** full editor — a `name` input per row and a structured gate editor.

## Pipeline change (fan-out)

`run_transform_pipeline` no longer threads `current_input = output`. It:
1. Loads the source markdown artifact + the org's transforms ordered by `position`.
2. Runs each transform on the **source artifact**, accumulating
   `outputs: dict[name, output_artifact_id]`.
3. Before running a transform with a gate, evaluates it against `outputs`.

A gate can only reference a transform at an **earlier position** (its output must
already exist in `outputs`).

*Side effect:* `score`/`summarize`/`classify` now operate on the article, not each
other's outputs. Cross-transform input-chaining is an explicit non-goal for v1.

## Schema changes (`Transformation`)

One Alembic migration adds:
- `name`: `TEXT NOT NULL`, unique per org — `UniqueConstraint("org_id", "name")`.
- `gate`: `JSONB NULL`.

## Gate config

```json
{ "source": "newsworthiness", "field": "score", "op": ">=", "value": 5 }
```
- `source` — the **name** of the transform whose output to check (must be earlier).
- `field` — a top-level field in that output's JSON.
- `op` — one of `eq | ne | gt | gte | lt | lte | in | contains`.
- `value` — the comparison value.
- MVP: a single condition. (Extensible to an AND-list later.)

Gates realistically reference JSON-output transforms (`score`, `classify`,
`knowledge`); `summarize` emits plain markdown, so a `field` gate on it fails.

## Evaluation + halt

In `position` order, for each transform T:
1. If T has a `gate`: resolve `outputs[gate.source]`. If missing (source didn't run,
   was skipped, or its output isn't JSON / lacks `field`) → **gate fails**.
   Otherwise load the artifact, parse JSON, read `field`, apply `op` vs `value`.
2. **Gate passes (or no gate):** run T on the source artifact; record its output
   under T's `name`; mark the run `COMPLETED`.
3. **Gate fails:** record a `TransformRun` with status `SKIPPED` and an
   `error_message` reason (e.g. `gate: newsworthiness.score >= 5 not met (got 3)`);
   **break the loop** — no later transform runs.
4. A transform that itself errors still marks `FAILED` and breaks (unchanged).

Op semantics: numeric/string compare for `eq/ne/gt/gte/lt/lte`; `in` = value is a
member of the field (field is a list) or the field is in `value` (value is a list);
`contains` = field (list/str) contains `value`. Type-mismatch → gate fails, not error.

## Statuses

Add `TransformRunStatus.SKIPPED = "skipped"`.

## Validation (`validate_transform_config`)

Extended (called from seed and the API path):
- `name` required and non-empty.
- If `gate` present: `source`, `field`, `value` present; `op` in the allowed set;
  `source` names an existing transform in the org at an earlier `position`.
  (Uniqueness of `name` and the earlier-position check need the org's other
  transforms in scope — the caller passes them.)

## Frontend (full editor)

- **Name**: a required text input per row (added to `transformationInputSchema` and
  the editable table).
- **Gate** (optional): a structured editor in the expanded row —
  - `source`: a `<select>` of the org's **other** transforms by name (only those at
    an earlier position are valid; render all, validate the ordering).
  - `field`: text input.
  - `op`: a `<select>` of the op set.
  - `value`: text input (parsed to number/bool/string).
  - A "no gate" empty state; clearing it sends `gate: null`.
- Zod schema (`app/app/schemas/transformation.ts`) gains `name` (required) and an
  optional `gate` object; the resource routes and service carry them through.

## Seed

Give the seeded transforms names (`summary`, `newsworthiness`, `knowledge`) and add
a sample gate: `knowledge` gated on `{"source":"newsworthiness","field":"score","op":">=","value":5}`.

## Testing

- **Unit:** gate evaluation (`op` semantics incl. type-mismatch → fail, missing
  source → fail); `validate_transform_config` (name required, gate source must be
  earlier); the shared Zod schema (name required, gate optional/valid).
- **Integration (ingestion):** a fan-out pipeline run where a failing gate halts
  (later transform records no run), and a passing gate runs it; outputs addressable
  by name.
- **Frontend:** editor renders name + gate controls; snapshot; a create/edit round
  trip carrying name + gate.

## Out of scope (v1)

- Multiple gate conditions / boolean expressions (single condition only).
- Cross-transform input chaining (all transforms read the source).
- Gating on non-JSON outputs by content (only JSON-field gates).
- Nested-field paths (top-level `field` only).
