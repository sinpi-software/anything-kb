# Local Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the entire stack up locally with `docker compose up`, against a local Postgres-backed Prefect server, so a change can be made and seen without touching the k3s cluster.

**Architecture:** Every service runs in Docker Compose. Application source is bind-mounted with reload enabled, so Compose is the supervisor — `docker compose restart <service>` restarts one piece. Compose profiles split the backend core (default) from the two React Router frontends (`ui` profile). Prefect stores its state in a `prefect` database inside the same Postgres container the application uses, mirroring the cluster.

**Tech Stack:** Docker Compose, Postgres 16, Neo4j 5, Prefect 3 (`prefecthq/prefect:3-python3.12`), Python 3.12 + uv, Node 24 + pnpm 10, React Router 8 / Vite.

**Spec:** `docs/superpowers/specs/2026-07-26-local-stack-design.md`

## Global Constraints

- **The design doc is the source of truth.** Read it before starting. Where this plan and the spec disagree, the spec wins and the plan is wrong.
- **Nothing in `deploy/` may be touched.** Not `deploy.sh`, not `__main__.py`, not the Pulumi config. The k3s deployment path is out of scope.
- **Flow logic may not change.** Only packaging, wiring, and where things run. The one exception is the `_schedules` helper in Task 2, which is explicitly specified.
- **`env_file` order is `[.env.sample, .env.local, .env]` on every service that takes one — in that exact order.** See Task 4 for why; getting this backwards silently breaks LLM calls.
- **Every URL a container uses must be a Compose service name**, except `PREFECT_UI_API_URL`, which must be a host address because the browser resolves it.
- **Postgres credentials are `ingestion:ingestion`,** Neo4j is `neo4j:ingestion`, matching the existing `docker-compose.yml`.
- **Verify against the running stack.** The failures this work is exposed to — a shadowed venv, a name that resolves only in-network, a browser-side API URL — are invisible to unit tests. A step that says "load it in a browser" means load it in a browser.
- Existing commands that must keep working: `uv run pytest`, `uv run ruff check .`, `uv run mypy .` in both `ingestion/` and `neonews/`, run from the host.

---

### Task 1: Move the Python venvs out of `/app`

The images build their venv at `/app/.venv`. Bind-mounting source over `/app` would replace it with the host's `.venv` — and `ingestion/.venv` and `neonews/.venv` both exist on this machine, built against a host interpreter. Relocating the venv to `/opt/venv` makes the bind mount cover source and nothing else.

**Files:**
- Modify: `ingestion/Dockerfile:8-10` (the `ENV` block)
- Modify: `neonews/Dockerfile:8-10` (the `ENV` block)

**Interfaces:**
- Consumes: nothing.
- Produces: both images have their virtualenv at `/opt/venv`, with `/opt/venv/bin` first on `PATH`. Every later task's `command:` relies on `uv` and `uvicorn`/`python` resolving from there with `/app` bind-mounted over.

- [ ] **Step 1: Change the `ingestion` image's environment block**

In `ingestion/Dockerfile`, replace:

```dockerfile
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
```

with:

```dockerfile
# The venv lives outside /app so a bind mount over /app (local development,
# docker-compose.yml) covers source only and cannot shadow the dependencies.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
```

- [ ] **Step 2: Make the identical change to `neonews/Dockerfile`**

Replace the same three-line `ENV` block with the same five-line replacement shown in Step 1. The two Dockerfiles are deliberately parallel; keep them so.

- [ ] **Step 3: Build both images and verify the venv moved**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker build -q -t local-ingestion-check ./ingestion
docker build -q -t local-neonews-check ./neonews
docker run --rm local-ingestion-check sh -c "test -d /opt/venv && test ! -e /app/.venv && which uvicorn"
docker run --rm local-neonews-check   sh -c "test -d /opt/venv && test ! -e /app/.venv && which python"
```

Expected: both `docker run` commands exit 0 and print `/opt/venv/bin/uvicorn` and `/opt/venv/bin/python`. A non-zero exit means either the venv did not move or `/app/.venv` is still being created.

- [ ] **Step 4: Verify the image still runs its real entrypoint**

```bash
docker run --rm local-ingestion-check python -c "import fastapi, prefect, neo4j; print('ingestion deps ok')"
docker run --rm local-neonews-check   python -c "import prefect, sqlalchemy, httpx; print('neonews deps ok')"
```

Expected: `ingestion deps ok` and `neonews deps ok`. This is the check that catches a `PATH` typo — the import fails if `/opt/venv/bin` is not being used.

- [ ] **Step 5: Clean up the check images**

```bash
docker rmi local-ingestion-check local-neonews-check
```

- [ ] **Step 6: Commit**

```bash
git add ingestion/Dockerfile neonews/Dockerfile
git commit -m "build: move the python venvs to /opt/venv

A bind mount over /app for local development would otherwise replace the
image's venv with the host's. Functionally inert for the cluster images."
```

---

### Task 2: An empty cron registers a deployment with no schedule

Locally the neonews flows should appear in the Prefect UI but not fire — `poll-sources`, `ingest-items` and `draft-issue` all call OpenRouter, and a stack left running while editing would spend credits in the background. `serve.py` currently always constructs a `CronSchedule`, so it needs a way to express "none".

**Files:**
- Modify: `neonews/serve.py:34-42` (the `deployments` function)
- Test: `neonews/test_serve.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `serve._schedules(cron: str) -> list[CronSchedule]` — returns `[]` for an empty string, otherwise a one-element list. `serve.deployments()` keeps its existing signature `() -> list[Any]` and its four deployment names. Task 5 sets `NEONEWS_POLL_CRON=""` and siblings in Compose to reach the empty branch.

- [ ] **Step 1: Write the failing test**

`neonews/test_serve.py` currently imports only `serve`. Add `import pytest` above it — `mypy` runs with `strict = true`, so the `monkeypatch` parameter must be annotated:

```python
import pytest

import serve
```

Then append the test:

```python
def test_empty_cron_registers_the_deployment_with_no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local development registers the flows so they are visible and manually runnable
    in the Prefect UI, without them firing on their own — poll/ingest/draft each spend
    OpenRouter credits. An empty cron is how Compose asks for that."""
    monkeypatch.setattr(serve, "POLL_CRON", "")
    by_name = {d.name: d for d in serve.deployments()}

    assert by_name["poll-sources"].schedules == []
    # The others are untouched, so an empty cron is per-flow, not global.
    assert len(by_name["ingest-items"].schedules) == 1
    assert by_name["ingest-items"].schedules[0].schedule.cron == serve.INGEST_CRON
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/steve/Source/sinpi/anything_handwritten/neonews
uv run pytest test_serve.py::test_empty_cron_registers_the_deployment_with_no_schedule -v
```

Expected: FAIL. `CronSchedule(cron="")` raises a validation error, so the failure surfaces as an exception from `deployments()` rather than an assertion mismatch.

- [ ] **Step 3: Add the `_schedules` helper and use it**

In `neonews/serve.py`, add above `deployments`:

```python
def _schedules(cron: str) -> list[CronSchedule]:
    """Empty cron means register the deployment with no schedule — how local
    development gets the flows into the UI without them firing on their own."""
    return [CronSchedule(cron=cron, timezone=SCHEDULE_TZ)] if cron else []
```

`CronSchedule` is already imported at the top of `serve.py`; `Any` stays imported for `deployments`' own return type.

Then rewrite `deployments` to use it:

```python
def deployments() -> list[Any]:
    return [
        poll_sources.to_deployment(name="poll-sources", schedules=_schedules(POLL_CRON)),
        ingest_items.to_deployment(name="ingest-items", schedules=_schedules(INGEST_CRON)),
        check_jobs.to_deployment(name="check-jobs", schedules=_schedules(JOBS_CRON)),
        draft_issue.to_deployment(name="draft-issue", schedules=_schedules(DRAFT_CRON)),
    ]
```

- [ ] **Step 4: Run the new test to verify it passes**

```bash
uv run pytest test_serve.py::test_empty_cron_registers_the_deployment_with_no_schedule -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole neonews suite plus lint and types**

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Expected: all pass. `test_every_deployment_carries_its_configured_cron` must still pass — it reads the module-level constants, which are unchanged when no monkeypatch is applied.

- [ ] **Step 6: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add neonews/serve.py neonews/test_serve.py
git commit -m "feat(neonews): an empty cron registers a deployment unscheduled

Local development wants the flows visible and manually runnable in the
Prefect UI without them firing on their own, since poll/ingest/draft each
spend OpenRouter credits. The cluster sets real crons and is unaffected."
```

---

### Task 3: Infrastructure and a Postgres-backed Prefect

Replaces the ad-hoc `stupefied_kirch` Prefect container with a declared service. Prefect's server fails at startup against a database that does not exist, so a one-shot init service creates it first.

**Files:**
- Modify: `docker-compose.yml` (add `prefect-db-init` and `prefect`; leave `postgres`, `neo4j`, `schemaspy` and the existing volumes as they are)

**Interfaces:**
- Consumes: nothing.
- Produces: service names `postgres` (port 5432), `neo4j` (7687), `prefect` (4200), all resolvable on the default Compose network. `prefect` reports healthy only once `GET /api/health` succeeds, so later tasks can gate on `condition: service_healthy`.

- [ ] **Step 1: Free the ports the new services need**

The ad-hoc Prefect container holds `:4200`. A host uvicorn holds `:8000` and a host Vite dev server holds `:5173`; those matter from Task 4 on, but stop them now so nothing is half-migrated.

```bash
docker rm -f stupefied_kirch
```

Then stop any host `uvicorn` and `react-router dev`/`vite` processes by hand (Ctrl-C in their terminals, or `pkill -f 'uvicorn main:app'` and `pkill -f 'react-router dev'`). Confirm all three ports are free:

```bash
ss -ltn | grep -E ':(4200|8000|5173)' || echo "4200, 8000, 5173 all free"
```

Expected: `4200, 8000, 5173 all free`. Losing the ad-hoc container's run history is intended — `serve()` re-registers deployments on start.

- [ ] **Step 2: Add `prefect-db-init` to `docker-compose.yml`**

Insert after the `neo4j` service, before `schemaspy`:

```yaml
  # Prefect's server fails at startup against a database that does not exist, and
  # Compose has no equivalent of the cluster's init Job. Idempotent: the catalog
  # check makes repeated `up` runs safe.
  prefect-db-init:
    image: postgres:16
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      PGPASSWORD: ingestion
    entrypoint:
      - sh
      - -c
      - |
        psql -h postgres -U ingestion -d ingestion -tAc "SELECT 1 FROM pg_database WHERE datname='prefect'" | grep -q 1 \
          || psql -h postgres -U ingestion -d ingestion -c "CREATE DATABASE prefect"
    restart: "no"
```

- [ ] **Step 3: Add the `prefect` service**

Insert immediately after `prefect-db-init`:

```yaml
  prefect:
    image: prefecthq/prefect:3-python3.12
    container_name: ingestion-prefect
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      prefect-db-init:
        condition: service_completed_successfully
    environment:
      # Prefect 3 requires the asyncpg driver; the plain postgresql:// scheme
      # fails at server startup. asyncpg 0.31.0 ships in this image.
      PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://ingestion:ingestion@postgres:5432/prefect
      PREFECT_SERVER_API_HOST: 0.0.0.0
      # Browser-side. A Compose service name resolves only inside the network, so
      # the UI would load and every request it makes would fail.
      PREFECT_UI_API_URL: http://localhost:4200/api
    command: prefect server start --host 0.0.0.0
    ports:
      - "4200:4200"
    healthcheck:
      # The image has no curl or wget, so the check goes through Python.
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:4200/api/health')"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s
```

- [ ] **Step 4: Bring up the infrastructure and verify health**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose up -d postgres neo4j prefect
docker compose ps
```

Expected: `postgres`, `neo4j` and `prefect` all show `healthy`; `prefect-db-init` shows `exited (0)`. If `prefect` is `unhealthy`, read `docker compose logs prefect` — a `postgresql://` scheme instead of `postgresql+asyncpg://` is the usual cause.

- [ ] **Step 5: Verify the `prefect` database exists and the init is idempotent**

```bash
docker compose exec postgres psql -U ingestion -d ingestion -tAc \
  "SELECT datname FROM pg_database WHERE datname='prefect'"
docker compose up -d prefect-db-init
docker compose logs --no-log-prefix prefect-db-init | tail -3
```

Expected: the first command prints `prefect`. The second run must exit 0 without an "already exists" error — that is the idempotency check.

- [ ] **Step 6: Load the Prefect UI in a browser**

Open `http://localhost:4200` in a real browser. Expected: the dashboard renders and the deployments/flow-runs pages load without errors. This is the only check that catches a wrong `PREFECT_UI_API_URL`; `curl`ing the API will pass either way.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): run Prefect locally, backed by Postgres

Replaces an ad-hoc container that had no volume and no recorded way to be
recreated. A one-shot init service creates the prefect database, since the
server fails at startup without it. Mirrors the cluster's configuration."
```

---

### Task 4: The engine — migrate, API, worker

Adds the three `ingestion` services. This is also where the `env_file` ordering rule is established, so read Step 1 carefully.

**Files:**
- Modify: `docker-compose.yml` (add `ingestion-migrate`, `ingestion-api`, `ingestion-worker`)

**Interfaces:**
- Consumes: the `postgres`, `neo4j` and `prefect` services from Task 3; the `/opt/venv` layout from Task 1.
- Produces: service name `ingestion-api` on port 8000, reachable in-network as `http://ingestion-api:8000` and from the host as `http://localhost:8000`. Task 5 points `NEONEWS_ENGINE_URL` at the former; Task 6 points `INTERNAL_API_URL` there too.

- [ ] **Step 1: Understand the `env_file` ordering before writing it**

The three env files disagree, and Compose and `python-dotenv` resolve conflicts in **opposite** directions:

- `python-dotenv` is called with `override=False` in load order `.env`, `.env.local`, `.env.sample` — so **the first file to set a key wins**, making `.env` highest priority.
- Compose's `env_file` list is **last-wins**.

`.env` holds the real `INGESTION_OPENROUTER_API_KEY`; `.env.local` holds the literal placeholder `_insert_key_here_`. So `env_file: [.env, .env.local]` would hand every container the placeholder and every LLM call would fail against a garbage key.

Reversing the list reproduces dotenv's precedence exactly. Every service that takes an env file uses, verbatim:

```yaml
    env_file:
      - .env.sample
      - .env.local
      - .env
```

- [ ] **Step 2: Add the three engine services to `docker-compose.yml`**

Insert after the `prefect` service:

```yaml
  ingestion-migrate:
    build: ./ingestion
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env.sample
      - .env.local
      - .env
    environment:
      INGESTION_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
    volumes:
      - ./ingestion:/app
    # uv sync re-syncs /opt/venv against the bind-mounted lockfile, so a dependency
    # change is `docker compose restart`, never `docker compose build`.
    command: sh -c "uv sync --frozen --no-dev && exec alembic upgrade head"
    restart: "no"

  ingestion-api:
    build: ./ingestion
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      neo4j:
        condition: service_healthy
      ingestion-migrate:
        condition: service_completed_successfully
    env_file:
      - .env.sample
      - .env.local
      - .env
    environment:
      INGESTION_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
      INGESTION_NEO4J_URI: bolt://neo4j:7687
      INGESTION_NEO4J_USER: neo4j
      INGESTION_NEO4J_PASSWORD: ingestion
    ports:
      - "8000:8000"
    volumes:
      - ./ingestion:/app
    command: sh -c "uv sync --frozen --no-dev && exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

  ingestion-worker:
    build: ./ingestion
    restart: unless-stopped
    depends_on:
      neo4j:
        condition: service_healthy
      prefect:
        condition: service_healthy
      ingestion-migrate:
        condition: service_completed_successfully
    env_file:
      - .env.sample
      - .env.local
      - .env
    environment:
      INGESTION_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
      INGESTION_NEO4J_URI: bolt://neo4j:7687
      INGESTION_NEO4J_USER: neo4j
      INGESTION_NEO4J_PASSWORD: ingestion
      PREFECT_API_URL: http://prefect:4200/api
      # 15s rather than the cluster's 60s: the reason for 60 there is scheduler
      # exhaust on a shared 10Gi PVC, which a local volume `down -v` resets.
      INGESTION_DRAIN_INTERVAL_SECONDS: "15"
    volumes:
      - ./ingestion:/app
    command: sh -c "uv sync --frozen --no-dev && exec python serve_worker.py"
```

- [ ] **Step 3: Bring the engine up**

```bash
docker compose up -d ingestion-api ingestion-worker
docker compose ps
```

Expected: `ingestion-migrate` shows `exited (0)`; `ingestion-api` and `ingestion-worker` are running.

- [ ] **Step 4: Verify the API answers and the placeholder key did not win**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/docs
docker compose exec ingestion-api sh -c 'case "$INGESTION_OPENROUTER_API_KEY" in *insert_key_here*) echo "BROKEN: placeholder key won"; exit 1;; *) echo "env_file order correct";; esac'
```

Expected: `200`, then `env_file order correct`. The second check is the whole point of Step 1 — if it prints `BROKEN`, the `env_file` list is in the wrong order.

- [ ] **Step 5: Verify `drain-jobs` registered with Prefect**

```bash
docker compose logs ingestion-worker | tail -20
```

Expected: Prefect's serve banner listing the `drain-jobs` deployment. Then open `http://localhost:4200/deployments` in a browser: `drain-jobs` appears with a 15-second interval schedule.

- [ ] **Step 6: Verify reload works**

Add a blank line to `ingestion/main.py`, save, then:

```bash
docker compose logs --tail=5 ingestion-api
```

Expected: uvicorn logs a reload (`Reloading...` / `Application startup complete`). Remove the blank line afterwards. This confirms the bind mount is live and `watchfiles` (from `uvicorn[standard]`) is present in `/opt/venv`.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): run the engine locally with reload

env_file order is [.env.sample, .env.local, .env] — Compose is last-wins
where dotenv's override=False is first-wins, and .env.local holds a
placeholder OpenRouter key that would otherwise beat the real one in .env."
```

---

### Task 5: neonews — migrate and serve

Adds the two `neonews` services, with the four flows registered but unscheduled via the Task 2 helper.

**Files:**
- Modify: `docker-compose.yml` (add `neonews-migrate`, `neonews-serve`)

**Interfaces:**
- Consumes: `postgres` and `prefect` (Task 3), `ingestion-api` (Task 4), `serve._schedules` (Task 2).
- Produces: four Prefect deployments — `poll-sources`, `ingest-items`, `check-jobs`, `draft-issue` — registered with no schedule.

- [ ] **Step 1: Add both services to `docker-compose.yml`**

Insert after `ingestion-worker`:

```yaml
  neonews-migrate:
    build: ./neonews
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env.sample
      - .env.local
      - .env
    environment:
      NEONEWS_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
    volumes:
      - ./neonews:/app
    # Its own Alembic chain uses version_table="alembic_version_neonews", so the two
    # chains in this one database cannot stamp over each other.
    command: sh -c "uv sync --frozen --no-dev && exec alembic upgrade head"
    restart: "no"

  neonews-serve:
    build: ./neonews
    restart: unless-stopped
    depends_on:
      prefect:
        condition: service_healthy
      neonews-migrate:
        condition: service_completed_successfully
      ingestion-api:
        condition: service_started
    env_file:
      - .env.sample
      - .env.local
      - .env
    environment:
      NEONEWS_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
      NEONEWS_ENGINE_URL: http://ingestion-api:8000
      PREFECT_API_URL: http://prefect:4200/api
      # Empty cron means registered but unscheduled. poll/ingest/draft each call
      # OpenRouter, so a stack left running while editing would spend credits for
      # nothing. Trigger runs from the UI instead.
      NEONEWS_POLL_CRON: ""
      NEONEWS_INGEST_CRON: ""
      NEONEWS_JOBS_CRON: ""
      NEONEWS_DRAFT_CRON: ""
    volumes:
      - ./neonews:/app
    command: sh -c "uv sync --frozen --no-dev && exec python serve.py"
```

- [ ] **Step 2: Bring neonews up**

```bash
docker compose up -d neonews-serve
docker compose ps
```

Expected: `neonews-migrate` shows `exited (0)`; `neonews-serve` is running. If it crashloops, `docker compose logs neonews-serve` — an unreachable Prefect is the honest failure mode here, not a silent idle.

- [ ] **Step 3: Verify the `neonews_*` tables exist alongside the engine's**

```bash
docker compose exec postgres psql -U ingestion -d ingestion -c "\dt neonews_*"
docker compose exec postgres psql -U ingestion -d ingestion -tAc \
  "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'alembic_version%' ORDER BY 1"
```

Expected: the `neonews_items` and `neonews_issues` tables are listed, and the second command prints **both** `alembic_version` and `alembic_version_neonews` — confirming the two chains are independent.

- [ ] **Step 4: Verify all five deployments, and that four are unscheduled**

Open `http://localhost:4200/deployments` in a browser.

Expected: five deployments. `poll-sources`, `ingest-items`, `check-jobs` and `draft-issue` each show **no schedule**. `drain-jobs` shows a 15-second interval. If the four show crons, `NEONEWS_*_CRON` is not reaching the container — check for a conflicting value in `.env`, which outranks the Compose `environment:` block only if the block is missing the key.

- [ ] **Step 5: Verify a flow change is picked up by a restart**

`serve()` registers flows at import time and has no hot reload, so the restart path is the one that matters here. Temporarily rename a deployment in `neonews/serve.py` — change `name="check-jobs"` to `name="check-jobs-tmp"` — then:

```bash
docker compose restart neonews-serve
```

Expected: `check-jobs-tmp` appears at `http://localhost:4200/deployments` within a few seconds. Revert the rename and restart again, confirming `check-jobs` returns. Leave `serve.py` byte-identical to how Task 2 left it.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): run neonews locally, flows registered unscheduled

The four flows appear in the UI and are manually runnable, but do not fire
on their own — poll/ingest/draft each spend OpenRouter credits."
```

---

### Task 6: The two frontends, behind a `ui` profile

Both apps run stock `node:24-alpine`. Their existing Dockerfiles are production multi-stage builds that `pnpm prune --prod` and run `react-router-serve`; they cannot serve Vite, and forking them would add two files to maintain for no gain.

**Files:**
- Modify: `docker-compose.yml` (add `app`, `neonews-site`, and two named volumes)

**Interfaces:**
- Consumes: `ingestion-api` (Task 4), `postgres` (Task 3).
- Produces: the desk UI on `http://localhost:5173` and the reader on `http://localhost:5174`, both started only under `--profile ui`.

- [ ] **Step 1: Add both services to `docker-compose.yml`**

Insert after `neonews-serve`:

```yaml
  # `profiles` keeps these out of a bare `docker compose up`. Start them with:
  #   docker compose --profile ui up
  app:
    profiles: ["ui"]
    image: node:24-alpine
    working_dir: /app
    restart: unless-stopped
    depends_on:
      ingestion-api:
        condition: service_started
    environment:
      # auth.server.ts defaults this to the cluster-internal service name, which
      # resolves nowhere locally.
      INTERNAL_API_URL: http://ingestion-api:8000
    ports:
      - "5173:5173"
    volumes:
      - ./app:/app
      # pnpm writes a symlinked virtual store into node_modules/.pnpm, and those
      # symlinks do not survive a bind mount. A named volume shadows the host's.
      - app-node-modules:/app/node_modules
    command: sh -c "corepack enable && pnpm install --frozen-lockfile && exec pnpm dev --host 0.0.0.0 --port 5173"

  neonews-site:
    profiles: ["ui"]
    image: node:24-alpine
    working_dir: /app
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      # db.server.ts reads this and nothing sets it locally — Node does not load
      # the repo-root .env the way the Python entry points do.
      NEONEWS_POSTGRES_URL: postgresql://ingestion:ingestion@postgres:5432/ingestion
    ports:
      - "5174:5174"
    volumes:
      - ./neonews-site:/app
      - site-node-modules:/app/node_modules
    command: sh -c "corepack enable && pnpm install --frozen-lockfile && exec pnpm dev --host 0.0.0.0 --port 5174"
```

- [ ] **Step 2: Declare the two named volumes**

In the `volumes:` block at the bottom of `docker-compose.yml`, alongside `ingestion-pgdata` and `ingestion-neo4jdata`, add:

```yaml
  app-node-modules:
  site-node-modules:
```

- [ ] **Step 3: Verify a bare `up` still leaves the frontends out**

```bash
docker compose up -d
docker compose ps --services
```

Expected: `app` and `neonews-site` are **absent** from the list. That is the backend-only group.

- [ ] **Step 4: Start the frontends**

```bash
docker compose --profile ui up -d
docker compose logs -f app neonews-site
```

Expected: both log a Vite dev-server banner. The first start installs dependencies into the empty named volumes and takes a minute or two; later starts are seconds.

- [ ] **Step 5: Load both in a browser**

Open `http://localhost:5173` — the desk UI renders and its server-side calls reach `ingestion-api` (log in, or load a page that requires a session). Open `http://localhost:5174` — the reader renders its issue list from Postgres without a database error.

An `ECONNREFUSED` from the reader means `NEONEWS_POSTGRES_URL` is not reaching it; a cluster hostname in the desk UI's error output means `INTERNAL_API_URL` is not.

- [ ] **Step 6: Verify hot reload**

Edit a visible string in `neonews-site/app/components/site-header.tsx`, save, and watch the browser. Expected: the change appears without a manual refresh. Revert it afterwards.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): serve both frontends under a ui profile

Stock node:24-alpine with Vite — the existing Dockerfiles are production
builds that prune dev deps and cannot serve a dev server. node_modules sits
on a named volume because pnpm's symlinked store does not survive a mount."
```

---

### Task 7: End-to-end verification and documentation

The pipeline has never run in this configuration. This task drives it once, start to finish, and writes down what an operator needs.

**Files:**
- Modify: `.env.sample`
- Modify: `ingestion/README.md`
- Modify: `neonews/README.md`

**Interfaces:**
- Consumes: the full stack from Tasks 3–6.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Seed an API key and record it**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose up -d
docker compose exec ingestion-api python seed.py
```

Copy the printed key into `.env.local` as `NEONEWS_ENGINE_API_KEY=<key>`. While editing, also set `NEONEWS_OPENROUTER_API_KEY` — it is empty in every env file today, so drafting would fail without it; reuse the value of `INGESTION_OPENROUTER_API_KEY` from `.env`.

This points at the local Postgres and Neo4j, so it is a throwaway local knowledge base with no path to a real account's data.

```bash
docker compose restart neonews-serve
```

- [ ] **Step 2: Run the pipeline from the Prefect UI**

At `http://localhost:4200/deployments`, trigger a run of `poll-sources` and wait for it to complete. Then verify:

```bash
docker compose exec postgres psql -U ingestion -d ingestion -tAc "SELECT count(*) FROM neonews_items"
```

Expected: a non-zero count.

- [ ] **Step 3: Submit to the engine and let the worker drain it**

Trigger `ingest-items` in the UI. Then, without triggering anything else, wait for `drain-jobs` to pick the work up on its 15-second interval:

```bash
docker compose exec postgres psql -U ingestion -d ingestion -tAc \
  "SELECT status, count(*) FROM ingest_jobs GROUP BY status"
```

Expected: jobs reach `done` unaided. That the worker drains without a manual trigger is the check that `PREFECT_API_URL` and the interval schedule are both correct.

- [ ] **Step 4: Draft an issue and inspect its body**

Trigger `draft-issue` in the UI, then:

```bash
docker compose exec postgres psql -U ingestion -d ingestion -tAc \
  "SELECT left(body, 600) FROM neonews_issues ORDER BY id DESC LIMIT 1"
```

Expected: markdown whose citations name **real sources**, not `untitled`. `untitled` citations mean metadata is not surviving the process boundary — the specific defect this check exists to catch. Then confirm `http://localhost:5174` renders that issue.

- [ ] **Step 5: Confirm host-side tests still pass**

```bash
cd ingestion && uv run pytest && uv run ruff check . && uv run mypy . && cd ..
cd neonews   && uv run pytest && uv run ruff check . && uv run mypy . && cd ..
```

Expected: all pass. Postgres, Neo4j and Prefect keep their published host ports, so nothing about the host test path changed.

- [ ] **Step 6: Document the local variables in `.env.sample`**

Add to the `# --- neonews ---` section, replacing the existing empty `NEONEWS_ENGINE_API_KEY=` and `NEONEWS_OPENROUTER_API_KEY=` lines:

```bash
# Printed by: docker compose exec ingestion-api python seed.py
# Decides which knowledge base neonews ingests into and drafts from.
NEONEWS_ENGINE_API_KEY=
NEONEWS_OPENROUTER_API_KEY=

# --- local stack (docker-compose.yml) ---
# Compose sets every in-container URL itself, so nothing below is required.
# These are the knobs worth knowing about:
#   NEONEWS_{POLL,INGEST,JOBS,DRAFT}_CRON  empty means registered but unscheduled
#   INGESTION_DRAIN_INTERVAL_SECONDS       worker drain interval, 15s locally
#
# env files are read by Compose in the order [.env.sample, .env.local, .env],
# which is last-wins and reproduces python-dotenv's first-wins precedence over
# the same three files. Do not reorder: .env.local holds a placeholder
# OpenRouter key that would otherwise beat the real one in .env.
```

- [ ] **Step 7: Replace the Prerequisites and Run sections of `ingestion/README.md`**

Replace the `## Prerequisites` and `## Run` sections with:

````markdown
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
ingestion-worker` follows one. Editing a `.py` file reloads the API in place. A
dependency change needs only a restart — each service re-runs `uv sync --frozen` on
start — never a rebuild.

First run needs an API key: `docker compose exec ingestion-api python seed.py`.

To run against the host instead (Postgres, Neo4j and Prefect stay in Docker):

```bash
docker compose up -d postgres neo4j prefect
uv sync && uv run alembic upgrade head
uv run uvicorn main:app --reload   # HTTP API + GraphQL on :8000
uv run python worker.py            # separate process: drains ingest_jobs
```
````

- [ ] **Step 8: Update the Run section of `neonews/README.md`**

After the existing `## Run` code block and its "engine's own worker" note, replace the line `To run on a schedule, `uv run python serve.py` (needs `PREFECT_API_URL`).` with:

````markdown
To run on a schedule, `uv run python serve.py` (needs `PREFECT_API_URL`). An empty
`NEONEWS_*_CRON` registers that deployment with no schedule — how the local stack gets
the flows into the Prefect UI without them firing on their own, since poll, ingest and
draft each spend OpenRouter credits.

Easiest path is the whole stack in Docker, from the repo root:

```bash
docker compose up
```

neonews runs as `neonews-serve`, its four flows registered unscheduled. Trigger them from
http://localhost:4200/deployments. `docker compose restart neonews-serve` picks up a flow
change — `serve()` has no hot reload.
````

- [ ] **Step 9: Commit**

```bash
git add .env.sample ingestion/README.md neonews/README.md
git commit -m "docs: how to run the whole stack locally

Records the env_file ordering trap, the seed step, and that a dependency
change needs a restart rather than a rebuild."
```

- [ ] **Step 10: Confirm a cold start works end to end**

The real test of the whole plan — tear it down and bring it back:

```bash
docker compose --profile ui down
docker compose --profile ui up -d
docker compose ps
```

Expected: every service reaches healthy or running, both migrate services exit 0, and no manual intervention is needed. Note that `down` without `-v` preserves the volumes, so the seeded API key and the drafted issue survive; `docker compose down -v` is the full reset and would require re-running Step 1.

---

## Notes for the implementer

- **`docker compose down -v` destroys the local knowledge base**, including the seeded API key. Use plain `down` unless a reset is what you want.
- **`neonews-serve` has no hot reload.** Prefect's `serve()` registers flows at import time; a flow change needs `docker compose restart neonews-serve`. This is expected, and is a large part of why Compose was chosen over a bare shell script.
- **If a service cannot find its dependencies after a `pyproject.toml` change**, restart it rather than rebuilding — `uv sync --frozen` runs on every start. If the lockfile itself changed, run `uv lock` on the host first, since `--frozen` refuses to update it.
- **Run history accumulates** in the local `prefect` database with no retention, exactly as in the cluster. `docker compose down -v` is the local reset.
