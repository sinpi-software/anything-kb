# Knowledge Graph Engine

Content in → per-org relevance filter → typed entity/relationship extraction → Neo4j graph, read over GraphQL.

## Prerequisites

- Postgres + Neo4j: `docker compose up -d postgres neo4j` (from the repo root)
- Copy `.env.sample` to `.env` and set `INGESTION_OPENROUTER_API_KEY`.

## Setup

```bash
uv sync
uv run alembic upgrade head
uv run python seed.py   # creates the default org + config and prints an API key (once)
```

## Run

```bash
uv run uvicorn main:app --reload   # HTTP API + GraphQL on :8000
uv run python worker.py            # separate process: drains ingest_jobs
```

## API

All endpoints require `Authorization: Bearer <api_key>`.

- `POST /content` — `{ "text": "...", "metadata": { ... } }` → `202 { "job_id": "..." }`
- `GET /content/{job_id}` — job status + relevance_reason/error
- `PUT /config` — `{ "relevance_prompt", "entity_types": [...], "relationship_types": [...] }`
- `POST /graphql` — generic read schema:

```graphql
{ nodes(type: "Person", search: "ada", limit: 50) { id type name summary edges { type target { name } } } }
```

### Example curl calls

```bash
API_KEY="paste-the-key-seed.py-printed"

curl -X POST http://localhost:8000/content \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Ada Lovelace worked with Charles Babbage on the Analytical Engine.", "metadata": {"source": "example"}}'

curl http://localhost:8000/content/<job_id> \
  -H "Authorization: Bearer $API_KEY"

curl -X PUT http://localhost:8000/config \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"relevance_prompt": "Is this about technology?", "entity_types": ["Person", "Organization"], "relationship_types": ["WORKS_AT"]}'

curl -X POST http://localhost:8000/graphql \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "{ nodes(type: \"Person\", search: \"ada\", limit: 50) { id type name summary edges { type target { name } } } }"}'
```

## Test

```bash
uv run pytest        # Neo4j/Postgres-backed tests skip when those stores are unreachable
```
