"""Central configuration. Every tunable lives here or in a referenced env var."""

# --- Engine (the knowledge-graph API neonews consumes) ---
ENGINE_URL_ENV = "NEONEWS_ENGINE_URL"
ENGINE_URL_DEFAULT = "http://localhost:8000"
ENGINE_API_KEY_ENV = "NEONEWS_ENGINE_API_KEY"
ENGINE_TIMEOUT_SECONDS = 30.0

# --- Postgres (neonews' own state; same instance as the engine, neonews_* tables) ---
POSTGRES_URL_ENV = "NEONEWS_POSTGRES_URL"

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

# --- Drafting ---
# On the first run (no watermark), cover this much history.
DEFAULT_LOOKBACK_HOURS = 24
# Sources requested per draft run. The engine clamps at its own NODES_MAX_LIMIT (500).
SOURCES_QUERY_LIMIT = 200
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
