"""P1 support-ticket SLA breach detection (ACTION_ITEMS.md G8).

Pins:
  - sla_deadline() / is_breached() — pure deadline computation, P1 = "Urgent"
    priority only, closed tickets never breach.
  - run_sweep() claims each breached ticket atomically before emitting the
    spinr_support_ticket_sla_breach_total metric — a lost claim race (another
    replica already claimed it) must NOT double-count the metric.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import metrics
from backend.utils.support_sla import (
    SLA_HOURS,
    is_breached,
    run_sweep,
    sla_deadline,
)

pytestmark = pytest.mark.unit


def _counter_total(name: str) -> int:
    return sum(metrics.snapshot()["counters"].get(name, {}).values())


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# sla_deadline / is_breached
# --------------------------------------------------------------------------


def test_sla_deadline_only_for_urgent_priority():
    created = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    assert sla_deadline(_iso(created), "Urgent") == created + timedelta(hours=SLA_HOURS)
    assert sla_deadline(_iso(created), "High") is None
    assert sla_deadline(_iso(created), "Medium") is None
    assert sla_deadline(_iso(created), None) is None


def test_sla_deadline_unparseable_created_time_returns_none():
    assert sla_deadline("not-a-date", "Urgent") is None
    assert sla_deadline(None, "Urgent") is None


def test_is_breached_true_past_deadline_open_ticket():
    now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    created = now - timedelta(hours=SLA_HOURS, minutes=1)
    row = {"created_time": _iso(created), "priority": "Urgent", "status": "Open", "status_type": "Open"}
    assert is_breached(row, now=now) is True


def test_is_breached_false_before_deadline():
    now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    created = now - timedelta(minutes=30)
    row = {"created_time": _iso(created), "priority": "Urgent", "status": "Open", "status_type": "Open"}
    assert is_breached(row, now=now) is False


def test_is_breached_false_when_closed():
    now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    created = now - timedelta(hours=SLA_HOURS, minutes=30)
    row = {"created_time": _iso(created), "priority": "Urgent", "status": "Closed", "status_type": "Closed"}
    assert is_breached(row, now=now) is False
    # A custom "Resolved"-style status without status_type still counts as
    # closed (matches zoho_desk_sync.py's own closed-ticket check).
    row2 = {"created_time": _iso(created), "priority": "Urgent", "status": "Resolved (Closed)", "status_type": ""}
    assert is_breached(row2, now=now) is False


def test_is_breached_false_for_non_p1_priority():
    now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    created = now - timedelta(hours=SLA_HOURS, minutes=30)
    row = {"created_time": _iso(created), "priority": "High", "status": "Open", "status_type": "Open"}
    assert is_breached(row, now=now) is False


# --------------------------------------------------------------------------
# run_sweep — atomic claim + metric emission
# --------------------------------------------------------------------------


def _breached_row(zoho_id: str = "zt-1", hours_over: float = 0.5) -> dict:
    created = datetime.now(timezone.utc) - timedelta(hours=SLA_HOURS + hours_over)
    return {
        "zoho_id": zoho_id,
        "created_time": _iso(created),
        "priority": "Urgent",
        "status": "Open",
        "status_type": "Open",
    }


@pytest.mark.anyio
async def test_run_sweep_claims_and_emits_metric_once():
    row = _breached_row()
    before = _counter_total("spinr_support_ticket_sla_breach_total")

    with (
        patch("backend.utils.support_sla.db_supabase.get_rows", new=AsyncMock(return_value=[row])),
        patch(
            "backend.utils.support_sla.db_supabase.update_one", new=AsyncMock(return_value={"zoho_id": "zt-1"})
        ) as m_update,
    ):
        result = await run_sweep()

    assert result == {"breached": 1}
    assert _counter_total("spinr_support_ticket_sla_breach_total") == before + 1
    # Claim filter must be the idempotency guard, not an unconditional update.
    claim_filters = m_update.call_args.args[1]
    assert claim_filters == {"zoho_id": "zt-1", "sla_breach_alerted_at": None}


@pytest.mark.anyio
async def test_run_sweep_lost_claim_race_does_not_double_count():
    """Another replica claimed the ticket first (update_one returns 0 rows /
    None) — this replica must NOT increment the metric or log a breach."""
    row = _breached_row()
    before = _counter_total("spinr_support_ticket_sla_breach_total")

    with (
        patch("backend.utils.support_sla.db_supabase.get_rows", new=AsyncMock(return_value=[row])),
        patch("backend.utils.support_sla.db_supabase.update_one", new=AsyncMock(return_value=None)),
    ):
        result = await run_sweep()

    assert result == {"breached": 0}
    assert _counter_total("spinr_support_ticket_sla_breach_total") == before


@pytest.mark.anyio
async def test_run_sweep_skips_rows_missing_zoho_id():
    row = _breached_row()
    row["zoho_id"] = None

    with (
        patch("backend.utils.support_sla.db_supabase.get_rows", new=AsyncMock(return_value=[row])),
        patch("backend.utils.support_sla.db_supabase.update_one", new=AsyncMock()) as m_update,
    ):
        result = await run_sweep()

    assert result == {"breached": 0}
    m_update.assert_not_called()


@pytest.mark.anyio
async def test_run_sweep_no_candidates_is_a_noop():
    with (
        patch("backend.utils.support_sla.db_supabase.get_rows", new=AsyncMock(return_value=[])),
        patch("backend.utils.support_sla.db_supabase.update_one", new=AsyncMock()) as m_update,
    ):
        result = await run_sweep()

    assert result == {"breached": 0}
    m_update.assert_not_called()
