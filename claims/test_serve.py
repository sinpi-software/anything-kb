from serve import deployments


def test_every_flow_is_registered() -> None:
    names = {deployment.name for deployment in deployments()}
    assert names == {"submit-url", "extract-claims", "verify-claims", "report-documents"}


def test_submit_url_is_registered_without_a_schedule() -> None:
    """It takes a URL parameter — there is nothing for a cron to submit."""
    submit = next(deployment for deployment in deployments() if deployment.name == "submit-url")
    assert not submit.schedules


def test_the_three_sweeps_refuse_to_overlap_with_themselves() -> None:
    """Two overlapping extract-claims runs would duplicate every claim of a document
    (no unique constraint on claims_claims); two overlapping verify-claims runs would
    spend twice the LLM calls the batch size budgeted for. concurrency_limit=1 makes
    Prefect refuse to start a new run of one of these while a previous one is still
    in flight."""
    by_name = {deployment.name: deployment for deployment in deployments()}
    for name in ("extract-claims", "verify-claims", "report-documents"):
        assert by_name[name].concurrency_limit == 1


def test_submit_url_is_left_unlimited() -> None:
    """Parameterized and idempotent — nothing to protect it from."""
    submit = next(deployment for deployment in deployments() if deployment.name == "submit-url")
    assert submit.concurrency_limit is None
