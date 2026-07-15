"""V2 (issue #11): broadcast_ride_status stamps a monotonic ride ``version``
into the unified ``ride_status_changed`` payload.

Contract:
  - When a caller supplies ``version=N`` (from the post-update rides row, bumped
    by the DB trigger in migration 225), every recipient — rider, driver, and
    every admin — receives that ``version`` in the payload so clients can drop
    stale / out-of-order events by comparing against the highest they've applied.
  - When no version is available (``version=None`` — an un-migrated caller, or a
    row read before the column existed), the field is OMITTED entirely, never
    emitted as ``"version": None``. A client must be able to tell "no ordering
    info" apart from "version 0"; a null in the payload blurs that line.

These are unit tests against the ConnectionManager method directly; personal and
admin fan-out are mocked so the assertion is purely about the emitted payload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def _manager():
    from socket_manager import ConnectionManager

    mgr = ConnectionManager()
    mgr.send_personal_message = AsyncMock()
    mgr.broadcast_to_admins = AsyncMock()
    return mgr


@pytest.mark.anyio
async def test_broadcast_ride_status_stamps_version_to_all_recipients():
    """version=N reaches the rider, the driver, and the admin fan-out."""
    mgr = _manager()

    await mgr.broadcast_ride_status(
        "ride_1",
        "driver_accepted",
        rider_id="r1",
        driver_user_id="d1",
        version=7,
    )

    personal_payloads = [c.args[0] for c in mgr.send_personal_message.await_args_list]
    assert len(personal_payloads) == 2  # rider + driver
    for payload in personal_payloads:
        assert payload["type"] == "ride_status_changed"
        assert payload["version"] == 7

    admin_payload = mgr.broadcast_to_admins.await_args.args[0]
    assert admin_payload["version"] == 7


@pytest.mark.anyio
async def test_broadcast_ride_status_omits_version_when_none():
    """version=None (no ordering info) must NOT appear in the payload as null."""
    mgr = _manager()

    await mgr.broadcast_ride_status(
        "ride_1",
        "completed",
        rider_id="r1",
        version=None,
    )

    payload = mgr.send_personal_message.await_args.args[0]
    assert "version" not in payload, (
        "version=None must be omitted, not emitted as 'version': None — "
        "clients cannot distinguish absent-ordering from version 0"
    )
    admin_payload = mgr.broadcast_to_admins.await_args.args[0]
    assert "version" not in admin_payload


@pytest.mark.anyio
async def test_broadcast_ride_status_omits_version_when_unset():
    """A caller that never mentions version behaves exactly as before (no key)."""
    mgr = _manager()

    await mgr.broadcast_ride_status("ride_1", "searching", rider_id="r1")

    payload = mgr.send_personal_message.await_args.args[0]
    assert "version" not in payload
