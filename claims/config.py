"""Central configuration. Every tunable lives here or in a referenced env var."""

import os

import dotenv

# Every module imports config, so loading here (before any env var is read) is what
# makes the repo-root .env actually take effect — `uv run` does not load it itself.
# Same files, same order, same override=False semantics as neonews/config.py.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")

# --- Postgres (claims' own state; the engine's instance, claims_* tables) ---
POSTGRES_URL_ENV = "CLAIMS_POSTGRES_URL"
POSTGRES_URL_DEFAULT = "postgresql://ingestion:ingestion@localhost:5432/ingestion"
# The test suite's own database, isolated from the operator's live one.
POSTGRES_TEST_URL_ENV = "CLAIMS_TEST_POSTGRES_URL"
POSTGRES_TEST_URL_DEFAULT = "postgresql://ingestion:ingestion@localhost:5432/claims_test"

# --- LLM (OpenRouter, over httpx) ---
# The `openrouter` SDK drops the web plugin's citations: ChatAssistantMessage has no
# `annotations` field and its BaseModel does not allow extras, so pydantic discards
# them. verify.py's grounding filter needs those citations, so this app speaks to the
# API directly. See the design doc.
OPENROUTER_API_KEY_ENV = "CLAIMS_OPENROUTER_API_KEY"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Three models, three jobs: cheap structured extraction, web-enabled research, and the
# strongest of the three to judge. Mirrors the engine's split of extraction onto its own.
EXTRACT_MODEL = "openai/gpt-5-mini"
RESEARCH_MODEL = "openai/gpt-5-mini"
JUDGE_MODEL = "openai/gpt-5"
LLM_TIMEOUT_SECONDS = 120.0
# Prefect global concurrency limit name, acquired with strict=False so an absent
# limit is a no-op rather than an error.
LLM_CONCURRENCY_LIMIT = "openrouter"
# Web-search results the plugin may return per research call.
WEB_MAX_RESULTS = 8

# --- Fetching ---
USER_AGENT = "claims/0.1"
FETCH_TIMEOUT_SECONDS = 30
# One enormous page must not become one enormous prompt. Truncation is logged.
FULL_TEXT_MAX_CHARS = 60_000

# --- Extraction ---
# Documents prepared and extracted per `extract-claims` run.
EXTRACT_BATCH_SIZE = 10
# A document that has failed this many times is dead-lettered, not re-driven.
MAX_EXTRACT_ATTEMPTS = 3

# --- The verification gate ---
# Only empirical claims are checkable; the rest are stored and shown, never spent on.
CHECKABLE_CLAIM_TYPE = "empirical"
CLAIM_TYPES = frozenset({"empirical", "predictive", "normative", "opinion"})
# Claims verified per document, the top N by checkworthiness.
VERIFY_MAX_PER_DOCUMENT = 8
# A floor as well as a ranking cutoff: a document whose best claim is weak gets zero
# checks rather than N weak ones.
CHECKWORTHINESS_MIN = 0.4

# --- Verification ---
# Claims verified per `verify-claims` run. Each claim is three LLM calls, two of them
# web-enabled, so this is where the money is.
VERIFY_BATCH_SIZE = 25
MAX_VERIFY_ATTEMPTS = 3
VERDICTS = frozenset({"supported", "disputed", "refuted", "unverifiable"})
STANCES = frozenset({"supports", "contradicts", "context"})
# Claims verified concurrently within one run.
VERIFY_CONCURRENCY = 4
# Characters of surrounding document text handed to the research calls as context.
CLAIM_CONTEXT_CHARS = 2_000

# --- Reporting ---
# Documents reported per `report-documents` run.
REPORT_BATCH_SIZE = 25
OUTPUT_DIR = "reports"

# --- Deployment schedules (serve.py) ---
SCHEDULE_TZ_ENV = "CLAIMS_SCHEDULE_TZ"
SCHEDULE_TZ_DEFAULT = "America/Los_Angeles"
EXTRACT_CRON_ENV = "CLAIMS_EXTRACT_CRON"
EXTRACT_CRON_DEFAULT = "*/10 * * * *"
VERIFY_CRON_ENV = "CLAIMS_VERIFY_CRON"
VERIFY_CRON_DEFAULT = "*/15 * * * *"
REPORT_CRON_ENV = "CLAIMS_REPORT_CRON"
REPORT_CRON_DEFAULT = "*/20 * * * *"
SERVE_CONCURRENCY_ENV = "CLAIMS_SERVE_CONCURRENCY"
SERVE_CONCURRENCY_DEFAULT = 4
