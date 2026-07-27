# claims — design

A sub-app with one job: given a link, extract every claim the page makes, attribute
each to whoever asserted it, check it against the web, and score it with a rationale
and cited evidence.

It borrows neonews' shape — a standalone `uv` project whose flows are sweeps over
durable Postgres state — but shares nothing with it at runtime. `claims` never calls
the knowledge-graph engine; it owns the `claims_*` tables in the same database and
talks only to OpenRouter and the open web.

## Pipeline

```
submit-url        a URL              → claims_documents
extract-claims    extracted_at NULL  → fetch + trafilatura + LLM → claims_claims
verify-claims     verdict NULL       → research + refute + judge → claims_evidence, verdict
report-documents  nothing pending    → reports/<slug>-<ts>.md
```

Each flow is a `WHERE` clause over durable columns, so re-running one is harmless and
anything missed is picked up next run. There is no watermark table: unlike neonews,
no sweep here is scoped to a time window.

## Module layout

```
claims/
  config.py      every tunable, .env loading (same preamble as neonews/config.py)
  db.py          session factory over the shared Postgres
  models.py      Document, Claim, Evidence, Report
  alembic/       version_table="alembic_version_claims", include_object gates on "claims_"
  net_guard.py   SSRF guard, lifted from neonews
  fetch.py       url → (title, author, published_at, text); also canonicalize_url,
                 lifted from neonews/sources.py
  llm.py         OpenRouter client, strict-schema helper, web-plugin toggle
  submit.py      flow: submit-url
  extract.py     flow: extract-claims
  verify.py      flow: verify-claims
  report.py      flow: report-documents
  serve.py       deployments
  test_*.py
```

`llm.py` exists because `extract` and `verify` both need `json_schema` structured
output and `verify` additionally needs the web plugin — one seam rather than
neonews' `_strict_schema` copied twice. It is also the single patch point that keeps
the test suite from spending money.

There is no `claims.toml`. neonews has one because an operator declares sources and
an editorial voice; here there is no source list, and prompts only developers tune
belong in code.

## Data model

All tables carry `id uuid` and `created_at`, as `neonews._BaseModel` does. All
timestamps are `TIMESTAMP(timezone=True)`.

### claims_documents

| column | type | notes |
| --- | --- | --- |
| `url` | text | as submitted |
| `canonical_url` | text | unique; dedup key, canonicalized like `sources.canonicalize_url` |
| `title`, `author` | text | from the page, nullable |
| `published_at` | ts | nullable |
| `full_text` | text | readable text, truncated to `FULL_TEXT_MAX_CHARS` |
| `fetched_at` | ts | stamped on successful fetch |
| `extracted_at` | ts | stamped with the claim rows, one transaction |
| `reported_at` | ts | stamped with the report row |
| `attempts` | int | guards extract |
| `error` | text | last failure |

### claims_claims

| column | type | notes |
| --- | --- | --- |
| `document_id` | fk | |
| `text` | text | the assertion, normalized to stand alone without the article |
| `quote` | text | verbatim span it came from |
| `attributed_to` | text | "Mayor Chen", "the CDC", "the article" |
| `attribution_type` | text | `quoted_person` \| `cited_org` \| `document_itself` |
| `cited_source` | text | upstream origin named in the text; null for most claims |
| `cited_source_url` | text | if the text links it; null for most claims |
| `claim_type` | text | `empirical` \| `predictive` \| `normative` \| `opinion` |
| `checkworthiness` | real | 0.0–1.0 |
| `selected_for_verification` | bool | the gate decision, stamped at extraction |
| `verdict` | text | `supported` \| `disputed` \| `refuted` \| `unverifiable`; NULL until judged |
| `confidence` | real | 0.0–1.0, confidence in the verdict |
| `rationale` | text | |
| `verified_at` | ts | |
| `attempts` | int | guards verify |
| `error` | text | last failure |

### claims_evidence

| column | type | notes |
| --- | --- | --- |
| `claim_id` | fk | |
| `url`, `title`, `snippet` | text | snippet quoted from the source |
| `stance` | text | `supports` \| `contradicts` \| `context` |
| `published_at` | ts | nullable, for recency |

### claims_reports

| column | type | notes |
| --- | --- | --- |
| `document_id` | fk | |
| `generated_at` | ts | |
| `path` | text | dev convenience; dies with the pod |
| `claim_count`, `verified_count` | int | |
| `body` | text | the rendered markdown — the durable copy |

### Two decisions worth stating

**`selected_for_verification` is stamped at extraction, not computed at verify time.**
"Top-N by checkworthiness" as a live query is a moving target: the set shifts as rows
resolve, which makes "is this document finished?" unanswerable. Deciding once, in the
transaction that writes the claims, reduces the verify sweep to a flat predicate and
makes "reportable" the plain absence of pending rows.

**Idempotency is transactional, not a unique constraint.** neonews needs
`unique(source_id, dedup_key)` because polling re-sees the same feed entries. Nothing
re-arrives here. Extraction inserts every claim and stamps `extracted_at` in one
commit; verification writes evidence rows and the verdict in one commit. A crash
mid-stage rolls back to "not yet done" and the next sweep redoes it cleanly.

## Flows

### submit-url

Takes a URL parameter, canonicalizes it, inserts a `claims_documents` row (no-op if
`canonical_url` already exists), returns the id. Runnable as
`uv run python submit.py <url>` and as a parameterized Prefect deployment.

### extract-claims

Sweeps `extracted_at IS NULL AND attempts < MAX_EXTRACT_ATTEMPTS`,
`EXTRACT_BATCH_SIZE` per run.

1. If `full_text` is already present, skip straight to step 2; otherwise fetch
   through `net_guard.fetch`, extract readable text with trafilatura, truncate to
   `FULL_TEXT_MAX_CHARS` (logged when it bites), stamp `fetched_at`, and commit.
   Fetching is stamped separately from extraction for the same reason
   `neonews/ingest.py` does it: a document whose *LLM call* failed is retried without
   re-fetching the page.
2. One `EXTRACT_MODEL` call returns every claim with its attribution, type, and
   checkworthiness.
3. Apply the gate: `claim_type == "empirical"`, `checkworthiness >=
   CHECKWORTHINESS_MIN`, top `VERIFY_MAX_PER_DOCUMENT` by checkworthiness. Stamp
   `selected_for_verification`.
4. Insert claims and stamp `extracted_at` in one commit.

### verify-claims

Sweeps `selected_for_verification AND verdict IS NULL AND attempts <
MAX_VERIFY_ATTEMPTS`, `VERIFY_BATCH_SIZE` per run, fanned out as Prefect tasks under
a `ThreadPoolTaskRunner` with each call inside
`concurrency(LLM_CONCURRENCY_LIMIT, strict=False)` — `draft.py`'s arrangement.

Three calls per claim:

1. **Research** — `RESEARCH_MODEL` with `plugins: [{"id": "web"}]`. Given the claim,
   its attribution, and surrounding document context, search and return evidence
   items (`url`, `title`, `snippet`, `stance`, `published_at`).
2. **Refutation** — the same model and plugin under an opposed brief: find evidence
   the claim is false, misleading, or missing crucial context; say so plainly if none
   exists.
3. **Judge** — `JUDGE_MODEL` with **no** web plugin. Sees the claim, its attribution,
   and the pooled evidence; returns `verdict`, `confidence`, `rationale`.

Evidence rows and the verdict commit together.

**Why the refutation call exists.** Search here is the OpenRouter web plugin, chosen
so the app needs no second API key. That fuses searching into the model's own
reasoning, and a single call asked to "check this claim" is a call that mostly
confirms it. Two calls under opposed briefs recover the adversarial structure that a
standalone search API would have given for free.

**Why the judge has no web access.** It can only reason over evidence that exists as
rows in `claims_evidence`, so no rationale ever rests on something the reader cannot
click.

**Grounding guard.** The research calls use `json_schema` output *and* the web
plugin, so the model emits URL strings into a JSON field — strings it can invent.
OpenRouter returns genuine citations separately as message `annotations`. `verify.py`
filters emitted evidence against the annotation URL set: where annotations are
present, evidence citing a URL absent from them is dropped and logged; where a
response carries no annotations at all there is nothing to check against, so evidence
is kept and a warning is logged.

**Dedup** compares `(canonicalize_url(url), stance)` while storing the URL as given.
The same URL arriving from both calls with opposite stances is kept as two rows — a
source that genuinely cuts both ways is signal the judge should see.

**Calibration.** The judge prompt states that `confidence` is confidence in the
*verdict*, and that thin or absent evidence means `unverifiable`, never a
low-confidence `refuted`. Separating direction from certainty is the reason the score
has two axes instead of being one 0–100 number.

### report-documents

Sweeps `extracted_at IS NOT NULL AND reported_at IS NULL` where no claim of the
document is pending — pending being `selected_for_verification AND verdict IS NULL
AND attempts < MAX_VERIFY_ATTEMPTS`. Renders markdown to `reports/<slug>-<ts>.md`,
writes the `claims_reports` row with `body`, stamps `reported_at`.

```
# Claim check — <title>
<url> · <author> · published <date> · checked <date>

12 claims extracted · 8 checked · 3 supported, 1 disputed, 2 refuted, 2 unverifiable

## "Violent crime fell 20% year over year"
Attributed to Mayor Chen (quoted) · citing the 2025 city crime report
**Refuted** · confidence 0.82
<rationale>
Contradicting: <url> — "…4.1% decrease…"
Context:       <url> — "…Q1 alone fell 19%…"

## Not checked
- "Housing costs will keep rising" — predictive
- "The council acted shamefully" — normative
- "Turnout was the lowest since 1998" — below checkworthiness floor
- "Bond issuance totalled $4.2M" — could not be checked: <error>
```

## Failure handling

| failure | handling |
| --- | --- |
| Fetch failure (network, timeout, blocked URL) | `attempts++`, `error` recorded, retried to `MAX_EXTRACT_ATTEMPTS` |
| Empty extraction (page loads, no readable text) | dead-lettered at once, `error="no readable text"` |
| Zero claims extracted | not a failure; `extracted_at` stamps, the report says so |
| One claim's verify call fails | caught per claim, `attempts++`; the sweep continues |
| Verify exhausts its attempts | dead-lettered with `error` set and **`verdict` left NULL** |

Empty extraction is deterministic — a JS-rendered page yields nothing on the third
try too — so retrying only spends attempts to learn the same thing three times.
Dead-lettering it means setting `attempts = MAX_EXTRACT_ATTEMPTS` alongside the
error, which is what drops the row out of the sweep predicate; there is no separate
"abandoned" flag to keep in sync.

A document that never gets past fetch or extraction has `extracted_at IS NULL` and so
is never reportable — it produces no report at all, by design. Those documents are
found by querying `claims_documents WHERE error IS NOT NULL`, which is also what the
flow logs at WARNING as it dead-letters them.

A dead-lettered claim keeps `verdict` NULL rather than taking `unverifiable` because
"searched and found nothing" and "the call failed three times" are different facts,
and the report must be able to distinguish them. Dead-lettered claims are not
pending, so they unblock the report and appear in it as "could not be checked".

## Configuration

`config.py` holds every tunable, loading `.env`, `.env.local`, `.env.sample` in
neonews' order.

Env vars (added to `.env.sample`): `CLAIMS_POSTGRES_URL`, `CLAIMS_TEST_POSTGRES_URL`,
`CLAIMS_OPENROUTER_API_KEY`, `CLAIMS_SCHEDULE_TZ`, `CLAIMS_SERVE_CONCURRENCY`,
`CLAIMS_{EXTRACT,VERIFY,REPORT}_CRON`.

Constants: `EXTRACT_MODEL`, `RESEARCH_MODEL`, `JUDGE_MODEL` (three knobs, following
the repo's recent split of extraction onto its own model), `VERIFY_MAX_PER_DOCUMENT`,
`CHECKWORTHINESS_MIN`, `EXTRACT_BATCH_SIZE`, `VERIFY_BATCH_SIZE`,
`MAX_EXTRACT_ATTEMPTS`, `MAX_VERIFY_ATTEMPTS`, `FULL_TEXT_MAX_CHARS`,
`LLM_TIMEOUT_MS`, `LLM_CONCURRENCY_LIMIT`, `FETCH_TIMEOUT_SECONDS`, `USER_AGENT`,
`OUTPUT_DIR`.

`CHECKWORTHINESS_MIN` is a floor as well as a ranking cutoff: a document whose best
claim is weak gets zero checks rather than N weak ones.

`serve.py` registers four deployments. `submit-url` is parameterized and
unscheduled — it takes a URL. The other three run on short intervals, with an empty
cron registering a deployment unscheduled, as neonews does. `docker-compose.yml`
gains a `claims-serve` service mirroring `neonews-serve`.

## Testing

Postgres-backed tests run against `CLAIMS_TEST_POSTGRES_URL` and **fail rather than
skip** when it is unreachable, as neonews' suite does. Every LLM call is patched at
the `llm.py` seam, so the suite never spends money. `net_guard`'s tests come across
with the module.

Cases that must exist:

- The grounding filter drops evidence citing a URL absent from the annotations, and
  keeps everything when a response has no annotations at all.
- The gate stamps `selected_for_verification` deterministically: type, floor, and
  top-N together.
- A rolled-back extraction leaves no orphan claim rows.
- Verify dead-letters at the attempt cap without setting a verdict.
- `report-documents` stays blocked while a claim is pending and unblocks once that
  claim dead-letters.
- Evidence dedup keeps two rows for one URL under opposing stances, one under
  matching stances.
- Truncation at `FULL_TEXT_MAX_CHARS` is logged.

`ruff check` and `mypy` strict, with neonews' settings.

## Out of scope

- Any engine integration. Claims do not become graph nodes. If that is wanted later
  it is a consumer of `claims_*`, not a change to this design.
- Re-checking a claim over time. Verdicts are written once. Storing them durably is
  what makes re-checking a later flow rather than a rewrite.
- A UI. The markdown report and the tables are the interface.
