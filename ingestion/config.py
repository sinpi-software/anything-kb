"""Central configuration. Every tunable lives here (or a referenced env var) —
nothing is configured through the Prefect UI."""

from typing import Final

# --- Prefect deployment / schedule ---
FLOW_NAME = "poll-rss-feeds"
POLL_INTERVAL_SECONDS = 60 * 60
TRANSFORM_PIPELINE_DEPLOYMENT_NAME = "transform-pipeline"

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
