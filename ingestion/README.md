# Knowledge Graph Engine

Content in → per-org relevance filter → typed entity/relationship extraction → Neo4j graph, read over GraphQL.

## Prerequisites

- Copy `.env.sample` to `.env` and set `INGESTION_OPENROUTER_API_KEY`.

## Run

The whole stack, in Docker, with reload:

```bash
docker compose up                    # from the repo root: engine, worker, neonews, Prefect
docker compose --profile ui up       # the above plus both frontends
```

- engine API — http://localhost:8000
- Prefect UI — http://localhost:4200
- desk UI — http://localhost:5173, reader — http://localhost:5174

`docker compose restart ingestion-api` restarts one service; `docker compose logs -f
ingestion-worker` follows one. Editing a `.py` file reloads the API in place. Editing
`pyproject.toml` needs `uv lock` run on the host first — each service runs `uv sync
--frozen`, which refuses to update the lockfile itself — then a `restart` picks it up,
no rebuild needed. `restart` does not re-read `env_file` changes, though: editing
`.env.local` needs `docker compose up -d <service>` instead.

First run needs an API key: `docker compose exec ingestion-api python seed.py`. Paste
the key it prints into `.env.local` as `NEONEWS_ENGINE_API_KEY`.

To run against the host instead (Postgres, Neo4j and Prefect stay in Docker):

```bash
docker compose up -d postgres neo4j prefect
uv sync && uv run alembic upgrade head
uv run python seed.py              # creates the default org + config and prints an API key (once)
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
