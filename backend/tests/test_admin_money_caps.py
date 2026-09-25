"""N23 / ADMIN-OPS-001: per-admin daily cap + single-action alert on admin money
actions (services/admin_money_caps.py).

The daily-total read goes through the real db_supabase.get_rows ->
repositories._base path against the conftest ``mock_supabase_client`` (already
patched in as ``repositories._base.supabase`` by the autouse fixture), so the
PostgREST filter chain the service builds is asserted, not just a stubbed
return value. Settings and the audit writer are patched on the service module.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

MOD = "backend.services.admin_money_caps"
ADMIN = {"id": "admin-A", "role": "super_admin"}


def _row(action, **details):
    return {"action": action, "details": details}


# admin-A's own rows today: $40 credit, $25 debit, $30 approved refund = $95.
# The rejected dispute and support.py's no-refund resolve row move nothing.
TODAY_ROWS = [
    _row("wallet_credit", amount="40.00"),
    _row("wallet_debit", amount="25.00"),
    _row("dispute_resolved", resolution="approved", refund_amount="30.00"),
    _row("dispute_resolved", resolution="rejected", refund_amount="50.00"),
    _row("dispute_resolved", status="resolved", notes="support.py resolve"),
]


@pytest.fixture
def svc(monkeypatch):
    import backend.services.admin_money_caps as m

    # Read back in tests as svc.log_admin_action (the mock, while patched).
    monkeypatch.setattr(m, "log_admin_action", AsyncMock(return_value="audit-1"))
    return m


def _settings(monkeypatch, svc, **values):
    monkeypatch.setattr(svc, "get_app_settings", AsyncMock(return_value=dict(values)))


@pytest.fixture
def audit_rows(mock_supabase_client):
    """Serve ``rows`` for the audit_logs read through the mocked client."""
    table = mock_supabase_client.table.return_value
    table.range.return_value = table  # get_rows uses .range() when offset is set
    state = {"rows": []}
    table.execute.side_effect = lambda: MagicMock(data=list(state["rows"]))
    return state


async def test_under_cap_is_allowed(monkeypatch, svc, audit_rows, mock_supabase_client):
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="100.00")
    audit_rows["rows"] = TODAY_ROWS
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("5.00"), action="wallet_credit", resource="user", resource_id="user-1"
    )  # 95 + 5 == 100: at the cap is allowed
    svc.log_admin_action.assert_not_awaited()


async def test_over_cap_raises_403_and_audits_block(monkeypatch, svc, audit_rows):
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="100.00")
    audit_rows["rows"] = TODAY_ROWS
    with pytest.raises(HTTPException) as exc:
        await svc.enforce_admin_money_action_cap(
            ADMIN, Decimal("5.01"), action="dispute_refund", resource="dispute", resource_id="disp-1"
        )
    assert exc.value.status_code == 403
    assert "$95.00 already moved" in exc.value.detail
    assert "Nothing was moved" in exc.value.detail
    args = svc.log_admin_action.await_args.args
    assert args[1] == "admin_money_cap_blocked"
    assert args[4] == {"money_action": "dispute_refund", "amount": "5.01", "moved_today": "95.00", "cap": "100.00"}


async def test_negative_amount_counts_as_absolute(monkeypatch, svc, audit_rows):
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="100.00")
    audit_rows["rows"] = TODAY_ROWS
    with pytest.raises(HTTPException) as exc:
        await svc.enforce_admin_money_action_cap(
            ADMIN, Decimal("-10"), action="wallet_debit", resource="user", resource_id="user-1"
        )
    assert exc.value.status_code == 403


async def test_daily_sum_filters_to_this_admin_today_and_money_actions(
    monkeypatch, svc, audit_rows, mock_supabase_client
):
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="1000.00")
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("1"), action="wallet_credit", resource="user", resource_id="user-1"
    )
    mock_supabase_client.table.assert_any_call("audit_logs")
    table = mock_supabase_client.table.return_value
    table.select.assert_called_with("action,details")
    table.eq.assert_any_call("actor_id", "admin-A")
    table.in_.assert_any_call("action", ["wallet_credit", "wallet_debit", "dispute_resolved"])
    gte_col, gte_val = table.gte.call_args.args
    assert gte_col == "created_at"
    assert gte_val.endswith("T00:00:00+00:00")  # start of the UTC day


async def test_cap_sums_across_actions_for_same_admin_only(monkeypatch, svc):
    """Another admin's rows must not count toward admin-A's total."""
    rows_by_admin = {
        "admin-A": TODAY_ROWS,
        "admin-B": [_row("wallet_credit", amount="900.00")],
    }

    async def fake_get_rows(table, filters=None, **kwargs):
        assert table == "audit_logs"
        return list(rows_by_admin[filters["actor_id"]])

    monkeypatch.setattr(svc.db_supabase, "get_rows", fake_get_rows)
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="100.00")

    assert await svc._moved_today("admin-A") == Decimal("95.00")
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("5"), action="wallet_debit", resource="user", resource_id="user-1"
    )
    with pytest.raises(HTTPException):
        await svc.enforce_admin_money_action_cap(
            {"id": "admin-B"}, Decimal("101"), action="wallet_debit", resource="user", resource_id="user-1"
        )


async def test_null_cap_and_threshold_skip_the_query_entirely(monkeypatch, svc):
    get_rows = AsyncMock(side_effect=AssertionError("no audit read when disabled"))
    monkeypatch.setattr(svc.db_supabase, "get_rows", get_rows)
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin=None, admin_money_alert_threshold=None)
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("9999.99"), action="wallet_credit", resource="user", resource_id="user-1"
    )
    get_rows.assert_not_awaited()
    svc.log_admin_action.assert_not_awaited()


async def test_threshold_alert_fires_but_allows(monkeypatch, svc):
    import sentry_sdk

    capture = MagicMock()
    monkeypatch.setattr(sentry_sdk, "capture_message", capture)
    _settings(monkeypatch, svc, admin_money_alert_threshold="500.00")
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("500.00"), action="wallet_credit", resource="user", resource_id="user-1"
    )
    args = svc.log_admin_action.await_args.args
    assert args[1:4] == ("admin_money_threshold_alert", "user", "user-1")
    assert args[4] == {"money_action": "wallet_credit", "amount": "500.00", "threshold": "500.00"}
    assert capture.call_args.kwargs["tags"]["domain"] == "admin"
    assert capture.call_args.kwargs["contexts"]["admin_money"]["admin_id"] == "admin-A"


async def test_below_threshold_does_not_alert(monkeypatch, svc):
    _settings(monkeypatch, svc, admin_money_alert_threshold="500.00")
    await svc.enforce_admin_money_action_cap(
        ADMIN, Decimal("499.99"), action="wallet_credit", resource="user", resource_id="user-1"
    )
    svc.log_admin_action.assert_not_awaited()


async def test_sum_query_failure_fails_closed_503(monkeypatch, svc, caplog):
    try:
        from backend.utils.error_handling import DatabaseError
    except ImportError:  # pragma: no cover
        from utils.error_handling import DatabaseError

    err = DatabaseError(details={"original": "PGRST timeout"})
    monkeypatch.setattr(svc.db_supabase, "get_rows", AsyncMock(side_effect=err))
    _settings(monkeypatch, svc, admin_money_daily_cap_per_admin="100.00")
    with caplog.at_level("ERROR", logger=MOD):
        with pytest.raises(HTTPException) as exc:
            await svc.enforce_admin_money_action_cap(
                ADMIN, Decimal("1"), action="wallet_credit", resource="user", resource_id="user-1"
            )
    assert exc.value.status_code == 503
    assert "PGRST timeout" in caplog.text


async def test_settings_read_failure_fails_closed_503(monkeypatch, svc):
    monkeypatch.setattr(svc, "get_app_settings", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(HTTPException) as exc:
        await svc.enforce_admin_money_action_cap(
            ADMIN, Decimal("1"), action="wallet_credit", resource="user", resource_id="user-1"
        )
    assert exc.value.status_code == 503
