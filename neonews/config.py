"""Central configuration. Every tunable lives here or in a referenced env var."""

import os

import dotenv

# Every module imports config, so loading here (before any env var is read) is what
# makes the repo-root .env actually take effect — `uv run` does not load it itself.
# Same files, same order, same override=False semantics as ingestion/main.py.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")

# --- Engine (the knowledge-graph API neonews consumes) ---
ENGINE_URL_ENV = "NEONEWS_ENGINE_URL"
ENGINE_URL_DEFAULT = "http://localhost:8000"
ENGINE_API_KEY_ENV = "NEONEWS_ENGINE_API_KEY"
ENGINE_TIMEOUT_SECONDS = 30.0

# --- Postgres (neonews' own state; same instance as the engine, neonews_* tables) ---
POSTGRES_URL_ENV = "NEONEWS_POSTGRES_URL"
# The test suite's own database, isolated from the operator's live one. Every
# Postgres-backed test module points NEONEWS_POSTGRES_URL here before importing
# config/db, so a sweeping test can never touch a real row.
POSTGRES_TEST_URL_ENV = "NEONEWS_TEST_POSTGRES_URL"
POSTGRES_TEST_URL_DEFAULT = "postgresql://ingestion:ingestion@localhost:5432/neonews_test"

# --- LLM (OpenRouter) ---
OPENROUTER_API_KEY_ENV = "NEONEWS_OPENROUTER_API_KEY"
LLM_MODEL = "openai/gpt-5-mini"
LLM_TIMEOUT_MS = 90_000
# Prefect global concurrency limit name, acquired with strict=False so an absent
# limit is a no-op rather than an error.
LLM_CONCURRENCY_LIMIT = "openrouter"
FETCH_CONCURRENCY_LIMIT = "fetch"

# --- Fetching ---
USER_AGENT = "neonews/0.1"
FETCH_TIMEOUT_SECONDS = 30
# Feed entries considered per poll, newest first.
POLL_ITEM_LIMIT = 50

# --- Ingestion ---
# Items prepared and submitted per `ingest` run.
INGEST_BATCH_SIZE = 25
# A submission that has failed this many times is dead-lettered, not re-driven.
# This is where the LLM spend is, so this is where the cap belongs.
MAX_SUBMIT_ATTEMPTS = 3
# Job outcomes that end an item's lifecycle.
TERMINAL_JOB_STATUSES = frozenset({"done", "skipped", "failed"})

# --- Jobs ---
# check-jobs items checked per run, oldest first. Unbounded, a long engine outage
# (thousands of non-terminal rows, each a 30s-timeout GET) can occupy every serve()
# worker slot with stalled check-jobs runs.
JOBS_BATCH_SIZE = 200

# --- Drafting ---
# On the first run (no watermark), cover this much history.
DEFAULT_LOOKBACK_HOURS = 24
# Sources requested per draft run, newest first. Set to the engine's own ceiling
# (ingestion/config.py's NODES_MAX_LIMIT) to make truncation as rare as possible: the
# engine has no `until`/ascending-order parameter, so a truncated window's remainder
# is simply lost (see draft.py's truncation handling) rather than picked up next run.
SOURCES_QUERY_LIMIT = 500
# The engine hard-clamps `sources(limit:)` at its own NODES_MAX_LIMIT (500,
# ingestion/config.py) — `min(max(limit, 1), NODES_MAX_LIMIT)` in graph_read.py — so a
# larger value here would silently ask for more than the engine will ever return.
# draft.py detects truncation as `len(rows) >= SOURCES_QUERY_LIMIT`; if this constant
# ever exceeded the engine's ceiling, a run that hit the *engine's* clamp instead of
# this one would come back with fewer rows than SOURCES_QUERY_LIMIT and the truncation
# check would silently miss it — exactly the loss Item 2 exists to make loud. This
# assertion keeps that invariant from drifting unnoticed; test_draft.py pins it too.
assert SOURCES_QUERY_LIMIT <= 500, "SOURCES_QUERY_LIMIT must not exceed the engine's NODES_MAX_LIMIT (500)"
# Sources in one story cluster. A cluster larger than this is truncated, newest first,
# so one hub entity can't pull the whole window into a single unwritable story.
CLUSTER_MAX_SOURCES = 12
# Where issues are written.
OUTPUT_DIR = "issues"
# Operator-editable sources + beat prompt.
CONFIG_FILE = "neonews.toml"

# --- Watermark keys (neonews_job_state.key) ---
DRAFT_WATERMARK_KEY = "draft_issue"

# --- Deployment schedules (serve.py) ---
SCHEDULE_TZ_ENV = "NEONEWS_SCHEDULE_TZ"
SCHEDULE_TZ_DEFAULT = "America/Los_Angeles"
POLL_CRON_ENV = "NEONEWS_POLL_CRON"
POLL_CRON_DEFAULT = "0 * * * *"
INGEST_CRON_ENV = "NEONEWS_INGEST_CRON"
INGEST_CRON_DEFAULT = "10 * * * *"
JOBS_CRON_ENV = "NEONEWS_JOBS_CRON"
JOBS_CRON_DEFAULT = "*/5 * * * *"
DRAFT_CRON_ENV = "NEONEWS_DRAFT_CRON"
DRAFT_CRON_DEFAULT = "30 7 * * *"
# Flow runs executed concurrently by serve(). Each run is a subprocess.
SERVE_CONCURRENCY_ENV = "NEONEWS_SERVE_CONCURRENCY"
SERVE_CONCURRENCY_DEFAULT = 4
