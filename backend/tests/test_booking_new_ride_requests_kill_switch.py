"""Tests for the G5 demand-side kill switch (ACTION_ITEMS.md).

new_ride_requests_enabled is checked at the very top of POST /rides
(create_ride), before validate_ride_location or any DB write — distinct
from the E5 flags (test_kill_switch_flags.py), none of which stop new
bookings generally.

**Settings-read failure falls back to the LAST KNOWN value of the flag**
(2026-09-20 review, finding E2). This file previously pinned plain
fail-OPEN, on the reasoning that this flag blocks ALL new bookings
platform-wide — a far wider blast radius than a narrowly-scoped money kill
switch — so a degraded app_settings read must not itself take booking down.
That reasoning was right and is preserved; what it missed is that the
flag's default is True ("not killing anything"), so a read failure silently
RESUMED bookings during exactly the incident an operator had paused them
for. A kill switch that stops working when things break is not a kill
switch.

Last-known-good satisfies both, because get_app_settings() only writes its
cache after a successful read — a failed read never overwrites it:

  * paused, then the DB degrades       -> last known False -> still paused
  * steady state, one transient blip   -> last known True  -> booking proceeds
  * cold start, never read settings    -> nothing to fall back to -> proceed,
                                          matching the original posture

Contrast services/payment_service.py::settle_corporate's
corporate_billing_enabled, which fails CLOSED outright on the same kind of
read error (WS-1, plans/2026-09-03-path-to-a-implementation-plan.md): its
blast radius is one corporate settlement, so hard-stopping is affordable
there and is not here. The two flags still differ by design.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-g5-1"
_USER = {"id": _RIDER_ID}


def _starlette_request(method="POST", path="/rides"):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }
    return SR(scope)


def _body(**kw):
    from backend.schemas import CreateRideRequest

    defaults = dict(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


async def test_flag_off_rejects_new_ride_with_clean_503():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with patch(
        "backend.routes.rides._deps.get_app_settings",
        new_callable=AsyncMock,
        return_value={"new_ride_requests_enabled": False},
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert "temporarily unavailable" in exc_info.value.detail.lower()


async def test_flag_off_rejects_before_any_db_write():
    """The 503 must fire before validate_ride_location or any DB call --
    confirms the guard is the very first thing in the handler, not just
    somewhere before the response."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location") as mock_validate,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides._deps.get_app_settings",
            new_callable=AsyncMock,
            return_value={"new_ride_requests_enabled": False},
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    mock_validate.assert_not_called()
    mock_supabase.get_rows.assert_not_called()


async def test_flag_omitted_defaults_to_enabled():
    """A settings row that predates this flag (or the in-process defaults
    dict) must not silently start rejecting bookings -- default-True is
    the whole point of a kill switch."""
    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        # Fails on the next real DB call (service_areas fetch) -- proves we
        # got PAST the kill-switch guard rather than being rejected by it.
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    # 503 from the service_areas failure downstream, not from the kill
    # switch (which would also be 503 -- the assertion that matters is
    # that we reached this later guard at all, per mock_validate calls
    # implicitly proven by get_rows having been called 3 times).
    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


def _read_fails(last_known):
    """Patch context where the live settings read raises and the last-known-good
    cache holds ``last_known`` (pass None for 'this process never loaded').

    ``get_last_known_app_settings`` is patched explicitly in every one of these
    tests rather than left to the real module-level ``_settings_cache``. That
    cache is process-global and is populated by whatever else ran earlier in
    the session, so leaving it unpatched would make these assertions depend on
    pytest ordering -- passing alone and failing in a full run, or worse, the
    reverse.
    """
    return (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db"),
        patch("backend.routes.rides._deps.db_supabase"),
        patch(
            "backend.routes.rides._deps.get_app_settings",
            new_callable=AsyncMock,
            side_effect=RuntimeError("settings db down"),
        ),
        patch("backend.routes.rides._deps.get_last_known_app_settings", return_value=last_known),
    )


async def test_read_failure_with_no_last_known_value_proceeds():
    """Cold start: this process has never completed a settings read, so there
    is nothing to fall back to. Proceed -- a process that just booted cannot
    have observed an operator's pause, and taking every booking down because
    of one cold-start read failure is the outcome the original fail-open
    posture existed to prevent."""
    from backend.routes.rides import create_ride

    p_validate, p_db, p_supabase, p_settings, p_last_known = _read_fails(None)
    with p_validate, p_db as mock_db, p_supabase as mock_supabase, p_settings, p_last_known:
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    # Proves we got past the kill-switch guard (reached the later, unrelated
    # service_areas failure) despite the settings lookup itself raising.
    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


async def test_read_failure_falls_back_to_last_known_enabled_and_proceeds():
    """Steady state plus a transient read blip: the last value seen said
    bookings were enabled, so booking must continue. This is the case the
    original fail-open behaviour protected, and it must keep working -- a
    settings hiccup must never take the whole platform's demand side down."""
    from backend.routes.rides import create_ride

    p_validate, p_db, p_supabase, p_settings, p_last_known = _read_fails({"new_ride_requests_enabled": True})
    with p_validate, p_db as mock_db, p_supabase as mock_supabase, p_settings, p_last_known:
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


async def test_read_failure_falls_back_to_last_known_paused_and_still_rejects():
    """The hole this change closes, and the reason it exists.

    An operator pauses bookings, then the database degrades. Under the old
    plain fail-open behaviour the flag's default (True) took over and bookings
    silently RESUMED -- during exactly the incident the switch was flipped for.
    The last successfully-read value said False, so it must still reject.
    """
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    p_validate, p_db, p_supabase, p_settings, p_last_known = _read_fails({"new_ride_requests_enabled": False})
    with p_validate as mock_validate, p_db, p_supabase as mock_supabase, p_settings, p_last_known:
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert "temporarily unavailable" in exc_info.value.detail.lower()
    # Same guarantee as test_flag_off_rejects_before_any_db_write: the reject
    # lands before any validation or DB work, so the fallback path is the real
    # guard and not an accidental later failure that happens to also be 503.
    mock_validate.assert_not_called()
    mock_supabase.get_rows.assert_not_called()


async def test_an_explicitly_null_flag_is_treated_as_enabled_on_the_live_path():
    """A column that EXISTS but is NULL must not pause the platform.

    `.get(key, default)` only supplies its default when the key is *absent*, so
    a settings row with `new_ride_requests_enabled = NULL` yields None, and
    `bool(None)` is False -- which under the previous `.get(..., True)` shape
    would have rejected every booking platform-wide because of a null column
    rather than a deliberate pause. Found in review; this pins the fix on the
    live path, and the test below pins it on the fallback path.
    """
    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides._deps.get_app_settings",
            new_callable=AsyncMock,
            return_value={"new_ride_requests_enabled": None},
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    # Reached the later, unrelated failure -- i.e. the kill switch let us past.
    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


async def test_an_explicitly_null_flag_in_the_last_known_value_is_treated_as_enabled():
    """Same NULL-column case, on the fallback path."""
    from backend.routes.rides import create_ride

    p_validate, p_db, p_supabase, p_settings, p_last_known = _read_fails({"new_ride_requests_enabled": None})
    with p_validate, p_db as mock_db, p_supabase as mock_supabase, p_settings, p_last_known:
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


async def test_last_known_value_missing_the_flag_is_treated_as_enabled():
    """A last-known dict from before this flag existed has no such key. It must
    read as enabled, for the same default-True reason
    test_flag_omitted_defaults_to_enabled pins on the live path -- an absent
    key is not a pause."""
    from backend.routes.rides import create_ride

    p_validate, p_db, p_supabase, p_settings, p_last_known = _read_fails({"some_other_flag": True})
    with p_validate, p_db as mock_db, p_supabase as mock_supabase, p_settings, p_last_known:
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3
