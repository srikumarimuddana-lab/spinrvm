"""PII scrubbing for AI assistant traffic (PIPEDA / DV-16).

Applied to every user-authored message BEFORE it is sent to any third-party
LLM provider and BEFORE it is persisted to ai_messages. Extracted from
routes/support.py so the rider AI mode and the legacy driver support chat
share one implementation.

Names cannot be scrubbed reliably with regex; mitigate via data-minimization:
system prompts never ask for names, and the patterns below cover the
highest-risk identifiers (phones, emails, GPS coordinates, postal codes,
payment card numbers, grouped SINs). Driver's license numbers are similarly
unmitigated by regex (no fixed cross-provincial format) and rely on the same
data-minimization mitigation — see the pattern list below for specifics.
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
    # Payment card numbers (PAN). Prefix-gated the same way the NANP phone fix
    # above is: an ungated 13-19 digit run would collide with this codebase's
    # own ride/order/session ids (the exact regression documented above for
    # phones), so a recognized card-network IIN prefix is the discriminator —
    # Visa (4, 13 or 16 digits), Mastercard (51-55 or the 2221-2720 range, 16
    # digits), Amex (34/37, 15 digits), Discover (6011 or 65, 16 digits). Each
    # brand's remaining digits after the prefix are chunked into groups of up
    # to 4 with an optional space/dash between groups, matching how a card
    # number is conventionally displayed — Amex's real 4-6-5 grouping is NOT
    # matched when dash/space-separated (chunked here as 4-4-4-3 instead); a
    # bare, unseparated Amex number is still caught, which is the common case
    # for a rider troubleshooting a decline in chat.
    # CLAUDE.md: "Payment card numbers — Stripe handles; never log even masked
    # PANs." Spinr never stores or transmits a PAN server-side; this exists
    # for the case a rider pastes one into an AI chat message or support
    # ticket while troubleshooting a declined card.
    (
        re.compile(
            r"(?<!\d)(?:"
            r"4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"  # Visa 16
            r"|4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1}"  # Visa 13
            r"|5[1-5]\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"  # Mastercard (legacy range)
            r"|2(?:22[1-9]|2[3-9]\d|[3-6]\d{2}|7[01]\d|720)[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"  # Mastercard (2-series)
            r"|3[47]\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{3}"  # Amex
            r"|6011[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"  # Discover (6011)
            r"|65\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"  # Discover (65)
            r")(?!\d)"
        ),
        "[CARD]",
    ),
    # Canadian Social Insurance Number — grouped 3-3-3 only ("123-456-789" /
    # "123 456 789"). A bare ungrouped 9-digit run is deliberately NOT matched:
    # unlike the card-prefix gate above, there is no reliable discriminator for
    # 9 bare digits in this codebase's own log/id shapes, so matching on digit
    # count alone would repeat the timestamp-collision regression documented
    # above for phones. The separator is the discriminator instead — it is how
    # a SIN is conventionally written, and not how this codebase's ids are
    # formatted.
    #
    # Driver's license numbers are NOT covered: format varies by province (see
    # regulatory-sk.md) with no fixed shape to gate on. Mitigated the same way
    # as names (see module docstring): prompts.py never asks for a SIN, DL
    # number, or other government ID.
    (re.compile(r"(?<!\d)\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)"), "[GOVID]"),
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


# ACTION_ITEMS.md AI13: the driver-persona-secrecy prompt rule (2026-07-28)
# tells the model never to print tool names or internal jargon, but a prompt
# rule alone isn't enforced -- nothing greps the reply text for a leak.
# Matches snake_case-shaped tokens generally (not just the current tool
# registry) so the backstop stays structural against a hallucinated or
# future tool/internal-identifier name too, not just today's known list.
# Natural assistant prose essentially never produces a multi-word
# underscore-joined lowercase token on its own, so this is low false-positive
# risk for a rider-facing chat reply.
_SNAKE_CASE_LEAK_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def filter_tool_leakage(text: str) -> str:
    """Replace snake_case-shaped tokens (tool-name / internal-jargon leakage)
    with a neutral placeholder.

    Applied to the persisted/replayed copy only (mirrors scrub_pii's own
    convention at its call site) -- the raw text has already streamed to the
    client this turn by the time this runs, so this doesn't retroactively
    change what the rider saw, only what gets stored/cached/replayed.
    """
    return _SNAKE_CASE_LEAK_RE.sub("[internal]", text)
