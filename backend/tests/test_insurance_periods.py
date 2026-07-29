"""Unit tests for backend.utils.insurance_periods (M-5b).

Compliance-grade audit logging — these tests pin the behaviour we
explicitly need under failure: programmer-error inputs raise, but DB
errors NEVER propagate to the caller.

record_period_transition does the close-old + open-new insurance
period transition via ONE atomic Postgres RPC
(record_insurance_period_transition) rather than two separate
.table().update() + .table().insert() calls — the RPC owns atomicity
around the partial unique index `driver_insurance_periods_open` (see
the module's own docstring). Tests here mock/assert against
sb.rpc("record_insurance_period_transition", ...) accordingly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.utils import insurance_periods

pytestmark = pytest.mark.unit


def _fake_supabase(rpc_data=None, rpc_raises: Exception | None = None) -> MagicMock:
    """Build a minimal Supabase mock around the single RPC call the
    real code makes: sb.rpc("record_insurance_period_transition", params).execute()."""
    sb = MagicMock()
    rpc_chain = MagicMock()
    sb.rpc.return_value = rpc_chain
    if rpc_raises is not None:
        rpc_chain.execute.side_effect = rpc_raises
    else:
        resp = MagicMock()
        resp.data = rpc_data if rpc_data is not None else {"status": "ok"}
        rpc_chain.execute.return_value = resp
    return sb


@pytest.mark.anyio
async def test_happy_path_period_1_no_prior() -> None:
    """First go_online: one atomic RPC call opens period 1, nothing prior to close."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    sb.rpc.assert_called_once_with(
        "record_insurance_period_transition",
        {"p_driver_id": "d1", "p_new_period": 1, "p_ride_id": None},
    )


@pytest.mark.anyio
async def test_transition_period_1_to_period_2_with_ride() -> None:
    """Prior period open → RPC atomically closes it and opens period 2 carrying ride_id."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=2,
            ride_id="r99",
        )

    sb.rpc.assert_called_once_with(
        "record_insurance_period_transition",
        {"p_driver_id": "d1", "p_new_period": 2, "p_ride_id": "r99"},
    )


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
    sb = _fake_supabase(rpc_raises=RuntimeError("supabase exploded"))
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
    """Race / no-op transition: the RPC itself reports status="noop" when the
    driver already has this period open (the partial unique index serialises
    racing callers; the loser gets this status rather than an exception)."""
    sb = _fake_supabase(rpc_data={"status": "noop"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        with caplog.at_level("INFO"):
            await insurance_periods.record_period_transition(
                driver_id="d1",
                new_period=1,
            )
    assert any("no-op transition" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_supabase_client_unavailable_drops_transition_and_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """db_supabase.supabase is None (client not initialised) — the transition
    is dropped (never raised, per the module's compliance trade-off) and an
    ERROR is logged so it's Sentry-visible and backfillable from `rides`."""
    with patch.object(insurance_periods.db_supabase, "supabase", None):
        with caplog.at_level("ERROR"):
            await insurance_periods.record_period_transition(
                driver_id="d1",
                new_period=1,
            )
    assert any("supabase client unavailable" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_metric_inc_is_a_noop_when_metrics_module_unavailable() -> None:
    """_metric_inc guards against _metrics being None (the module's own
    dual-import fallback when utils.metrics can't be imported) — must not
    raise, and the RPC call itself must still complete normally."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with (
        patch.object(insurance_periods.db_supabase, "supabase", sb),
        patch.object(insurance_periods, "_metrics", None),
    ):
        await insurance_periods.record_period_transition(driver_id="d1", new_period=1)
    sb.rpc.assert_called_once()


@pytest.mark.anyio
async def test_metric_inc_failure_is_swallowed() -> None:
    """Metrics are explicitly best-effort (module docstring) — a broken
    metrics backend must never fail the insurance-period write itself."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    broken_metrics = MagicMock()
    broken_metrics.inc.side_effect = RuntimeError("metrics backend down")
    with (
        patch.object(insurance_periods.db_supabase, "supabase", sb),
        patch.object(insurance_periods, "_metrics", broken_metrics),
    ):
        # Must not raise despite _metrics.inc blowing up internally.
        await insurance_periods.record_period_transition(driver_id="d1", new_period=1)
    sb.rpc.assert_called_once()


@pytest.mark.anyio
async def test_race_status_from_rpc_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """RPC reports status="race" (concurrent transition lost the partial
    unique index race) — logged as a warning, never raised."""
    sb = _fake_supabase(rpc_data={"status": "race"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        with caplog.at_level("WARNING"):
            await insurance_periods.record_period_transition(
                driver_id="d1",
                new_period=2,
                ride_id="r1",
            )
    assert any("concurrent transition" in r.message for r in caplog.records)


# ── Edge-flow tests (M-5c) ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_period_2_to_0_driver_offline_mid_ride() -> None:
    """Driver goes offline while period 2 is open (ride in driver_assigned).
    The prior period 2 row closes and period 0 (offline) is opened.
    """
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=0,
        )

    sb.rpc.assert_called_once_with(
        "record_insurance_period_transition",
        {"p_driver_id": "d1", "p_new_period": 0, "p_ride_id": None},
    )


@pytest.mark.anyio
async def test_period_2_to_1_on_ride_cancel_after_driver_arrived() -> None:
    """Rider cancels after driver_arrived: period 2 closes, period 1 opened."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    sb.rpc.assert_called_once_with(
        "record_insurance_period_transition",
        {"p_driver_id": "d1", "p_new_period": 1, "p_ride_id": None},
    )


@pytest.mark.anyio
async def test_period_3_to_1_on_ride_complete() -> None:
    """Trip completes: period 3 closes (ride_id present), period 1 opened."""
    sb = _fake_supabase(rpc_data={"status": "ok"})
    with patch.object(insurance_periods.db_supabase, "supabase", sb):
        await insurance_periods.record_period_transition(
            driver_id="d1",
            new_period=1,
        )

    sb.rpc.assert_called_once_with(
        "record_insurance_period_transition",
        {"p_driver_id": "d1", "p_new_period": 1, "p_ride_id": None},
    )
