"""Tests for validators.sanitize_string() — pure unit tests, no DB/external deps."""
import pytest
from fastapi import HTTPException
from validators import sanitize_string


@pytest.fixture(autouse=True)
def patch_external_dependencies():
    """Override the conftest autouse fixture — these tests need no mocking."""
    yield


# ── Happy path ──

class TestBasicSanitization:
    def test_normal_string(self):
        ok, val = sanitize_string("Hello world")
        assert ok is True
        assert val == "Hello world"

    def test_strips_whitespace_by_default(self):
        ok, val = sanitize_string("  hello  ")
        assert ok is True
        assert val == "hello"

    def test_preserves_whitespace_when_disabled(self):
        ok, val = sanitize_string("  hello  ", strip_whitespace=False)
        assert ok is True
        assert val == "  hello  "

    def test_non_string_converted(self):
        ok, val = sanitize_string(12345)
        assert ok is True
        assert val == "12345"


# ── HTML stripping ──

class TestHTMLStripping:
    def test_strips_html_by_default(self):
        ok, val = sanitize_string("<b>bold</b>")
        assert ok is True
        assert val == "bold"

    def test_strips_script_tags(self):
        ok, val = sanitize_string('<script>alert("xss")</script>')
        assert ok is True
        assert "<" not in val

    def test_strips_nested_html(self):
        ok, val = sanitize_string("<div><p>text</p></div>")
        assert ok is True
        assert val == "text"

    def test_allows_html_when_enabled(self):
        ok, val = sanitize_string("<b>bold</b>", allow_html=True)
        assert ok is True
        assert val == "<b>bold</b>"


# ── Length validation ──

class TestMaxLength:
    def test_within_limit(self):
        ok, val = sanitize_string("short", max_length=10)
        assert ok is True

    def test_at_exact_limit(self):
        ok, val = sanitize_string("x" * 100, max_length=100)
        assert ok is True
        assert len(val) == 100

    def test_exceeds_limit_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string("x" * 101, max_length=100)
        assert exc_info.value.status_code == 400
        assert "maximum length" in exc_info.value.detail

    def test_exceeds_limit_no_raise(self):
        ok, val = sanitize_string("x" * 101, max_length=100, raise_exception=False)
        assert ok is False
        assert val is None


# ── None / empty ──

class TestNoneAndEmpty:
    def test_none_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string(None)
        assert exc_info.value.status_code == 400

    def test_none_no_raise(self):
        ok, val = sanitize_string(None, raise_exception=False)
        assert ok is False
        assert val is None

    def test_empty_string_raises(self):
        with pytest.raises(HTTPException):
            sanitize_string("")

    def test_whitespace_only_raises(self):
        with pytest.raises(HTTPException):
            sanitize_string("   ")

    def test_empty_no_raise(self):
        ok, val = sanitize_string("", raise_exception=False)
        assert ok is False
        assert val is None


# ── Suspicious patterns (logged but not rejected) ──

class TestSuspiciousPatterns:
    def test_sql_injection_still_passes(self):
        ok, val = sanitize_string("SELECT * FROM users")
        assert ok is True

    def test_sql_comment_markers(self):
        ok, val = sanitize_string("normal text -- comment")
        assert ok is True

    def test_normal_text_not_flagged(self):
        ok, val = sanitize_string("Pick me up at 123 Main St")
        assert ok is True
        assert val == "Pick me up at 123 Main St"


# ── Real-world inputs ──

class TestRealWorldInputs:
    def test_address(self):
        ok, val = sanitize_string("123 Main St, Suite #4, Toronto, ON M5V 2T6")
        assert ok is True

    def test_unicode_name(self):
        ok, val = sanitize_string("Jean-Pierre Côté")
        assert ok is True
        assert val == "Jean-Pierre Côté"

    def test_emoji(self):
        ok, val = sanitize_string("Great ride! 🚗👍")
        assert ok is True

    def test_multiline(self):
        ok, val = sanitize_string("Line 1\nLine 2\nLine 3")
        assert ok is True
