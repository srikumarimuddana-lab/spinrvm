"""
P2-14: SOS E2E — in-ride emergency button (R13)

Backend emergency endpoint is fully implemented:
  POST /{ride_id}/emergency — persists incident, notifies admin WS,
                               attempts emergency-contact SMS

These tests pin:
  - Rider triggers SOS → incident persisted in emergencies table
  - Rider triggers SOS → admin dashboard notified via WS
  - Driver triggers SOS → persisted with role="driver"
  - Non-participant cannot trigger SOS (403)
  - Unknown ride returns 404
  - Response includes incident_id (unique UUID per trigger)
  - Lat/lon forwarded to incident record

Run:
    pytest backend/tests/test_p2_sos.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

RIDER_ID = "rider_p2_14"
DRIVER_USER_ID = "driver_user_p2_14"
DRIVER_ID = "driver_row_p2_14"
RIDE_ID = "ride_p2_14_001"


def _ride(status: str = "in_progress") -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
    }


def _driver_row(user_id: str = DRIVER_USER_ID) -> dict:
    return {"id": DRIVER_ID, "user_id": user_id}


class _Req:
    message = "Emergency!"
    latitude = 43.651070
    longitude = -79.347015


# ─────────────────────────────────────────────────────────────────────────────
# POST /{ride_id}/emergency
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestTriggerEmergency:
    """Pins trigger_emergency: persist + WS notify admin.

    Code under test: backend/routes/rides.py::trigger_emergency (~line 1661).
    """

    async def _trigger(
        self,
        sender_user_id: str,
        driver_row=None,
        ride=None,
        emergency_contacts=None,
        send_sms_side_effect=None,
        get_app_settings_side_effect=None,
    ):
        from backend.routes import rides as rides_mod

        persisted = []
        ws_calls = []
        sms_calls = []

        async def _insert(table, row):
            persisted.append((table, row))

        async def _broadcast_to_admins(message):
            ws_calls.append(("admin_broadcast", message))

        async def _get_rows(table, query, **kwargs):
            if table == "drivers":
                return [driver_row] if driver_row else []
            if table == "emergency_contacts":
                return emergency_contacts or []
            return []

        async def _default_send_sms(phone, body, **kwargs):
            sms_calls.append({"phone": phone, "body": body, **kwargs})
            return {"success": True, "provider": "mock"}

        active_ride = ride if ride is not None else _ride()
        send_sms_mock = AsyncMock(side_effect=send_sms_side_effect or _default_send_sms)
        settings_mock = (
            AsyncMock(side_effect=get_app_settings_side_effect)
            if get_app_settings_side_effect
            else AsyncMock(return_value={})
        )

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=active_ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
            patch(
                "backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock(side_effect=_broadcast_to_admins)
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides._deps.send_sms", send_sms_mock),
        ):
            result = await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_Req(),
                current_user={"id": sender_user_id},
            )

        return result, persisted, ws_calls, sms_calls

    async def test_rider_sos_incident_persisted(self):
        result, persisted, _, _sms = await self._trigger(RIDER_ID)

        assert result["success"] is True
        assert persisted, "Incident not persisted"
        table, row = persisted[0]
        # Consolidated onto safety_incidents (migration 94) -- the legacy
        # `emergencies` table was never read by anything and was dropped.
        assert table == "safety_incidents"
        assert row["ride_id"] == RIDE_ID
        assert row["reported_by_user_id"] == RIDER_ID
        assert row["role"] == "rider"

    async def test_db_insert_failure_returns_503_not_silent_500(self):
        """A DB failure on the safety_incidents insert must surface as a
        clean 503 (which SOSButton.tsx's client-side retry loop treats as a
        retryable failure, matching backend/routes/safety.py's identical
        pattern for the non-urgent report endpoint) -- not an unhandled 500.
        None of the downstream notify steps (admin WS, safety-team email,
        contact SMS) may fire, since the incident was never persisted."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ws_calls = []

        async def _broadcast_to_admins(message):
            ws_calls.append(message)

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(
                "backend.routes.rides._deps.db_supabase.insert_one",
                AsyncMock(side_effect=RuntimeError("connection reset")),
            ),
            patch(
                "backend.routes.rides._deps.manager.broadcast_to_admins",
                AsyncMock(side_effect=_broadcast_to_admins),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.trigger_emergency(
                    ride_id=RIDE_ID,
                    body=_Req(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 503
        assert ws_calls == [], "admin must not be notified for an incident that was never persisted"

    async def test_rider_sos_admin_notified_via_ws(self):
        _, _, ws_calls, _sms = await self._trigger(RIDER_ID)

        admin_events = [(ch, msg) for ch, msg in ws_calls if "admin" in str(ch)]
        assert admin_events, "Admin was not notified via WS"
        assert admin_events[0][1]["type"] == "emergency_alert"
        assert admin_events[0][1]["incident"]["ride_id"] == RIDE_ID

    async def test_driver_sos_persisted_with_driver_role(self):
        result, persisted, _, _sms = await self._trigger(
            DRIVER_USER_ID,
            driver_row=_driver_row(),
        )

        assert result["success"] is True
        _, row = persisted[0]
        assert row["role"] == "driver"
        assert row["reported_by_user_id"] == DRIVER_USER_ID

    async def test_non_participant_cannot_trigger_sos(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.trigger_emergency(
                    ride_id=RIDE_ID,
                    body=_Req(),
                    current_user={"id": "outsider-user"},
                )

        assert exc_info.value.status_code == 403

    async def test_unknown_ride_returns_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.trigger_emergency(
                    ride_id="nonexistent-ride",
                    body=_Req(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 404

    async def test_response_contains_unique_incident_id(self):
        result1, _, _, _sms = await self._trigger(RIDER_ID)
        result2, _, _, _sms = await self._trigger(RIDER_ID)

        assert result1["incident_id"] != result2["incident_id"]
        UUID(result1["incident_id"])
        UUID(result2["incident_id"])

    async def test_lat_lon_forwarded_to_incident_record(self):
        _, persisted, _, _sms = await self._trigger(RIDER_ID)

        _, row = persisted[0]
        assert row["latitude"] == _Req.latitude
        assert row["longitude"] == _Req.longitude

    async def test_rider_sos_confirmation_push_sent_to_triggering_rider(self):
        """N15/R38 (ACTION_ITEMS.md): the rider who triggered SOS must get a
        confirmation push, not just the admin/safety-team/contacts channels.
        Fired via spawn() (fire-and-forget, matching the N5 cancellation-push
        pattern) -- yield once so the scheduled task actually runs."""
        from backend.routes import rides as rides_mod

        push_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.send_sms", AsyncMock(return_value={"success": True})),
            patch("backend.routes.rides._deps.send_push_notification", push_mock),
        ):
            await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_Req(),
                current_user={"id": RIDER_ID},
            )
            await asyncio.sleep(0)

        push_mock.assert_awaited_once()
        call = push_mock.await_args
        assert call.args[0] == RIDER_ID
        assert call.kwargs.get("priority") == "safety"
        assert call.kwargs.get("target_app") == "rider"
        assert call.kwargs.get("data", {}).get("type") == "sos_confirmation"
        # Copy must confirm receipt without claiming to replace/guarantee
        # emergency response -- "What Spinr Is NOT" guardrail.
        body_text = call.args[2].lower()
        assert "reached" in body_text or "received" in body_text
        assert "911" in body_text

    async def test_driver_sos_confirmation_push_targets_driver_app(self):
        """Same confirmation must reach a driver-triggered SOS too, routed to
        the driver app's token column, not the rider's."""
        from backend.routes import rides as rides_mod

        push_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "Driver"}),
            ),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.send_sms", AsyncMock(return_value={"success": True})),
            patch("backend.routes.rides._deps.send_push_notification", push_mock),
        ):
            await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_Req(),
                current_user={"id": DRIVER_USER_ID},
            )
            await asyncio.sleep(0)

        push_mock.assert_awaited_once()
        call = push_mock.await_args
        assert call.args[0] == DRIVER_USER_ID
        assert call.kwargs.get("target_app") == "driver"
        assert call.kwargs.get("priority") == "safety"

    async def test_emergency_contacts_receive_sms(self):
        contacts = [
            {"id": "ec-1", "phone": "+13061112222", "name": "Mom"},
            {"id": "ec-2", "phone": "+13063334444", "name": "Dad"},
        ]
        result, _, _, sms_calls = await self._trigger(RIDER_ID, emergency_contacts=contacts)

        assert result["contacts_notified"] == 2
        phones_notified = {c["phone"] for c in sms_calls}
        assert phones_notified == {"+13061112222", "+13063334444"}
        assert all("URGENT" in c["body"] for c in sms_calls)

    async def test_contact_without_phone_is_skipped(self):
        contacts = [
            {"id": "ec-1", "phone": "", "name": "No Phone"},
            {"id": "ec-2", "phone": "+13065556666", "name": "Has Phone"},
        ]
        result, _, _, sms_calls = await self._trigger(RIDER_ID, emergency_contacts=contacts)

        assert result["contacts_notified"] == 1
        assert sms_calls[0]["phone"] == "+13065556666"

    async def test_no_contacts_returns_zero_notified(self):
        result, _, _, sms_calls = await self._trigger(RIDER_ID, emergency_contacts=[])

        assert result["contacts_notified"] == 0
        assert sms_calls == []

    async def test_sms_exception_for_one_contact_does_not_block_the_others(self):
        """A raised exception from send_sms for one contact is logged (type
        name only — PIPEDA, never the exception text which may embed the
        phone number) and does not stop the other contacts from being
        notified. routes/rides/safety.py logs via loguru (imported through
        _deps), which doesn't propagate to caplog — patch the module-level
        `logger` name directly instead."""
        contacts = [
            {"id": "ec-1", "phone": "+13061112222", "name": "Mom"},
            {"id": "ec-2", "phone": "+13063334444", "name": "Dad"},
        ]

        async def _flaky_send_sms(phone, body, **kwargs):
            if phone == "+13061112222":
                raise RuntimeError("Twilio exploded for +13061112222")
            return {"success": True, "provider": "mock"}

        with patch("backend.routes.rides.safety.logger") as mock_logger:
            result, _, _, _sms = await self._trigger(
                RIDER_ID, emergency_contacts=contacts, send_sms_side_effect=_flaky_send_sms
            )

        assert result["contacts_notified"] == 1
        error_calls = [
            c.args[0] for c in mock_logger.error.call_args_list if "SOS SMS failed for contact ec-1" in c.args[0]
        ]
        assert error_calls, "expected an error log for the failing contact"
        assert "RuntimeError" in error_calls[0]
        assert "+13061112222" not in error_calls[0]  # PIPEDA: no raw phone number in the log

    async def test_sms_failure_result_is_logged_and_not_counted(self):
        """send_sms returning success=False (not raising) is logged via the
        PII-free 'error' string send_sms guarantees, and that contact is not
        counted as notified."""
        contacts = [{"id": "ec-1", "phone": "+13061112222", "name": "Mom"}]

        async def _failing_send_sms(phone, body, **kwargs):
            return {"success": False, "error": "type=twilio_error status=400"}

        with patch("backend.routes.rides.safety.logger") as mock_logger:
            result, _, _, _sms = await self._trigger(
                RIDER_ID, emergency_contacts=contacts, send_sms_side_effect=_failing_send_sms
            )

        assert result["contacts_notified"] == 0
        assert any("type=twilio_error status=400" in c.args[0] for c in mock_logger.error.call_args_list)

    async def test_response_includes_per_contact_status(self):
        """B16: the driver-app Safety overlay's per-contact '✓ Notified' list
        needs individual status, not just the aggregate contacts_notified
        count. One contact's SMS succeeds, the other fails (non-throwing) --
        the new `contacts` array must reflect each outcome by id/name."""
        contacts = [
            {"id": "ec-1", "phone": "+13061112222", "name": "Mom"},
            {"id": "ec-2", "phone": "+13063334444", "name": "Dad"},
        ]

        async def _mixed_send_sms(phone, body, **kwargs):
            if phone == "+13061112222":
                return {"success": True, "provider": "mock"}
            return {"success": False, "error": "type=twilio_error status=400"}

        result, _, _, _sms = await self._trigger(
            RIDER_ID, emergency_contacts=contacts, send_sms_side_effect=_mixed_send_sms
        )

        assert result["contacts_notified"] == 1
        assert result["contacts"] == [
            {"id": "ec-1", "name": "Mom", "notified": True},
            {"id": "ec-2", "name": "Dad", "notified": False},
        ]

    async def test_contacts_key_present_on_degraded_sms_failure(self):
        """The outer-exception (e.g. get_app_settings down) branch must also
        carry the `contacts` key -- empty, not missing -- so a client never
        has to conditionally check for its presence."""
        result, _, _, _sms = await self._trigger(
            RIDER_ID,
            emergency_contacts=[{"id": "ec-1", "phone": "+13061112222", "name": "Mom"}],
            get_app_settings_side_effect=RuntimeError("settings service down"),
        )

        assert result["contacts"] == []

    async def test_contact_notification_outer_failure_returns_warning(self):
        """A failure anywhere in the contact-notification block (e.g.
        get_app_settings blowing up) must not fail the whole request — the
        incident is already persisted by this point. The response instead
        carries a notification_warning telling the rider to call contacts
        directly."""
        with patch("backend.routes.rides.safety.logger") as mock_logger:
            result, persisted, _, sms_calls = await self._trigger(
                RIDER_ID,
                emergency_contacts=[{"id": "ec-1", "phone": "+13061112222", "name": "Mom"}],
                get_app_settings_side_effect=RuntimeError("settings service down"),
            )

        assert result["success"] is True
        assert result["contacts_notified"] == 0
        assert "notification_warning" in result
        assert persisted, "the incident itself must still be persisted"
        assert sms_calls == []
        assert any("SOS emergency contact notification failed" in c.args[0] for c in mock_logger.error.call_args_list)
