# claims

Given a link: extract every claim the page makes, attribute each to whoever asserted
it, check it against the web, and score it with a rationale and cited evidence.

```
submit-url        a URL              → claims_documents
extract-claims    extracted_at NULL  → fetch + trafilatura + LLM → claims_claims
verify-claims     verdict NULL       → research + refute + judge → claims_evidence, verdict
report-documents  nothing pending    → reports/<slug>-<ts>.md
```

Each flow is a **sweep over durable state**: re-running one is harmless, anything
missed is picked up next run, and no Prefect server is needed to develop or run it.

## How a claim is checked

Three LLM calls. Two are web-enabled and deliberately opposed — one looks for
evidence, one is briefed to refute — because search here is OpenRouter's web plugin
rather than a standalone search API, and a single call asked to "check this claim"
mostly confirms it. The third call, the judge, has **no** web access: it reasons only
over evidence stored as rows, so no rationale rests on something you cannot click.

Evidence the model emits is filtered against OpenRouter's own `url_citation`
annotations, so an invented URL never reaches the database.

Only empirical claims above `CHECKWORTHINESS_MIN`, capped at
`VERIFY_MAX_PER_DOCUMENT`, are checked. Predictions and value judgments are stored
and shown, never spent on.

## Setup

```bash
cd claims
uv sync
uv run alembic upgrade head
```

Environment (repo-root `.env`):

- `CLAIMS_POSTGRES_URL` — the engine's Postgres; claims owns the `claims_*` tables
- `CLAIMS_OPENROUTER_API_KEY` — for extraction, research, and judgment

## Run

`submit-url` is the only flow that takes a parameter — a URL. It just inserts a row;
every downstream flow finds its own work by sweeping for documents in the right state,
so `submit-url` has nothing to schedule and is registered in `serve.py` without a cron.

```bash
uv run python submit.py https://example.com/article
uv run python extract.py
uv run python verify.py
uv run python report.py
```

To run on a schedule, `uv run python serve.py` (needs `PREFECT_API_URL`). An empty
`CLAIMS_*_CRON` registers that deployment with no schedule rather than firing it on a
timer. `extract` and `verify` each place LLM calls that spend OpenRouter credits — that
is why `docker-compose.yml` sets `CLAIMS_EXTRACT_CRON` and `CLAIMS_VERIFY_CRON` (and,
for symmetry, `CLAIMS_REPORT_CRON`) to `""` for the local stack: a Compose stack left
running while you edit code must not bill you for it. Trigger a run manually from the
Prefect UI, or set a real cron in your own `.env`.

Easiest path is the whole stack in Docker, from the repo root:

```bash
docker compose up
```

`claims-migrate` runs `alembic upgrade head` first; `claims-serve` waits for it to
finish (`depends_on: ... condition: service_completed_successfully`) before starting,
so the tables always exist before the flows do. claims runs as `claims-serve`, its
four flows registered unscheduled. Trigger them from
http://localhost:4200/deployments. `docker compose restart claims-serve` picks up a
flow change — `serve()` has no hot reload.

## Test

Postgres-backed tests run against their own database, never the operator's live one.
This is a **one-time** setup — create the database once and every test run after that
just needs `CLAIMS_TEST_POSTGRES_URL` pointed at it (the default already does):

`alembic upgrade head` on its own targets whatever `CLAIMS_POSTGRES_URL` already
points at — your real database — so the one-time migration below sets
`CLAIMS_POSTGRES_URL` explicitly to `claims_test` for that single command only:

```bash
createdb -U ingestion -h localhost claims_test
CLAIMS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/claims_test \
  uv run alembic upgrade head
```

```bash
uv run pytest         # Postgres-backed tests FAIL (not skip) if claims_test is unreachable
uv run ruff check .
uv run mypy .
```

No test can make a real LLM call: `conftest.py` monkeypatches `llm._post` to raise on
every test, unconditionally. Individual tests patch `llm.complete` (or
`<module>.llm.complete`) to supply the behavior they need; anything that reaches
`_post` without having done so hits that guard instead of OpenRouter.

## Notes

`llm.py` calls OpenRouter over httpx rather than using the `openrouter` SDK, unlike
`neonews/write.py`. The SDK's `ChatAssistantMessage` has no `annotations` field and
its `BaseModel` does not allow extras, so pydantic discards the web plugin's
citations — and the grounding filter needs exactly those.

**`RESEARCH_MODEL` must return citations under structured output.** The grounding
filter (`verify.ground_evidence`) trusts OpenRouter's `url_citation` annotations as
its allowlist for model-emitted evidence URLs. Some models only emit those
annotations when the web plugin is the *only* thing shaping the response — add
`response_format: json_schema` (which `llm.complete` always sends) and they go
silent, with no error, no matter how many web results were used. `openai/gpt-5-mini`
does exactly this: annotations with the web plugin alone, none with json_schema also
set. Since `verify.py` needs both at once, that combination made the filter inert —
every emitted URL was kept, unchecked. This was only caught by an end-to-end run
against a real article; every unit test stubs the LLM, so no test can see it.

Before changing `RESEARCH_MODEL`, confirm the candidate returns annotations under
this exact combination (costs one cheap API call):

```bash
cd claims && uv run python -c "
import config, llm
from pydantic import BaseModel

class Answer(BaseModel):
    answer: str

r = llm.complete(model='the/candidate-model', system='Answer using web search.',
                  user='What is the capital of France?', schema_name='answer',
                  schema=Answer, web=True)
print('had_annotations:', r.had_annotations, 'citation_urls:', r.citation_urls)
"
```

`had_annotations: True` with a non-empty `citation_urls` means the filter has
something to check against. `had_annotations: False` means it does not, silently.
Verified 2026-07-27: `google/gemini-2.5-flash` (in use), `anthropic/claude-sonnet-4.5`,
and `perplexity/sonar` all pass; `openai/gpt-5-mini` fails.

`claims_reports` has no unique constraint on `document_id`. Sequential runs of
`report-documents` are idempotent by construction (`reported_at` gates the sweep), but
two concurrent runs could both read `reported_at IS NULL` for the same document before
either commits, and both would insert a report row. The compose deployment sets
`CLAIMS_REPORT_CRON` to `""`, so that path never fires two runs on its own — but under
`serve.py` with a real cron, nothing stops the next scheduled run from starting while
a slow previous run is still in flight (`REPORT_CRON_DEFAULT` is `*/20 * * * *` against
a `limit` of several concurrent flow runs), so the exposure is live wherever a
schedule is. Noted here rather than fixed with a schema change, which is out of scope
for this task.
