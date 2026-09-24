"""HTTP helpers for v2 offer accept/decline (backend design 4.2, section 5, C2).

The protocol follows the offer row; these helpers only parse the optional
request body and map a ``resolve_driver_offer`` result to the wire contract.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_EPOCH_RE = re.compile(r"^[0-9]{1,19}$")
_MAX_REQUEST_ID = 128
_MAX_REASON = 64

# Codes that are always 409 conflicts the client resolves by reconciling.
_CONFLICT_CODES = frozenset(
    {
        "OFFER_EXPIRED",
        "RIDE_STATE_CONFLICT",
        "SESSION_SUPERSEDED",
        "ONLINE_EPOCH_STALE",
        "DRIVER_OFFLINE",
        "CLAIM_MISMATCH",
        "OFFER_MISMATCH",
        "OFFER_ALREADY_RESOLVED",
        "IDEMPOTENCY_KEY_CONFLICT",
        "SESSION_RECONCILE_REQUIRED",
        "AVAILABILITY_V2_DISABLED",
        "OFFER_PROTOCOL_MISMATCH",
    }
)
_DETAIL_KEYS = ("reason_code", "offer_id", "expires_at", "server_time", "ride_status", "online_epoch")


def is_v2_driver(driver: dict | None) -> bool:
    """A driver on the v2 protocol has a string controller session."""
    return isinstance((driver or {}).get("controller_session_id"), str)


def _bad_body(field: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": "INVALID_OFFER_DECISION", "field": field})


def _uuid_field(body: dict, key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise _bad_body(key) from None


async def parse_decision_body(request: Request | None) -> dict[str, Any]:
    """Parse the optional ``{offer_id, claim_id, online_epoch, request_id, reason}`` body.

    An absent or empty body yields ``{}`` (legacy clients post nothing).
    Present fields are validated; an invalid one is a 422.
    """
    if request is None:
        return {}
    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        body = await request.json()
    except Exception:
        raise _bad_body("body") from None
    if not isinstance(body, dict):
        raise _bad_body("body")

    parsed: dict[str, Any] = {}
    for key in ("offer_id", "claim_id"):
        value = _uuid_field(body, key)
        if value is not None:
            parsed[key] = value
    epoch = body.get("online_epoch")
    if epoch is not None:
        if isinstance(epoch, bool) or not _EPOCH_RE.match(str(epoch)):
            raise _bad_body("online_epoch")
        parsed["online_epoch"] = str(epoch)
    request_id = body.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= _MAX_REQUEST_ID:
            raise _bad_body("request_id")
        parsed["request_id"] = request_id
    reason = body.get("reason")
    if reason:
        parsed["reason"] = str(reason).strip()[:_MAX_REASON] or None
    return parsed


def _conflict_code(result: dict) -> tuple[str, str | None]:
    """Map OFFER_ALREADY_RESOLVED outcomes to the code the client acts on (X2)."""
    code = result.get("code") or "OFFER_ALREADY_RESOLVED"
    reason = result.get("reason_code")
    if code != "OFFER_ALREADY_RESOLVED":
        return code, reason
    outcome = str(result.get("outcome") or "")
    if outcome.startswith("expired_"):
        return "OFFER_EXPIRED", None
    if outcome == "preempted":
        return "RIDE_STATE_CONFLICT", "RIDE_TAKEN"
    if outcome == "cancelled":
        return "RIDE_STATE_CONFLICT", "RIDE_CANCELLED"
    return code, reason


def decision_http_exception(result: dict, snapshot: dict | None = None) -> HTTPException:
    """Build the HTTPException for a non-success decision result.

    404 OFFER_NOT_FOUND; 409 for every conflict (with ``snapshot`` when
    given); anything unrecognised is a 503 ELIGIBILITY_UNAVAILABLE so the
    client retries instead of trusting a half-understood answer.
    """
    raw_code = result.get("code")
    if raw_code == "OFFER_NOT_FOUND":
        return HTTPException(status_code=404, detail={"code": "OFFER_NOT_FOUND"})
    if raw_code not in _CONFLICT_CODES:
        logger.error("unexpected offer decision code=%s", raw_code)
        return HTTPException(status_code=503, detail={"code": "ELIGIBILITY_UNAVAILABLE"})
    code, reason_code = _conflict_code(result)
    detail: dict[str, Any] = {"code": code}
    if reason_code:
        detail["reason_code"] = reason_code
    for key in _DETAIL_KEYS[1:]:
        value = result.get(key)
        if value is not None:
            detail[key] = str(value) if key == "online_epoch" else value
    if snapshot is not None:
        detail["snapshot"] = snapshot
    return HTTPException(status_code=409, detail=detail)


def decline_is_success(result: dict) -> bool:
    """Decline is 200 for OK and for an offer this driver already declined."""
    code = result.get("code")
    return code == "OK" or (code == "OFFER_ALREADY_RESOLVED" and result.get("outcome") == "declined")
