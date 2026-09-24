"""Offer receipt route and repository wrapper (T6-3, T6-4)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.repositories import driver_offer_repo
from backend.routes.drivers import offer_receipts

OFFER_ID = "0b6a2b8e-3c1f-4a55-9d3e-1f2a3b4c5d6e"
CLAIM_ID = "9f8e7d6c-5b4a-4321-8fed-cba987654321"


def _body(**overrides):
    return {
        "claim_id": CLAIM_ID,
        "event": "received",
        "channel": "ws",
        "app_state": "background",
        "remaining_ms": 12000,
        **overrides,
    }


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


async def _call(body, session="sess-A", rpc_result=None):
    handler = getattr(offer_receipts.record_offer_receipt, "__wrapped__", offer_receipts.record_offer_receipt)
    rpc = AsyncMock(return_value=rpc_result or {"code": "OK", "recorded": True, "offer_status": "pending"})
    with patch.object(offer_receipts.driver_offer_repo, "record_offer_receipt", rpc):
        result = await handler(OFFER_ID, _Req(body), current_user={"id": "user-1"}, token_session_id=session)
    return result, rpc


def test_parse_accepts_float_remaining_ms_and_rounds():
    parsed = offer_receipts.parse_receipt_body(OFFER_ID, _body(remaining_ms=1234.6))
    assert parsed["remaining_ms"] == 1235
    assert parsed["offer_id"] == OFFER_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "seen"},
        {"channel": "sms"},
        {"app_state": "foreground"},
        {"event": "presented", "app_state": "background"},
        {"remaining_ms": 3_600_001},
        {"remaining_ms": "12"},
        {"remaining_ms": True},
        {"claim_id": "not-a-uuid"},
    ],
)
def test_parse_rejects_invalid_receipt(overrides):
    with pytest.raises(HTTPException) as exc:
        offer_receipts.parse_receipt_body(OFFER_ID, _body(**overrides))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_RECEIPT"


def test_parse_rejects_bad_offer_id():
    with pytest.raises(HTTPException) as exc:
        offer_receipts.parse_receipt_body("nope", _body())
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_route_returns_contract_keys_only():
    result, rpc = await _call(
        _body(event="presented", app_state="active"),
        rpc_result={
            "code": "OK",
            "recorded": True,
            "offer_status": "pending",
            "expires_at": "2026-09-24T12:00:15+00:00",
            "offered_at": "2026-09-24T12:00:00+00:00",
            "server_time": "2026-09-24T12:00:02+00:00",
            "late": False,
        },
    )
    assert result == {
        "recorded": True,
        "offer_status": "pending",
        "expires_at": "2026-09-24T12:00:15+00:00",
        "server_time": "2026-09-24T12:00:02+00:00",
        "late": False,
    }
    kwargs = rpc.await_args.kwargs
    assert kwargs["session_id"] == "sess-A"
    assert kwargs["user_id"] == "user-1"
    assert kwargs["event"] == "presented"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "status"),
    [("OFFER_NOT_FOUND", 404), ("CLAIM_MISMATCH", 409), ("SESSION_SUPERSEDED", 409), ("INVALID_RECEIPT", 422)],
)
async def test_route_maps_rpc_codes(code, status):
    with pytest.raises(HTTPException) as exc:
        await _call(_body(), rpc_result={"code": code})
    assert exc.value.status_code == status
    assert exc.value.detail["code"] == code


@pytest.mark.anyio
async def test_route_without_session_is_superseded():
    with pytest.raises(HTTPException) as exc:
        await _call(_body(), session=None)
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "SESSION_SUPERSEDED"}


@pytest.mark.anyio
async def test_route_rpc_failure_is_structured_503():
    handler = getattr(offer_receipts.record_offer_receipt, "__wrapped__", offer_receipts.record_offer_receipt)
    with patch.object(
        offer_receipts.driver_offer_repo, "record_offer_receipt", AsyncMock(side_effect=RuntimeError("down"))
    ):
        with pytest.raises(HTTPException) as exc:
            await handler(OFFER_ID, _Req(_body()), current_user={"id": "user-1"}, token_session_id="sess-A")
    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "RECEIPT_UNAVAILABLE"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [{"event": "seen"}, {"channel": "sms"}, {"app_state": "foreground"}, {"remaining_ms": 4_000_000}],
)
async def test_repo_rejects_bad_arguments(overrides):
    kwargs = {
        "user_id": "user-1",
        "session_id": "sess-A",
        "event": "received",
        "channel": "ws",
        "app_state": "active",
        "remaining_ms": 100,
        **overrides,
    }
    with pytest.raises(ValueError):
        await driver_offer_repo.record_offer_receipt(OFFER_ID, CLAIM_ID, **kwargs)


def _flatten(routes, prefix=""):
    """(path, methods) in match order; handles lazily included routers."""
    out = []
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = getattr(ctx, "prefix", "") or ""
            out.extend(_flatten(original.routes, prefix + sub_prefix))
        else:
            out.append((prefix + getattr(route, "path", ""), sorted(getattr(route, "methods", None) or [])))
    return out


def test_receipt_route_is_mounted_before_status_catch_all():
    from backend.routes.drivers import api_router

    paths = _flatten(api_router.routes)
    receipt = ("/drivers/offers/{offer_id}/receipts", ["POST"])
    assert receipt in paths
    catch_all = [i for i, (p, _m) in enumerate(paths) if p.startswith("/drivers/{driver_id}")]
    assert catch_all and paths.index(receipt) < min(catch_all)
