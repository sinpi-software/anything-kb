import pytest

import net_guard


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.0.202:8000/content",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http:///nohost",
    ],
)
def test_blocks_non_public_urls(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int | None, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        table = {
            "127.0.0.1": "127.0.0.1",
            "localhost": "127.0.0.1",
            "169.254.169.254": "169.254.169.254",
            "10.0.0.5": "10.0.0.5",
            "192.168.0.202": "192.168.0.202",
        }
        if host not in table:
            raise OSError("unresolvable")
        return [(2, 1, 6, "", (table[host], port or 80))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.assert_public_url(url)


def test_allows_a_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        net_guard.socket, "getaddrinfo", lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    assert net_guard.assert_public_url("https://example.com/feed.xml") == "https://example.com/feed.xml"


def test_blocks_when_any_dns_answer_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host resolving to both a public and a private address is a DNS-rebinding vector."""
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.assert_public_url("https://sneaky.example.com/feed.xml")
