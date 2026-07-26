"""Serve the engine's drain-jobs flow on an interval.

Run as a long-lived process (the ingestion-worker Deployment). `serve()` registers
the deployment and executes the runs it schedules — no work pool required.
"""

import os
from datetime import timedelta
from typing import Any

from prefect import serve

from worker import drain_jobs

# Seconds between drain passes. Overlapping runs are safe (FOR UPDATE SKIP LOCKED),
# so this is a latency knob, not a correctness one. Kept at 60s rather than lower:
# Prefect 3 ships no flow-run retention, so every run's state/task/log rows persist
# forever in the shared `prefect` database on the engine's single 10Gi local-path
# PVC. At 15s (5,760 runs/day) that's roughly 1.4GB/month of scheduler exhaust; at
# 60s it's a quarter of that. This is a single node with no HA — if that volume
# fills, Postgres stops and takes the engine and neonews down with it.
INTERVAL_SECONDS = int(os.environ.get("INGESTION_DRAIN_INTERVAL_SECONDS", "60"))

# Concurrent flow runs allowed. Each run is a spawned OS process that re-imports
# prefect + sqlalchemy + neo4j + httpx (~250MB RSS) inside a container capped at
# `memory: 1Gi` — three or four concurrent runs OOMKills the pod, and a SIGKILL
# skips pause_on_shutdown, so the scheduler keeps materializing runs and OOMs again
# on restart. Claiming is safe to overlap (FOR UPDATE SKIP LOCKED) but the process
# memory is not, so this stays at 1 to match the serial behaviour the old polling
# loop had. Named so it is tunable without hunting for the `serve()` call.
CONCURRENCY_LIMIT = 1

if __name__ == "__main__":
    # `to_deployment` is typed as returning RunnerDeployment | Coroutine[...] because
    # Flow is generic over sync/async; drain_jobs is sync, so this is always a
    # RunnerDeployment. neonews/serve.py hits the same Prefect typing gap and uses
    # the same Any escape hatch.
    deployment: Any = drain_jobs.to_deployment(name="drain-jobs", interval=timedelta(seconds=INTERVAL_SECONDS))
    serve(deployment, limit=CONCURRENCY_LIMIT)
