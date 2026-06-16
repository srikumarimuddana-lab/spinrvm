"""C6: LIKE/ILIKE wildcard escaping in the repository $regex filter path.

A user-supplied `%` or `_` in a $regex search must be escaped so it matches
literally (no over-match / cheap scan), matching the $or sibling's escaping.
"""

from __future__ import annotations


class _FakeQuery:
    """Records the last like/ilike call and returns itself for chaining."""

    def __init__(self):
        self.like_calls = []
        self.ilike_calls = []

    def like(self, col, pattern):
        self.like_calls.append((col, pattern))
        return self

    def ilike(self, col, pattern):
        self.ilike_calls.append((col, pattern))
        return self


def test_escape_like_escapes_wildcards_and_escape_char():
    from backend.repositories._base import _escape_like

    # backslash first, then % and _
    assert _escape_like("50%_off") == r"50\%\_off"
    assert _escape_like(r"a\b") == r"a\\b"
    assert _escape_like("plain") == "plain"


def test_regex_filter_wraps_escaped_pattern_for_like():
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"name": {"$regex": "100%_match"}})
    assert q.like_calls == [("name", r"%100\%\_match%")]


def test_regex_filter_case_insensitive_uses_ilike_escaped():
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"name": {"$regex": "a_b", "$options": "i"}})
    assert q.ilike_calls == [("name", r"%a\_b%")]
