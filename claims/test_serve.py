from serve import deployments


def test_every_flow_is_registered() -> None:
    names = {deployment.name for deployment in deployments()}
    assert names == {"submit-url", "extract-claims", "verify-claims", "report-documents"}


def test_submit_url_is_registered_without_a_schedule() -> None:
    """It takes a URL parameter — there is nothing for a cron to submit."""
    submit = next(deployment for deployment in deployments() if deployment.name == "submit-url")
    assert not submit.schedules
