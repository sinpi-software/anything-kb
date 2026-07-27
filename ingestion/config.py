"""Central configuration. Every tunable lives here or in a referenced env var."""

# --- LLM (OpenRouter) ---
OPENROUTER_API_KEY_ENV = "INGESTION_OPENROUTER_API_KEY"
# Default model for relevance judging and article synthesis.
LLM_MODEL = "openai/gpt-5-nano"
# Extraction gets its own model, and the most capable one in the stack. A new entity's article is
# its extracted description stored verbatim — synthesize_article runs only when an entity is merged,
# and most entities are named by exactly one document, so extraction authors the great majority of
# the knowledge base and nothing revisits it. Measured on gpt-5-nano, descriptions averaged ~100
# characters however the prompt was worded ("Town in Oregon where Randy Stapilus resides"), which is
# a capability ceiling rather than a prompt defect. It is also the highest-volume call — once per
# ingested item — so this is the expensive choice made deliberately, and only here: relevance
# judging is a cheap yes/no and synthesis touches only the minority of entities that recur.
# Benchmarked against five alternatives on one article, same prompt. Beat gpt-5-nano decisively
# (226 vs 100 average description characters) and came within 9% of anthropic/claude-haiku-4.5's
# 247 at a twenty-fifth of the input price — while producing 25 distinct relationship types to
# haiku's 3, which matters more here, since a vocabulary collapsed onto one catch-all type was the
# defect this pipeline just spent a long night fixing. Cheaper on output than the gpt-5-nano it
# replaces ($0.17 against $0.40 per M).
EXTRACTION_MODEL = "openai/gpt-oss-120b"
# The salience/type-admission gate (consolidate_types) runs on a more capable model:
# it is low-volume (once per novel type) and is the sole guard against fragment types.
TYPE_GATE_MODEL = "openai/gpt-5-mini"
# The precision-critical entity-resolution merge check: it is batched (once per item) and a
# wrong merge fuses two unrelated subjects into one node, which is not cheaply undone.
# Verified on OpenRouter to support `structured_outputs`, which this call requires — the
# merge check sends a strict json_schema and a model without it would fail every request.
RESOLUTION_MODEL = "deepseek/deepseek-v4-flash"
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
