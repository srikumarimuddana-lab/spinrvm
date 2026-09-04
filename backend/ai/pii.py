"""PII scrubbing for AI assistant traffic (PIPEDA / DV-16).

Applied to every user-authored message BEFORE it is sent to any third-party
LLM provider and BEFORE it is persisted to ai_messages. Extracted from
routes/support.py so the rider AI mode and the legacy driver support chat
share one implementation. Every caller names the egress boundary it is
protecting via ScrubPolicy (below); the default is the strict one.

Names cannot be scrubbed reliably with regex; mitigate via data-minimization:
system prompts never ask for names, and the patterns below cover the
highest-risk identifiers (phones, emails, GPS coordinates, postal codes,
payment card numbers, grouped SINs). Driver's license numbers are similarly
unmitigated by regex (no fixed cross-provincial format) and rely on the same
data-minimization mitigation — see the pattern list below for specifics.
"""

import re
from enum import Enum
from typing import Any

# (category, pattern, replacement). The category tag is what a ScrubPolicy
# skips by (see _POLICY_SKIPS) so this list stays the single source of truth.
_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # North American phone numbers with separators (+1 optional)
    ("phone", re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"), "[PHONE]"),
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
    ("phone", re.compile(r"(?<![\d+])(?:\+1?\d{10}|1\d{10}|[2-9]\d{9})(?!\d)"), "[PHONE]"),
    # Email addresses
    ("email", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # GPS coordinates — labelled "lat=… lng=…" (any of =/:/space separators).
    # The comma-only pattern below missed this shape, which is exactly how the
    # coordinate log sites formatted them.
    (
        "coords",
        re.compile(r"lat\s*[=:]\s*-?\d{1,2}\.\d{2,}[\s,;/]+lng\s*[=:]\s*-?\d{1,3}\.\d{2,}", re.IGNORECASE),
        "[COORDS]",
    ),
    # GPS coordinates — bare "lat,lng" or "lat/lng" pairs (±90/±180 range).
    ("coords", re.compile(r"-?\d{1,2}\.\d{2,}\s*[,/]\s*-?\d{1,3}\.\d{2,}"), "[COORDS]"),
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
        "coords",
        re.compile(
            r"""(?<![A-Za-z_])(['"]?)(lat|latitude|lng|lon|longitude)\1\s*[=:]\s*-?\d{1,3}\.\d{2,}""",
            re.IGNORECASE,
        ),
        r"\2=[COORD]",
    ),
    # Canadian postal codes (A1A 1A1 or A1A1A1)
    ("postal", re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
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
        "card",
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
    ("govid", re.compile(r"(?<!\d)\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)"), "[GOVID]"),
]


class ScrubPolicy(Enum):
    """Which egress boundary a scrub is protecting. Full model and rationale:
    docs/adr/012-ai-egress-trust-boundaries.md.

    STRICT  -- the default. Telemetry and third parties that have no business
               seeing location data at all: log lines (utils/log_guard.py),
               Sentry (utils/sentry_scrub.py), support tickets
               (routes/support.py, ai/support_assistant.py), the anonymous
               web assistant (ai/public_assistant.py) and the /mcp response
               serializer (ai/mcp_server.py). Every pattern category is
               redacted, bracketed trip pins included.

    AI_CHAT -- the authenticated in-app assistant only: ai/orchestrator.py's
               user-message and reply-persistence path, and ai/tools.py's
               model-facing tool-result cap. Identifiers (phone, email, card,
               SIN, free-text GPS) are still redacted, but trip-LOCATION data
               is kept: app-generated bracketed "[lat,lng]" trip pins and
               Canadian postal codes. Both are components of the trip
               endpoints this path already carries to the provider (the
               street address beside them was never regex-scrubbable and the
               coordinates ride the tool traffic as floats), so redacting them
               buys no privacy and costs fidelity -- the 2026-09-04 regression
               rewrote every postal code in the rider-facing cards, the
               tapped-card message, the model's prose and ultimately
               rides.pickup_address to the literal "[POSTAL]". Accepted
               trip-endpoint exception: docs/compliance/pia-ai-surfaces-
               2026-08.md Section 3.

    A policy is an explicit per-call-site opt-in, never a default, because
    scrub_pii is shared: raw GPS must never reach Sentry (PIPEDA hard rule),
    and tests/test_ai_pii.py enumerates every file that opts in to AI_CHAT.
    """

    STRICT = "strict"
    AI_CHAT = "ai_chat"


# Pattern categories a policy leaves unredacted, keyed by the category tag on
# each _PII_PATTERNS entry.
_POLICY_SKIPS: dict[ScrubPolicy, frozenset[str]] = {
    ScrubPolicy.STRICT: frozenset(),
    ScrubPolicy.AI_CHAT: frozenset({"postal"}),
}

# App-generated trip-endpoint coordinates -- "[50.40790,-104.65010]" -- written
# into user messages by the quote-card tap and the map-pin picker. Under
# ScrubPolicy.AI_CHAT they are stashed before the pattern pass and restored
# after it: the model must receive them verbatim (scrubbing them re-introduced
# the re-geocode drift the bracketed format exists to prevent), and as trip
# endpoints they are the same data class the rides table stores openly and
# tool traffic already carries to the provider. They are not the rider's live
# device location. Under STRICT a bracketed pair inside an error string gets
# no pass. Free-text coordinates are scrubbed under both policies.
_BRACKETED_COORDS = re.compile(r"\[-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+\]")


def _check_policy(policy: Any) -> None:
    # Raise, never coerce. scrub_pii_deep swallows exceptions from the pattern
    # pass by design (a scrub failure must not break a chat turn), so a wrong-
    # typed policy has to be rejected BEFORE that guard -- otherwise it would
    # silently hand the value back unscrubbed, which is a privacy regression
    # dressed up as resilience.
    # Membership in _POLICY_SKIPS is checked too: a future enum member added
    # without a skip-set row would otherwise KeyError inside the pattern pass
    # and be swallowed the same way.
    if not isinstance(policy, ScrubPolicy) or policy not in _POLICY_SKIPS:
        raise TypeError(f"policy must be a ScrubPolicy with a _POLICY_SKIPS entry, got {policy!r}")


def scrub_pii(text: str, *, policy: ScrubPolicy = ScrubPolicy.STRICT) -> str:
    """Replace high-risk identifiers with redaction tokens.

    ``policy`` names the egress boundary being protected (see ScrubPolicy).
    STRICT is the default; only the authenticated in-app assistant passes
    AI_CHAT, which keeps bracketed trip pins and Canadian postal codes.
    """
    _check_policy(policy)
    skips = _POLICY_SKIPS[policy]
    protected: list[str] = []

    def _stash(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    if policy is ScrubPolicy.AI_CHAT:
        text = _BRACKETED_COORDS.sub(_stash, text)
    for category, pattern, token in _PII_PATTERNS:
        if category in skips:
            continue
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


# 2026-08-18 fleet audit: scrub_pii above was only ever applied to the user's
# own chat message and the model's final reply text -- never to a tool
# RESULT, which re-enters the model context on the very next turn of the
# same tool loop (orchestrator.py). scrub_pii_deep is wired into tools.py's
# _cap_result for the MODEL-facing portion of every result (ScrubPolicy.
# AI_CHAT). The rider-facing ``_client_action`` card is exempt there -- it
# goes back to the data subject, not to a third party -- and /mcp, whose
# client IS a third party, re-scrubs its whole response under STRICT in
# mcp_server._serialize_tool_payload (2026-09-04, ADR 012).
_MAX_SCRUB_DEPTH = 6


def scrub_pii_deep(value: Any, depth: int = 0, *, policy: ScrubPolicy = ScrubPolicy.STRICT) -> Any:
    """Recursively apply scrub_pii to every string leaf in a JSON-like tool
    result (nested dicts/lists/tuples). Value-pattern scrubbing only --
    deliberately NOT key-name-based like utils/sentry_scrub.py's _scrub_deep,
    whose KEY_ALLOWLIST treats a bare "name" key as a benign stack-frame
    symbol (correct for Sentry breadcrumbs, wrong here: a tool result's
    "name" key is routinely a person's actual name). Regex cannot catch a
    plain name either way -- see this module's docstring; tools that surface
    a person's name must data-minimize at the source (see
    tools_rides.py::_driver_public), not rely on this scrub to catch it.
    This closes the regex-detectable categories (phone/email/GPS/card/SIN/
    postal code) for every current and future tool, not just the one already
    known to leak.

    Bounded recursion so a pathological/cyclic result can never spin the
    scrubber. Never raises on content -- a scrub failure must not break the
    chat turn. A wrong-typed or unmapped ``policy`` DOES raise, deliberately,
    once at the top level and outside that guard (see _check_policy); the
    recursion below runs unchecked so the check is never inside the swallow.
    """
    _check_policy(policy)
    return _scrub_deep(value, depth, policy)


def _scrub_deep(value: Any, depth: int, policy: ScrubPolicy) -> Any:
    if depth >= _MAX_SCRUB_DEPTH:
        return value
    try:
        if isinstance(value, str):
            return scrub_pii(value, policy=policy)
        if isinstance(value, dict):
            return {k: _scrub_deep(v, depth + 1, policy) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(_scrub_deep(v, depth + 1, policy) for v in value)
    except Exception:  # noqa: BLE001 - never let scrubbing break a tool result
        return value
    return value
