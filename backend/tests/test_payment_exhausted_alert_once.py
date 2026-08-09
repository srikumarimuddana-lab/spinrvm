"""Payment exhaustion must alert exactly once per ride, on every path.

Three branches reach exhaustion — the already-exhausted branch at the top of
the scan, the unexpected-intent-state branch, and the exception branch — and
only the first claimed before alerting. The other two fire on the tick the
counter *crosses* MAX_RETRIES; the first then fires again on the next tick.

That produced two admin alerts five minutes apart, which nobody noticed. It
matters now that the rider gets an email: two identical "you are blocked from
booking" messages is how you train someone to ignore you.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.payment_retry as pr

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_RIDE_ID = "ride-1"


async def test_claim_returns_true_only_for_the_winner():
    with patch.object(pr.db, "update_one", AsyncMock(return_value={"id": _RIDE_ID})):
        assert await pr._claim_exhausted_alert(_RIDE_ID) is True
    with patch.object(pr.db, "update_one", AsyncMock(return_value=None)):
        assert await pr._claim_exhausted_alert(_RIDE_ID) is False


async def test_claim_is_a_compare_and_swap_on_the_unalerted_flag():
    # Filtering on the current value is what makes it atomic across the
    # replicas this loop runs on.
    update = AsyncMock(return_value={"id": _RIDE_ID})
    with patch.object(pr.db, "update_one", update):
        await pr._claim_exhausted_alert(_RIDE_ID)
    table, filters, body = update.await_args[0]
    assert table == "rides"
    assert filters == {"id": _RIDE_ID, "admin_alerted_payment_exhausted": False}
    assert body["$set"]["admin_alerted_payment_exhausted"] is True


async def test_second_caller_on_the_same_ride_does_not_alert():
    """The real-world sequence: a transition path alerts, then the next tick's
    already-exhausted branch tries again and must find the flag taken."""
    alert = AsyncMock()
    # First call wins the claim, second loses it.
    with (
        patch.object(pr.db, "update_one", AsyncMock(side_effect=[{"id": _RIDE_ID}, None])),
        patch.object(pr, "_alert_admins_payment_exhausted", alert),
    ):
        if await pr._claim_exhausted_alert(_RIDE_ID):
            await pr._alert_admins_payment_exhausted({"id": _RIDE_ID})
        if await pr._claim_exhausted_alert(_RIDE_ID):
            await pr._alert_admins_payment_exhausted({"id": _RIDE_ID})
    alert.assert_awaited_once()


async def test_every_exhaustion_branch_is_gated_on_the_claim():
    """Guards against a fourth branch being added without the claim.

    Asserted on the source rather than by driving each branch: the two
    transition branches need a live Stripe intent in a specific state to
    reach, and this catches the omission the same way.
    """
    import inspect

    src = inspect.getsource(pr.retry_failed_payments)
    calls = src.count("await _alert_admins_payment_exhausted(")
    gated = src.count("await _claim_exhausted_alert(")
    assert calls >= 3, "expected the three known exhaustion branches"
    assert gated == calls, "every _alert_admins_payment_exhausted call must be gated on a claim"


async def test_rider_email_is_sent_from_the_alert():
    email = AsyncMock(return_value=True)
    with (
        patch.object(pr, "send_payment_blocked_email", email),
        patch.object(pr.manager, "broadcast_to_admins", AsyncMock()),
        patch.object(pr.db, "get_rows", AsyncMock(return_value=[])),
    ):
        await pr._alert_admins_payment_exhausted({"id": _RIDE_ID, "rider_id": "u1", "total_fare": "31.20"})
    email.assert_awaited_once()
    assert email.await_args.args[0] == "u1"


async def test_rider_email_failure_does_not_stop_the_admin_alerts():
    """The admin alerts are the operational signal that a human is needed.

    Nothing about the rider email may cost them — not a provider outage, not a
    failed lookup, not a future refactor of that module. The sender swallows
    its own errors today, so this drives the failure past that boundary to
    prove the guarantee holds locally rather than by inheritance.
    """
    broadcast = AsyncMock()
    with (
        patch.object(pr, "send_payment_blocked_email", AsyncMock(side_effect=RuntimeError("SES down"))),
        patch.object(pr.manager, "broadcast_to_admins", broadcast),
        patch.object(pr.db, "get_rows", AsyncMock(return_value=[])),
    ):
        await pr._alert_admins_payment_exhausted({"id": _RIDE_ID, "rider_id": "u1", "total_fare": "1"})
    broadcast.assert_awaited_once()


async def test_no_rider_id_skips_the_email_without_erroring():
    email = AsyncMock(return_value=True)
    with (
        patch.object(pr, "send_payment_blocked_email", email),
        patch.object(pr.manager, "broadcast_to_admins", AsyncMock()),
        patch.object(pr.db, "get_rows", AsyncMock(return_value=[])),
    ):
        await pr._alert_admins_payment_exhausted({"id": _RIDE_ID, "total_fare": "1"})
    email.assert_not_awaited()
