from datetime import UTC, datetime
from pathlib import Path

import pytest

from sources import Item, SourceSpec, canonicalize_url, fetch_items, load_config, parse_feed

TOML = """
[[sources]]
kind = "rss"
url = "https://example.com/feed.xml"
title = "Example News"

[[sources]]
kind = "files"
path = "./drop"

[editorial]
beat = "Write for readers of the county paper."
"""

FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example News</title>
  <item>
    <title>Council approves budget</title>
    <link>https://example.com/a?utm_source=rss&amp;id=2</link>
    <guid>tag:example.com,2026:a</guid>
    <description>The council approved it.</description>
    <pubDate>Wed, 22 Jul 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>&lt;a&gt;Markup&lt;/a&gt; in a title</title>
    <link>https://example.com/b/</link>
    <description>Body b.</description>
  </item>
  <item>
    <title>Mangled link falls back to guid</title>
    <link>&lt;a href="https://example.com/c"&gt;https://example.com/c&lt;/a&gt;</link>
    <guid>https://example.com/c-canonical</guid>
    <description>Body c.</description>
  </item>
</channel></rss>
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/a/?utm_source=rss&id=2", "https://example.com/a?id=2"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?fbclid=xyz", "https://example.com/a"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_parse_feed_returns_title_and_items() -> None:
    title, items = parse_feed(FEED)
    assert title == "Example News"
    assert len(items) == 3


def test_parse_feed_prefers_guid_as_dedup_key() -> None:
    _, items = parse_feed(FEED)
    assert items[0].dedup_key == "tag:example.com,2026:a"


def test_parse_feed_falls_back_to_canonical_link_as_dedup_key() -> None:
    _, items = parse_feed(FEED)
    assert items[1].dedup_key == "https://example.com/b"


def test_parse_feed_strips_markup_from_titles() -> None:
    _, items = parse_feed(FEED)
    assert items[1].title == "Markup in a title"


def test_parse_feed_uses_clean_link_as_url() -> None:
    _, items = parse_feed(FEED)
    assert items[0].url == "https://example.com/a?utm_source=rss&id=2"
    assert items[1].url == "https://example.com/b/"


def test_parse_feed_falls_back_to_guid_as_url_when_link_has_embedded_markup() -> None:
    _, items = parse_feed(FEED)
    assert items[2].url == "https://example.com/c-canonical"


def test_parse_feed_reads_published_at_as_utc() -> None:
    _, items = parse_feed(FEED)
    assert items[0].published_at == datetime(2026, 7, 22, 10, 0, tzinfo=UTC)


def test_parse_feed_leaves_published_at_none_when_absent() -> None:
    _, items = parse_feed(FEED)
    assert items[1].published_at is None


def test_parse_feed_skips_entries_with_no_usable_key() -> None:
    _, items = parse_feed(
        b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>x</title></item></channel></rss>'
    )
    assert items == []


def test_parse_feed_raises_on_unparseable_input() -> None:
    with pytest.raises(ValueError):
        parse_feed(b"this is not a feed at all")


def test_item_is_frozen() -> None:
    item = Item(dedup_key="k", title="t", text="x", url=None, published_at=None)
    with pytest.raises(Exception):  # noqa: B017 - only frozen-ness is asserted, not the exception type
        item.title = "changed"  # type: ignore[misc]


def test_load_config_returns_specs_and_beat(tmp_path: Path) -> None:
    path = tmp_path / "neonews.toml"
    path.write_text(TOML)
    specs, beat = load_config(path)
    assert specs == [
        SourceSpec(kind="rss", locator="https://example.com/feed.xml", title="Example News"),
        SourceSpec(kind="files", locator="./drop", title=None),
    ]
    assert beat == "Write for readers of the county paper."


def test_load_config_rejects_an_unknown_kind(tmp_path: Path) -> None:
    path = tmp_path / "neonews.toml"
    path.write_text('[[sources]]\nkind = "carrier-pigeon"\nurl = "x"\n')
    with pytest.raises(ValueError, match="carrier-pigeon"):
        load_config(path)


def test_load_config_defaults_beat_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "neonews.toml"
    path.write_text('[[sources]]\nkind = "files"\npath = "./drop"\n')
    _, beat = load_config(path)
    assert beat == ""


def test_files_adapter_reads_a_directory(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("First body.")
    (tmp_path / "two.md").write_text("Second body.")
    (tmp_path / "skip.bin").write_bytes(b"\x00\x01")
    title, items = fetch_items("files", str(tmp_path))
    assert title is None
    assert {i.title for i in items} == {"one.txt", "two.md"}
    assert {i.text for i in items} == {"First body.", "Second body."}


def test_files_adapter_dedup_key_changes_with_content(tmp_path: Path) -> None:
    """Editing a dropped file makes it a new item; re-polling an unchanged one does not."""
    path = tmp_path / "one.txt"
    path.write_text("First body.")
    _, before = fetch_items("files", str(tmp_path))
    _, again = fetch_items("files", str(tmp_path))
    path.write_text("Edited body.")
    _, after = fetch_items("files", str(tmp_path))
    assert before[0].dedup_key == again[0].dedup_key
    assert before[0].dedup_key != after[0].dedup_key


def test_files_adapter_on_a_missing_directory_returns_nothing(tmp_path: Path) -> None:
    title, items = fetch_items("files", str(tmp_path / "nope"))
    assert (title, items) == (None, [])


def test_fetch_items_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="carrier-pigeon"):
        fetch_items("carrier-pigeon", "x")


def test_rss_adapter_fetches_through_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, **_kw: object) -> bytes:
        calls.append(url)
        return FEED

    monkeypatch.setattr("sources.net_guard.fetch", fake_fetch)
    title, items = fetch_items("rss", "https://example.com/feed.xml")
    assert calls == ["https://example.com/feed.xml"]
    assert title == "Example News"
    assert len(items) == 3
