"""Central configuration. Every tunable lives here or in a referenced env var."""

# --- LLM (OpenRouter) ---
OPENROUTER_API_KEY_ENV = "INGESTION_OPENROUTER_API_KEY"
# Default model for relevance judging and knowledge extraction. gpt-5-nano was too
# weak to follow the extraction salience guardrails (kept coining TimeWindow/fragment
# types despite explicit instruction not to); mini follows the negative instructions.
LLM_MODEL = "openai/gpt-5-mini"
# Per-request timeout. Without it a stuck reasoning model hangs the worker forever.
LLM_TIMEOUT_MS = 90_000
# Max concurrent OpenRouter calls, enforced by a threading.Semaphore in knowledge._chat.
LLM_CONCURRENCY = 5
# How many existing entities to offer the LLM as resolution candidates.
KNOWLEDGE_RESOLUTION_CANDIDATES = 5

# --- Neo4j ---
NEO4J_URI_ENV = "INGESTION_NEO4J_URI"
NEO4J_USER_ENV = "INGESTION_NEO4J_USER"
NEO4J_PASSWORD_ENV = "INGESTION_NEO4J_PASSWORD"

# --- GraphQL ---
# Hard cap on `nodes(limit:)` regardless of what a caller requests.
NODES_MAX_LIMIT = 500

# --- Worker ---
# Jobs claimed per loop iteration (FOR UPDATE SKIP LOCKED batch size).
WORKER_BATCH_SIZE = 5
# A job that has failed this many times stays failed instead of retrying.
WORKER_MAX_ATTEMPTS = 3
# Seconds to sleep when a claim finds no pending jobs.
WORKER_POLL_INTERVAL_SECONDS = 2.0
