"""Phase 1 live-activity coverage (docs/live-ride-activity-plan.md §2-§4):

* content-state builder — headline mapping, area-only minimisation, lock-screen
  PII safety (no exact address, coordinates, or full surname);
* log-only dispatch — no-activity skip, END marks rows ended, UPDATE does not,
  DB errors are swallowed (best-effort, never raised into the ride path);
* register endpoint — ownership 403/404, insert vs upsert, platform validation.

Async dispatch is driven with ``asyncio.run`` (no anyio backend coupling). DB
helpers are patched on the exact module object the code under test references.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

try:
    from backend.utils import live_activity
except ImportError:  # pragma: no cover - bare-path test runs
    from utils import live_activity

try:
    from backend.routes import rides as rides_mod
except ImportError:  # pragma: no cover
    from routes import rides as rides_mod

try:
    from backend import features as features_mod
except ImportError:  # pragma: no cover
    import features as features_mod

try:
    from backend.utils import apns_client as apns_mod
except ImportError:  # pragma: no cover
    from utils import apns_client as apns_mod


# --------------------------------------------------------------------------- #
# content-state builder (pure)
# --------------------------------------------------------------------------- #


def test_area_label_strips_house_number_and_street():
    assert live_activity._area_label("1742 Main Street, Regina, SK, S4P 3A1") == "Regina"
    assert live_activity._area_label("Main Street, Regina, SK") == "Regina"
    assert live_activity._area_label("Regina, SK") == "Regina"
    assert live_activity._area_label(None) is None
    assert live_activity._area_label("   ") is None


def test_area_label_never_leaks_a_street():
    # Regression (security audit): an address WITHOUT a province token must still
    # never surface the street as the "area".
    assert live_activity._area_label("Oak Lane, Regina") == "Regina"
    assert live_activity._area_label("123 Elm Drive, Saskatoon") == "Saskatoon"
    # Street-only (no locality) → None, not the street.
    assert live_activity._area_label("Oak Lane") is None
    assert live_activity._area_label("1742 Main Street") is None


def test_area_label_keeps_multiword_cities():
    # Place-ambiguous words (Bay, Jaw…) are NOT treated as streets.
    assert live_activity._area_label("Thunder Bay, ON") == "Thunder Bay"
    assert live_activity._area_label("Moose Jaw, SK") == "Moose Jaw"


def test_first_name_only():
    assert live_activity._first_name("Jordan Smith") == "Jordan"
    assert live_activity._first_name("Jordan") == "Jordan"
    assert live_activity._first_name(None) is None


def test_vehicle_label_formats_and_degrades():
    full = {
        "vehicle_color": "Silver",
        "vehicle_make": "Honda",
        "vehicle_model": "Civic",
        "license_plate": "ABC7Q9",
    }
    assert live_activity._vehicle_label(full) == "Silver Honda Civic · ABC7Q9"
    assert live_activity._vehicle_label({"vehicle_make": "Honda", "vehicle_model": "Civic"}) == "Honda Civic"
    assert live_activity._vehicle_label({}) is None


@pytest.mark.parametrize(
    "status,area,expected",
    [
        ("driver_accepted", None, "Driver on the way"),
        ("driver_arrived", None, "Your driver has arrived"),
        ("in_progress", "Regina", "On your way to Regina"),
        ("in_progress", None, "On your way"),
        ("completed", None, "Trip complete"),
        ("cancelled", None, "Ride cancelled"),
        ("searching", None, "Ride update"),
    ],
)
def test_headline_mapping(status, area, expected):
    assert live_activity._headline(status, dropoff_area=area) == expected


def test_content_state_is_area_only_and_pii_safe():
    ride = {
        "id": "r1",
        "status": "in_progress",
        "code": "SPR-AB7QCD",
        "pickup_address": "1742 Main Street, Regina, SK",
        "dropoff_address": "4856 Elm Avenue, Saskatoon, SK",
        "driver": {
            "name": "Jordan Smith",
            "vehicle_color": "Silver",
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "license_plate": "ABC7Q9",
            "lat": 50.4452,
            "lng": -104.6189,
        },
    }
    cs = live_activity.build_content_state(ride, eta_minutes=7)
    assert cs["status"] == "in_progress"
    assert cs["headline"] == "On your way to Saskatoon"
    assert cs["eta_minutes"] == 7
    assert cs["driver_name"] == "Jordan"  # first name only
    assert cs["vehicle_label"] == "Silver Honda Civic · ABC7Q9"
    assert cs["pickup_area"] == "Regina"
    assert cs["dropoff_area"] == "Saskatoon"
    assert cs["ride_code"] == "SPR-AB7QCD"

    # Lock-screen safety: never a house number, street, surname, or coordinates.
    blob = str(cs)
    for leak in ("1742", "Main Street", "4856", "Elm Avenue", "Smith", "50.4452", "-104.6189"):
        assert leak not in blob, f"PII leak in content-state: {leak!r}"


# --------------------------------------------------------------------------- #
# dispatch (Android = FCM data, iOS = log-only)
# --------------------------------------------------------------------------- #


def _android_row():
    return [{"id": "a1", "platform": "android", "ended_at": None, "rider_id": "rider-1"}]


def test_fcm_data_stringifies_and_omits_none():
    content = {
        "status": "in_progress",
        "headline": "On your way to Regina",
        "eta_minutes": 7,
        "driver_name": "Jordan",
        "vehicle_label": None,  # omitted
        "pickup_area": "Saskatoon",
        "dropoff_area": "Regina",
        "ride_code": "SPR-AB7QCD",
    }
    data = live_activity._fcm_data(content, live_activity.EVENT_UPDATE, "r1")
    assert data["type"] == "live_activity"
    assert data["event"] == "update"
    assert data["ride_id"] == "r1"
    assert data["eta_minutes"] == "7"  # stringified
    assert "vehicle_label" not in data  # None omitted
    assert all(isinstance(v, str) for v in data.values())  # FCM requires strings


def test_dispatch_skips_when_no_activity_registered(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=[]))
    upd = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    monkeypatch.setattr(features_mod, "send_push_notification", send)

    asyncio.run(
        live_activity.send_live_activity_update({"id": "r1", "status": "in_progress"}, live_activity.EVENT_UPDATE)
    )
    upd.assert_not_awaited()
    send.assert_not_awaited()


def test_dispatch_android_update_sends_fcm_not_ended(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=_android_row()))
    upd = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    monkeypatch.setattr(features_mod, "send_push_notification", send)

    asyncio.run(
        live_activity.send_live_activity_update({"id": "r1", "status": "driver_arrived"}, live_activity.EVENT_UPDATE)
    )
    send.assert_awaited_once()
    assert send.await_args.args[0] == "rider-1"  # rider id
    assert send.await_args.kwargs["target_app"] == "rider"
    data = send.await_args.kwargs["data"]
    assert data["type"] == "live_activity" and data["event"] == "update" and data["ride_id"] == "r1"
    upd.assert_not_awaited()  # an update never ends the activity


def test_dispatch_android_end_sends_fcm_and_marks_ended(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=_android_row()))
    upd = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    monkeypatch.setattr(features_mod, "send_push_notification", send)

    asyncio.run(live_activity.send_live_activity_update({"id": "r1", "status": "completed"}, live_activity.EVENT_END))
    send.assert_awaited_once()
    assert send.await_args.kwargs["data"]["event"] == "end"
    upd.assert_awaited_once()
    assert upd.await_args.args[2]["ended_at"] is not None


def _ios_row():
    return [{"id": "a1", "platform": "ios", "ended_at": None, "rider_id": "rider-1", "push_token": "APNSTOK"}]


def test_dispatch_ios_sends_apns_not_fcm(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=_ios_row()))
    upd = AsyncMock()
    fcm = AsyncMock()
    apns = AsyncMock(return_value=(True, False))
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    monkeypatch.setattr(features_mod, "send_push_notification", fcm)
    monkeypatch.setattr(apns_mod, "send_apns_live_activity", apns)

    asyncio.run(
        live_activity.send_live_activity_update({"id": "r1", "status": "driver_arrived"}, live_activity.EVENT_UPDATE)
    )
    apns.assert_awaited_once()
    assert apns.await_args.args[0] == "APNSTOK"  # the ActivityKit token
    fcm.assert_not_awaited()  # iOS does NOT ride the FCM path
    upd.assert_not_awaited()  # an update never ends the activity


def test_dispatch_ios_dead_token_ends_row(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=_ios_row()))
    upd = AsyncMock()
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    # APNs reports the token dead (410 / BadDeviceToken) → row must be ended.
    monkeypatch.setattr(apns_mod, "send_apns_live_activity", AsyncMock(return_value=(False, True)))

    asyncio.run(
        live_activity.send_live_activity_update({"id": "r1", "status": "driver_arrived"}, live_activity.EVENT_UPDATE)
    )
    upd.assert_awaited_once()
    assert upd.await_args.args[2]["ended_at"] is not None


def test_dispatch_android_send_failure_is_swallowed_and_still_ends(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(return_value=_android_row()))
    upd = AsyncMock()
    monkeypatch.setattr(live_activity.db_supabase, "update_one", upd)
    monkeypatch.setattr(features_mod, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down")))

    # A send failure must not block the terminal mark-ended, nor raise.
    asyncio.run(live_activity.send_live_activity_update({"id": "r1", "status": "completed"}, live_activity.EVENT_END))
    upd.assert_awaited_once()


def test_dispatch_swallows_db_error(monkeypatch):
    monkeypatch.setattr(live_activity.db_supabase, "get_rows", AsyncMock(side_effect=RuntimeError("db down")))
    # Best-effort: a DB failure must NOT propagate into the ride request path.
    asyncio.run(
        live_activity.send_live_activity_update({"id": "r1", "status": "in_progress"}, live_activity.EVENT_UPDATE)
    )


# --------------------------------------------------------------------------- #
# register endpoint
# --------------------------------------------------------------------------- #


@pytest.fixture
def rider_client(test_client):
    from backend.server import app

    try:
        from backend.dependencies import get_current_user
    except ImportError:  # pragma: no cover
        from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": "rider-1", "is_driver": False}
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


_URL = "/api/v1/rides/r1/live-activity/register"


def test_register_404_when_ride_missing(rider_client, monkeypatch):
    monkeypatch.setattr(rides_mod.db_supabase, "get_ride", AsyncMock(return_value=None))
    resp = rider_client.post(_URL, json={"platform": "ios", "push_token": "tok-1"})
    assert resp.status_code == 404


def test_register_403_when_not_owner(rider_client, monkeypatch):
    monkeypatch.setattr(
        rides_mod.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "someone-else"})
    )
    resp = rider_client.post(_URL, json={"platform": "ios", "push_token": "tok-1"})
    assert resp.status_code == 403


def test_register_inserts_when_new(rider_client, monkeypatch):
    monkeypatch.setattr(rides_mod.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "rider-1"}))
    monkeypatch.setattr(rides_mod.db_supabase, "get_rows", AsyncMock(return_value=[]))
    ins = AsyncMock()
    monkeypatch.setattr(rides_mod.db_supabase, "insert_one", ins)

    resp = rider_client.post(_URL, json={"platform": "ios", "push_token": "tok-1"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    ins.assert_awaited_once()
    table, row = ins.await_args.args
    assert table == "ride_live_activities"
    assert row["ride_id"] == "r1"
    assert row["rider_id"] == "rider-1"
    assert row["platform"] == "ios"
    assert row["push_token"] == "tok-1"


def test_register_upserts_when_existing(rider_client, monkeypatch):
    monkeypatch.setattr(rides_mod.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "rider-1"}))
    monkeypatch.setattr(rides_mod.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "act-1"}]))
    upd = AsyncMock()
    ins = AsyncMock()
    monkeypatch.setattr(rides_mod.db_supabase, "update_one", upd)
    monkeypatch.setattr(rides_mod.db_supabase, "insert_one", ins)

    resp = rider_client.post(_URL, json={"platform": "android", "push_token": "tok-2"})
    assert resp.status_code == 200
    upd.assert_awaited_once()
    ins.assert_not_awaited()
    # Re-registration replaces the token and clears any prior ended_at.
    patch = upd.await_args.args[2]
    assert patch["push_token"] == "tok-2"
    assert patch["ended_at"] is None


def test_register_rejects_path_chars_in_token(rider_client, monkeypatch):
    monkeypatch.setattr(rides_mod.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "rider-1"}))
    resp = rider_client.post(_URL, json={"platform": "ios", "push_token": "ab/../cd"})
    assert resp.status_code == 422


def test_register_rejects_unknown_platform(rider_client, monkeypatch):
    monkeypatch.setattr(rides_mod.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "rider-1"}))
    resp = rider_client.post(_URL, json={"platform": "windows", "push_token": "x"})
    assert resp.status_code == 422
