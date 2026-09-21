"""Per-app scoping of the in-app notification inbox (migration 436).

A dual-role user is a single ``users`` row (routes/auth.py reuses the row
found by ``get_user_by_phone`` on OTP verify, setting both ``is_rider`` and
``is_driver``), so every inbox row keyed on ``user_id`` alone was rendered by
both apps. These tests pin the write half: the ``audience`` column is derived
from the caller's own ``target_app`` at the single choke point every push
flows through, and is never guessed.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _inbox_row(insert_mock):
    """The row dict handed to db.insert_one by the background inbox write."""
    assert insert_mock.await_args_list, "no inbox row was written"
    return insert_mock.await_args_list[0].args[1]


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("target_app", "expected_audience"),
    [
        ("rider", "rider"),
        ("driver", "driver"),
        (None, "both"),
    ],
)
async def test_record_inbox_notification_derives_audience_from_target_app(target_app, expected_audience):
    from backend import features as f

    with patch.object(f.db, "insert_one", AsyncMock()) as insert:
        f._record_inbox_notification("user-1", "Title", "Body", {"type": "ride_update"}, target_app)
        await asyncio.sleep(0)

    assert _inbox_row(insert)["audience"] == expected_audience


@pytest.mark.anyio
@pytest.mark.unit
async def test_unrecognised_target_app_falls_back_to_both_not_to_itself():
    """A bad value must widen to 'both', never be written through.

    The CHECK constraint added by migration 436 only allows
    rider/driver/both, so writing an unexpected string through would turn a
    cosmetic bug into a failed insert — and a dropped inbox row is worse than
    a duplicated one.
    """
    from backend import features as f

    with patch.object(f.db, "insert_one", AsyncMock()) as insert:
        f._record_inbox_notification("user-1", "Title", "Body", {"type": "ride_update"}, "admin")
        await asyncio.sleep(0)

    assert _inbox_row(insert)["audience"] == "both"


@pytest.mark.anyio
@pytest.mark.unit
async def test_send_push_notification_carries_target_app_into_the_inbox_row():
    """End-to-end through the choke point, not just the helper in isolation.

    This is the assertion that actually protects the user-visible behavior:
    a rider-directed push must not land in the driver app's Notifications
    list for a dual-role account.
    """
    from backend import features as f

    with (
        patch.object(f.db, "get_rows", AsyncMock(return_value=[])),
        patch.object(f.db, "find_one", AsyncMock(return_value={"fcm_token_rider": "tok-rider"})),
        patch.object(f, "_deliver_push_now", AsyncMock(return_value=True)),
        patch.object(f.db, "insert_one", AsyncMock()) as insert,
    ):
        assert (
            await f.send_push_notification(
                "dual-role-user",
                "Driver Assigned!",
                "Your driver is on the way.",
                {"type": "driver_accepted", "ride_id": "r-1"},
                target_app="rider",
            )
            is True
        )
        await asyncio.sleep(0)

    row = _inbox_row(insert)
    assert row["audience"] == "rider"
    assert row["user_id"] == "dual-role-user"


@pytest.mark.anyio
@pytest.mark.unit
async def test_undeclared_target_app_stays_visible_in_both_apps():
    """The additive guarantee: an unswept call site must not lose its inbox row.

    Every push whose call site has not yet declared a target_app keeps
    today's behavior (visible in both apps) rather than silently vanishing
    from one of them mid-rollout.
    """
    from backend import features as f

    with (
        patch.object(f.db, "get_rows", AsyncMock(return_value=[])),
        patch.object(f.db, "find_one", AsyncMock(return_value={"fcm_token": "tok"})),
        patch.object(f, "_deliver_push_now", AsyncMock(return_value=True)),
        patch.object(f.db, "insert_one", AsyncMock()) as insert,
    ):
        await f.send_push_notification("u", "Account suspended", "…", {"type": "account"})
        await asyncio.sleep(0)

    assert _inbox_row(insert)["audience"] == "both"
