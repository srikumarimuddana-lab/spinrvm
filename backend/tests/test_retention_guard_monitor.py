"""Unit tests for utils/retention_guard_monitor.py (A35 defense-in-depth,
extended by A37's realtime-event merge).

Patch target: everything patched on the module's own bindings (``db``,
``redis_set_nx``, ``log_admin_action``, ``_metric_inc``), same convention as
``test_dual_run_monitor.py`` — this module binds them at import time.
"""

import json
from unittest.mock import AsyncMock

import pytest

from backend.utils import retention_guard_monitor as rgm

pytestmark = pytest.mark.unit

DISABLED_ROW = {
    "table_name": "driver_insurance_periods",
    "trigger_name": "driver_insurance_periods_no_mutate",
    "tgenabled": "D",
}


def _realtime_audit_log_row(table_name: str, trigger_name: str) -> dict:
    """A row shaped exactly like what migration 318's event trigger writes —
    ``details`` is a JSON *string* (audit_logs.details is TEXT in
    production, not JSONB — see migration 318's own comment), not a dict."""
    return {
        "action": rgm._REALTIME_EVENT_ACTION,
        "created_at": "2026-08-17T12:00:00+00:00",
        "details": json.dumps(
            {
                "source": "ddl_command_end_event_trigger",
                "detected_at": "2026-08-17T12:00:00+00:00",
                "disabled_triggers": [{"table_name": table_name, "trigger_name": trigger_name, "tgenabled": "D"}],
            }
        ),
    }


@pytest.fixture
def patched(monkeypatch):
    calls = {"metric": [], "audit": AsyncMock(), "redis_set_nx": AsyncMock(return_value=True)}
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[]))
    monkeypatch.setattr(rgm.db, "get_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(rgm, "redis_set_nx", calls["redis_set_nx"])
    monkeypatch.setattr(rgm, "log_admin_action", calls["audit"])
    monkeypatch.setattr(rgm, "_metric_inc", lambda name, by=1: calls["metric"].append((name, by)))
    return calls


@pytest.mark.anyio
async def test_no_disabled_triggers_is_a_silent_noop(patched):
    stats = await rgm._check()
    assert stats == {"disabled": 0, "alerted": 0, "deduped": 0, "realtime_events": 0}
    patched["audit"].assert_not_awaited()
    assert patched["metric"] == []


@pytest.mark.anyio
async def test_disabled_trigger_escalates_and_writes_audit_row(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    stats = await rgm._check()
    assert stats == {"disabled": 1, "alerted": 1, "deduped": 0, "realtime_events": 0}
    patched["audit"].assert_awaited_once()
    assert patched["audit"].await_args.args[1] == "regulatory_guard_trigger_disabled_detected"
    assert patched["metric"] == [("spinr_admin_disabled_guard_trigger_total", 1)]


@pytest.mark.anyio
async def test_dedupe_suppresses_repeat_alert_within_window(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    patched["redis_set_nx"].return_value = False  # key already set -> already alerted
    stats = await rgm._check()
    assert stats == {"disabled": 1, "alerted": 0, "deduped": 1, "realtime_events": 0}
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
    assert stats == {"disabled": 0, "alerted": 0, "deduped": 0, "realtime_events": 0}
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
    assert stats == {"disabled": 2, "alerted": 2, "deduped": 0, "realtime_events": 0}
    disabled_arg = patched["audit"].await_args.kwargs["details"]["disabled_triggers"]
    assert len(disabled_arg) == 2


# --- A37: realtime-event-log merge -----------------------------------------


@pytest.mark.anyio
async def test_realtime_event_alone_escalates_even_when_state_poll_is_clean(patched, monkeypatch):
    """The entire point of A37: a guard disabled and already re-enabled again
    between ticks is invisible to the state poll (rpc returns []) but must
    still be caught via the event-trigger's audit_logs row."""
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        rgm.db,
        "get_rows",
        AsyncMock(return_value=[_realtime_audit_log_row("financial_events", "financial_events_no_mutate")]),
    )
    stats = await rgm._check()
    assert stats == {"disabled": 1, "alerted": 1, "deduped": 0, "realtime_events": 1}
    patched["audit"].assert_awaited_once()
    disabled_arg = patched["audit"].await_args.kwargs["details"]["disabled_triggers"]
    assert disabled_arg[0]["table_name"] == "financial_events"
    assert disabled_arg[0]["trigger_name"] == "financial_events_no_mutate"


@pytest.mark.anyio
async def test_realtime_event_and_state_poll_for_same_trigger_page_once(patched, monkeypatch):
    """A trigger caught by both sources in the same window (e.g. genuinely
    still disabled right now, AND the event trigger logged the disable
    moment earlier) must page once, not twice — same dedupe key covers both."""
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    monkeypatch.setattr(
        rgm.db,
        "get_rows",
        AsyncMock(return_value=[_realtime_audit_log_row(DISABLED_ROW["table_name"], DISABLED_ROW["trigger_name"])]),
    )
    # Real SET NX only succeeds once per key -- the plain AsyncMock in
    # `patched` returns True unconditionally, which can't tell apart the two
    # rows (one from each source) sharing the same dedupe key. Swap in a
    # stateful fake so this test actually exercises the same-window dedupe
    # it claims to.
    seen_keys: set[str] = set()

    async def _stateful_set_nx(key, *_args, **_kwargs):
        if key in seen_keys:
            return False
        seen_keys.add(key)
        return True

    monkeypatch.setattr(rgm, "redis_set_nx", _stateful_set_nx)
    stats = await rgm._check()
    assert stats["disabled"] == 2  # one from each source, pre-dedupe
    assert stats["alerted"] == 1  # deduped down to one page
    assert stats["deduped"] == 1
    patched["audit"].assert_awaited_once()


@pytest.mark.anyio
async def test_realtime_event_fetch_failure_does_not_block_state_poll(patched, monkeypatch):
    monkeypatch.setattr(rgm.db, "rpc", AsyncMock(return_value=[DISABLED_ROW]))
    monkeypatch.setattr(rgm.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down")))
    stats = await rgm._check()  # must not raise
    assert stats["alerted"] == 1
    assert stats["realtime_events"] == 0


@pytest.mark.anyio
async def test_realtime_event_with_unparseable_details_is_skipped_not_fatal(patched, monkeypatch):
    monkeypatch.setattr(
        rgm.db,
        "get_rows",
        AsyncMock(return_value=[{"action": rgm._REALTIME_EVENT_ACTION, "details": "not valid json {{{"}]),
    )
    stats = await rgm._check()  # must not raise
    assert stats == {"disabled": 0, "alerted": 0, "deduped": 0, "realtime_events": 0}
    patched["audit"].assert_not_awaited()


@pytest.mark.anyio
async def test_realtime_events_fetches_with_correct_filter(patched, monkeypatch):
    """Confirms the query is scoped to the right action + a $gte cutoff, not
    an unbounded scan of audit_logs."""
    get_rows_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(rgm.db, "get_rows", get_rows_mock)
    await rgm._check()
    get_rows_mock.assert_awaited_once()
    _, kwargs = get_rows_mock.await_args
    assert kwargs["filters"]["action"] == rgm._REALTIME_EVENT_ACTION
    assert "$gte" in kwargs["filters"]["created_at"]
