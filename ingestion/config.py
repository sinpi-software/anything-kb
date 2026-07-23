"""Central configuration. Every tunable lives here (or a referenced env var) —
nothing is configured through the Prefect UI."""

from typing import Final

# --- Prefect deployment / schedule ---
FLOW_NAME = "poll-rss-feeds"
POLL_INTERVAL_SECONDS = 60 * 60
TRANSFORM_PIPELINE_DEPLOYMENT_NAME = "transform-pipeline"
# Max flow runs `serve` executes at once. Each run is its own OS subprocess, so an
# uncapped serve spawns one per event (e.g. one per ingested article) and can thrash
# the machine. Keep this low; the transform pipeline is the bulk of the fan-out.
SERVE_FLOW_RUN_LIMIT = 3
# Max feed items processed per poll cycle (each becomes a markdown artifact -> transform
# run). The remainder stay PENDING and drain over later cycles, so a feed with hundreds
# of new items can't flood the pipeline at once.
MAX_ITEMS_PER_POLL = 25

# --- Concurrency (name -> max concurrent) ---
# v2 global concurrency limits, applied to the server on startup by main.ensure_concurrency_limits()
# and acquired via prefect.concurrency around the LLM call.
LLM_CONCURRENCY_NAME = "llm"
CONCURRENCY_LIMITS: dict[str, int] = {
    LLM_CONCURRENCY_NAME: 5,
}

# --- Article fetching ---
IMPERSONATE_BROWSER: Final = "chrome"
ARTICLE_FETCH_TIMEOUT_SECONDS = 15
ARTICLE_FETCH_RETRIES = 2
ARTICLE_FETCH_RETRY_DELAY_SECONDS = 5

# --- LLM ---
OPENROUTER_API_KEY_ENV = "INGESTION_OPENROUTER_API_KEY"
# Per-request timeout. Without it a slow/stuck model response (e.g. a reasoning model
# thinking indefinitely) hangs the transform — and its flow run — forever.
LLM_TIMEOUT_MS = 90_000

# --- Neo4j ---
NEO4J_URI_ENV = "INGESTION_NEO4J_URI"
NEO4J_USER_ENV = "INGESTION_NEO4J_USER"
NEO4J_PASSWORD_ENV = "INGESTION_NEO4J_PASSWORD"

# How many existing entities to offer the LLM as resolution candidates.
KNOWLEDGE_RESOLUTION_CANDIDATES = 5
