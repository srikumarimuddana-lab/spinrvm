"""WS-1 subtasks 1-2 (plans/2026-09-03-path-to-a-implementation-plan.md):
money-path app_settings flag reads must never silently proceed as if the
flag were enabled/disabled-as-before on a read error.

Before this change, both settle_corporate's corporate_billing_enabled kill
switch and _atomic_settle_enabled's ledger_atomic_settle_enabled flag logged
a `logger.warning(...)` and continued on any settings-read exception --
exactly the anti-pattern CLAUDE.md's "Do not silently swallow errors"
section forbids. The two flags now have deliberately different failure
semantics (see the ADR this change-log links):

  * corporate_billing_enabled (settle_corporate): fails CLOSED. Its entire
    purpose is to stop corporate money movement during an incident, so a
    settings-read error must behave the same as the flag being off --
    otherwise the kill switch cannot be trusted during exactly the incident
    it exists for.
  * ledger_atomic_settle_enabled (_atomic_settle_enabled): keeps its
    existing fall-back-to-legacy-path behavior on a read error (a settle
    must not fail outright just because a flag read blipped -- the legacy
    path is itself a fully safe settlement path), but the failure is now
    logged at ERROR (not WARNING) and counted, instead of silently
    swallowed.

Both paths increment spinr_payment_settings_read_failed_total{flag=...} so
the read failure itself is observable even when the caller can't see it.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.payment_service import _atomic_settle_enabled, settle_corporate

_RIDE = {
    "id": "ride_1",
    "rider_id": "rider_1",
    "corporate_account_id": "company_1",
    "corporate_member_id": "member_1",
}


@pytest.mark.anyio
async def test_settle_corporate_fails_closed_on_settings_read_error():
    """A settings-read exception must behave exactly like the flag being
    explicitly off: 503, no wallet delta attempted, no silent proceed."""
    apply_debit_mock = AsyncMock()
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch("backend.utils.metrics.inc") as metric_inc_mock,
        patch("backend.services.payment_service.corporate_allowance_service.apply_ride_debit", apply_debit_mock),
        patch("backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock()) as member_mock,
    ):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is False
    assert result.status_code == 503

    # Must never reach the member lookup / wallet debit -- the kill switch
    # short-circuits before any of settle_corporate's money-moving logic.
    member_mock.assert_not_awaited()
    apply_debit_mock.assert_not_awaited()

    metric_inc_mock.assert_called_once_with(
        "spinr_payment_settings_read_failed_total",
        {"flag": "corporate_billing_enabled"},
    )


@pytest.mark.anyio
async def test_fail_closed_releases_the_settlement_claim():
    """auto_settle_guest_corporate claims the ride pending/failed ->
    'processing' BEFORE calling settle_corporate and, per its own comment,
    relies on settle_corporate resetting payment_status on its known failure
    paths (its except-branch only fires on a raise; this path returns). Every
    other failure branch in settle_corporate resets it; the fail-closed branch
    must too, or the ride sticks at 'processing' forever -- the guest-corporate
    retry sweep only polls 'pending', and stripe_reconcile's healer bails on a
    ride with no payment_intent_id, which a company_allowance ride never has.
    """
    update_ride_mock = AsyncMock()
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch("backend.utils.metrics.inc"),
        patch("backend.services.payment_service.db_supabase.update_ride", update_ride_mock),
    ):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.status_code == 503
    update_ride_mock.assert_awaited_once_with("ride_1", {"payment_status": "pending"})


@pytest.mark.anyio
async def test_fail_closed_still_503s_when_the_claim_release_also_fails():
    """The read failure that triggers this branch is a degraded settings/DB
    read, which can take the release write down too. A failed release must be
    logged, not raised -- it must never mask the 503 the caller depends on."""
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch("backend.utils.metrics.inc"),
        patch(
            "backend.services.payment_service.db_supabase.update_ride",
            AsyncMock(side_effect=RuntimeError("db still down")),
        ),
    ):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is False
    assert result.status_code == 503


@pytest.mark.anyio
async def test_settle_corporate_explicit_false_still_503s():
    """Regression guard: the flag explicitly set to False must keep working
    exactly as before -- this change only touches the read-error branch."""
    with patch(
        "backend.settings_loader.get_app_settings",
        AsyncMock(return_value={"corporate_billing_enabled": False}),
    ):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is False
    assert result.status_code == 503
    assert result.error == "Corporate billing is temporarily disabled"


@pytest.mark.anyio
async def test_atomic_settle_enabled_logs_and_counts_on_read_error():
    """_atomic_settle_enabled keeps falling back to the legacy (non-RPC)
    settle path on a read error -- that path is itself fully safe -- but the
    failure must now be counted, not silently swallowed."""
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch("backend.utils.metrics.inc") as metric_inc_mock,
    ):
        use_rpc = await _atomic_settle_enabled()

    assert use_rpc is False
    metric_inc_mock.assert_called_once_with(
        "spinr_payment_settings_read_failed_total",
        {"flag": "ledger_atomic_settle_enabled"},
    )


@pytest.mark.anyio
async def test_atomic_settle_enabled_no_metric_on_successful_read():
    """The counter must only fire on an actual read failure, not on every
    call -- a flag genuinely off (or unset) is normal, not an error."""
    with (
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch("backend.utils.metrics.inc") as metric_inc_mock,
    ):
        use_rpc = await _atomic_settle_enabled()

    assert use_rpc is False  # ledger_atomic_settle_enabled defaults to False
    metric_inc_mock.assert_not_called()
