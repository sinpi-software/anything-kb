from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from cluster import Cluster
from write import Story, assemble_issue, cluster_brief, render_beat, write_story


def _cluster() -> Cluster:
    return Cluster(
        sources=(
            {"id": "job-1", "label": "County Gazette", "publishedAt": "2026-07-22T00:00:00", "entities": []},
            {"id": "job-2", "label": "Town Herald", "publishedAt": None, "entities": []},
        ),
        entities=(
            {
                "id": "e1",
                "name": "Ada Lovelace",
                "type": "Person",
                "summary": "A mathematician.",
                "article": "Long article body.",
            },
        ),
    )


def test_render_beat_substitutes_context() -> None:
    assert render_beat("Issue for {{ issue.date }}.", {"issue": {"date": "2026-07-25"}}) == "Issue for 2026-07-25."


def test_render_beat_leaves_missing_variables_empty_rather_than_raising() -> None:
    assert render_beat("Hello {{ nope.deeply.missing }}!", {}) == "Hello !"


def test_render_beat_blocks_sandbox_escapes() -> None:
    """The beat prompt is operator-authored text, rendered in a sandbox."""
    with pytest.raises(Exception):  # noqa: B017 - the sandbox may raise any of several exception types
        render_beat("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})


def test_cluster_brief_includes_entity_articles_and_source_labels() -> None:
    brief = cluster_brief(_cluster())
    assert "Ada Lovelace" in brief
    assert "Long article body." in brief
    assert "County Gazette" in brief
    assert "Town Herald" in brief


def test_write_story_returns_the_parsed_story() -> None:
    class FakeClient:
        class chat:
            @staticmethod
            def send(**kwargs: Any) -> Any:
                class Message:
                    content = '{"headline": "Council approves budget", "body": "The council approved it."}'

                class Choice:
                    message = Message()

                class Result:
                    choices: ClassVar[list[Any]] = [Choice()]

                return Result()

    story = write_story(FakeClient(), "Be plain-spoken.", _cluster())
    assert story.headline == "Council approves budget"
    assert story.body == "The council approved it."


def test_write_story_raises_when_the_model_returns_nothing() -> None:
    class EmptyClient:
        class chat:
            @staticmethod
            def send(**kwargs: Any) -> Any:
                class Message:
                    content = None

                class Choice:
                    message = Message()

                class Result:
                    choices: ClassVar[list[Any]] = [Choice()]

                return Result()

    with pytest.raises(ValueError):
        write_story(EmptyClient(), "beat", _cluster())


def test_assemble_issue_renders_headlines_bodies_and_citations() -> None:
    story = Story(headline="Council approves budget", body="The council approved it.")
    markdown = assemble_issue(
        [(story, _cluster())],
        generated_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        covers_since=datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
    )
    assert "# Issue — 2026-07-25" in markdown
    assert "## Council approves budget" in markdown
    assert "The council approved it." in markdown
    assert "County Gazette" in markdown
    assert "2026-07-22" in markdown


def test_assemble_issue_handles_a_source_with_no_date() -> None:
    story = Story(headline="H", body="B")
    markdown = assemble_issue(
        [(story, _cluster())],
        generated_at=datetime(2026, 7, 25, tzinfo=UTC),
        covers_since=datetime(2026, 7, 24, tzinfo=UTC),
    )
    assert "Town Herald" in markdown


def test_assemble_issue_with_no_stories_says_so() -> None:
    markdown = assemble_issue(
        [], generated_at=datetime(2026, 7, 25, tzinfo=UTC), covers_since=datetime(2026, 7, 24, tzinfo=UTC)
    )
    assert "No new stories" in markdown
