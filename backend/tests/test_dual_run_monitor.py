"""Unit tests for utils/dual_run_monitor.py (dual-run cutover signals, A34/P3.1).

Patch target note (see CLAUDE.md Testing Conventions): everything is patched
on ``backend.utils.dual_run_monitor``'s own module globals, since that module
binds ``get_app_settings``/``log_user_action``/``_metric_inc``/``db_supabase``
at import time.
"""

from unittest.mock import AsyncMock

import pytest

from backend.utils import dual_run_monitor as drm

pytestmark = pytest.mark.unit

LEGACY_DRIVER = {
    "id": "drv-1",
    "legacy_import_metadata": {
        "source": "legacy_saskatoon_driver_import",
        "old_driver_id": "2365152199",
        "batch": "b1",
    },
}
ORGANIC_DRIVER = {"id": "drv-2", "legacy_import_metadata": {}}
USER = {"id": "user-1", "role": "driver"}


@pytest.fixture
def patched(monkeypatch):
    calls = {"metric": [], "audit": AsyncMock(), "update": AsyncMock()}
    monkeypatch.setattr(drm, "_metric_inc", lambda name, labels=None, by=1: calls["metric"].append((name, labels)))
    monkeypatch.setattr(drm, "log_user_action", calls["audit"])
    monkeypatch.setattr(drm.db_supabase, "update_one", calls["update"])
    monkeypatch.setattr(drm, "get_app_settings", AsyncMock(return_value={}))
    return calls


def test_is_legacy_driver():
    assert drm.is_legacy_driver(LEGACY_DRIVER) is True
    assert drm.is_legacy_driver(ORGANIC_DRIVER) is False
    assert drm.is_legacy_driver({"id": "x", "legacy_import_metadata": None}) is False
    assert drm.is_legacy_driver({"id": "x"}) is False


@pytest.mark.anyio
async def test_go_online_organic_counts_but_no_audit(patched):
    await drm.record_go_online_flip(ORGANIC_DRIVER, USER)
    assert patched["metric"] == [("spinr_drivers_go_online_total", {"is_legacy_import": "false"})]
    patched["audit"].assert_not_awaited()
    patched["update"].assert_not_awaited()


@pytest.mark.anyio
async def test_go_online_legacy_first_time_stamps_and_audits(patched):
    await drm.record_go_online_flip(dict(LEGACY_DRIVER), USER)
    assert patched["metric"] == [("spinr_drivers_go_online_total", {"is_legacy_import": "true"})]
    patched["update"].assert_awaited_once()
    args = patched["update"].await_args.args
    assert args[0] == "drivers"
    assert args[1] == {"id": "drv-1"}
    update = args[2]
    stamped = update["$set"]["legacy_import_metadata"]
    assert stamped[drm.FIRST_GO_ONLINE_KEY]
    assert stamped["old_driver_id"] == "2365152199"  # existing keys preserved
    patched["audit"].assert_awaited_once()
    assert patched["audit"].await_args.args[1] == "legacy_driver_first_go_online"


@pytest.mark.anyio
async def test_go_online_legacy_second_time_is_once_only(patched):
    driver = {
        "id": "drv-1",
        "legacy_import_metadata": {"source": "s", drm.FIRST_GO_ONLINE_KEY: "2026-08-15T00:00:00Z"},
    }
    await drm.record_go_online_flip(driver, USER)
    # counter still fires; stamp and audit do not
    assert patched["metric"] == [("spinr_drivers_go_online_total", {"is_legacy_import": "true"})]
    patched["update"].assert_not_awaited()
    patched["audit"].assert_not_awaited()


@pytest.mark.anyio
async def test_flag_off_disables_everything(patched):
    drm.get_app_settings.return_value = {drm.FLAG_KEY: False}
    await drm.record_go_online_flip(dict(LEGACY_DRIVER), USER)
    await drm.record_legacy_payout(LEGACY_DRIVER, "p1", 10)
    assert patched["metric"] == []
    patched["audit"].assert_not_awaited()
    patched["update"].assert_not_awaited()


@pytest.mark.anyio
async def test_settings_failure_fails_open_to_enabled(patched):
    drm.get_app_settings.side_effect = RuntimeError("db down")
    await drm.record_legacy_payout(LEGACY_DRIVER, "p1", 10)
    assert patched["metric"] == [("spinr_payments_legacy_driver_payout_total", None)]


@pytest.mark.anyio
async def test_payout_signal_legacy_only(patched):
    await drm.record_legacy_payout(ORGANIC_DRIVER, "p1", 10)
    assert patched["metric"] == []
    await drm.record_legacy_payout(LEGACY_DRIVER, "p2", 12.5)
    assert patched["metric"] == [("spinr_payments_legacy_driver_payout_total", None)]


@pytest.mark.anyio
async def test_monitoring_failure_never_raises(patched):
    patched["update"].side_effect = RuntimeError("write failed")
    # must not raise — monitoring only
    await drm.record_go_online_flip(dict(LEGACY_DRIVER), USER)
    # metric already emitted before the failing write
    assert patched["metric"] == [("spinr_drivers_go_online_total", {"is_legacy_import": "true"})]
