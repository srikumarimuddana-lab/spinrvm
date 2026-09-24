"""v2 offer accept/decline: body parsing and code -> HTTP mapping (T5-4b)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.routes.drivers import offer_decisions as od

OFFER = "11111111-1111-4111-8111-111111111111"
CLAIM = "22222222-2222-4222-8222-222222222222"


def _request(body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = MagicMock()
    req.body = AsyncMock(return_value=raw)
    req.json = AsyncMock(side_effect=lambda: json.loads(raw))
    return req


def test_is_v2_driver():
    assert od.is_v2_driver({"controller_session_id": "s"})
    assert not od.is_v2_driver({"controller_session_id": None})
    assert not od.is_v2_driver({})
    assert not od.is_v2_driver(None)


async def test_parse_empty_and_absent_bodies():
    assert await od.parse_decision_body(None) == {}
    assert await od.parse_decision_body(_request(b"")) == {}


async def test_parse_full_body():
    body = {"offer_id": OFFER.upper(), "claim_id": CLAIM, "online_epoch": "12", "request_id": "r-1", "reason": " x "}
    parsed = await od.parse_decision_body(_request(body))
    assert parsed == {"offer_id": OFFER, "claim_id": CLAIM, "online_epoch": "12", "request_id": "r-1", "reason": "x"}


async def test_parse_accepts_integer_epoch():
    assert (await od.parse_decision_body(_request({"online_epoch": 3})))["online_epoch"] == "3"


@pytest.mark.parametrize(
    "body",
    [
        {"offer_id": "nope"},
        {"claim_id": 5},
        {"online_epoch": "-1"},
        {"online_epoch": "1.5"},
        {"online_epoch": True},
        {"request_id": ""},
        {"request_id": "x" * 129},
        {"request_id": 7},
        [1, 2],
        b"{not json",
    ],
)
async def test_parse_rejects_invalid_fields(body):
    with pytest.raises(HTTPException) as exc:
        await od.parse_decision_body(_request(body))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_OFFER_DECISION"


def test_offer_not_found_is_404():
    exc = od.decision_http_exception({"code": "OFFER_NOT_FOUND"})
    assert (exc.status_code, exc.detail) == (404, {"code": "OFFER_NOT_FOUND"})


@pytest.mark.parametrize(
    "result,code,reason",
    [
        ({"code": "OFFER_EXPIRED", "offer_id": OFFER, "expires_at": "e", "server_time": "t"}, "OFFER_EXPIRED", None),
        ({"code": "OFFER_EXPIRED", "reason_code": "OFFER_SESSION_ENDED"}, "OFFER_EXPIRED", "OFFER_SESSION_ENDED"),
        ({"code": "RIDE_STATE_CONFLICT", "reason_code": "RIDE_TAKEN"}, "RIDE_STATE_CONFLICT", "RIDE_TAKEN"),
        ({"code": "SESSION_SUPERSEDED"}, "SESSION_SUPERSEDED", None),
        ({"code": "ONLINE_EPOCH_STALE", "online_epoch": 9}, "ONLINE_EPOCH_STALE", None),
        ({"code": "DRIVER_OFFLINE"}, "DRIVER_OFFLINE", None),
        ({"code": "CLAIM_MISMATCH"}, "CLAIM_MISMATCH", None),
        ({"code": "IDEMPOTENCY_KEY_CONFLICT"}, "IDEMPOTENCY_KEY_CONFLICT", None),
        ({"code": "OFFER_ALREADY_RESOLVED", "outcome": "declined"}, "OFFER_ALREADY_RESOLVED", None),
        ({"code": "OFFER_ALREADY_RESOLVED", "outcome": "expired_nonresponse"}, "OFFER_EXPIRED", None),
        ({"code": "OFFER_ALREADY_RESOLVED", "outcome": "preempted"}, "RIDE_STATE_CONFLICT", "RIDE_TAKEN"),
        ({"code": "OFFER_ALREADY_RESOLVED", "outcome": "cancelled"}, "RIDE_STATE_CONFLICT", "RIDE_CANCELLED"),
    ],
)
def test_conflicts_are_409(result, code, reason):
    exc = od.decision_http_exception(result)
    assert exc.status_code == 409
    assert exc.detail["code"] == code
    assert exc.detail.get("reason_code") == reason
    assert "snapshot" not in exc.detail


def test_conflict_carries_snapshot_and_string_epoch():
    exc = od.decision_http_exception({"code": "ONLINE_EPOCH_STALE", "online_epoch": 9}, snapshot={"state": "paused"})
    assert exc.detail == {"code": "ONLINE_EPOCH_STALE", "online_epoch": "9", "snapshot": {"state": "paused"}}


def test_unknown_code_is_503():
    exc = od.decision_http_exception({"code": "SOMETHING_NEW"})
    assert (exc.status_code, exc.detail) == (503, {"code": "ELIGIBILITY_UNAVAILABLE"})


def test_decline_is_success():
    assert od.decline_is_success({"code": "OK"})
    assert od.decline_is_success({"code": "OFFER_ALREADY_RESOLVED", "outcome": "declined"})
    assert not od.decline_is_success({"code": "OFFER_ALREADY_RESOLVED", "outcome": "preempted"})
