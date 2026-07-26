# The whole stack, locally — Design

**Status:** design. Captured 2026-07-26.

**Goal:** bring every piece of the stack up locally with one command, against a local
Prefect server, so a change can be made and seen without touching the cluster.

Today the local story is partial and undocumented. `docker-compose.yml` holds Postgres
and Neo4j. Prefect runs as an ad-hoc container (`stupefied_kirch`) created by hand with no
volume, so its SQLite database lives in the container's writable layer and dies with it —
there is no recorded way to recreate it. The engine API, the engine worker, neonews, and
the two React Router apps are each started by hand, in separate terminals, from README
snippets. Nothing gates startup order, and two of the five processes are misconfigured for
local use out of the box.

The deployed counterpart is `docs/superpowers/specs/2026-07-26-prefect-and-neonews-in-k3s-design.md`.
This design does not change it.

## Shape

Everything runs in Docker Compose, application code included, with source bind-mounted and
reload enabled. Compose is the supervisor: `docker compose restart neonews-serve` restarts
one service, `docker compose logs -f` reads one service's output.

This was chosen over running the application processes natively on the host. Native
processes give a faster edit→effect loop — an in-process uvicorn reload rather than a
container reload, and Prefect flow runs as host subprocesses rather than container ones —
but need a supervisor, and this machine has none installed (no tmux, foreman, overmind,
just, or process-compose). Compose is already here and already understood. The loop cost is
accepted deliberately; if it proves too slow, moving the Python services back to the host is
a contained change, because the infrastructure services stay in Compose either way.

Compose profiles provide the two startup groups:

```
docker compose up                    # backends + Prefect
docker compose --profile ui up       # the above plus both frontends
```

Unprofiled services always start; `app` and `neonews-site` carry `profiles: [ui]`.

## Service graph

Arrows are `depends_on` conditions, not bare ordering.

```
postgres (healthy) ─┬─> prefect-db-init ──> prefect (healthy) ─┬─> ingestion-worker
                    │   CREATE DATABASE                        │   serve_worker.py
                    │   prefect (idempotent)                   └─> neonews-serve
                    │                                              serve.py
                    ├─> ingestion-migrate ──> ingestion-api ──────────┘
                    │   alembic (completed)   uvicorn --reload  :8000
                    │
                    └─> neonews-migrate ────> neonews-serve
                        alembic (completed)

neo4j (healthy) ────> ingestion-api, ingestion-worker

[profile ui]
app          :5173  ──> ingestion-api
neonews-site :5174  ──> postgres
```

Read as a list, because the diagram compresses one node's edges: `neonews-serve` waits on
three things — `prefect` healthy, `neonews-migrate` completed, and `ingestion-api` started,
since its `ingest-items` flow posts to the engine. `ingestion-worker` waits on `prefect`
healthy, `ingestion-migrate` completed, and `neo4j` healthy.

**Prefect's backing store is Postgres,** a `prefect` database inside the same Postgres
container the application uses, reached over `postgresql+asyncpg://`. This mirrors the
cluster exactly. SQLite in a named volume would have been simpler and would have kept
Prefect's write-heavy run history off the application database; matching the deployed
configuration was judged worth more, because a difference in the backing store is a
difference in exactly the layer whose failures are hardest to reproduce.

**`prefect-db-init`** is a one-shot `postgres:16` container running `CREATE DATABASE
prefect`, guarded by a `pg_database` catalog check so repeated `up` runs are safe. It exists
because the Prefect server fails at startup against a database that does not exist, and
Compose has no equivalent of the cluster's init Job.

**The two migrate services** are one-shot (`restart: "no"`). Their consumers depend on them
with `service_completed_successfully`, giving the same guarantee the cluster's migrate Jobs
give: nothing starts against an unmigrated schema. Both Alembic chains already use distinct
version tables (`alembic_version_neonews` for neonews), so sharing the `ingestion` database
is safe and is what the cluster does too.

**Ports currently in conflict.** `stupefied_kirch` holds `:4200`, a host uvicorn holds
`:8000`, and a host Vite dev server holds `:5173`. All three must be stopped before the
stack comes up. `docker rm -f stupefied_kirch` is part of the switchover; its local run
history goes with it, and nothing real is lost because `serve()` re-registers deployments
on start.

## Mounts

Bind-mounting source over the container's working directory shadows the dependencies
installed there at build time. This bites twice.

On the Python side, `ingestion/Dockerfile` and `neonews/Dockerfile` build a venv at
`/app/.venv`. Mounting `./ingestion:/app` replaces it with the host's `.venv` — and both
`ingestion/.venv` and `neonews/.venv` exist on this machine, built against a host
interpreter. The fix is `UV_PROJECT_ENVIRONMENT=/opt/venv` with `PATH=/opt/venv/bin:$PATH`
in both Dockerfiles: the venv moves out of `/app`, so the mount covers source and nothing
else. The change is functionally inert for the cluster images — it relocates a path.

On the Node side, pnpm writes a symlinked virtual store into `node_modules/.pnpm`, and
those symlinks do not survive being mounted into a container. `node_modules` therefore
lives on a named volume per app.

The conventional fix for both — an anonymous volume over the dependency directory — is
rejected. Anonymous volumes are reused across rebuilds, so after a dependency change the
container silently runs the previous dependency set. That is a worse failure than the one
it fixes, because it is invisible.

Instead each service re-syncs dependencies in its `command:` before exec'ing:

```yaml
ingestion-api:
  command: sh -c "uv sync --frozen --no-dev && exec uvicorn main:app --reload --host 0.0.0.0 --port 8000"
neonews-site:
  image: node:24-alpine
  command: sh -c "corepack enable && pnpm install --frozen-lockfile && pnpm dev --host 0.0.0.0 --port 5174"
```

Both are near-no-ops when already correct (one to two seconds), and they mean a dependency
change is `docker compose restart`, never `docker compose build`. Putting this in `command:`
rather than a Dockerfile `ENTRYPOINT` keeps the production images unchanged apart from the
venv path.

The Node services run stock `node:24-alpine`. `app/Dockerfile` and `neonews-site/Dockerfile`
are production multi-stage builds that `pnpm prune --prod` and run `react-router-serve`;
they cannot serve Vite, and forking them would add two files to maintain for no gain.

## Environment

In-container dotenv loading is already dead, and correctly so. Both `ingestion/main.py` and
`neonews/config.py` compute `_project_root` as `dirname(dirname(__file__))`, which resolves
to `/` when code lives at `/app`. They look for `/.env` and find nothing. The cluster
supplies environment via `envFrom`, so this never mattered there — and it means Compose must
supply environment the same way rather than relying on the mounted repo files.

Each service therefore takes `env_file: [.env, .env.local]` for secrets, plus an
`environment:` block overriding every URL. Compose resolves `environment` over `env_file`,
and `dotenv`'s `override=False` means a real environment variable wins over a file value
regardless, so no application code changes:

| Service | Override |
| --- | --- |
| `prefect` | `PREFECT_API_DATABASE_CONNECTION_URL=postgresql+asyncpg://ingestion:ingestion@postgres:5432/prefect` |
| `prefect` | `PREFECT_SERVER_API_HOST=0.0.0.0` |
| `prefect` | `PREFECT_UI_API_URL=http://localhost:4200/api` |
| `ingestion-api`, `ingestion-worker`, `ingestion-migrate` | `INGESTION_POSTGRES_URL=postgresql://ingestion:ingestion@postgres:5432/ingestion` |
| `ingestion-api`, `ingestion-worker` | `INGESTION_NEO4J_URI=bolt://neo4j:7687` |
| `ingestion-worker` | `PREFECT_API_URL=http://prefect:4200/api`, `INGESTION_DRAIN_INTERVAL_SECONDS=15` |
| `neonews-serve`, `neonews-migrate` | `NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@postgres:5432/ingestion` |
| `neonews-serve` | `NEONEWS_ENGINE_URL=http://ingestion-api:8000`, `PREFECT_API_URL=http://prefect:4200/api` |
| `app` | `INTERNAL_API_URL=http://ingestion-api:8000` |
| `neonews-site` | `NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@postgres:5432/ingestion` |

The last two fix live defects for local use. `app/app/lib/auth.server.ts:16` defaults
`INTERNAL_API_URL` to `http://ingestion-api.ingestion.svc.cluster.local:80`, a name that
resolves only inside the cluster. `neonews-site/app/lib/db.server.ts:17` reads
`process.env.NEONEWS_POSTGRES_URL`, which nothing sets locally — Node does not load the
repo-root `.env` the way the Python entry points do.

`PREFECT_UI_API_URL` is the one that fails silently. Set to `http://prefect:4200/api` the
server is healthy and the UI loads, and every request the browser makes fails, because that
name resolves only inside the Compose network. It must be the host address.

Postgres, Neo4j and Prefect keep their published host ports, so host-side `uv run pytest`
continues to work unchanged against `localhost`.

## Schedules

The neonews flows are registered but unscheduled locally. `poll-sources`, `ingest-items`
and `draft-issue` all call OpenRouter, and a stack left running while editing would spend
real credits in the background for no benefit. Runs are triggered from the Prefect UI or
`prefect deployment run` instead, which also makes the pipeline steppable — a property worth
having when the point is to watch one stage's output.

`serve.py` currently always constructs a `CronSchedule`, so it needs a way to express
"none":

```python
def _schedules(cron: str) -> list[CronSchedule]:
    """Empty cron means register the deployment with no schedule — how local
    development gets the flows into the UI without them firing on their own."""
    return [CronSchedule(cron=cron, timezone=SCHEDULE_TZ)] if cron else []
```

Compose sets `NEONEWS_POLL_CRON=""` and its three siblings on `neonews-serve` alone. The
cluster sets real crons and so takes the same code path it takes today; its behaviour is
unchanged.

`drain-jobs` keeps an interval schedule, shortened to 15 seconds locally. It costs nothing
until a job exists, and the cluster's reason for 60 seconds — scheduler exhaust accumulating
on a shared 10Gi PVC — does not apply to a local volume that `docker compose down -v` resets.

## Setup

One manual step, once: `docker compose exec ingestion-api python seed.py` prints an API key,
which goes into `.env.local` as `NEONEWS_ENGINE_API_KEY`.

This points at the local Postgres and Neo4j, so it is a throwaway local knowledge base with
no path to a real account's data.

## Changes

| File | Change |
| --- | --- |
| `docker-compose.yml` | Rewritten: 11 stack services, `ui` profile, healthchecks, `depends_on` conditions. The existing `schemaspy` service and its `tools` profile are retained unchanged |
| `ingestion/Dockerfile`, `neonews/Dockerfile` | `UV_PROJECT_ENVIRONMENT=/opt/venv`, adjusted `PATH` |
| `neonews/serve.py` | `_schedules` helper; empty cron means no schedule |
| `neonews/test_serve.py` | Case covering an empty cron |
| `.env.sample` | Document the local variables |
| `ingestion/README.md`, `neonews/README.md` | Document the local stack |

## Verification

Against the running stack. The failures this design is most exposed to — a browser-side API
URL that resolves only in-network, a shadowed venv, a service name typo — are all invisible
to unit tests.

1. `docker compose up` brings every service healthy; both migrate services exit 0.
2. The Prefect UI at `http://localhost:4200` loads **in a browser**, and lists five
   deployments: four neonews flows with no schedule, `drain-jobs` at 15 seconds.
3. Triggering `poll-sources` produces `neonews_items` rows.
4. Triggering `ingest-items` submits jobs, and `drain-jobs` carries them to `done` unaided.
5. Triggering `draft-issue` produces a `neonews_issues` row whose `body` holds markdown
   whose citations name real sources, not `untitled`.
6. `http://localhost:5174` renders that issue. `http://localhost:5173` loads the desk UI and
   authenticates through `ingestion-api`.
7. Editing a `.py` file reloads `ingestion-api`; editing a `.tsx` file hot-reloads;
   `docker compose restart neonews-serve` picks up a flow change.
8. Host-side `uv run pytest` still passes in both Python projects.

## Out of scope

- The k3s deploy path, `deploy/`, and `deploy.sh`.
- Flow logic. Only packaging, wiring, and where things run.
- Authenticating the local Prefect UI.
- Flow-run retention. History accumulates in the local `prefect` database exactly as it does
  in the cluster; `docker compose down -v` is the local reset.
