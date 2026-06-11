"""PII scrubbing for AI assistant traffic (PIPEDA / DV-16).

Applied to every user-authored message BEFORE it is sent to any third-party
LLM provider and BEFORE it is persisted to ai_messages. Extracted from
routes/support.py so the rider AI mode and the legacy driver support chat
share one implementation.

Names cannot be scrubbed reliably with regex; mitigate via data-minimization:
system prompts never ask for names, and the patterns below cover the
highest-risk identifiers (phones, emails, GPS coordinates, postal codes).
"""

import re

_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # North American phone numbers (+1 optional, various separators)
    (re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"), "[PHONE]"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # GPS coordinates  lat,lng or lat/lng (±90/±180 range)
    (re.compile(r"-?\d{1,2}\.\d{4,},\s*-?\d{1,3}\.\d{4,}"), "[COORDS]"),
    # Canadian postal codes (A1A 1A1 or A1A1A1)
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
]


def scrub_pii(text: str) -> str:
    """Replace high-risk identifiers with redaction tokens."""
    for pattern, token in _PII_PATTERNS:
        text = pattern.sub(token, text)
    return text
