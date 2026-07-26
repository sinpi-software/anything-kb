# Prefect + neonews in k3s — Design

**Status:** design. Captured 2026-07-26.

**Goal:** run Prefect in the k3s cluster, run neonews there against it, persist drafted
issues in Postgres, and move the engine's ingestion worker onto Prefect as well.

Today the cluster runs the engine only — `ingestion-api`, `ingestion-worker`, `web`,
`postgres`, `neo4j`, `cloudflared`, all in the `ingestion` namespace on the single node
`skynet` (192.168.0.202). Prefect runs as a Docker container on the workstation, not in
the cluster. neonews is merged to `main` and CI-green but deployed nowhere: `deploy.sh`
builds only the engine and web images, so its source sits on the node inert.

This is a home lab. Single node, `local-path` volumes, no HA — that is a deliberate
constraint, not an oversight, and the design leans on it (one Postgres pod serves
several databases; a LAN-only service needs no auth layer).

## Pieces, in order

| | Piece | Depends on |
| --- | --- | --- |
| **A** | Prefect server in the cluster | — |
| **B** | neonews deployed against it | A |
| **C** | Drafted issues stored in Postgres | — |
| **D** | Engine ingestion worker as a Prefect flow | A |

A is the foundation and ships independently. C is small and rides with B. D was
originally deferred because a concurrent workstream owned `ingestion/`; that work has
since been picked up and closed (`71d14fd`, plan landed at `335963d`), so D is in scope.

## A · Prefect server

A `prefect` Deployment in the `ingestion` namespace, adapted from the working prior art
at `~/Source/anything_blog/iac/prefect.py`:

- image `prefecthq/prefect:3-python3.12`, command `prefect server start --host 0.0.0.0`
- `strategy: Recreate` — the server is a singleton over one database, and two replicas
  sharing it is the failure this strategy exists to prevent
- readiness probe `GET /api/health` on 4200
- `PREFECT_SERVER_API_HOST=0.0.0.0`

**Storage.** A second database, `prefect`, inside the existing `postgres` pod, created by
a small idempotent init Job (`CREATE DATABASE prefect` guarded by a `pg_database` catalog
check, so re-running `pulumi up` is safe). Prefect 3 requires the asyncpg driver, so the
connection URL is `postgresql+asyncpg://ingestion:<pw>@postgres:5432/prefect` — the wrong
scheme here fails at server startup, which is worth stating because it is easy to miss.

**Exposure.** `Service type: LoadBalancer`, so k3s ServiceLB binds `:4200` on the node and
the UI is reachable at `http://192.168.0.202:4200` from the LAN and nowhere else. Prefect
ships no authentication, which is exactly why it is not going behind the Cloudflare tunnel
next to `desk.sinpi.software`. `PREFECT_UI_API_URL` must be set to that same host address:
the UI is browser-side, so a cluster-internal name like `http://prefect:4200/api` would
resolve inside the cluster and fail in the browser.

**Accepted risk.** Prefect's run history is write-heavy and will share the engine's
Postgres pod and its single 10Gi `local-path` PVC. If that volume fills, it takes the
engine down with it. Acceptable at home-lab scale; splitting Prefect onto its own pod and
volume later is a contained change.

## B · neonews in the cluster

**Image.** A `neonews/Dockerfile` mirroring `ingestion/Dockerfile`: `python:3.12-slim`, uv
from `ghcr.io/astral-sh/uv`, `uv sync --frozen --no-dev --no-install-project` on the
lockfiles as a cached layer, then the code, modules running top-level from `/app`. Built in
`deploy/deploy.sh` beside the engine and web images and pushed to the node's local
registry.

**Migration Job.** `alembic upgrade head` with `NEONEWS_POSTGRES_URL`, creating the
`neonews_*` tables in the engine's `ingestion` database. It gates the serve Deployment via
`depends_on`, exactly as the engine's `migrate` Job gates api/worker, so neonews can never
start against an unmigrated schema. Its own Alembic chain uses
`version_table="alembic_version_neonews"`, so the two chains in one database cannot stamp
over each other.

**Secret `neonews-secret`,** injected wholesale via `envFrom` — keys are exactly the env
var names `neonews/config.py` reads:

- `NEONEWS_POSTGRES_URL` → `postgresql://ingestion:<pw>@postgres:5432/ingestion`
- `NEONEWS_ENGINE_URL` → `http://ingestion-api` (in-cluster service, port 80)
- `NEONEWS_ENGINE_API_KEY` → from Pulumi config
- `NEONEWS_OPENROUTER_API_KEY` → from Pulumi config
- `PREFECT_API_URL` → `http://prefect:4200/api`

**Which knowledge base.** Deliberately not decided here. `NEONEWS_ENGINE_API_KEY` is a
Pulumi stack secret, so the target knowledge base is chosen when the secret is set. The key
determines both what neonews ingests into and what it drafts from.

**`neonews-serve` Deployment.** One replica, `Recreate`, running `python serve.py`, which
registers the four flows with their cron schedules and executes the runs they schedule — no
work pool or custom worker image needed. This is what makes runs visible in the UI that A
exposes. If Prefect is unreachable the pod crashloops rather than sitting idle, which is
the honest failure mode.

Schedules come from `serve.py`'s existing env-var-backed constants (`NEONEWS_POLL_CRON` and
siblings), already defaulted in `config.py`. They belong in code because `serve()`
reconciles deployments on restart and would overwrite a schedule set only in the UI.

## C · Issues in Postgres

`draft.py` writes each issue as a markdown file. A CronJob or a restarted pod loses it, so
`neonews_issues` gains a `body` column holding the rendered markdown, and `draft.py` writes
it there in the same transaction that records the row and advances the watermark.

The file write stays — it is how an issue is read during local development — but the
database is the durable copy. The watermark ordering is unchanged and load-bearing: the row
and the watermark commit only after the issue content exists, so a crash re-covers the
window rather than skipping it.

Needs an Alembic migration adding the column, nullable so existing rows are valid.

## D · Engine ingestion worker as a Prefect flow

`ingestion/worker.py` is a standalone loop: claim a batch with
`SELECT … FOR UPDATE SKIP LOCKED`, process each job, sleep, repeat. Prefect was removed
from this project on 2026-07-23 (`ce11ce4`) and this loop replaced it. Putting it back is a
deliberate reversal, reasonable now that Prefect is in the cluster for neonews anyway.

**Shape:** keep the claim-and-process logic exactly as it is, wrap one drain pass in a
`@flow`, and schedule it on a short interval. The `FOR UPDATE SKIP LOCKED` claim already
makes concurrent runs safe, so overlapping runs are harmless — which is what makes an
interval schedule appropriate rather than requiring a singleton.

The standalone `python worker.py` entry point stays runnable for local development; the
flow wraps it rather than replacing it.

**Deployment change:** `ingestion-worker` stops being a long-running Deployment and becomes
a flow registered on the engine's own serve process, or an interval deployment alongside
neonews's. Which of those is an implementation detail for the plan; the constraint is that
exactly one process registers engine deployments, so schedules cannot be reconciled twice.

## Verification

- **A:** `/api/health` returns 200 in-cluster; the UI loads at `http://192.168.0.202:4200`
  from the LAN; the `prefect` database exists and the init Job is idempotent across two
  `pulumi up` runs.
- **B:** the four deployments appear in the UI with their schedules; a manual
  `poll-sources` run produces a flow run there; the migration Job shows `Completed` and the
  `neonews_*` tables exist.
- **C:** a drafted issue produces a `neonews_issues` row whose `body` contains the markdown
  and whose citations name real sources — not `untitled`. That specific check is what caught
  a metadata-key defect locally that no unit test could see, because it only appears across
  the process boundary.
- **D:** submitted content reaches `done` with the long-running worker Deployment gone; the
  engine's 149 tests still pass.

Every piece is verified against the deployed cluster, not only locally. A skipped or
mocked check does not count here — the failures this design is most exposed to (a wrong
connection scheme, an unreachable UI API URL, a service name that resolves only inside the
cluster) are all invisible to unit tests.

## Out of scope

- Splitting Prefect onto its own Postgres pod and volume.
- Authenticating the Prefect UI, or exposing it beyond the LAN.
- Multi-node or HA anything.
- Changing neonews's flow logic. Only its packaging, its issue storage, and where it runs.
