"""Unit tests for utils/retention_guard_monitor.py (A35 defense-in-depth).

Patch target: everything patched on the module's own bindings (``db``,
``redis_set_nx``, ``log_admin_action``, ``_metric_inc``), same convention as
``test_dual_run_monitor.py`` — this module binds them at import time.
"""

from unittest.mock import AsyncMock

import pytest

from backend.utils import retention_guard_monitor as rgm

pytestmark = pytest.mark.unit

DISABLED_ROW = {
    "table_name": "driver_insurance_periods",
    "trigger_name": "driver_insurance_periods_no_mutate",
    "tgenabled": "D",
}


@pytest.fixture
def patched(monkeypatch):
    calls = {"metric": [], "audit": AsyncMock(), "redis_set_nx": AsyncMock(return_value=True)}
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[]))
    monkeypatch.setattr(rgm, "redis_set_nx", calls["redis_set_nx"])
    monkeypatch.setattr(rgm, "log_admin_action", calls["audit"])
    monkeypatch.setattr(rgm, "_metric_inc", lambda name, by=1: calls["metric"].append((name, by)))
    return calls


@pytest.mark.anyio
async def test_no_disabled_triggers_is_a_silent_noop(patched):
    stats = await rgm._check()
    assert stats == {"disabled": 0, "alerted": 0, "deduped": 0}
    patched["audit"].assert_not_awaited()
    assert patched["metric"] == []


@pytest.mark.anyio
async def test_disabled_trigger_escalates_and_writes_audit_row(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    stats = await rgm._check()
    assert stats == {"disabled": 1, "alerted": 1, "deduped": 0}
    patched["audit"].assert_awaited_once()
    assert patched["audit"].await_args.args[1] == "regulatory_guard_trigger_disabled_detected"
    assert patched["metric"] == [("spinr_admin_disabled_guard_trigger_total", 1)]


@pytest.mark.anyio
async def test_dedupe_suppresses_repeat_alert_within_window(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    patched["redis_set_nx"].return_value = False  # key already set -> already alerted
    stats = await rgm._check()
    assert stats == {"disabled": 1, "alerted": 0, "deduped": 1}
    patched["audit"].assert_not_awaited()


@pytest.mark.anyio
async def test_redis_failure_fails_open_and_still_alerts(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    patched["redis_set_nx"].side_effect = RuntimeError("redis down")
    stats = await rgm._check()
    assert stats["alerted"] == 1
    patched["audit"].assert_awaited_once()


@pytest.mark.anyio
async def test_rpc_failure_does_not_raise(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(side_effect=RuntimeError("db down")))
    stats = await rgm._check()
    assert stats == {"disabled": 0, "alerted": 0, "deduped": 0}
    patched["audit"].assert_not_awaited()


@pytest.mark.anyio
async def test_audit_write_failure_never_raises(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    patched["audit"].side_effect = RuntimeError("audit write failed")
    stats = await rgm._check()  # must not raise
    assert stats["alerted"] == 1
    # metric still incremented — escalation itself (log/Sentry) already happened
    assert patched["metric"] == [("spinr_admin_disabled_guard_trigger_total", 1)]


@pytest.mark.anyio
async def test_multiple_disabled_triggers_all_reported(patched, monkeypatch):
    second = {"table_name": "audit_logs", "trigger_name": "audit_logs_no_delete", "tgenabled": "D"}
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW, second]))
    stats = await rgm._check()
    assert stats == {"disabled": 2, "alerted": 2, "deduped": 0}
    disabled_arg = patched["audit"].await_args.kwargs["details"]["disabled_triggers"]
    assert len(disabled_arg) == 2
