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
# so this is a latency knob, not a correctness one.
INTERVAL_SECONDS = int(os.environ.get("INGESTION_DRAIN_INTERVAL_SECONDS", "15"))

if __name__ == "__main__":
    # `to_deployment` is typed as returning RunnerDeployment | Coroutine[...] because
    # Flow is generic over sync/async; drain_jobs is sync, so this is always a
    # RunnerDeployment. neonews/serve.py hits the same Prefect typing gap and uses
    # the same Any escape hatch.
    deployment: Any = drain_jobs.to_deployment(name="drain-jobs", interval=timedelta(seconds=INTERVAL_SECONDS))
    serve(deployment)
