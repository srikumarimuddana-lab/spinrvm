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


# Bound the recursion so a pathological/cyclic event can never spin the scrubber.
_MAX_SCRUB_DEPTH = 6


def _scrub_deep(value: Any, depth: int = 0) -> Any:
    """Recursively scrub strings inside nested dicts/lists (event['extra'],
    contexts, request, breadcrumb data). Sentry's LoggingIntegration populates
    event['extra'] from record.args — exactly where the %-formatted coordinate
    args landed, which the message-only scrub never touched. Never raises."""
    if depth >= _MAX_SCRUB_DEPTH:
        return value
    try:
        if isinstance(value, str):
            return _scrub(value)
        if isinstance(value, dict):
            return {k: _scrub_deep(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(_scrub_deep(v, depth + 1) for v in value)
    except Exception:  # noqa: BLE001 - never let scrubbing raise
        return value
    return value


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
