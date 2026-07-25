from datetime import UTC, datetime

import pytest

from sources import Item, canonicalize_url, parse_feed

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
    assert len(items) == 2


def test_parse_feed_prefers_guid_as_dedup_key() -> None:
    _, items = parse_feed(FEED)
    assert items[0].dedup_key == "tag:example.com,2026:a"


def test_parse_feed_falls_back_to_canonical_link_as_dedup_key() -> None:
    _, items = parse_feed(FEED)
    assert items[1].dedup_key == "https://example.com/b"


def test_parse_feed_strips_markup_from_titles() -> None:
    _, items = parse_feed(FEED)
    assert items[1].title == "Markup in a title"


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
