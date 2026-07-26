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
