# Transform Gates + Fan-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-transform `name` + optional `gate` (a condition on a named prior transform's output). The pipeline becomes fan-out (every transform reads the source article); a failed gate skips that transform (records `SKIPPED`) and halts the pipeline.

**Architecture:** Two new `Transformation` columns (`name`, `gate`) via one migration; a pure `evaluate_gate` helper; `run_transform_pipeline` rewritten from linear-pipe to fan-out + gate checkpoints; validation, seed, and the full React editor (name input + structured gate editor) updated.

**Tech Stack:** Python 3 / SQLAlchemy / Alembic / Prefect / pydantic / pytest / ruff / mypy (ingestion); React Router 8 / TypeScript / Zod / Drizzle / Vitest (app).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-07-23-transform-gates-design.md` — the source of truth.
- **Fan-out:** every transform runs on the SOURCE markdown artifact (not chained outputs). Outputs recorded in `outputs: dict[name, output_artifact_id]`.
- **Gate:** `{"source": "<name>", "field": "<json field>", "op": "eq|ne|gt|gte|lt|lte|in|contains", "value": <any>}`. One condition. Missing/failed/skipped source, non-JSON output, missing field, or type-mismatch → gate **fails** (never raises).
- **On gate fail:** record a `TransformRun` with status `SKIPPED` + a reason `error_message`, then **break** the loop (halt). A transform that itself errors still `FAILED`s and breaks.
- **`name`:** `TEXT NOT NULL`, unique per org (`UniqueConstraint("org_id","name")`). `gate`: `JSONB NULL`.
- ingestion: ruff + mypy strict + pytest green, `uv run`. app: ruff/typecheck + vitest green, Node via `nvm use` in `app/` (22.23.1). RR8 source lives under `app/app/`.
- Alembic owns the schema; Python migrates, then `drizzle-kit introspect` regenerates `app/app/db/schema.ts` (Node never migrates).
- Postgres + Neo4j are running in Docker.

## File Structure

- `ingestion/models.py` — `name`+`gate` on `Transformation`, `SKIPPED` status (modify)
- `ingestion/alembic/versions/<new>.py` — migration (create via autogenerate + hand-edit)
- `ingestion/gates.py` — `evaluate_gate`, `GATE_OPS` (create)
- `ingestion/transformations.py` — fan-out `run_transform_pipeline`, gate integration, `validate_transform_config` (modify)
- `ingestion/seed.py` — names + sample gate (modify)
- `ingestion/test_gates.py` — gate unit tests (create); `ingestion/test_transformations.py` — validate tests (modify)
- `app/app/db/schema.ts` — re-introspected (regenerate)
- `app/app/schemas/transformation.ts` — `name`+`gate` in Zod (modify) + `transformation.test.ts`
- `app/app/services/transformations.server.ts` — carry name+gate (modify)
- `app/app/routes/api.transformations.ts` / `.$id.ts` — carry name+gate (modify)
- `app/app/routes/desk.transformations.tsx` — Name input + gate editor (modify) + snapshot

---

## Task 1: Schema — name + gate columns, SKIPPED status, migration

**Files:**
- Modify: `ingestion/models.py`
- Create: `ingestion/alembic/versions/<new>.py`
- Regenerate: `app/app/db/schema.ts` (drizzle introspect)
- Test: `ingestion/test_gates.py` (a schema smoke test)

**Interfaces:**
- Produces: `Transformation.name: str`, `Transformation.gate: dict | None`, `TransformRunStatus.SKIPPED`. Drizzle `transformations` gains `name`, `gate`.

- [ ] **Step 1: Add columns + status to models.py**

In `TransformRunStatus`:
```python
    FAILED = "failed"
    SKIPPED = "skipped"
```
In `Transformation` (add `name` + `gate`, extend `__table_args__` with the unique constraint):
```python
    __table_args__ = (
        UniqueConstraint("org_id", "position", deferrable=True, initially="DEFERRED"),
        UniqueConstraint("org_id", "name"),
    )
    ...
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    gate: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
```
(Place `name`/`gate` after `params`.)

- [ ] **Step 2: Autogenerate the migration**

Run: `cd ingestion && uv run alembic revision --autogenerate -m "transform name and gate"`
Open the generated file. Autogenerate handles adding the columns and the unique constraint but **`name` is NOT NULL on a table with existing rows** — it will fail on apply without a backfill. Edit `upgrade()` to add `name` nullable, backfill, then enforce NOT NULL, in this order:
```python
def upgrade() -> None:
    op.add_column("transformations", sa.Column("name", sa.TEXT(), nullable=True))
    op.add_column("transformations", sa.Column("gate", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # backfill a unique-per-org name for existing rows
    op.execute("UPDATE transformations SET name = type || '-' || position WHERE name IS NULL")
    op.alter_column("transformations", "name", nullable=False)
    op.create_unique_constraint("transformations_org_id_name_key", "transformations", ["org_id", "name"])


def downgrade() -> None:
    op.drop_constraint("transformations_org_id_name_key", "transformations", type_="unique")
    op.drop_column("transformations", "gate")
    op.drop_column("transformations", "name")
```
Ensure `from sqlalchemy.dialects import postgresql` is imported in the migration.

- [ ] **Step 3: Apply the migration**

Run: `cd ingestion && uv run alembic upgrade head`
Verify:
```bash
docker exec -i $(docker compose -f ../docker-compose.yml ps -q postgres) psql -U ingestion -d ingestion -tAc "\d transformations" | grep -E "name|gate"
```
Expected: `name text not null`, `gate jsonb`.

- [ ] **Step 4: Schema smoke test**

Create `ingestion/test_gates.py`:
```python
from sqlalchemy import inspect

from db import get_postgres_session
from models import Transformation, TransformRunStatus


def test_skipped_status_exists() -> None:
    assert TransformRunStatus.SKIPPED.value == "skipped"


def test_transformation_has_name_and_gate_columns() -> None:
    with get_postgres_session() as session:
        cols = {c["name"] for c in inspect(session.bind).get_columns("transformations")}
    assert {"name", "gate"} <= cols
```

- [ ] **Step 5: Re-introspect Drizzle**

Run: `cd app && nvm use && npx drizzle-kit introspect`
Confirm `app/app/db/schema.ts`'s `transformations` table now has `name` and `gate`.

- [ ] **Step 6: Lint, type, test, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy . && uv run pytest test_gates.py -q
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/models.py ingestion/alembic/versions ingestion/test_gates.py app/app/db/schema.ts app/app/db/relations.ts
git commit -m "feat(ingestion): transformation name + gate columns, SKIPPED status"
```

---

## Task 2: Gate evaluation (pure)

**Files:**
- Create: `ingestion/gates.py`
- Test: `ingestion/test_gates.py` (append)

**Interfaces:**
- Produces:
  - `GATE_OPS: dict[str, Callable[[Any, Any], bool]]` (eq, ne, gt, gte, lt, lte, in, contains)
  - `evaluate_gate(gate: dict[str, Any], source_data: str | None) -> tuple[bool, str]` — `(passed, reason)`. `source_data` is the raw `data` string of the gate's source output artifact, or `None` if the source didn't run. Never raises.

- [ ] **Step 1: Write failing tests**

Append to `ingestion/test_gates.py`:
```python
from gates import evaluate_gate  # noqa: E402


def _gate(op: str, value: object) -> dict[str, object]:
    return {"source": "sc", "field": "score", "op": op, "value": value}


def test_gate_passes_numeric() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), '{"score": 7}')
    assert ok is True


def test_gate_fails_numeric() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), '{"score": 3}')
    assert ok is False
    assert "score" in reason


def test_gate_fails_when_source_missing() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), None)
    assert ok is False and "no output" in reason


def test_gate_fails_on_non_json() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), "not json at all")
    assert ok is False


def test_gate_fails_on_missing_field() -> None:
    ok, _ = evaluate_gate(_gate("gte", 5), '{"other": 1}')
    assert ok is False


def test_gate_type_mismatch_fails_not_raises() -> None:
    ok, reason = evaluate_gate(_gate("gte", 5), '{"score": "high"}')
    assert ok is False and "mismatch" in reason


def test_gate_contains_and_in() -> None:
    assert evaluate_gate({"source": "c", "field": "categories", "op": "contains", "value": "tech"},
                         '{"categories": ["tech", "science"]}')[0] is True
    assert evaluate_gate({"source": "c", "field": "cat", "op": "in", "value": ["a", "b"]},
                         '{"cat": "a"}')[0] is True
```

- [ ] **Step 2: Run (RED)**

Run: `cd ingestion && uv run pytest test_gates.py -k gate -v` — fails to import `gates`.

- [ ] **Step 3: Implement `gates.py`**

```python
import json
from collections.abc import Callable
from typing import Any

GATE_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: bool(a == b),
    "ne": lambda a, b: bool(a != b),
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "in": lambda a, b: a in b,  # field value is a member of `value`
    "contains": lambda a, b: b in a,  # field value (list/str) contains `value`
}


def evaluate_gate(gate: dict[str, Any], source_data: str | None) -> tuple[bool, str]:
    """(passed, reason). Any problem — no source output, non-JSON, missing field,
    unknown op, or a type mismatch — fails the gate rather than raising."""
    source, field, op, value = gate.get("source"), gate.get("field"), gate.get("op"), gate.get("value")
    label = f"{source}.{field} {op} {value!r}"
    if source_data is None:
        return False, f"gate: {source} produced no output"
    try:
        parsed = json.loads(source_data)
    except (ValueError, TypeError):
        return False, f"gate: {source} output is not JSON"
    if not isinstance(parsed, dict) or field not in parsed:
        return False, f"gate: {source}.{field} missing"
    fn = GATE_OPS.get(str(op))
    if fn is None:
        return False, f"gate: unknown op {op!r}"
    try:
        passed = bool(fn(parsed[field], value))
    except TypeError:
        return False, f"gate: {label} type mismatch"
    return passed, (f"gate passed: {label}" if passed else f"gate not met: {label} (got {parsed[field]!r})")
```

- [ ] **Step 4: Run (GREEN)**

Run: `cd ingestion && uv run pytest test_gates.py -k gate -v` — all pass.

- [ ] **Step 5: Lint, type, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/gates.py ingestion/test_gates.py
git commit -m "feat(ingestion): pure gate evaluation"
```

---

## Task 3: Fan-out pipeline + gate integration + validation

**Files:**
- Modify: `ingestion/transformations.py`
- Test: `ingestion/test_gates.py` (integration), `ingestion/test_transformations.py` (validate)

**Interfaces:**
- Consumes: `gates.evaluate_gate`; `models.Transformation` (now with `name`/`gate`), `TransformRunStatus.SKIPPED`.
- Produces: `run_transform_pipeline` runs each transform on the source artifact, records `outputs[name]`, evaluates gates, halts+`SKIPPED` on fail. `validate_transform_config(transform_type, model, prompt, params, name, gate)` — `name` required; `gate` shape validated if present.

- [ ] **Step 1: Rewrite `run_transform_pipeline` to fan-out + gates**

Replace the loop body. Key changes: load `name`/`gate` per transform; keep `source_id = input_artifact_id`; maintain `outputs: dict[str, str]`; evaluate the gate before running; on gate fail record `SKIPPED` and `break`; run the handler on `source_id` (not chained); record `outputs[name] = output_artifact_id`. Full replacement:
```python
    with get_postgres_session() as session:
        artifact = session.get(Artifact, input_artifact_id)
        if artifact is None:
            logger.warning("Missing artifact %s; nothing to transform", input_artifact_id)
            return
        org_id = artifact.org_id
        if org_id is None:
            logger.warning("Artifact %s has no org; skipping transforms", input_artifact_id)
            return
        transforms = session.query(Transformation).filter_by(org_id=org_id).order_by(Transformation.position).all()
        pipeline = [(t.id, t.type, t.name, t.gate) for t in transforms]

    if not pipeline:
        logger.info("No transforms configured for org %s", org_id)
        return

    outputs: dict[str, str] = {}  # transform name -> its output artifact id
    for transformation_id, transform_type, name, gate in pipeline:
        handler = DISPATCH.get(transform_type)
        if handler is None:
            logger.warning("No handler for transform type %r; skipping", transform_type)
            continue

        if gate:
            source_output_id = outputs.get(gate.get("source"))
            source_data = None
            if source_output_id is not None:
                with get_postgres_session() as session:
                    src = session.get(Artifact, source_output_id)
                    source_data = src.data if src else None
            passed, reason = evaluate_gate(gate, source_data)
            if not passed:
                with get_postgres_session() as session:
                    run = TransformRun(
                        transformation_id=transformation_id,
                        input_artifact_id=input_artifact_id,
                        status=TransformRunStatus.SKIPPED.value,
                        error_message=reason,
                    )
                    session.add(run)
                    session.commit()
                logger.info("Gate closed for %s (%s); halting pipeline", name, reason)
                break

        with get_postgres_session() as session:
            run = TransformRun(
                transformation_id=transformation_id,
                input_artifact_id=input_artifact_id,
                status=TransformRunStatus.RUNNING.value,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            session.commit()

        try:
            output_artifact_id = handler(input_artifact_id, transformation_id)
        except Exception as exc:
            _mark_run(run_id, TransformRunStatus.FAILED, error_message=str(exc))
            logger.warning("Transform %s failed on artifact %s: %s", transformation_id, input_artifact_id, exc)
            break

        _mark_run(run_id, TransformRunStatus.COMPLETED, output_artifact_id=output_artifact_id)
        outputs[name] = output_artifact_id
        logger.info("Transform %s (%s) produced artifact %s", transformation_id, name, output_artifact_id)
```
Add `from gates import evaluate_gate` to the imports. Every handler now receives `input_artifact_id` (the source) — this is the intended fan-out fix.

- [ ] **Step 2: Extend `validate_transform_config`**

Change the signature and add checks (keep existing type/model/prompt/params checks):
```python
def validate_transform_config(
    transform_type: str,
    model: str | None,
    prompt: str,
    params: dict[str, Any] | None,
    name: str,
    gate: dict[str, Any] | None = None,
) -> None:
    ...existing checks...
    if not name or not name.strip():
        raise ValueError("transform requires a name")
    if gate is not None:
        missing = [k for k in ("source", "field", "op", "value") if k not in gate]
        if missing:
            raise ValueError(f"gate missing keys: {missing}")
        if gate["op"] not in GATE_OPS:
            raise ValueError(f"gate has unknown op {gate['op']!r}")
```
Add `from gates import GATE_OPS`.

- [ ] **Step 3: Update validate tests + add integration test**

In `ingestion/test_transformations.py`, update existing `validate_transform_config` calls to pass a `name` (they currently pass 4 args; add a 5th `name="x"`). Add:
```python
def test_validate_requires_name() -> None:
    import pytest
    with pytest.raises(ValueError, match="name"):
        validate_transform_config("score", "m", "p", None, "")


def test_validate_rejects_bad_gate_op() -> None:
    import pytest
    with pytest.raises(ValueError, match="op"):
        validate_transform_config("score", "m", "p", None, "sc",
                                  {"source": "x", "field": "f", "op": "bogus", "value": 1})
```
Add an integration test in `ingestion/test_gates.py` (`@requires` Postgres — mirror `test_knowledge.py`'s guards): seed a throwaway Org + a source markdown Artifact + two Transformations where the second has a gate on the first's name that will FAIL, monkeypatch `DISPATCH` handlers to write a known JSON output artifact for the first and assert the second records a `SKIPPED` run and never runs (its handler is never called). Then a passing-gate variant where it does run. The implementer wires the throwaway rows via `get_postgres_session` and cleans them up.

- [ ] **Step 4: Run**

Run: `cd ingestion && uv run pytest test_gates.py test_transformations.py -q` — all pass.

- [ ] **Step 5: Lint, type, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/transformations.py ingestion/test_gates.py ingestion/test_transformations.py
git commit -m "feat(ingestion): fan-out pipeline with gate checkpoints"
```

---

## Task 4: Seed names + sample gate

**Files:**
- Modify: `ingestion/seed.py`

- [ ] **Step 1: Give seeded transforms names + a gate**

Read `seed.py`'s transform-chain seeding. Give each step a unique `name` and pass it (and any `gate`) to `validate_transform_config` and into the `Transformation` row. Names: `summary`, `newsworthiness` (the score step), `knowledge`. Add a gate to the knowledge step:
```python
{"source": "newsworthiness", "field": "score", "op": ">=", "value": 5}
```
Ensure the `Transformation` rows set `name=` and `gate=`, and `validate_transform_config` is called with the new `name`/`gate` args. Keep the `get_or_create` keying working (it keys on org_id+position).

- [ ] **Step 2: Run seed + verify**

Run: `cd ingestion && uv run python seed.py` (idempotent, no error).
```bash
docker exec -i $(docker compose -f ../docker-compose.yml ps -q postgres) psql -U ingestion -d ingestion -tAc "SELECT position, name, type, gate FROM transformations ORDER BY position;"
```
Expected: rows named `summary`/`newsworthiness`/`knowledge`; knowledge has the gate JSON.

- [ ] **Step 3: Lint, type, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/seed.py
git commit -m "chore(ingestion): seed transform names + a sample gate"
```

---

## Task 5: Frontend schema + service + resource routes

**Files:**
- Modify: `app/app/schemas/transformation.ts` (+ `.test.ts`), `app/app/services/transformations.server.ts`, `app/app/routes/api.transformations.ts`, `app/app/routes/api.transformations.$id.ts`

**Interfaces:**
- Produces: `transformationInputSchema` gains `name: z.string().min(1)` and `gate: gateSchema.nullable().optional()`; `gateSchema` = `{ source, field, op: z.enum([...]), value }`. Service create/update and resource routes carry `name`+`gate` through to Drizzle.

- [ ] **Step 1: Extend the Zod schema (TDD)**

Add to `app/app/schemas/transformation.ts`:
```ts
export const GATE_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"] as const;
export const gateSchema = z.object({
  source: z.string().min(1),
  field: z.string().min(1),
  op: z.enum(GATE_OPS),
  value: z.union([z.string(), z.number(), z.boolean(), z.array(z.string())]),
});
```
In `transformationInputSchema` add:
```ts
  name: z.string().trim().min(1, "Name is required"),
  gate: gateSchema.nullable().optional(),
```
Add tests in `transformation.test.ts`: valid input requires `name`; a valid `gate` passes; a bad `op` fails; `gate` omitted/null is allowed.

- [ ] **Step 2: Carry name+gate through service + routes**

In `app/app/services/transformations.server.ts` `create`/`update`, include `name: input.name` and `gate: input.gate ?? null` in the Drizzle `.values(...)`/`.set(...)`. (The introspected schema from Task 1 has the columns; cast the jsonb `gate` if TS complains, per the Task 3 jsonb pattern in the knowledge feature.)
The resource routes already `safeParse` with `transformationInputSchema`, so `name`/`gate` flow through automatically once the schema has them — verify no route hardcodes a field list.

- [ ] **Step 3: Run typecheck + tests + a live create**

```bash
cd app && nvm use
npm run typecheck && npm test
```
Then with the dev server running, POST a transform with a `name` and a `gate` via curl and confirm it persists (get org id from psql). Expected 201 + the row has name+gate.

- [ ] **Step 4: Commit**

```bash
git add app/app/schemas/transformation.ts app/app/schemas/transformation.test.ts app/app/services/transformations.server.ts app/app/routes/api.transformations.ts app/app/routes/api.transformations.\$id.ts
git commit -m "feat(app): name + gate in transformation schema, service, routes"
```

---

## Task 6: Editor — Name input + gate editor

**Files:**
- Modify: `app/app/routes/desk.transformations.tsx` (+ snapshot)

**Interfaces:**
- Consumes: `transformationInputSchema`, `GATE_OPS`, `gateSchema`; the loader's `transformations` list (for the gate `source` dropdown of other transforms' names).

- [ ] **Step 1: Add a Name column/input + thread it into the draft/payload**

The row `Draft` gains `name`. Add a **Name** input to the summary row (before or after Type), bind it to the form (autosave on blur), and include `name` in `buildPayload`'s candidate object. `name` is required — a blank name surfaces the Zod error inline/toast (existing failure path).

- [ ] **Step 2: Add the gate editor to the expanded detail**

In the expanded row (next to Params), add an optional gate editor:
```tsx
// state shape on the draft: gate: { source: string; field: string; op: string; value: string } | null
```
- A checkbox / "Add gate" toggle. When enabled, render:
  - `source`: a `<Select>` whose options are the OTHER transforms' `name`s from `loaderData.transformations` (exclude this row; ideally only earlier `position`, but list all and let validation/eval enforce ordering).
  - `field`: an `<Input>`.
  - `op`: a `<Select>` over `GATE_OPS`.
  - `value`: an `<Input>` (string; the resource route/Zod coerces).
- When the gate toggle is off, the payload sends `gate: null`.
- `buildPayload` maps the gate-draft to `{ source, field, op, value }` (parse `value` to number if numeric, else string), and includes it; validates via `transformationInputSchema` (which now has `gate`). On invalid, the existing inline-error path fires.

- [ ] **Step 3: Update the snapshot + a gate round-trip test**

Update the fixture rows in `desk.transformations.test.tsx` to include `name` (and `gate: null`) so they type-check against the new `TransformationRow`. Run `npx vitest run app/app/routes/desk.transformations.test.tsx -u`; open the snapshot and confirm the Name input renders. Add/extend a test: expand a row, enable the gate, and assert the source/op selects + field/value inputs appear.

- [ ] **Step 4: Typecheck + tests + browser check**

```bash
cd app && nvm use
npm run typecheck && npm test
```
Browser (dev server): open `/desk/<org>/transformations`, confirm each row has a Name input, and expanding a row shows the gate editor with the `source` dropdown listing other transforms' names. Create a knowledge transform named `knowledge` with a gate on `newsworthiness`; confirm it persists (psql).

- [ ] **Step 5: Commit**

```bash
git add app/app/routes/desk.transformations.tsx app/app/routes/__snapshots__ app/app/routes/desk.transformations.test.tsx
git commit -m "feat(app): transform Name input + gate editor"
```

---

## Self-Review Notes

- **Spec coverage:** name+gate columns + SKIPPED (Task 1); pure gate eval + op semantics incl. type-mismatch/missing (Task 2); fan-out pipeline + halt-on-gate-fail + validation (Task 3); seed names+gate (Task 4); Zod/service/routes (Task 5); Name input + gate editor + source dropdown (Task 6). Testing: gate unit, validate, fan-out integration (halt vs pass), Zod, editor snapshot + gate round-trip.
- **Watch-points:** the migration's NOT-NULL-name backfill must run before the constraint (Task 1 hand-edit — autogenerate won't order it); re-introspect Drizzle after the migration (Task 1 Step 5) or Task 5 won't compile; `validate_transform_config`'s new required `name` arg breaks existing callers — Task 3 updates its tests, Task 4 updates seed (sequential); fan-out means score/summarize now read the article (intended, but a behavior change); the gate `value` is stringly-typed from the UI — the frontend parses numerics before validating.
- **Consistency:** gate shape `{source, field, op, value}` identical across `evaluate_gate`, `validate_transform_config`, `gateSchema`, and the editor; `GATE_OPS` keys match between `gates.py` and the Zod `GATE_OPS`.
