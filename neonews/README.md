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

```bash
uv run pytest        # Postgres-backed tests skip when Postgres is unreachable
uv run ruff check .
uv run mypy .
```
