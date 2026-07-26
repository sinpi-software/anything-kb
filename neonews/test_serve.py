import pytest

import serve


def test_every_flow_is_registered() -> None:
    names = {d.name for d in serve.deployments()}
    assert names == {"poll-sources", "ingest-items", "check-jobs", "draft-issue"}


def test_every_deployment_carries_its_configured_cron() -> None:
    """Schedules belong in code — serve() reconciles deployments on restart, so a
    schedule set only in the Prefect UI is overwritten on the next deploy. Assert the
    cron actually reaches the deployment, not merely that the constant is non-empty."""
    expected = {
        "poll-sources": serve.POLL_CRON,
        "ingest-items": serve.INGEST_CRON,
        "check-jobs": serve.JOBS_CRON,
        "draft-issue": serve.DRAFT_CRON,
    }
    for deployment in serve.deployments():
        schedules = deployment.schedules
        assert len(schedules) == 1, f"{deployment.name} should carry exactly one schedule"
        assert schedules[0].schedule.cron == expected[deployment.name]
        assert schedules[0].schedule.timezone == serve.SCHEDULE_TZ


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
