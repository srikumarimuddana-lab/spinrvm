"""Argument checks and transport for the v2 offer decision wrappers (CI-only)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.repositories import driver_offer_repo as repo

OFFER = "11111111-1111-4111-8111-111111111111"
CLAIM = "22222222-2222-4222-8222-222222222222"


def _client(data):
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
    return client


async def _run_sync(fn, **kwargs):
    assert kwargs == {"retry_policy": "idempotent_write"}
    return fn()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "claim", "request_id": "r"},
        {"action": "accept", "request_id": ""},
        {"action": "accept", "request_id": "x" * 129, "expected_epoch": 1, "actor_session_id": "s"},
        {"action": "accept", "request_id": "r", "expected_epoch": None, "actor_session_id": "s"},
        {"action": "accept", "request_id": "r", "expected_epoch": -1, "actor_session_id": "s"},
        {"action": "accept", "request_id": "r", "expected_epoch": True, "actor_session_id": "s"},
        {"action": "decline", "request_id": "r", "expected_epoch": 1, "actor_session_id": ""},
        {"action": "decline", "request_id": "r", "expected_epoch": 1, "actor_session_id": "system:finalize"},
        {"action": "expire", "request_id": "r", "expected_epoch": 1},
        {"action": "cancel_unaccepted", "request_id": "r", "actor_session_id": "s"},
        {"action": "expire", "request_id": "r", "miss_threshold": 0},
        {"action": "expire", "request_id": "r", "miss_threshold": 21},
    ],
)
async def test_resolve_offer_rejects_bad_arguments(kwargs):
    with pytest.raises(ValueError):
        await repo.resolve_offer(OFFER, CLAIM, **kwargs)


@pytest.mark.parametrize("offer_id,claim_id", [("not-a-uuid", CLAIM), (OFFER, None)])
async def test_resolve_offer_requires_uuids(offer_id, claim_id):
    with pytest.raises(ValueError):
        await repo.resolve_offer(offer_id, claim_id, action="expire", request_id="r")


async def test_resolve_offer_calls_rpc_and_invalidates_cache():
    client = _client({"code": "OK", "driver_id": "d1", "driver_user_id": "u1"})
    invalidate = AsyncMock()
    with (
        patch.object(repo, "supabase", client),
        patch.object(repo, "run_sync", _run_sync),
        patch.object(repo, "invalidate_driver_cache", invalidate),
    ):
        result = await repo.resolve_offer(
            OFFER, CLAIM, action="accept", request_id="accept:x", expected_epoch=7, actor_session_id="sess"
        )
    assert result["code"] == "OK"
    client.rpc.assert_called_once_with(
        "resolve_driver_offer",
        {
            "p_offer_id": OFFER,
            "p_claim_id": CLAIM,
            "p_expected_epoch": 7,
            "p_actor_session_id": "sess",
            "p_action": "accept",
            "p_request_id": "accept:x",
            "p_miss_threshold": 3,
        },
    )
    invalidate.assert_awaited_once_with(driver_id="d1", user_id="u1")


async def test_resolve_offer_non_dict_raises_type_error():
    with (
        patch.object(repo, "supabase", _client([{"code": "OK"}])),
        patch.object(repo, "run_sync", _run_sync),
    ):
        with pytest.raises(TypeError):
            await repo.resolve_offer(OFFER, CLAIM, action="expire", request_id=f"expire:{OFFER}")


async def test_resolve_offer_without_client_raises():
    with patch.object(repo, "supabase", None):
        with pytest.raises(RuntimeError):
            await repo.resolve_offer(OFFER, CLAIM, action="expire", request_id="r")


async def test_finalize_argument_checks():
    with pytest.raises(ValueError):
        await repo.finalize_deferred_availability("", request_id="r")
    with pytest.raises(ValueError):
        await repo.finalize_deferred_availability("d1", request_id="")


async def test_finalize_calls_rpc_and_invalidates_only_when_finalized():
    invalidate = AsyncMock()
    client = _client({"code": "OK", "finalized": True})
    with (
        patch.object(repo, "supabase", client),
        patch.object(repo, "run_sync", _run_sync),
        patch.object(repo, "invalidate_driver_cache", invalidate),
    ):
        result = await repo.finalize_deferred_availability("d1", request_id="finalize:o1")
        assert result["finalized"] is True
        client.rpc.assert_called_once_with(
            "finalize_deferred_driver_availability", {"p_driver_id": "d1", "p_request_id": "finalize:o1"}
        )
        invalidate.assert_awaited_once()
        client.rpc.return_value.execute.return_value = SimpleNamespace(data={"code": "OK", "finalized": False})
        await repo.finalize_deferred_availability("d1", request_id="finalize:o2")
        invalidate.assert_awaited_once()
