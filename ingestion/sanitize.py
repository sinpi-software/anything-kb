"""Cleanup for text we persist — a home for storage/encoding gotchas.

Route all stored text through `sanitize` so callers stay simple. Add new rules
here as we hit them (lone surrogates, BOMs, etc.).
"""

import re

# Control characters that break Postgres text/jsonb (NUL is a hard error) or are
# just junk in fetched content. Tab (0x09), newline (0x0a), and CR (0x0d) are kept.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize(text: str) -> str:
    return _CONTROL_CHARS.sub("", text)
