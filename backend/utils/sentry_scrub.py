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
    except Exception:  # noqa: BLE001 - never drop an event because scrubbing failed
        return event
    return event


def scrub_breadcrumb(crumb: dict, hint: Optional[dict] = None) -> dict:
    """``before_breadcrumb`` hook: redact PII from breadcrumb messages."""
    try:
        if isinstance(crumb.get("message"), str):
            crumb["message"] = _scrub(crumb["message"])
    except Exception:  # noqa: BLE001
        return crumb
    return crumb
