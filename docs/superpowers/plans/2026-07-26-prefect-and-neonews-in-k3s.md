# Prefect + neonews in k3s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Prefect in the k3s cluster, run neonews there against it, persist drafted issues in Postgres, and move the engine's ingestion worker onto Prefect.

**Architecture:** Five tasks. C first (a self-contained code change with no infrastructure dependency), then A (Prefect server), then B (neonews packaged and deployed against it), then D (engine worker as a flow). All cluster resources are added to the single existing Pulumi program `deploy/__main__.py`, following the helpers already there (`meta()`, `db()`, `engine()`), and all images are built by `deploy/deploy.sh` into the node's local registry.

**Tech Stack:** Pulumi (Python) + `pulumi_kubernetes`, k3s on one node, Prefect 3, Postgres 16, uv, Docker.

**Spec:** `docs/superpowers/specs/2026-07-26-prefect-and-neonews-in-k3s-design.md`

## Global Constraints

- **This is a home lab: single node (`skynet`, 192.168.0.202), `local-path` volumes, no HA.** Prefect deliberately shares the engine's Postgres pod and PVC — that risk is accepted, not to be "fixed".
- **Prefect is LAN-only.** `Service type: LoadBalancer` (k3s ServiceLB binds `:4200` on the node). It must NOT go behind the Cloudflare tunnel or a Traefik ingress — Prefect ships no authentication.
- **Prefect 3 requires the asyncpg driver**: `postgresql+asyncpg://…`. The plain `postgresql://` scheme fails at server startup.
- **`PREFECT_UI_API_URL` must be the LAN address** (`http://192.168.0.202:4200/api`), not a cluster-internal name — the UI is browser-side.
- **The Prefect server is a singleton**: `strategy: Recreate`. Two replicas over one database is the failure that strategy prevents.
- Python `>=3.12`. Each Python project keeps its own uv project: `ingestion/` and `neonews/` never share dependencies.
- ruff `line-length = 120`, select `["E","F","I","UP","B","SIM","C4","RUF"]`, plus `ruff format`; mypy `strict = true`. CI enforces all of these for both projects.
- neonews tests run against their own `neonews_test` database and must never touch live data. No `TRUNCATE`, no bare table-wide `DELETE`/`UPDATE`, no mock answering for rows a test does not own.
- No test spends money: every OpenRouter and engine call is stubbed.
- **Verification happens against the real cluster.** The failure modes this work is most exposed to — a wrong driver scheme, an unreachable UI API URL, a service name that only resolves in-cluster — are invisible to unit tests. A skipped check is not a passing check.
- `KUBECONFIG=deploy/kubeconfig` for all `kubectl` commands. Never commit that file (already gitignored).

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `neonews/models.py` | `Issue.body` column | C |
| `neonews/alembic/versions/<rev>_issue_body.py` | Migration adding the column | C |
| `neonews/draft.py` | Write the markdown into `body` | C |
| `neonews/test_draft.py` | Prove the body is persisted | C |
| `deploy/__main__.py` | All cluster resources: Prefect, neonews, worker change | A, B, D |
| `deploy/deploy.sh` | Build + push the neonews image | B |
| `neonews/Dockerfile` | neonews image (mirrors `ingestion/Dockerfile`) | B |
| `neonews/.dockerignore` | Keep the build context small | B |
| `ingestion/worker.py` | `drain_jobs` flow wrapping the existing pass | D |
| `ingestion/test_worker.py` | Prove the flow drains and is safe concurrently | D |

---

### Task 1 (piece C): Persist drafted issues in Postgres

**Files:**
- Modify: `neonews/models.py` (the `Issue` class)
- Create: `neonews/alembic/versions/<generated>_issue_body.py`
- Modify: `neonews/draft.py` (the `Issue(...)` construction)
- Test: `neonews/test_draft.py`

**Interfaces:**
- Consumes: `models.Issue`, `write.assemble_issue` (both already exist).
- Produces: `Issue.body: Mapped[str | None]` — later tasks don't depend on it, but the deployed `draft` flow relies on it to survive pod restarts.

- [ ] **Step 1: Write the failing test**

Add to `neonews/test_draft.py` (it already has the `output_dir` and watermark fixtures, and monkeypatches `draft.engine.recent_sources`, `draft._llm_client`, `draft.write_story` — follow the existing tests in that file for the exact fixture names):

Note: this file has **no** `@requires_postgres` decorator to copy — it calls
`_require_test_postgres()` once at module scope, which raises if `neonews_test` is
unreachable rather than skipping. Add your test undecorated, like the existing ones.

```python
def test_issue_body_is_persisted_to_postgres(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    """A CronJob pod's filesystem dies with it, so the markdown must live in the row.
    Without this, a deployed run leaves an Issue row pointing at a path nothing can read."""
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"])])
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))

    result = draft.draft_issue()

    on_disk = Path(result["path"]).read_text()
    with get_postgres_session() as s:
        row = s.query(Issue).order_by(Issue.generated_at.desc()).first()
        assert row is not None
        assert row.body is not None
        assert "## H" in row.body
        # The durable copy and the dev-convenience file must not diverge.
        assert row.body == on_disk
        s.query(Issue).filter(Issue.id == row.id).delete()
        s.commit()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd neonews && uv run pytest test_draft.py::test_issue_body_is_persisted_to_postgres -v
```

Expected: FAIL — `AttributeError` / `Issue` has no attribute `body`.

- [ ] **Step 3: Add the column to the model**

In `neonews/models.py`, in `class Issue`, below `path`:

```python
    # The rendered markdown. `path` is a dev convenience that dies with the pod;
    # this is the durable copy.
    body: Mapped[str | None] = mapped_column(TEXT, nullable=True)
```

- [ ] **Step 4: Generate and apply the migration**

```bash
cd neonews && uv run alembic revision --autogenerate -m "issue body" && uv run alembic upgrade head
```

Open the generated file in `alembic/versions/` and confirm it only adds `body` to `neonews_issues` — nothing else, and no engine tables. Nullable is required: existing rows have no body.

Then apply it to the test database too:

```bash
cd neonews && NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test uv run alembic upgrade head
```

- [ ] **Step 5: Write the markdown into the row**

In `neonews/draft.py`, in the `session.add(Issue(...))` call, add `body=markdown` alongside the existing fields:

```python
        session.add(
            Issue(
                generated_at=run_start,
                covers_since=since,
                path=str(path),
                story_count=len(stories),
                body=markdown,
            )
        )
```

Leave the file write and the watermark ordering exactly as they are. The row and the watermark still commit only after the content exists, which is what makes a crash re-cover the window instead of skipping it.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd neonews && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

Expected: all pass, 0 skipped. Report the real counts.

- [ ] **Step 7: Commit**

```bash
git add neonews/models.py neonews/draft.py neonews/test_draft.py neonews/alembic/versions/
git commit -m "feat(neonews): persist drafted issue markdown in Postgres"
```

---

### Task 2 (piece A): Prefect server in the cluster

**Files:**
- Modify: `deploy/__main__.py` (add after the `migrate` Job, before the `engine()` helper's uses)
- Modify: `deploy/README.md` (document the new stack config and the UI URL)

**Interfaces:**
- Consumes: `NS`, `meta()`, `cfg`, `pg_password`, `postgres_deploy`, `ns_opts` — all already defined in `deploy/__main__.py`.
- Produces: a `prefect` Service reachable in-cluster at `http://prefect:4200/api`, and on the LAN at `http://192.168.0.202:4200`. Task 3 and Task 5 consume that in-cluster URL.

- [ ] **Step 1: Add the Prefect database init Job**

In `deploy/__main__.py`, after the existing `migrate` Job, add:

```python
# --- prefect ---------------------------------------------------------------
# Prefect gets its own database inside the existing postgres pod (home lab: one
# server, several databases). Idempotent: `pulumi up` re-runs this Job, so the
# CREATE is guarded by a catalog check rather than relying on IF NOT EXISTS,
# which CREATE DATABASE does not support.
prefect_db_init = k8s.batch.v1.Job(
    "prefect-db-init",
    metadata=meta("prefect-db-init"),
    spec={
        "backoffLimit": 5,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "createdb",
                        "image": "postgres:16",
                        "command": ["sh", "-c"],
                        "args": [
                            'psql -h postgres -U ingestion -tc "SELECT 1 FROM pg_database WHERE datname=\'prefect\'" '
                            '| grep -q 1 || createdb -h postgres -U ingestion prefect'
                        ],
                        "env": [
                            {
                                "name": "PGPASSWORD",
                                "valueFrom": {"secretKeyRef": {"name": "db-secret", "key": "POSTGRES_PASSWORD"}},
                            }
                        ],
                    }
                ],
            }
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres_deploy, db_secret]),
)
```

- [ ] **Step 2: Add the Prefect secret, Deployment, and Service**

Immediately below, still in `deploy/__main__.py`:

```python
# The LAN address the browser-side UI must call. Prefect's UI runs in the browser,
# so a cluster-internal name (http://prefect:4200/api) would resolve inside the
# cluster and fail from a laptop.
prefect_lan_url = cfg.get("prefectLanUrl") or "http://192.168.0.202:4200"

prefect_secret = k8s.core.v1.Secret(
    "prefect-secret",
    metadata=meta("prefect-secret"),
    string_data={
        # Prefect 3 requires the asyncpg driver; plain postgresql:// fails at startup.
        "PREFECT_API_DATABASE_CONNECTION_URL": pulumi.Output.concat(
            "postgresql+asyncpg://ingestion:", pg_password, "@postgres:5432/prefect"
        ),
    },
    opts=ns_opts,
)

prefect_deploy = k8s.apps.v1.Deployment(
    "prefect",
    metadata=meta("prefect"),
    spec={
        "replicas": 1,
        # Singleton over one database — never two servers at once.
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": "prefect"}},
        "template": {
            "metadata": {"labels": {"app": "prefect"}},
            "spec": {
                "containers": [
                    {
                        "name": "prefect",
                        "image": "prefecthq/prefect:3-python3.12",
                        "command": ["prefect", "server", "start", "--host", "0.0.0.0"],
                        "ports": [{"containerPort": 4200}],
                        "env": [
                            {"name": "PREFECT_SERVER_API_HOST", "value": "0.0.0.0"},
                            {"name": "PREFECT_API_URL", "value": pulumi.Output.concat(prefect_lan_url, "/api")},
                            {"name": "PREFECT_UI_API_URL", "value": pulumi.Output.concat(prefect_lan_url, "/api")},
                            {
                                "name": "PREFECT_API_DATABASE_CONNECTION_URL",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": "prefect-secret",
                                        "key": "PREFECT_API_DATABASE_CONNECTION_URL",
                                    }
                                },
                            },
                        ],
                        "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"memory": "2Gi"}},
                        "readinessProbe": {
                            "httpGet": {"path": "/api/health", "port": 4200},
                            "initialDelaySeconds": 15,
                            "periodSeconds": 10,
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[prefect_db_init, prefect_secret]),
)

# LoadBalancer so k3s ServiceLB binds :4200 on the node — LAN only. Prefect has no
# auth of its own, so it must NOT be exposed via the tunnel or a Traefik ingress.
prefect_svc = k8s.core.v1.Service(
    "prefect",
    metadata=meta("prefect"),
    spec={
        "type": "LoadBalancer",
        "selector": {"app": "prefect"},
        "ports": [{"port": 4200, "targetPort": 4200}],
    },
    opts=pulumi.ResourceOptions(depends_on=[prefect_deploy]),
)

pulumi.export("prefect_ui", prefect_lan_url)
```

- [ ] **Step 3: Deploy it**

```bash
cd deploy && KUBECONFIG=./kubeconfig ./venv/bin/pulumi up --yes --stack home
```

If `pulumi` isn't on PATH, use `~/.pulumi/bin/pulumi`. The stack passphrase is read from `deploy/.passphrase` — do not print it.

- [ ] **Step 4: Verify against the real cluster**

```bash
export KUBECONFIG=deploy/kubeconfig
kubectl get job prefect-db-init -n ingestion          # Completions 1/1
kubectl get pods -n ingestion | grep prefect          # Running, 1/1 READY
kubectl logs -n ingestion deploy/prefect --tail=20    # no asyncpg / connection errors
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.202:4200/api/health   # 200
```

Expected: the Job completed, the pod is READY (its readiness probe gates on `/api/health`, so READY means the server really answered), and the LAN URL returns 200.

If the pod crashloops with a database error, check the driver scheme in the secret — `postgresql+asyncpg://`, not `postgresql://`.

- [ ] **Step 5: Verify the init Job is genuinely idempotent**

```bash
cd deploy && KUBECONFIG=./kubeconfig ./venv/bin/pulumi up --yes --stack home
kubectl logs -n ingestion job/prefect-db-init --tail=5
```

Expected: a second `pulumi up` succeeds and does not error on "database already exists". This is the property the catalog check exists for; a plain `createdb` would fail here.

- [ ] **Step 6: Open the UI**

Load `http://192.168.0.202:4200` in a browser. Expected: the Prefect dashboard renders with no console errors about an unreachable API. A UI that loads but shows "Can't connect to Server API" means `PREFECT_UI_API_URL` is wrong.

- [ ] **Step 7: Document and commit**

Add to `deploy/README.md` under the config section:

```markdown
- `prefectLanUrl` (optional) — LAN address of the Prefect UI/API, default
  `http://192.168.0.202:4200`. Used for both `PREFECT_API_URL` and
  `PREFECT_UI_API_URL`; the UI is browser-side, so this must be an address your
  browser can reach, not the in-cluster service name. Prefect has no auth — keep
  this on the LAN, never behind the tunnel.
```

```bash
git add deploy/__main__.py deploy/README.md
git commit -m "feat(deploy): Prefect server in k3s, LAN-only, own database"
```

---

### Task 3 (piece B, part 1): neonews image

**Files:**
- Create: `neonews/Dockerfile`
- Create: `neonews/.dockerignore`
- Modify: `deploy/deploy.sh`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: image `localhost:5000/anything-neonews:<tag>`, referenced by Task 4 as the Pulumi config value `neonewsImage`.

- [ ] **Step 1: Write the Dockerfile**

`neonews/Dockerfile` — mirrors `ingestion/Dockerfile`; read that file first and keep the structure identical:

```dockerfile
# neonews image. One image serves every role (serve / the four flows / migrate) —
# callers override the command. Build target: linux/amd64.
FROM python:3.12-slim

# uv for fast, locked installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependency layer — cached on the lockfiles. The project itself is not a
# package (no build-system), so --no-install-project installs only deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code (modules run top-level from /app).
COPY . .

CMD ["python", "serve.py"]
```

- [ ] **Step 2: Write the .dockerignore**

`neonews/.dockerignore` (check `ingestion/.dockerignore` and match it, adding neonews' own outputs):

```
.venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.mypy_cache
issues
drop
```

`issues` and `drop` are local run artifacts — they must not enter the image.

- [ ] **Step 3: Build it locally to verify**

```bash
cd /home/steve/Source/sinpi/anything_handwritten && docker build -t anything-neonews:test neonews
docker run --rm anything-neonews:test python -c "import config, poll, ingest, jobs, draft, serve; print('imports ok')"
```

Expected: the build succeeds and prints `imports ok`. This catches a missing dependency or a module that can't import without env vars — `serve.py` imports all four flows, so this exercises the whole package.

- [ ] **Step 4: Add the build to deploy.sh**

In `deploy/deploy.sh`, after the web image build and before `pulumi up`:

```bash
echo ">> build + push localhost:5000/anything-neonews:$TAG"
docker build -q -t "localhost:5000/anything-neonews:$TAG" "$HERE/../neonews" >/dev/null
docker push "localhost:5000/anything-neonews:$TAG" >/dev/null
```

And in the `pulumi config set` block, alongside the existing two:

```bash
pulumi config set neonewsImage "localhost:5000/anything-neonews:$TAG"
```

- [ ] **Step 5: Commit**

```bash
git add neonews/Dockerfile neonews/.dockerignore deploy/deploy.sh
git commit -m "feat(neonews): container image, built and pushed by deploy.sh"
```

---

### Task 4 (piece B, part 2): neonews workloads in the cluster

**Files:**
- Modify: `deploy/__main__.py` (after the Prefect resources from Task 2)
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: `prefect_svc` and `prefect_deploy` (Task 2), the `neonewsImage` config value (Task 3), plus the existing `NS`, `meta()`, `cfg`, `pg_password`, `postgres_deploy`.
- Produces: a `neonews-serve` Deployment registering the four flow deployments on the Prefect server.

- [ ] **Step 1: Add config, secret, and the migration Job**

In `deploy/__main__.py`, after the Prefect Service:

```python
# --- neonews (the automated newsroom; an external consumer of the engine API) ---
neonews_image = cfg.require("neonewsImage")  # e.g. localhost:5000/anything-neonews:<tag>
# Which knowledge base neonews reads and writes is decided by WHICH key you set here.
neonews_engine_api_key = cfg.require_secret("neonewsEngineApiKey")

# Keys are exactly the env var names neonews/config.py reads.
neonews_secret = k8s.core.v1.Secret(
    "neonews-secret",
    metadata=meta("neonews-secret"),
    string_data={
        "NEONEWS_POSTGRES_URL": pulumi.Output.concat(
            "postgresql://ingestion:", pg_password, "@postgres:5432/ingestion"
        ),
        "NEONEWS_ENGINE_URL": "http://ingestion-api",
        "NEONEWS_ENGINE_API_KEY": neonews_engine_api_key,
        "NEONEWS_OPENROUTER_API_KEY": openrouter_key,
        "PREFECT_API_URL": "http://prefect:4200/api",
    },
    opts=ns_opts,
)

# neonews owns its own Alembic chain (version_table alembic_version_neonews) in the
# same database, so this cannot collide with the engine's migrate Job.
neonews_migrate = k8s.batch.v1.Job(
    "neonews-migrate",
    metadata=meta("neonews-migrate"),
    spec={
        "backoffLimit": 5,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "migrate",
                        "image": neonews_image,
                        "imagePullPolicy": "Always",
                        "command": ["alembic", "upgrade", "head"],
                        "envFrom": [{"secretRef": {"name": "neonews-secret"}}],
                    }
                ],
            }
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres_deploy, neonews_secret]),
)
```

- [ ] **Step 2: Add the serve Deployment**

```python
# serve.py registers the four flow deployments and executes the runs they schedule —
# no work pool or custom worker image needed. Gated on the migration so neonews can
# never start against an unmigrated schema, and on Prefect so registration has an API
# to talk to. If Prefect is unreachable the pod crashloops, which is the honest failure.
neonews_serve = k8s.apps.v1.Deployment(
    "neonews-serve",
    metadata=meta("neonews-serve"),
    spec={
        "replicas": 1,
        # Exactly one process may register these deployments; two would reconcile
        # the same schedules against each other.
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": "neonews-serve"}},
        "template": {
            "metadata": {"labels": {"app": "neonews-serve"}},
            "spec": {
                "containers": [
                    {
                        "name": "neonews-serve",
                        "image": neonews_image,
                        "imagePullPolicy": "Always",
                        "command": ["python", "serve.py"],
                        "envFrom": [{"secretRef": {"name": "neonews-secret"}}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {"memory": "1Gi"},
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[neonews_migrate, prefect_deploy, api_svc]),
)
```

Note `api_svc` in `depends_on`: neonews calls the engine at `http://ingestion-api`, so that Service must exist first. It is already defined earlier in the file.

- [ ] **Step 3: Set the stack secret and deploy**

The API key determines which knowledge base neonews uses. Create a key for the knowledge base you want (or mint a fresh KB first), then:

```bash
cd deploy && KUBECONFIG=./kubeconfig ./venv/bin/pulumi config set --secret neonewsEngineApiKey '<the-key>' --stack home
cd .. && ssh node "cd ~/anything-kb/deploy && ./deploy.sh home manual-$(date +%s)"
```

If deploying from this workstation instead of the node, run `deploy/deploy.sh` locally — it builds the images and runs `pulumi up` in one pass.

- [ ] **Step 4: Verify against the real cluster**

```bash
export KUBECONFIG=deploy/kubeconfig
kubectl get job neonews-migrate -n ingestion                    # Completions 1/1
kubectl get pods -n ingestion | grep neonews                    # neonews-serve Running
kubectl logs -n ingestion deploy/neonews-serve --tail=30        # the four deployments registered
```

Expected: the migration completed and the serve pod logs show it serving `poll-sources`, `ingest-items`, `check-jobs`, and `draft-issue`. A `CrashLoopBackOff` with a connection error to `http://prefect:4200/api` means Task 2's Service name doesn't match.

- [ ] **Step 5: Verify the deployments appear in the UI**

```bash
curl -s -X POST http://192.168.0.202:4200/api/deployments/filter -H 'Content-Type: application/json' -d '{}' \
  | python3 -c "import sys,json; [print(d['name']) for d in json.load(sys.stdin)]"
```

Expected: the four deployment names. Then load `http://192.168.0.202:4200/deployments` in a browser and confirm each shows its cron schedule.

- [ ] **Step 6: Trigger one run end-to-end**

```bash
kubectl exec -n ingestion deploy/neonews-serve -- python poll.py
```

Expected: it completes and reports how many sources it polled. Then confirm the run is recorded:

```bash
curl -s -X POST http://192.168.0.202:4200/api/flow_runs/filter -H 'Content-Type: application/json' \
  -d '{"limit":3,"sort":"START_TIME_DESC"}' \
  | python3 -c "import sys,json; [print(r['name'], r['state_type']) for r in json.load(sys.stdin)]"
```

Expected: a COMPLETED `poll-sources` run. This is the check that proves the whole chain — image, secret, database, and Prefect registration — actually works together.

- [ ] **Step 7: Document and commit**

Add to `deploy/README.md`:

```markdown
- `neonewsImage` (required) — set by `deploy.sh`, e.g. `localhost:5000/anything-neonews:<tag>`.
- `neonewsEngineApiKey` (required, secret) — an engine API key. **Which key you set
  decides which knowledge base neonews ingests into and drafts from.**
```

```bash
git add deploy/__main__.py deploy/README.md
git commit -m "feat(deploy): neonews migration Job and serve Deployment"
```

---

### Task 5 (piece D): Engine ingestion worker as a Prefect flow

**Files:**
- Modify: `ingestion/worker.py`
- Modify: `ingestion/pyproject.toml` (add the `prefect` dependency)
- Modify: `ingestion/test_worker.py`
- Modify: `deploy/__main__.py` (the `ingestion-worker` Deployment)

**Interfaces:**
- Consumes: `worker.run_once() -> int` and `worker.claim_pending_job_ids`, both already in `ingestion/worker.py`; the `prefect` Service from Task 2.
- Produces: `worker.drain_jobs` — a Prefect `@flow` returning `dict[str, int]` with a `processed` key.

- [ ] **Step 1: Write the failing test**

Add to `ingestion/test_worker.py` (read the file first — it already monkeypatches `worker.config` and `worker.time.sleep`, which are re-exported for exactly this purpose):

```python
def test_drain_jobs_flow_returns_the_processed_count(monkeypatch):
    """The flow wraps one drain pass. Prefect's `serve` schedules it on an interval;
    the FOR UPDATE SKIP LOCKED claim in run_once is what makes overlapping runs safe,
    so the flow needs no locking of its own."""
    monkeypatch.setattr(worker, "run_once", lambda: 3)
    monkeypatch.setattr(worker, "bootstrap_schema", lambda: None)
    assert worker.drain_jobs() == {"processed": 3}


def test_drain_jobs_flow_bootstraps_the_neo4j_schema(monkeypatch):
    """main() bootstrapped the schema before looping; the flow must too, or a fresh
    cluster's first run writes entities with no fulltext index behind them."""
    calls = []
    monkeypatch.setattr(worker, "run_once", lambda: 0)
    monkeypatch.setattr(worker, "bootstrap_schema", lambda: calls.append("bootstrap"))
    worker.drain_jobs()
    assert calls == ["bootstrap"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ingestion && uv run pytest test_worker.py -k drain_jobs -v
```

Expected: FAIL — `module 'worker' has no attribute 'drain_jobs'`.

- [ ] **Step 3: Add prefect to the engine's dependencies**

```bash
cd ingestion && uv add "prefect>=3.7.0"
```

This reverses part of `2730ddf` ("drop prefect and dead rss deps") deliberately — the spec records why.

- [ ] **Step 4: Add the flow**

In `ingestion/worker.py`, add `from prefect import flow` to the existing import block at
the top of the file (its imports are all at the top — there is no E402 situation here, so
do not add a `noqa`). Then, after `run_once` and before `main`:

```python
@flow(name="drain-jobs")
def drain_jobs() -> dict[str, int]:
    """One drain pass, as a Prefect flow. Scheduled on a short interval rather than
    run as a loop: `claim_pending_job_ids` claims with FOR UPDATE SKIP LOCKED, so two
    overlapping runs cannot claim the same job and no singleton guarantee is needed."""
    bootstrap_schema()
    return {"processed": run_once()}
```

Leave `main()` and the `if __name__ == "__main__":` block untouched — `python worker.py` stays runnable for local development, which several existing tests and the README rely on.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd ingestion && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

Expected: all pass (149 + 2 new). Report real counts; note any skips.

- [ ] **Step 6: Serve the flow from the cluster**

In `deploy/__main__.py`, replace the `worker_deploy = engine("ingestion-worker", ["python", "worker.py"])` line with a serve process that registers the flow on an interval:

```python
# The worker is now a Prefect flow on an interval instead of a bare polling loop.
# One process registers it, matching neonews-serve's single-registrar constraint.
worker_deploy = engine(
    "ingestion-worker",
    ["python", "serve_worker.py"],
    {"envFrom": [{"secretRef": {"name": "app-secret"}}, {"secretRef": {"name": "prefect-url-secret"}}]},
)
```

Add the small Prefect URL secret **up with the other secrets, before the `engine()` calls**
(the engine's `app-secret` is shared with the migrate Job, which must not point at Prefect).
Placement matters: `engine()` hardcodes its own `depends_on`, so it will not wait for a
secret defined below it — the pod would sit failing to mount until Pulumi caught up.

```python
prefect_url_secret = k8s.core.v1.Secret(
    "prefect-url-secret",
    metadata=meta("prefect-url-secret"),
    string_data={"PREFECT_API_URL": "http://prefect:4200/api"},
    opts=ns_opts,
)
```

And create `ingestion/serve_worker.py`:

```python
"""Serve the engine's drain-jobs flow on an interval.

Run as a long-lived process (the ingestion-worker Deployment). `serve()` registers
the deployment and executes the runs it schedules — no work pool required.
"""

import os
from datetime import timedelta

from prefect import serve

from worker import drain_jobs

# Seconds between drain passes. Overlapping runs are safe (FOR UPDATE SKIP LOCKED),
# so this is a latency knob, not a correctness one.
INTERVAL_SECONDS = int(os.environ.get("INGESTION_DRAIN_INTERVAL_SECONDS", "15"))

if __name__ == "__main__":
    serve(drain_jobs.to_deployment(name="drain-jobs", interval=timedelta(seconds=INTERVAL_SECONDS)))
```

Note `engine()` already sets `envFrom` for `app-secret`; passing `container_extra` with `envFrom` overrides it, which is why both secrets are listed.

- [ ] **Step 7: Deploy and verify against the real cluster**

```bash
cd deploy && KUBECONFIG=./kubeconfig ./venv/bin/pulumi up --yes --stack home
export KUBECONFIG=./kubeconfig
kubectl logs -n ingestion deploy/ingestion-worker --tail=20   # serving drain-jobs
```

Then prove ingestion still works end to end — submit content with an engine API key and confirm it reaches `done`:

```bash
kubectl exec -n ingestion deploy/postgres -- psql -U ingestion -d ingestion \
  -c "SELECT status, count(*) FROM ingest_jobs GROUP BY 1"
```

Expected: `drain-jobs` runs appear in the Prefect UI, and newly submitted jobs move to `done` rather than sitting at `pending`. A job stuck at `pending` means the flow isn't running; one stuck at `processing` means it claimed and died — check the worker logs.

- [ ] **Step 8: Commit**

```bash
git add ingestion/worker.py ingestion/serve_worker.py ingestion/test_worker.py ingestion/pyproject.toml ingestion/uv.lock deploy/__main__.py
git commit -m "feat(ingestion): drain jobs as a Prefect flow on an interval"
```

---

## Rollback

Every task is a Pulumi resource addition or a small code change, so rollback is
`git revert` plus `pulumi up`. Two specifics worth knowing:

- **Task 2's `prefect` database is not deleted by `pulumi destroy`** — the init Job created
  it inside the postgres pod, outside Pulumi's model. Drop it by hand if you want it gone:
  `kubectl exec -n ingestion deploy/postgres -- dropdb -U ingestion prefect`.
- **Task 5 changes how the engine drains jobs.** Reverting the `deploy/__main__.py` hunk
  restores the long-running `python worker.py` loop, which needs no Prefect at all — so the
  engine keeps working even if Prefect is removed entirely.

## Notes for the implementer

- **A skipped check is not a passing check.** Several verification steps hit the real
  cluster; if `kubectl` can't reach it, say so rather than reporting success.
- **Never print secrets.** `deploy/.passphrase`, the Pulumi stack secrets, and the engine
  API key stay out of logs and commit messages.
- **Don't put Prefect behind the tunnel.** It has no authentication. LAN-only is a
  deliberate constraint, not an oversight.
- The engine's tests skip Neo4j/Postgres-backed cases when those stores are unreachable —
  report skip counts explicitly so a skip-heavy run isn't mistaken for a clean one.
