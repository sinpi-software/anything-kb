# Transform Gates + Fan-out Pipeline — Design

**Status:** implemented
**Date:** 2026-07-23

> **Revision (2026-07-23, during implementation):** gates changed from *incoming*
> (a check at the start of a step on an *earlier* step's output, referenced by
> name) to *outgoing* (a check at the end of a step on that step's *own* output).
> The incoming model let a reorder strand a step whose gate source ended up at a
> later position — the step could then never run. Outgoing gates depend only on
> the step they belong to, which always runs before its own gate, so reordering
> can never create a stuck state. This also removed the gate `source` reference
> entirely. The sections below describe the implemented (outgoing) design.

## Goal

Let a transformation declare a **gate** — a condition on **its own output** — that
must pass for the pipeline to continue past it. A failed gate leaves that step
`COMPLETED` (it ran and produced output) and **halts the later steps** (nothing at
a later position runs). The pipeline is **fan-out**: every transform reads the
source article, so a step's output is a self-contained thing to gate on.

## Decisions (locked)

1. **Gate condition:** a step's own output (the artifact it just produced).
2. **Input model:** fan-out — every transform reads the source article. (Also fixes
   the bug where `knowledge`, at position 2, received the `score` output as its
   input instead of the article.)
3. **Names:** each transform has a unique-per-org `name`, used as a human label in
   the editor (e.g. to tell two `score` steps apart). Gates no longer reference
   other steps, so names are not load-bearing for gating — just labels.
4. **Storage:** one migration adds two first-class columns to `Transformation`:
   `name` and `gate`.
5. **On gate fail:** the gate-owning step stays `COMPLETED`; the pipeline logs
   "gate closed, halting" and **breaks** — no later-position transform runs. Later
   steps produce no run records. Position order is a sequence of checkpoints.
6. **Frontend:** full editor — a `name` input per row and a structured gate editor.

## Pipeline change (fan-out)

`run_transform_pipeline` no longer threads `current_input = output`. It:
1. Loads the source markdown artifact + the org's transforms ordered by `position`.
2. Runs each transform on the **source artifact**, marking it `COMPLETED`.
3. After a transform with a gate completes, evaluates the gate against that
   transform's **own output**; a closed gate halts the later steps.

*Side effect:* `score`/`summarize`/`classify` now operate on the article, not each
other's outputs. Cross-transform input-chaining is an explicit non-goal for v1.

## Schema changes (`Transformation`)

One Alembic migration adds:
- `name`: `TEXT NOT NULL`, unique per org — `UniqueConstraint("org_id", "name")`.
- `gate`: `JSONB NULL`.

## Gate config

```json
{ "field": "score", "op": ">=", "value": 5 }
```
- `field` — a top-level field in this step's own output JSON.
- `op` — one of `eq | ne | gt | gte | lt | lte | in | contains` (the string keys;
  the example's `">="` is `gte`).
- `value` — the comparison value.
- MVP: a single condition. (Extensible to an AND-list later.)

Gates realistically apply to JSON-output transforms (`score`, `classify`,
`knowledge`); `summarize` emits plain markdown, so a `field` gate on it fails.

## Evaluation + halt

In `position` order, for each transform T:
1. Run T on the source artifact; record its output; mark the run `COMPLETED`.
   (If T itself errors, mark `FAILED` and break — unchanged.)
2. **If T has a gate:** load T's output, parse JSON, read `field`, apply `op` vs
   `value`. If the output is missing / not JSON / lacks `field`, the gate **fails**.
3. **Gate passes (or no gate):** continue to the next transform.
4. **Gate fails:** log the reason (e.g. `gate not met: score gte 5 (got 3)`) and
   **break** — no later transform runs. T's own run stays `COMPLETED`.

Op semantics: numeric/string compare for `eq/ne/gt/gte/lt/lte`; `in` = the field
value is a member of `value` (value is a list); `contains` = the field (list/str)
contains `value`. Type-mismatch → gate fails, not error.

## Statuses

`TransformRunStatus` is `pending | running | completed | failed`. (An earlier draft
added `skipped` for gate-blocked steps; with outgoing gates the blocked steps
simply don't run and produce no record, so `skipped` was removed.)

## Validation (`validate_transform_config`)

Extended (called from seed and tests):
- `name` required and non-empty.
- If `gate` present: `field`, `op`, `value` present; `op` in the allowed set.

On the API write path (TypeScript), a duplicate `name` (the `org_id, name` unique
constraint) is translated to a friendly `422` inline error rather than a generic
`500`.

## Frontend (full editor)

- **Name**: a required text input per row (added to `transformationInputSchema` and
  the editable table).
- **Gate** (optional): a structured editor in the expanded row —
  - a toggle ("Halt the chain unless this step's output meets a condition");
  - `field`: text input;
  - `op`: a `<select>` of the op set;
  - `value`: text input (parsed to number/string).
  - Clearing the toggle sends `gate: null`.
- Zod schema (`app/app/schemas/transformation.ts`) gains `name` (required) and an
  optional `gate` object `{ field, op, value }`; the resource routes and service
  carry them through.

## Seed

Give the seeded transforms names (`summary`, `newsworthiness`, `knowledge`). The
`newsworthiness` (score) step carries the sample gate
`{"field":"score","op":"gte","value":5}` — when the score is below 5 the chain
halts, so `knowledge` (a later step) does not run.

## Testing

- **Unit:** gate evaluation (`op` semantics incl. type-mismatch → fail, no output →
  fail); `validate_transform_config` (name required, gate shape); the shared Zod
  schema (name required, gate optional/valid).
- **Integration (ingestion):** a fan-out pipeline run where a step's failing gate
  halts (the later step records no run), and a passing gate lets it continue.
- **Integration (app):** the service persists an outgoing gate and rejects a
  duplicate name.
- **Frontend:** editor renders name + gate controls; snapshot; a create/edit round
  trip carrying name + gate.

## Out of scope (v1)

- Multiple gate conditions / boolean expressions (single condition only).
- Cross-transform input chaining (all transforms read the source).
- Gating on non-JSON outputs by content (only JSON-field gates).
- Nested-field paths (top-level `field` only).
