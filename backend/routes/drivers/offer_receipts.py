"""Driver offer delivery receipts (T6, migration 461).

``POST /api/v1/drivers/offers/{offer_id}/receipts`` records that a v2 offer
reached (``received``) or was shown in the foreground (``presented``) on the
addressed session. Only a ``presented`` receipt taken before ``expires_at``
lets an expiry count as a missed offer; no receipt means delivery unknown.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

try:
    from ...dependencies import get_current_user, get_token_session_id
    from ...repositories import driver_offer_repo
    from ...utils.datetime_utils import parse_iso_utc
    from ...utils.metrics import inc as _metric_inc
    from ...utils.metrics import observe as _metric_observe
    from ...utils.rate_limiter import ride_read_limit
except ImportError:  # pragma: no cover - top-level backend import mode
    from dependencies import get_current_user, get_token_session_id  # type: ignore
    from repositories import driver_offer_repo  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.rate_limiter import ride_read_limit  # type: ignore

from loguru import logger

router = APIRouter()

_STATUS_BY_CODE = {
    "OFFER_NOT_FOUND": 404,
    "CLAIM_MISMATCH": 409,
    "SESSION_SUPERSEDED": 409,
    "INVALID_RECEIPT": 422,
}


def _invalid(field: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": "INVALID_RECEIPT", "field": field})


def _uuid(value: Any, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise _invalid(field) from None


def parse_receipt_body(offer_id: str, body: Any) -> dict[str, Any]:
    """Validate the receipt body; raise 422 ``INVALID_RECEIPT`` on any bad field."""
    if not isinstance(body, dict):
        raise _invalid("body")
    event = body.get("event")
    channel = body.get("channel")
    app_state = body.get("app_state")
    if event not in driver_offer_repo.RECEIPT_EVENTS:
        raise _invalid("event")
    if channel not in driver_offer_repo.RECEIPT_CHANNELS:
        raise _invalid("channel")
    if app_state not in driver_offer_repo.RECEIPT_APP_STATES:
        raise _invalid("app_state")
    # Only a foreground, interactive app may claim the offer was presented.
    if event == "presented" and app_state != "active":
        raise _invalid("app_state")
    raw_remaining = body.get("remaining_ms")
    # Any JSON number is accepted and rounded; bool is not a number here.
    if isinstance(raw_remaining, bool) or not isinstance(raw_remaining, (int, float)):
        raise _invalid("remaining_ms")
    if not math.isfinite(raw_remaining):
        raise _invalid("remaining_ms")
    remaining_ms = int(round(raw_remaining))
    if abs(remaining_ms) > driver_offer_repo.RECEIPT_MAX_REMAINING_MS:
        raise _invalid("remaining_ms")
    return {
        "offer_id": _uuid(offer_id, "offer_id"),
        "claim_id": _uuid(body.get("claim_id"), "claim_id"),
        "event": event,
        "channel": channel,
        "app_state": app_state,
        "remaining_ms": remaining_ms,
    }


@router.post("/offers/{offer_id}/receipts")
@ride_read_limit
async def record_offer_receipt(
    offer_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
    """Record one delivery receipt for a v2 offer addressed to this session."""
    try:
        body = await request.json()
    except Exception:
        raise _invalid("body") from None
    parsed = parse_receipt_body(offer_id, body)
    if not isinstance(token_session_id, str) or not token_session_id:
        raise HTTPException(status_code=409, detail={"code": "SESSION_SUPERSEDED"})

    try:
        result = await driver_offer_repo.record_offer_receipt(
            parsed["offer_id"],
            parsed["claim_id"],
            user_id=current_user["id"],
            session_id=token_session_id,
            event=parsed["event"],
            channel=parsed["channel"],
            app_state=parsed["app_state"],
            remaining_ms=parsed["remaining_ms"],
        )
    except Exception as exc:
        logger.opt(exception=True).error(f"offer receipt RPC failed offer_id={parsed['offer_id']}")
        raise HTTPException(status_code=503, detail={"code": "RECEIPT_UNAVAILABLE"}) from exc

    code = result.get("code")
    if code != "OK":
        detail = {"code": code or "RECEIPT_UNAVAILABLE"}
        if result.get("reason_code"):
            detail["reason_code"] = result["reason_code"]
        raise HTTPException(status_code=_STATUS_BY_CODE.get(code, 503), detail=detail)

    labels = {"event": parsed["event"], "channel": parsed["channel"]}
    _metric_inc("spinr_dispatch_offer_receipt_total", labels)
    if result.get("recorded"):
        # Both timestamps come from the database clock.
        offered_at = parse_iso_utc(result.get("offered_at"))
        server_time = parse_iso_utc(result.get("server_time"))
        if offered_at and server_time:
            elapsed_ms = max(0.0, (server_time - offered_at).total_seconds() * 1000)
            _metric_observe("spinr_dispatch_offer_delivery_duration_ms", elapsed_ms, labels)
    return {
        "recorded": bool(result.get("recorded")),
        "offer_status": result.get("offer_status"),
        "expires_at": result.get("expires_at"),
        "server_time": result.get("server_time"),
        "late": bool(result.get("late")),
    }
