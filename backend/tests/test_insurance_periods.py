"""Unit tests for backend.utils.insurance_periods (M-5b).

Compliance-grade audit logging — these tests pin the behaviour we
explicitly need under failure: programmer-error inputs raise, but DB
errors NEVER propagate to the caller.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.utils import insurance_periods

pytestmark = pytest.mark.unit


def _fake_supabase(close_data=None, insert_raises: Exception | None = None) -> MagicMock:
    """Build a minimal Supabase mock that records the calls we make."""
    sb = MagicMock()
    table = MagicMock()
    sb.table.return_value = table

    # update().eq().is_().execute() chain — for the close step.
    update_chain = MagicMock()
    table.update.return_value = update_chain
    update_chain.eq.return_value = update_chain
    update_chain.is_.return_value = update_chain
    update_resp = MagicMock()
    update_resp.data = close_data or []
    update_chain.execute.return_value = update_resp

    # insert().execute() chain — for the open step.
    insert_chain = MagicMock()
    table.insert.return_value = insert_chain
    if insert_raises is not None:
        insert_chain.execute.side_effect = insert_raises
    else:
        insert_resp = MagicMock()
        insert_resp.data = [{"id": "new"}]
        insert_chain.execute.return_value = insert_resp

    return sb


@pytest.mark.anyio
async def test_happy_path_period_1_no_prior() -> None:
    """First go_online: nothing to close, one INSERT for period 1."""
    sb = _fake_supabase(close_data=[])
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    # close UPDATE was attempted...
    sb.table.assert_any_call("driver_insurance_periods")
    sb.table.return_value.update.assert_called_once()
    # ...and an INSERT followed.
    sb.table.return_value.insert.assert_called_once()
    inserted = sb.table.return_value.insert.call_args.args[0]
    assert inserted["driver_id"] == "d1"
    assert inserted["period"] == 1
    assert "ride_id" not in inserted


@pytest.mark.anyio
async def test_transition_period_1_to_period_2_with_ride() -> None:
    """Prior period open → close it, then open period 2 carrying ride_id."""
    sb = _fake_supabase(close_data=[{"id": "old"}])
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=2,
            ride_id="r99",
        )

    sb.table.return_value.update.assert_called_once()
    update_payload = sb.table.return_value.update.call_args.args[0]
    assert "ended_at" in update_payload
    sb.table.return_value.insert.assert_called_once()
    inserted = sb.table.return_value.insert.call_args.args[0]
    assert inserted["period"] == 2
    assert inserted["ride_id"] == "r99"


@pytest.mark.anyio
async def test_invalid_period_raises_value_error() -> None:
    with pytest.raises(ValueError, match="new_period"):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=7,
        )


@pytest.mark.anyio
async def test_period_3_without_ride_raises_value_error() -> None:
    with pytest.raises(ValueError, match="ride_id is required"):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=3,
            ride_id=None,
        )


@pytest.mark.anyio
async def test_db_failure_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """A boom from the DB layer must never propagate — caller doesn't see it."""
    sb = _fake_supabase(
        close_data=[],
        insert_raises=RuntimeError("supabase exploded"),
    )
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        with caplog.at_level("ERROR"):
            # Must NOT raise.
            await insurance_periods.record_period_transition(
                driver_id="d1",
                new_period=1,
            )
    assert any("FAILED" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_unique_violation_with_no_close_is_noop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Race / no-op transition: insert hits 23505 and close affected 0 rows."""
    sb = _fake_supabase(
        close_data=[],
        insert_raises=Exception("duplicate key value violates unique constraint (23505)"),
    )
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        with caplog.at_level("INFO"):
            await insurance_periods.record_period_transition(
                driver_id="d1",
                new_period=1,
            )
    assert any("no-op transition" in r.message for r in caplog.records)


# ── Edge-flow tests (M-5c) ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_period_2_to_0_driver_offline_mid_ride() -> None:
    """Driver goes offline while period 2 is open (ride in driver_assigned).
    The prior period 2 row closes and period 0 (offline) is opened.
    """
    sb = _fake_supabase(close_data=[{"id": "period2_row"}])
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=0,
        )

    update_payload = sb.table.return_value.update.call_args.args[0]
    assert "ended_at" in update_payload
    inserted = sb.table.return_value.insert.call_args.args[0]
    assert inserted["period"] == 0
    assert "ride_id" not in inserted


@pytest.mark.anyio
async def test_period_2_to_1_on_ride_cancel_after_driver_arrived() -> None:
    """Rider cancels after driver_arrived: period 2 closes, period 1 opened."""
    sb = _fake_supabase(close_data=[{"id": "period2_row"}])
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    update_payload = sb.table.return_value.update.call_args.args[0]
    assert "ended_at" in update_payload
    inserted = sb.table.return_value.insert.call_args.args[0]
    assert inserted["period"] == 1
    assert "ride_id" not in inserted


@pytest.mark.anyio
async def test_period_3_to_1_on_ride_complete() -> None:
    """Trip completes: period 3 closes (ride_id present), period 1 opened."""
    sb = _fake_supabase(close_data=[{"id": "period3_row"}])
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    update_payload = sb.table.return_value.update.call_args.args[0]
    assert "ended_at" in update_payload
    inserted = sb.table.return_value.insert.call_args.args[0]
    assert inserted["period"] == 1
