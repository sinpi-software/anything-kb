"""Turning a cluster into a story, and stories into an issue.

The beat prompt is operator-authored, so it renders in a Jinja SandboxedEnvironment
with missing variables rendering empty rather than raising. Structured output uses
OpenRouter's json_schema mode, the same way ingestion/knowledge.py does.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from jinja2 import ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment
from prefect.concurrency.sync import concurrency
from pydantic import BaseModel

import config
from cluster import Cluster


class Story(BaseModel):
    headline: str
    body: str


_SYSTEM = (
    "You are a newsroom writer. From the material below — synthesized encyclopedia-style entries for "
    "the entities involved, and the sources that mentioned them — write ONE story for today's issue.\n"
    "Write only what the material supports; never speculate beyond it. Lead with what changed and who "
    "it affects. Two to five short paragraphs of plain markdown, no headings inside the body."
)


def _strict_schema(node: Any) -> Any:
    # OpenAI structured outputs require additionalProperties:false and every key required
    # on each object; pydantic's model_json_schema() emits neither.
    if isinstance(node, dict):
        node.pop("default", None)
        if node.get("type") == "object" and node.get("properties"):
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            _strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            _strict_schema(value)
    return node


def render_beat(beat: str, context: dict[str, Any]) -> str:
    """Render the operator's beat prompt. Sandboxed, because this text is untrusted
    input to a template engine; undefined variables render empty rather than raising."""
    env = SandboxedEnvironment(undefined=ChainableUndefined, autoescape=False)
    return env.from_string(beat).render(**context)


def _date_of(source: dict[str, Any]) -> str:
    raw = source.get("publishedAt") or source.get("ingestedAt") or ""
    return str(raw)[:10]


def cluster_brief(cluster: Cluster) -> str:
    """The material handed to the model: each entity's article, then the sources."""
    parts = []
    for entity in cluster.entities:
        parts.append(
            f"### {entity.get('name')} ({entity.get('type')})\n{entity.get('article') or entity.get('summary') or ''}"
        )
    citations = "\n".join(
        f"- {source.get('label') or 'untitled'}" + (f" ({date})" if (date := _date_of(source)) else "")
        for source in cluster.sources
    )
    return "\n\n".join(parts) + f"\n\n### Sources\n{citations}\n"


def write_story(client: Any, beat: str, cluster: Cluster) -> Story:
    """One LLM call per cluster. Bounded by a Prefect global concurrency limit acquired
    with strict=False, so an absent limit is a no-op rather than an error."""
    messages = [
        {"role": "system", "content": f"{_SYSTEM}\n\n{beat}".strip()},
        {"role": "user", "content": cluster_brief(cluster)},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "story", "strict": True, "schema": _strict_schema(Story.model_json_schema())},
    }
    with concurrency(config.LLM_CONCURRENCY_LIMIT, strict=False):
        result = client.chat.send(
            model=config.LLM_MODEL,
            messages=messages,
            timeout_ms=config.LLM_TIMEOUT_MS,
            response_format=response_format,
        )
    content = result.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model returned no content")
    return Story.model_validate(json.loads(content))


def assemble_issue(stories: list[tuple[Story, Cluster]], generated_at: datetime, covers_since: datetime) -> str:
    """The issue as markdown: a dated header, each story, and its citations."""
    lines = [
        f"# Issue — {generated_at.date().isoformat()}",
        "",
        f"*Covering {covers_since.date().isoformat()} to {generated_at.date().isoformat()}.*",
        "",
    ]
    if not stories:
        lines.append("No new stories in this window.")
        return "\n".join(lines) + "\n"
    for story, cluster in stories:
        lines += [f"## {story.headline}", "", story.body, "", "**Sources:**"]
        for source in cluster.sources:
            date = _date_of(source)
            label = source.get("label") or "untitled"
            lines.append(f"- {label}" + (f" — {date}" if date else ""))
        lines.append("")
    return "\n".join(lines) + "\n"
