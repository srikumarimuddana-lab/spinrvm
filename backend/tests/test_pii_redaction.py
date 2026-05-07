"""Tests for ``backend.utils.pii`` PIPEDA redaction helpers.

These helpers are mandatory at every log/Sentry/audit emission site that would
otherwise leak phone numbers or emails. See ``CLAUDE.md`` § Compliance (PIPEDA).
"""

from __future__ import annotations

import pytest

try:
    from backend.utils.pii import redact_email, redact_phone
except ImportError:
    from utils.pii import redact_email, redact_phone  # type: ignore


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+13065551234", "****1234"),
        ("3065551234", "****1234"),
        ("(306) 555-1234", "****1234"),
        ("306.555.1234", "****1234"),
        ("306 555 1234", "****1234"),
        ("+1 306 555 1234", "****1234"),
    ],
)
def test_redact_phone_keeps_last_four(raw: str, expected: str):
    assert redact_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "abc", "12", "+", "1"])
def test_redact_phone_handles_short_or_empty(raw):
    assert redact_phone(raw) == "****"


def test_redact_phone_never_returns_full_number():
    raw = "+13065551234"
    masked = redact_phone(raw)
    assert raw not in masked
    # Only last-4 digits exposed
    digits_in_mask = [c for c in masked if c.isdigit()]
    assert len(digits_in_mask) == 4


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("alice@example.com", "a***@example.com"),
        ("bob@spinr.ca", "b***@spinr.ca"),
        ("x@y.io", "x***@y.io"),
        ("@nobody.com", "***@nobody.com"),
    ],
)
def test_redact_email_masks_local_part(raw: str, expected: str):
    assert redact_email(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "no-at-sign", "plaintext"])
def test_redact_email_rejects_malformed(raw):
    assert redact_email(raw) == "****"


def test_redact_email_never_leaks_local_beyond_first_char():
    raw = "secretive.username@example.com"
    masked = redact_email(raw)
    assert "secretive" not in masked
    assert "username" not in masked
    assert masked.startswith("s***@")
