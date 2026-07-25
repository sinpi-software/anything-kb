# neonews — Automated Newsroom — Design

**Status:** design. Captured 2026-07-25.

**Goal:** a Prefect-orchestrated newsroom in `./neonews` that feeds the knowledge-graph
engine and drafts editorial issues back out of it. It is an **external consumer** of the
engine's HTTP + GraphQL API — it never touches the engine's Neo4j or its tables.

The full loop:

```
gather   sources → dedup → POST /content            (the engine filters + extracts)
draft    sources(since: watermark) → cluster → LLM  → a dated markdown issue on disk
```

## Prior art

Two codebases inform this design.

- **`./ingestion`** (this repo) — the engine neonews consumes. Conventions taken from it:
  flat modules, co-located `test_*.py`, all tunables in `config.py`, ruff line-length 120,
  mypy strict, Python 3.12, uv, plain OpenRouter calls with JSON-schema structured output.
- **`~/Source/anything_blog`** — an earlier, complete newsroom on Prefect. Patterns taken:

| Pattern | Why |
| --- | --- |
| `net_guard.assert_public_url` | Source URLs are operator-supplied and fetched server-side. Applied to the initial request and every redirect hop. |
| `canonicalize_url` + guid/link `dedup_key` | Drops fragments, sorts query params, strips `utm_*`/tracking. The dedup key that survives real feeds. |
| `on_conflict_do_nothing(...).returning()`, act only on inserted rows, after commit | A downstream stage can never race a row that isn't persisted. |
| Per-source failure isolation (`failure_count`) | One bad feed must not fail the run. |
| Watermark table + `DEFAULT_LOOKBACK` on first run | Exactly what `draft-issue` needs. |
| Extraction separate from polling; stamp the attempt either way | Keeps polling fast; dead links don't retry forever. |
| Prefect global concurrency limits, acquired `strict=False` | Caps OpenRouter and page-fetch fan-out across concurrent runs. |
| A **cap** on re-driving a stuck item | Their code records that an uncapped re-drive loop "drained the account". |

**Deliberately not taken: the event-driven chain** (`DeploymentEventTrigger` + `serve()`).
That chain required three pieces of machinery — a reconciler, boot-time event re-emission,
and orphaned-run reaping — all of which exist to patch *at-most-once event delivery*.
neonews needs none of it: every stage is a sweep over durable state (items lacking
`full_text`, items lacking `job_id`, jobs not yet terminal, graph sources newer than the
watermark), so each flow is idempotent and self-healing by construction, and anything
dropped is picked up by the next run. It also means **no Prefect server is required to
develop, test, or run** the pipeline.

`anything_blog` also uses `deepagents` / `langchain-openai`. neonews follows *this* repo
instead: plain OpenRouter HTTP with JSON-schema structured output, no LangChain.

## Prerequisite: expose recency in the engine

Recency already exists in Neo4j — `Entity.created_at` / `Entity.updated_at`
(`knowledge.py:344-346`) and `Source.published_at` / `Source.ingested_at`, stored as native
datetimes precisely so "discovery queries can do duration math" (`knowledge.py:400-406`).
None of it reaches a client: `graph_read._NODE_RETURN` returns only id/type/name/summary/
article, and `nodes()` takes only `type`/`search`/`limit`. The only date on the GraphQL
surface is `references { date }`, per node, after you already have the node.

Changes to `./ingestion` (land first, as their own commit):

1. `graph_read._NODE_RETURN` also returns `created_at` and `updated_at`, as ISO strings
   (`toString(...)`, matching how `references.date` is already returned).
2. The `Node` GraphQL type gains `created_at` and `updated_at` fields.
3. `nodes(since: String)` → `WHERE e.updated_at >= datetime($since)`, ordered
   `updated_at DESC`. Absent `since`, behaviour is unchanged.
4. A new `sources(since: String, limit: Int)` query returning recent `Source` nodes —
   `id`, `label`, `published_at`, `ingested_at` — each with `entities { id type name summary
   article }` (the inverse of `MENTIONED_IN`).

Knowledge-base-scoped on every hop, like every existing query. `limit` is clamped by
`config.NODES_MAX_LIMIT`, as `nodes()` already is.

`sources(since:)` is what makes `draft-issue` a single round-trip: it returns both the
clustering keys (which sources share which entities) and the writing material (the
entities' articles).

Tests land in `ingestion/test_graph_api.py` beside the existing ones: `since` filters and
orders correctly; a `Source` belonging to knowledge base B never appears for a caller
scoped to A.

## Architecture

Four flows. No daemon, no in-app scheduler, no event chain.

```
poll-sources   (cron)      neonews.toml → adapters → dedup insert → neonews_items
ingest-items   (cron)      items with no job_id → extract → POST /content → store job_id
check-jobs     (interval)  items with a non-terminal job_id → GET /content/{job_id} → record
draft-issue    (cron)      sources(since: watermark) → cluster → LLM per cluster → issue.md
```

Each is independently runnable (`uv run python poll.py`) and each is a sweep, so
running one twice is harmless and skipping one costs only latency. `serve.py` registers all
four with cron schedules for when the pipeline is deployed; until then they run directly.

`draft-issue` forks a `write-story` task per cluster on a `ThreadPoolTaskRunner` — the
`author_daily` / `author_desk` shape from `anything_blog`, minus the multi-desk fan-out.

### Modules

`./neonews/` is its own uv project (own `pyproject.toml`, `.python-version`, venv), because
its dependencies — Prefect, feedparser, trafilatura, curl_cffi — have nothing to do with the
engine's.

| Module | Purpose |
| --- | --- |
| `config.py` | Every tunable and env var name. Mirrors `ingestion/config.py`. |
| `db.py` | Session factory. Mirrors `ingestion/db.py`. |
| `models.py` | The `neonews_*` tables. |
| `net_guard.py` | SSRF guard, copied from `anything_blog`. |
| `engine.py` | The only module that knows the engine's HTTP shape: `post_content`, `job_status`, `graphql`. |
| `sources.py` | The `Item` dataclass, the source-adapter protocol, and the RSS and local-file adapters. |
| `cluster.py` | Pure: `list[Source] → list[Cluster]`. Union-find over shared entities. No I/O, no LLM. |
| `write.py` | LLM story synthesis and markdown issue assembly. |
| `poll.py`, `ingest.py`, `jobs.py`, `draft.py` | One flow each — orchestration only, logic lives in the modules above. |
| `serve.py` | Deployment registration with cron schedules. |

Tests are co-located as `test_<module>.py`, per this repo's convention.

### Configuration

`neonews.toml`, in git, holds what a human edits:

```toml
[[sources]]
kind = "rss"
url = "https://example.com/feed.xml"
title = "Example Feed"

[[sources]]
kind = "files"
path = "./drop"

[editorial]
beat = """
Write for readers of ...
"""
```

Sources are upserted into `neonews_sources` at the start of each poll run: **config in git,
runtime state in the database**. The `beat` prompt renders through a **sandboxed Jinja**
environment (the `templates.py` pattern from `anything_blog`) with missing variables
rendering empty, so it can reference `{{ issue.date }}` and `{{ story.entities }}` without
an unsandboxed template ever evaluating operator-authored text.

The beat prompt layers on top of the knowledge base's own `interests` config — the engine
has already filtered *what is in the graph*; the beat shapes *how an issue reads*.

Env vars, all named in `config.py`: `NEONEWS_POSTGRES_URL`, `NEONEWS_ENGINE_URL`,
`NEONEWS_ENGINE_API_KEY`, `NEONEWS_OPENROUTER_API_KEY`.

### Source adapters

`sources.py` defines an `Item` (`dedup_key`, `title`, `text`, `url`, `published_at`) and a
fetch protocol. Two adapters ship:

- **rss** — `curl_cffi` fetch (behind `net_guard`) → `feedparser` → `Item` per entry.
  `dedup_key` is the entry guid, falling back to `canonicalize_url(link)`.
- **files** — reads a drop directory; `dedup_key` is the file's path plus content hash.

A keyed third-party API adapter is *not* built. The protocol is the seam; adding one later
is a single module. Building against an unchosen API would be speculative.

## Data model

Postgres — the same instance the engine uses, tables prefixed `neonews_`, migrations on
their own Alembic chain with `version_table="alembic_version_neonews"` so the two chains
cannot stamp over one another.

- **`neonews_sources`** — `kind`, `url`, `title`, `active`, `last_polled_at`,
  `failure_count`. Upserted from `neonews.toml`.
- **`neonews_items`** — one row per item, carrying its whole lifecycle: `source_id`,
  `dedup_key`, `url`, `title`, `content` (as the feed gave it), `published_at`,
  `extracted_at`, `full_text`, `job_id`, `job_status`, `attempts`, `error`. Unique on
  `(source_id, dedup_key)`. Every sweep is a `WHERE` clause over this table — which is what
  makes the flows self-healing without a reconciler.
- **`neonews_issues`** — `generated_at`, `covers_since`, `path`, `story_count`.
- **`neonews_job_state`** — `key`, `ran_at`. Holds the `draft_issue` watermark.

## Data flow

**Gathering.** `poll-sources` upserts `neonews.toml` into `neonews_sources`, then for each
active source fetches and parses it, inserting new items with
`on_conflict_do_nothing(...).returning()` so only genuinely new rows are acted on, after the
commit. A source that raises bumps its `failure_count` and the run continues.

`ingest-items` sweeps items that have no `job_id` and are under the attempt cap. For each,
if `extracted_at IS NULL` it extracts readable text (trafilatura, behind `net_guard`) and
stamps `extracted_at` whether or not text came back — so a dead link is attempted once, not
forever, while an item whose *submission* failed is retried without re-fetching the page.
Empty extraction falls back to the feed's own content. It then POSTs `{text, metadata}` to
the engine, where `metadata` carries the item's
url, title, and `published_at` (the engine reads `published_at` from job metadata to date
its `Source` nodes), and stores the returned `job_id`.

`check-jobs` sweeps items whose `job_status` is not terminal and records the engine's
verdict. It runs on an interval because the engine's own worker is asynchronous.

**Drafting.** `draft-issue` reads the watermark (or `now - DEFAULT_LOOKBACK` on the first
run), fetches `sources(since:)` in one GraphQL call, and clusters: sources are grouped by
connected components over shared entities, so two articles that mention the same entities
become one story. Each cluster is written by one LLM call — given the cluster's entity
articles, their relationships, and the beat prompt — and returns a story with citations back
to the source labels and dates. The stories are assembled into a dated markdown issue,
written to the output directory, recorded in `neonews_issues`, and only then does the
watermark advance.

## Error handling

- **Fetch** — Prefect task `retries=2, retry_delay_seconds=10`. `BlockedURLError` is a
  configuration error, not a transient one: it is not retried.
- **Per-source and per-item isolation** — one source's failure, or one item's extraction
  failure, never sinks a run.
- **Submission** — a non-2xx response bumps `attempts` and records `error`; the next run
  retries until `MAX_SUBMIT_ATTEMPTS`, after which the item is dead-lettered and surfaced
  rather than re-driven forever. This is where the LLM spend is, so this is where the cap
  belongs.
- **Job outcomes** — `skipped` (the engine judged it irrelevant) and `failed` are terminal
  and normal. Recorded, never retried. That verdict is the engine's to make.
- **Draft** — a cluster whose LLM call fails is dropped with a warning and the issue ships
  with the remaining stories. The watermark advances **only after the issue file is
  written**, so a crash mid-draft re-covers the same window on the next run. A duplicated
  issue is recoverable by hand; a silently skipped window is not.
- **LLM** — bounded by a Prefect global concurrency limit acquired with `strict=False`, plus
  a per-request timeout. Both live in `config.py`.

## Testing

Most of the logic is pure and tested without I/O: `canonicalize_url`, dedup-key derivation,
feed-entry → `Item`, clustering, and markdown issue assembly.

`engine.py` is tested against a stubbed `httpx` transport — asserting the `Authorization:
Bearer` header is sent, that a 202 yields a `job_id`, and that a GraphQL response carrying
`errors` raises rather than silently returning empty data.

Postgres-backed tests (the dedup constraint, watermark read/advance, the sweep queries) skip
when Postgres is unreachable, matching the existing pattern in `ingestion/`.

LLM calls are stubbed throughout; no test spends money.

Flows are called directly as functions, with their tasks' dependencies stubbed — no Prefect
server is needed to run the test suite.

## Out of scope

- Delivery. An issue is a markdown file on disk; email, HTTP serving, and feeding issues
  back into the knowledge base are all deliberately excluded.
- Deployment. `serve.py` carries the schedules, but standing up a Prefect server or a k3s
  CronJob is separate work.
- A keyed third-party API adapter — the protocol is defined, no vendor is wired.
- Per-issue beats. One beat prompt per neonews instance.
