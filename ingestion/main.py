import contextlib
import os

import dotenv

import config

# .env files live in the project root, one level up from this script's dir
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env.sample")

os.environ["PREFECT_API_URL"] = str(os.getenv("INGESTION_PREFECT_API_URL"))


def ensure_concurrency_limits() -> None:
    """Declare task concurrency limits in code so they never need to be set in the UI."""
    from prefect.client.orchestration import get_client
    from prefect.exceptions import ObjectNotFound

    with get_client(sync_client=True) as client:
        for name, limit in config.CONCURRENCY_LIMITS.items():
            # Drop the deprecated tag-based limit if present (its acquire path 500s); use v2 global.
            with contextlib.suppress(ObjectNotFound):
                client.delete_concurrency_limit_by_tag(name)
            client.upsert_global_concurrency_limit_by_name(name, limit)


def main() -> None:
    from prefect import serve
    from prefect.events import DeploymentEventTrigger

    from events import MARKDOWN_ARTIFACT_CREATED_EVENT
    from rss_feeds import rss_feed_flow
    from transformations import run_transform_pipeline

    ensure_concurrency_limits()

    # Ingestion runs on a schedule; the transform pipeline runs when a markdown artifact
    # is created — the trigger (and thus its automation) is declared here in code, not the UI.
    ingest = rss_feed_flow.to_deployment(name=config.FLOW_NAME, interval=config.POLL_INTERVAL_SECONDS)
    transform = run_transform_pipeline.to_deployment(
        name=config.TRANSFORM_PIPELINE_DEPLOYMENT_NAME,
        triggers=[
            DeploymentEventTrigger(
                expect={MARKDOWN_ARTIFACT_CREATED_EVENT},
                parameters={"input_artifact_id": "{{ event.payload.artifact_id }}"},
            )
        ],
    )
    serve(ingest, transform)  # type: ignore[arg-type]  # to_deployment returns RunnerDeployment in sync context


if __name__ == "__main__":
    main()
