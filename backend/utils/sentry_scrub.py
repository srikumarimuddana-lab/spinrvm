"""Sentry ``before_send`` / ``before_breadcrumb`` PII scrubber (C1, defense-in-depth).

No log line *should* contain PII — the pre-commit PII-in-logs check and the
PIPEDA conventions forbid it. But the loguru->Sentry bridge in ``server.py``
forwards arbitrary error-message strings to a third party, and there was no
scrubber between the app and Sentry. These hooks redact phones / emails / GPS
coordinates / postal codes out of event and breadcrumb text before egress, and
stamp ``surface=backend`` so events are at least minimally attributable.

Hard rule: scrubbing must NEVER drop an event. Any failure returns the event
unchanged so error reporting keeps working.
"""

import re
from typing import Any, Optional

try:
    from ..ai.pii import scrub_pii
except ImportError:  # pragma: no cover - dual import (uvicorn server:app vs -m backend.server)
    from ai.pii import scrub_pii


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return scrub_pii(value)
        except Exception:  # noqa: BLE001 - never let scrubbing raise
            return value
    return value


# ── Key-name redaction ──────────────────────────────────────────────
# Value patterns alone are not enough for structured payloads, and the reason is
# specific and load-bearing: Sentry serializes values BEFORE before_send runs, so
# a float local `pickup_lat = 52.1332` arrives here as the string '52.1332' in one
# dict key and '-106.67' in another. Every coordinate pattern in ai/pii.py needs
# lat and lng adjacent in a single string ("lat=… lng=…" or "lat,lng"), so a lone
# '52.1332' matches nothing and raw GPS egresses. Names are unmatched by regex at
# all (ai/pii.py says so explicitly).
#
# So for dicts we also redact on the KEY. This is the one place a name-denylist is
# the right tool: we control none of the values, and the key is the only reliable
# signal about what a value means.
_SENSITIVE_KEY_RE = re.compile(
    r"(lat|lng|lon|longitude|latitude|coord"
    r"|phone|mobile|tel"
    r"|email|e_mail"
    r"|(?:^|_)name$|full_name|first_name|last_name|display_name"
    r"|address|street|postal|zip"
    r"|sin|licence|license|passport|dob|birth"
    r"|vin|plate"
    r"|password|passwd|secret|token|api_key|apikey|authorization|auth|cookie|session"
    r"|card|pan|cvv|cvc|iban|account_number)",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"

# Keys that look sensitive by substring but are safe IDs we deliberately keep —
# they are how an event gets triaged at all, and CLAUDE.md lists them as ID-only.
_KEY_ALLOWLIST = frozenset(
    {
        "name",  # bare 'name' on a frame/module dict is a symbol name, not a person
        "filename",
        "module_name",
        "function_name",
        "event_id",
        "request_id",
        "ride_id",
        "driver_id",
        "rider_id",
        "user_id",
        "transaction_name",
    }
)


def _key_is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    if key.lower() in _KEY_ALLOWLIST:
        return False
    return bool(_SENSITIVE_KEY_RE.search(key))


# Bound the recursion so a pathological/cyclic event can never spin the scrubber.
_MAX_SCRUB_DEPTH = 6


def _scrub_deep(value: Any, depth: int = 0) -> Any:
    """Recursively scrub strings inside nested dicts/lists (event['extra'],
    contexts, request, breadcrumb data). Sentry's LoggingIntegration populates
    event['extra'] from record.args — exactly where the %-formatted coordinate
    args landed, which the message-only scrub never touched.

    Redacts on key name as well as value pattern: a JSON request body arrives as
    {'pickup_lat': '52.1332', 'pickup_lng': '-106.67'}, whose values no
    coordinate pattern can match once split across two keys. Never raises."""
    if depth >= _MAX_SCRUB_DEPTH:
        return value
    try:
        if isinstance(value, str):
            return _scrub(value)
        if isinstance(value, dict):
            return {
                k: (_REDACTED if _key_is_sensitive(k) else _scrub_deep(v, depth + 1)) for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return type(value)(_scrub_deep(v, depth + 1) for v in value)
    except Exception:  # noqa: BLE001 - never let scrubbing raise
        return value
    return value


def _scrub_frames(exc_container: Any) -> None:
    """Redact stack-frame local variables in place.

    ``include_local_variables=False`` in ``server.py`` is the real control — this
    is defense-in-depth for two cases it does not cover: someone re-enabling the
    option, and a non-``capture_exception`` path that populates ``vars`` anyway.

    This cannot be folded into ``_scrub_deep``: frame vars sit at
    exception→values[]→stacktrace→frames[]→vars→key, which is already past
    ``_MAX_SCRUB_DEPTH`` before reaching a single value, so a widened deep-scrub
    would silently no-op. Walks the exact path instead. Never raises.
    """
    try:
        if not isinstance(exc_container, dict):
            return
        for val in exc_container.get("values") or []:
            if not isinstance(val, dict):
                continue
            stack = val.get("stacktrace")
            if not isinstance(stack, dict):
                continue
            for frame in stack.get("frames") or []:
                if not isinstance(frame, dict):
                    continue
                frame_vars = frame.get("vars")
                if isinstance(frame_vars, dict):
                    frame["vars"] = {
                        k: (_REDACTED if _key_is_sensitive(k) else _scrub_deep(v)) for k, v in frame_vars.items()
                    }
    except Exception:  # noqa: BLE001 - never drop an event because scrubbing failed
        return


def pipeda_sentry_options() -> dict:
    """The PII-critical half of ``sentry_sdk.init``, as data.

    Extracted from ``server.py`` so it is assertable. The init call lives at module
    scope under ``if sentry_dsn:``, so it only runs on import of a module that
    mounts ~25 routers — meaning the options could not be tested without either
    importing the whole app or grepping source text. Neither is a real test, and
    the gap was not theoretical: a mutation that deleted
    ``include_local_variables=False`` from the init call passed the entire
    behavioural suite, because those tests configure their own client.

    Anything here is a PIPEDA control, not a preference. Callers merge these over
    their own options and must not override them.
    """
    return {
        # Never send IP, cookies, or auth headers.
        "send_default_pii": False,
        # Never send stack-frame locals. Defaults to True in sentry-sdk; with it on,
        # every capture_exception ships the locals of every frame — verified to
        # include pickup_lat/pickup_lng, rider phone, email and name from a booking
        # handler. before_send cannot be the primary control: Sentry serializes
        # values before before_send runs, so coordinates arrive as separate scalar
        # strings and no pattern in ai/pii.py matches a lone '52.1332'; names are
        # not regex-matchable at all.
        "include_local_variables": False,
        "before_send": scrub_event,
        "before_breadcrumb": scrub_breadcrumb,
    }


def scrub_event(event: dict, hint: Optional[dict] = None) -> dict:
    """``before_send`` hook: stamp ``surface`` tag and redact PII from event text."""
    try:
        tags = event.setdefault("tags", {})
        tags.setdefault("surface", "backend")

        if isinstance(event.get("message"), str):
            event["message"] = _scrub(event["message"])

        logentry = event.get("logentry")
        if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
            logentry["message"] = _scrub(logentry["message"])

        exc = event.get("exception")
        if isinstance(exc, dict):
            for val in exc.get("values", []) or []:
                if isinstance(val, dict) and isinstance(val.get("value"), str):
                    val["value"] = _scrub(val["value"])
            # Stack-frame locals are the densest PII carrier in an event (whole
            # request payloads, DB rows, user objects). Belt-and-braces behind
            # include_local_variables=False.
            _scrub_frames(exc)

        # Deep-scrub the structured payloads where PII actually accumulates.
        # LoggingIntegration copies record.args into event['extra'], so a
        # logger.error("... %s", raw_value) call lands the raw value here even
        # though the rendered message was clean.
        for key in ("extra", "contexts", "request"):
            if isinstance(event.get(key), (dict, list)):
                event[key] = _scrub_deep(event[key])
    except Exception:  # noqa: BLE001 - never drop an event because scrubbing failed
        return event
    return event


def scrub_breadcrumb(crumb: dict, hint: Optional[dict] = None) -> dict:
    """``before_breadcrumb`` hook: redact PII from breadcrumb messages."""
    try:
        if isinstance(crumb.get("message"), str):
            crumb["message"] = _scrub(crumb["message"])
        # Breadcrumb data carries structured args (e.g. HTTP request details).
        if isinstance(crumb.get("data"), (dict, list)):
            crumb["data"] = _scrub_deep(crumb["data"])
    except Exception:  # noqa: BLE001
        return crumb
    return crumb


# Structured-log context keys promoted onto Sentry tags so events are triageable
# by domain/surface and correlatable to a ride/driver/rider/request. All are IDs
# or enums — never PII (no phone/email/name/coords), per the logging conventions.
_TAG_KEYS = (
    "domain",
    "surface",
    "ride_id",
    "driver_id",
    "rider_id",
    "event_id",
    "request_id",
    "user_id",
)


def tags_from_log_extra(extra: Any) -> dict:
    """Lift structured-log ``extra={...}`` context into Sentry tags.

    The codebase already annotates many ``logger.*`` calls with
    ``extra={"domain": "payments", ...}``; without this the loguru->Sentry
    bridge dropped that context and events arrived with only ``environment``.
    Always stamps ``surface=backend`` so every event is at least attributable.
    Never raises.
    """
    tags: dict = {}
    try:
        if isinstance(extra, dict):
            for key in _TAG_KEYS:
                val = extra.get(key)
                if val is not None:
                    tags[key] = str(val)
    except Exception:  # noqa: BLE001
        pass
    tags.setdefault("surface", "backend")
    return tags
