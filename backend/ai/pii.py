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
    # North American phone numbers with separators (+1 optional)
    (re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"), "[PHONE]"),
    # E.164 / bare 10- or 11-digit North American numbers (no separators) —
    # this is how the system stores them (+13065551234), which the
    # separator-requiring pattern above missed entirely. Digit boundaries stop
    # it swallowing a longer id.
    #
    # NANP-aware, and that is not cosmetic. The previous form was
    # `(?<!\d)\+?1?\d{10}(?!\d)`, which matched ANY bare 10-digit run — including
    # every unix timestamp, since those are 10 digits until the year 2286. So
    # `ts=1769817600` was rewritten to `ts=[PHONE]`, and this function is applied
    # to Sentry event text by utils/sentry_scrub, meaning production Sentry events
    # were having their timestamps and millisecond durations corrupted. Observability
    # damage from a privacy helper is still damage.
    #
    # The discriminator is exact: a NANP area code cannot begin with 0 or 1, so a
    # bare 10-digit number starting with 1 is never a phone number, while a unix
    # timestamp in any plausible range always starts with 1. Three accepted shapes:
    #   +\d{10,11}   explicit international prefix (+13065551234, +3065551234)
    #   1\d{10}      11 digits with the country code (13065551234)
    #   [2-9]\d{9}   bare 10-digit with a valid area code (3065551234)
    (re.compile(r"(?<![\d+])(?:\+1?\d{10}|1\d{10}|[2-9]\d{9})(?!\d)"), "[PHONE]"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # GPS coordinates — labelled "lat=… lng=…" (any of =/:/space separators).
    # The comma-only pattern below missed this shape, which is exactly how the
    # coordinate log sites formatted them.
    (
        re.compile(r"lat\s*[=:]\s*-?\d{1,2}\.\d{2,}[\s,;/]+lng\s*[=:]\s*-?\d{1,3}\.\d{2,}", re.IGNORECASE),
        "[COORDS]",
    ),
    # GPS coordinates — bare "lat,lng" or "lat/lng" pairs (±90/±180 range).
    (re.compile(r"-?\d{1,2}\.\d{2,}\s*[,/]\s*-?\d{1,3}\.\d{2,}"), "[COORDS]"),
    # GPS coordinates inside a mapping repr — "{'lat': 52.1332, 'lng': -106.67}".
    #
    # This is the shape that actually leaked (T1: a log line interpolating a DB row),
    # and BOTH patterns above miss it. The labelled pattern needs `lat` immediately
    # followed by `=` or `:`, but a dict repr puts a quote in between (`'lat':`). The
    # bare-pair pattern needs the two numbers adjacent, but `, 'lng': ` sits between
    # them. So a dict repr defeated both.
    #
    # Matches each coordinate INDEPENDENTLY rather than requiring a pair, because
    # adjacency is exactly the assumption that failed. Keeps the key name in the
    # output (`lat=[COORD]`) so the line stays debuggable — you can still see that a
    # coordinate was there and which axis it was.
    #
    # The lookbehind stops `flat=1.5` / `latency=12.34` matching on a `lat` substring.
    (
        re.compile(
            r"""(?<![A-Za-z_])(['"]?)(lat|latitude|lng|lon|longitude)\1\s*[=:]\s*-?\d{1,3}\.\d{2,}""",
            re.IGNORECASE,
        ),
        r"\2=[COORD]",
    ),
    # Canadian postal codes (A1A 1A1 or A1A1A1)
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
]

# App-generated trip-endpoint coordinates — "[50.40790,-104.65010]" — written
# into user messages by the quote-card tap and the map-pin picker. The AI chat
# message path opts in to keeping them (keep_trip_pins=True): the model must
# receive them verbatim (scrubbing them re-introduced the re-geocode drift the
# bracketed format exists to prevent), and as trip endpoints they are the same
# data class the rides table stores openly and tool traffic already carries to
# the provider. They are not the rider's live device location.
#
# The exemption is opt-in, NOT the default, because scrub_pii is shared:
# utils/sentry_scrub.py runs it over exception messages and breadcrumbs, and
# raw GPS must never reach Sentry (PIPEDA hard rule) — a bracketed pair inside
# an error string gets no pass there. Callers that are not the chat-message
# path must never set keep_trip_pins.
_BRACKETED_COORDS = re.compile(r"\[-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+\]")


def scrub_pii(text: str, *, keep_trip_pins: bool = False) -> str:
    """Replace high-risk identifiers with redaction tokens.

    keep_trip_pins=True preserves app-generated bracketed "[lat,lng]" trip
    endpoints (AI chat messages only — see module comment). Free-text
    coordinates are scrubbed either way.
    """
    protected: list[str] = []

    def _stash(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    if keep_trip_pins:
        text = _BRACKETED_COORDS.sub(_stash, text)
    for pattern, token in _PII_PATTERNS:
        text = pattern.sub(token, text)
    for index, original in enumerate(protected):
        text = text.replace(f"\x00{index}\x00", original)
    return text
