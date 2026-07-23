from sanitize import sanitize


def test_strips_nul() -> None:
    assert sanitize("a\x00b") == "ab"


def test_strips_other_control_chars() -> None:
    assert sanitize("a\x07\x1fb") == "ab"


def test_keeps_tab_newline_cr() -> None:
    assert sanitize("a\tb\nc\rd") == "a\tb\nc\rd"


def test_leaves_clean_text_untouched() -> None:
    assert sanitize("hello world") == "hello world"
