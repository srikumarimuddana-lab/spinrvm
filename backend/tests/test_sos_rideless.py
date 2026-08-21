"""
Ride-less emergency (SOS) — ACTION_ITEMS.md B15(c)

POST /rides/emergency (backend/routes/rides/safety.py::trigger_emergency_rideless)
is the new sibling of trigger_emergency's POST /{ride_id}/emergency: same
side-effect bundle (safety_incidents insert, admin WS broadcast,
notify_safety_team, page_sos_on_call, confirmation push, emergency-contact
SMS), but with ride_id=None and no ride-membership check -- caller identity
alone decides who the alert is filed for.

These tests pin:
  - Flag off (AppSettings.rideless_sos_enabled=False, the default) -> 404,
    no side effects fire at all
  - Flag on -> incident persisted with ride_id=None, category="sos_button_rideless"
  - Role derived from current_user["is_driver"], not from any ride
  - Admin notified via WS (same emergency_alert event as the in-ride path)
  - DB insert failure -> clean 503, no downstream notifications
  - Response contains a unique incident_id
  - Lat/lon forwarded to incident record
  - Idempotency: a replayed key returns the original incident, not a new one

Mirrors backend/tests/test_p2_sos.py's structure and patch targets (both
functions share the single `_deps` module inside the `rides` package, so
patching `backend.routes.rides._deps.*` affects trigger_emergency_rideless
identically).

Run:
    pytest backend/tests/test_sos_rideless.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest

RIDER_ID = "rider_rideless_sos"
DRIVER_USER_ID = "driver_user_rideless_sos"


class _Req:
    message = "Emergency!"
    latitude = 43.651070
    longitude = -79.347015
    idempotency_key = None


@pytest.mark.unit
@pytest.mark.asyncio
class TestTriggerEmergencyRideless:
    """Pins trigger_emergency_rideless: flag gate + persist + WS notify admin.

    Code under test: backend/routes/rides/safety.py::trigger_emergency_rideless.
    """

    async def _trigger(
        self,
        sender_user_id: str,
        is_driver: bool = False,
        flag_enabled: bool = True,
        emergency_contacts=None,
        send_sms_side_effect=None,
        insert_one_side_effect=None,
        body=None,
    ):
        from backend.routes import rides as rides_mod

        persisted = []
        ws_calls = []

        async def _insert(table, row):
            if insert_one_side_effect:
                # Allow a one-shot exception on first call, success after, by
                # letting the caller pass a plain exception instance.
                raise insert_one_side_effect
            persisted.append((table, row))

        async def _broadcast_to_admins(message):
            ws_calls.append(("admin_broadcast", message))

        async def _get_rows(table, query, **kwargs):
            if table == "emergency_contacts":
                return emergency_contacts or []
            return []

        async def _default_send_sms(phone, body, **kwargs):
            return {"success": True, "provider": "mock"}

        send_sms_mock = AsyncMock(side_effect=send_sms_side_effect or _default_send_sms)
        settings_mock = AsyncMock(return_value={"rideless_sos_enabled": flag_enabled})

        with (
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
            patch(
                "backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock(side_effect=_broadcast_to_admins)
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
            patch("backend.routes.rides._deps.send_sms", send_sms_mock),
        ):
            result = await rides_mod.trigger_emergency_rideless(
                body=body or _Req(),
                current_user={"id": sender_user_id, "is_driver": is_driver},
            )

        return result, persisted, ws_calls

    async def test_flag_off_returns_404_and_fires_no_side_effects(self):
        """The default (AppSettings.rideless_sos_enabled=False) must 404 --
        server-side, fail-closed, not just 'the client won't call it'."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._trigger(RIDER_ID, flag_enabled=False)

        assert exc_info.value.status_code == 404

    async def test_rider_sos_incident_persisted_with_no_ride_id(self):
        result, persisted, _ = await self._trigger(RIDER_ID)

        assert result["success"] is True
        assert persisted, "Incident not persisted"
        table, row = persisted[0]
        assert table == "safety_incidents"
        assert row["ride_id"] is None
        assert row["reported_by_user_id"] == RIDER_ID
        assert row["role"] == "rider"
        assert row["category"] == "sos_button_rideless"

    async def test_driver_sos_persisted_with_driver_role(self):
        result, persisted, _ = await self._trigger(DRIVER_USER_ID, is_driver=True)

        assert result["success"] is True
        _, row = persisted[0]
        assert row["role"] == "driver"
        assert row["reported_by_user_id"] == DRIVER_USER_ID
        assert row["ride_id"] is None

    async def test_rider_sos_admin_notified_via_ws(self):
        _, _, ws_calls = await self._trigger(RIDER_ID)

        admin_events = [(ch, msg) for ch, msg in ws_calls if "admin" in str(ch)]
        assert admin_events, "Admin was not notified via WS"
        assert admin_events[0][1]["type"] == "emergency_alert"
        assert admin_events[0][1]["incident"]["ride_id"] is None

    async def test_db_insert_failure_returns_503_not_silent_500(self):
        """A DB failure on the safety_incidents insert must surface as a
        clean 503 -- matching trigger_emergency's identical posture -- so
        SOSButton.tsx's client-side retry loop treats it as retryable."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._trigger(RIDER_ID, insert_one_side_effect=RuntimeError("connection reset"))

        assert exc_info.value.status_code == 503

    async def test_response_contains_unique_incident_id(self):
        result1, _, _ = await self._trigger(RIDER_ID)
        result2, _, _ = await self._trigger(RIDER_ID)

        assert result1["incident_id"] != result2["incident_id"]
        UUID(result1["incident_id"])
        UUID(result2["incident_id"])

    async def test_lat_lon_forwarded_to_incident_record(self):
        _, persisted, _ = await self._trigger(RIDER_ID)

        _, row = persisted[0]
        assert row["latitude"] == _Req.latitude
        assert row["longitude"] == _Req.longitude

    async def test_malformed_idempotency_key_degrades_to_no_dedup_not_422(self):
        """A malformed key must never block the alert -- same fail-open
        contract as trigger_emergency's EmergencyRequest.idempotency_key."""

        class _ReqBadKey(_Req):
            idempotency_key = "!!! not a valid key !!!"

        result, persisted, _ = await self._trigger(RIDER_ID, body=_ReqBadKey())

        assert result["success"] is True
        _, row = persisted[0]
        assert "sos_idempotency_key" not in row

    async def test_duplicate_idempotency_key_returns_original_not_new_incident(self):
        """Replaying the same idempotency_key (a retry after a lost response)
        must return the ORIGINAL incident and fire zero new side effects --
        reuses the migration-315 UNIQUE index unmodified (no ride_id
        component), so this works identically to the in-ride path."""
        from backend.routes import rides as rides_mod

        class _ReqKeyed(_Req):
            idempotency_key = "test-retry-idem-001"

        existing_incident = {"id": "existing-incident-id"}
        settings_mock = AsyncMock(return_value={"rideless_sos_enabled": True})
        ws_calls = []

        async def _get_rows(table, query, **kwargs):
            if table == "safety_incidents":
                return [existing_incident]
            return []

        async def _broadcast_to_admins(message):
            ws_calls.append(message)

        with (
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()) as insert_mock,
            patch(
                "backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock(side_effect=_broadcast_to_admins)
            ),
        ):
            result = await rides_mod.trigger_emergency_rideless(
                body=_ReqKeyed(),
                current_user={"id": RIDER_ID, "is_driver": False},
            )

        assert result["success"] is True
        assert result["incident_id"] == "existing-incident-id"
        assert result["duplicate"] is True
        insert_mock.assert_not_called()
        assert ws_calls == [], "a deduplicated replay must fire zero new side effects"

    async def test_suppressed_contact_excluded_from_sos_sms(self):
        """Same suppression contract as trigger_emergency: a STOP'd-out
        contact is excluded from the SOS SMS and flagged distinctly, while a
        non-suppressed contact in the same request still gets the alert."""
        from backend.routes import rides as rides_mod

        contacts = [
            {"id": "ec-1", "phone": "+13061112222", "name": "Mom"},
            {"id": "ec-2", "phone": "+13063334444", "name": "Dad"},
        ]
        sms_calls = []

        async def _send_sms(phone, body, **kwargs):
            sms_calls.append({"phone": phone, "body": body})
            return {"success": True, "provider": "mock"}

        async def _is_suppressed(phone):
            return phone == "+13061112222"

        settings_mock = AsyncMock(return_value={"rideless_sos_enabled": True})

        async def _get_rows(table, query, **kwargs):
            if table == "emergency_contacts":
                return contacts
            return []

        with (
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
            patch("backend.routes.rides._deps.send_sms", AsyncMock(side_effect=_send_sms)),
            patch(
                "backend.routes.rides.safety.sos_contact_consent.is_suppressed",
                AsyncMock(side_effect=_is_suppressed),
            ),
        ):
            result = await rides_mod.trigger_emergency_rideless(
                body=_Req(),
                current_user={"id": RIDER_ID, "is_driver": False},
            )

        assert result["contacts_notified"] == 1
        phones_notified = {c["phone"] for c in sms_calls}
        assert phones_notified == {"+13063334444"}
        assert result["contacts"] == [
            {"id": "ec-1", "name": "Mom", "notified": False, "status": "suppressed"},
            {"id": "ec-2", "name": "Dad", "notified": True},
        ]

    async def test_suppression_check_failure_fails_open_sends_to_all(self):
        """SAFETY-CRITICAL: an unexpected failure in the suppression-check
        step must never block the SOS SMS -- every contact still gets the
        alert (fail-open end to end, not just at the service layer)."""
        from backend.routes import rides as rides_mod

        contacts = [
            {"id": "ec-1", "phone": "+13061112222", "name": "Mom"},
            {"id": "ec-2", "phone": "+13063334444", "name": "Dad"},
        ]
        sms_calls = []

        async def _send_sms(phone, body, **kwargs):
            sms_calls.append({"phone": phone, "body": body})
            return {"success": True, "provider": "mock"}

        broken_is_suppressed = Mock(side_effect=RuntimeError("suppression service unreachable"))
        settings_mock = AsyncMock(return_value={"rideless_sos_enabled": True})

        async def _get_rows(table, query, **kwargs):
            if table == "emergency_contacts":
                return contacts
            return []

        with (
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
            patch("backend.routes.rides._deps.send_sms", AsyncMock(side_effect=_send_sms)),
            patch("backend.routes.rides.safety.sos_contact_consent.is_suppressed", broken_is_suppressed),
        ):
            result = await rides_mod.trigger_emergency_rideless(
                body=_Req(),
                current_user={"id": RIDER_ID, "is_driver": False},
            )

        assert result["contacts_notified"] == 2
        phones_notified = {c["phone"] for c in sms_calls}
        assert phones_notified == {"+13061112222", "+13063334444"}
        assert result["contacts"] == [
            {"id": "ec-1", "name": "Mom", "notified": True},
            {"id": "ec-2", "name": "Dad", "notified": True},
        ]
