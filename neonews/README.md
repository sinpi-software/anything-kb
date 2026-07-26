# neonews

An automated newsroom over the knowledge-graph engine. It gathers sources into the
engine's API, then drafts an editorial issue back out of the graph.

```
poll-sources   neonews.toml → adapters → dedup insert → neonews_items
ingest-items   items with no job_id → extract → POST /content → store job_id
check-jobs     items with a non-terminal job_id → GET /content/{job_id} → record
draft-issue    sources(since: watermark) → cluster → LLM per cluster → issues/*.md
```

Each flow is a **sweep over durable state**, not a link in an event chain: re-running
one is harmless, anything missed is picked up next run, and no Prefect server is
needed to develop, test, or run the pipeline.

## Setup

```bash
cd neonews
uv sync
uv run alembic upgrade head
```

Environment (repo-root `.env`):

- `NEONEWS_ENGINE_URL` — the engine, e.g. `http://localhost:8000`
- `NEONEWS_ENGINE_API_KEY` — an engine API key (`ingestion/seed.py` prints one)
- `NEONEWS_POSTGRES_URL` — the same Postgres the engine uses; neonews owns the `neonews_*` tables
- `NEONEWS_OPENROUTER_API_KEY` — for story synthesis

Edit `neonews.toml` to declare sources and the editorial beat. Config lives in git;
runtime state lives in Postgres.

### Known limitation: draft window truncation

`draft-issue` asks the engine for every source since its watermark, newest first,
capped at `SOURCES_QUERY_LIMIT` (the engine's own ceiling, `NODES_MAX_LIMIT = 500`).
If a window ever holds more sources than that, the oldest ones in it are never
returned and are permanently skipped — the engine has no `until` parameter or
ascending-order option to page through the rest. When this happens, `draft-issue`
logs an ERROR naming the uncovered window and returns `truncated: True`; nothing
silently disappears. `SOURCES_QUERY_LIMIT` is already set to the engine's own
ceiling (`NODES_MAX_LIMIT = 500`) and **cannot be raised further** — the engine
hard-clamps at that value regardless of what's requested, so doing so would only
disable the truncation check itself. Fixing this for real needs an engine-side
change (`ingestion/`), which is out of scope here; the only mitigation available
from this side is tightening the draft schedule (running it more often, on a
smaller window) so fewer sources accumulate per run.

## Run

```bash
uv run python poll.py      # gather
uv run python ingest.py    # extract + submit to the engine
uv run python jobs.py      # record the engine's verdicts
uv run python draft.py     # write issues/YYYY-MM-DD-HHMM.md
```

The engine's own worker must be running for submitted content to be processed
(`cd ../ingestion && uv run python worker.py`).

To run on a schedule, `uv run python serve.py` (needs `PREFECT_API_URL`).

## Test

Postgres-backed tests run against their own database, never the operator's live one —
create it once and point `NEONEWS_TEST_POSTGRES_URL` at it:

```bash
createdb -U ingestion -h localhost neonews_test
NEONEWS_TEST_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test \
  uv run alembic upgrade head
```

`alembic upgrade head` on its own (no override) targets `NEONEWS_POSTGRES_URL` — the
real database — so the explicit `NEONEWS_TEST_POSTGRES_URL` override above is what
migrates `neonews_test` instead.

```bash
uv run pytest         # Postgres-backed tests FAIL (not skip) if neonews_test is unreachable
uv run ruff check .
uv run mypy .
```
