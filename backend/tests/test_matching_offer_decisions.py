"""process_expired_offer / batch timeout v2 branch (T5-7)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.routes.rides import matching

OFFER = "11111111-1111-4111-8111-111111111111"
V2_ROW = {"driver_id": "d1", "id": OFFER, "claim_id": OFFER, "online_epoch": 3}
LEGACY_ROW = {"driver_id": "d2", "id": "legacy", "claim_id": None, "online_epoch": None}


async def test_v2_offer_goes_to_resolve_rpc():
    expire = AsyncMock(return_value=True)
    with (
        patch("backend.services.driver_offer_service.expire_offer_v2", expire),
        patch.object(matching._deps.db_supabase, "run_sync", AsyncMock()) as legacy_write,
    ):
        assert await matching.process_expired_offer("r1", "d1", 3, offer=V2_ROW) is True
    expire.assert_awaited_once_with(V2_ROW, 3)
    legacy_write.assert_not_awaited()


async def test_v2_rpc_error_returns_false():
    with patch("backend.services.driver_offer_service.expire_offer_v2", AsyncMock(side_effect=RuntimeError("x"))):
        assert await matching.process_expired_offer("r1", "d1", 3, offer=V2_ROW) is False


async def test_legacy_offer_row_keeps_legacy_claim():
    expire = AsyncMock()
    legacy_write = AsyncMock(return_value=SimpleNamespace(data=[]))
    with (
        patch("backend.services.driver_offer_service.expire_offer_v2", expire),
        patch.object(matching._deps.db_supabase, "run_sync", legacy_write),
    ):
        assert await matching.process_expired_offer("r1", "d2", 3, offer=LEGACY_ROW) is False
    expire.assert_not_awaited()
    legacy_write.assert_awaited_once()


async def test_batch_handler_selects_offer_identity_and_passes_rows():
    select = MagicMock()
    table = MagicMock()
    table.select.return_value.eq.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[V2_ROW, LEGACY_ROW]
    )
    select.table.return_value = table

    async def _run_sync(fn, **_):
        return fn()

    process = AsyncMock(return_value=True)
    with (
        patch.object(matching.asyncio, "sleep", AsyncMock()),
        patch.object(matching._deps.db_supabase, "get_ride", AsyncMock(return_value={"status": "searching"})),
        patch.object(matching._deps.db_supabase, "supabase", select),
        patch.object(matching._deps.db_supabase, "run_sync", _run_sync),
        patch.object(matching._deps, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 2})),
        patch.object(matching._deps.manager, "send_personal_message", AsyncMock()),
        patch.object(matching, "process_expired_offer", process),
        patch.object(matching, "match_driver_to_ride", AsyncMock()),
    ):
        await matching._batch_offer_timeout_handler("r1", "rider", timeout_seconds=0)
    table.select.assert_called_once_with("driver_id,id,claim_id,online_epoch")
    assert [c.kwargs["offer"] for c in process.await_args_list] == [V2_ROW, LEGACY_ROW]
    assert [c.args for c in process.await_args_list] == [("r1", "d1", 2), ("r1", "d2", 2)]
