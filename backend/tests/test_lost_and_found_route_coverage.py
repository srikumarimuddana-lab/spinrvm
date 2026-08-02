"""Coverage for routes/lost_and_found.py (A1c, Sub-tier B).

Rider/driver Lost & Found case flow + chat thread. Distinct from
routes/rides/lost_found.py (already closed under A1) — this is the
newer dedicated case+chat router. Had no dedicated test file; only
25.85% coverage.

Endpoint functions are called directly (bypassing FastAPI's Depends
machinery) with a plain `current_user` dict, matching the pattern already
used elsewhere in this repo for handler-level unit tests.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

_RIDER = {"id": "rider-1"}
_DRIVER_USER = {"id": "driver-user-1"}
_DRIVER_ROW = {"id": "driverrow-1", "user_id": "driver-user-1"}


def _patches(**overrides):
    defaults = {
        "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[]),
        "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=None),
        "backend.routes.lost_and_found.db_supabase.insert_one": AsyncMock(return_value={"id": "case-1"}),
        "backend.routes.lost_and_found.db_supabase.update_one": AsyncMock(return_value={}),
        "backend.routes.lost_and_found.db_supabase.get_ride": AsyncMock(return_value=None),
        "backend.routes.lost_and_found.send_push_notification": AsyncMock(),
        "backend.routes.lost_and_found.create_ticket_for_lost_and_found": AsyncMock(),
    }
    defaults.update(overrides)
    return [patch(target, value) for target, value in defaults.items()]


def _start(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


# ── _driver_for_user / _require_participant / _insert_system_message ──────


class TestDriverForUser:
    @pytest.mark.anyio
    async def test_returns_driver_row_when_found(self):
        from backend.routes.lost_and_found import _driver_for_user

        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW])}))
        try:
            result = await _driver_for_user("driver-user-1")
            assert result == _DRIVER_ROW
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_returns_none_when_no_driver_row(self):
        from backend.routes.lost_and_found import _driver_for_user

        patches = _start(_patches())
        try:
            assert await _driver_for_user("rider-1") is None
        finally:
            _stop(patches)


class TestRequireParticipant:
    @pytest.mark.anyio
    async def test_case_not_found_raises_404(self):
        from backend.routes.lost_and_found import _require_participant

        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await _require_participant("case-1", "rider-1")
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_non_participant_raises_403(self):
        from backend.routes.lost_and_found import _require_participant

        case = {"id": "case-1", "reporter_id": "someone-else", "rider_user_id": "another-user", "driver_id": None}
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await _require_participant("case-1", "rider-1")
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_reporter_is_a_participant(self):
        from backend.routes.lost_and_found import _require_participant

        case = {"id": "case-1", "reporter_id": "rider-1", "rider_user_id": None, "driver_id": None}
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case)}))
        try:
            result = await _require_participant("case-1", "rider-1")
            assert result == case
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_assigned_driver_is_a_participant(self):
        from backend.routes.lost_and_found import _require_participant

        case = {"id": "case-1", "reporter_id": "rider-1", "rider_user_id": "rider-1", "driver_id": "driverrow-1"}
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case)}))
        try:
            result = await _require_participant("case-1", "driver-user-1", driver_id="driverrow-1")
            assert result == case
        finally:
            _stop(patches)


# ── list_cases ──────────────────────────────────────────────────────────


class TestListCases:
    @pytest.mark.anyio
    async def test_merges_and_dedupes_reporter_rider_driver_cases(self):
        from backend.routes.lost_and_found import list_cases

        case_a = {"id": "a", "created_at": "2026-07-01T00:00:00Z"}
        # `a` appears as both reporter and rider — must dedupe. Call order:
        # _driver_for_user lookup (empty — this caller has no driver row,
        # so the as_driver query never fires), then as_reporter, as_rider.
        get_rows = AsyncMock(side_effect=[[], [case_a], [case_a]])
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.get_rows": get_rows}))
        try:
            result = await list_cases(status=None, current_user=_RIDER)
            ids = [c["id"] for c in result["cases"]]
            assert ids == ["a"]
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_includes_driver_cases_when_caller_is_a_driver(self):
        from backend.routes.lost_and_found import list_cases

        driver_case = {"id": "d1", "created_at": "2026-07-03T00:00:00Z"}
        # db_supabase.get_rows is called 4 times in order: the
        # _driver_for_user lookup, then as_reporter/as_rider/as_driver.
        get_rows = AsyncMock(side_effect=[[_DRIVER_ROW], [], [], [driver_case]])
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.get_rows": get_rows}))
        try:
            result = await list_cases(status=None, current_user=_DRIVER_USER)
            assert [c["id"] for c in result["cases"]] == ["d1"]
        finally:
            _stop(patches)


# ── get_case ────────────────────────────────────────────────────────────


class TestGetCase:
    @pytest.mark.anyio
    async def test_returns_case_for_participant(self):
        from backend.routes.lost_and_found import get_case

        case = {"id": "case-1", "reporter_id": "rider-1", "rider_user_id": None, "driver_id": None}
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case)}))
        try:
            result = await get_case("case-1", current_user=_RIDER)
            assert result == {"case": case}
        finally:
            _stop(patches)


# ── driver_report_found_item ───────────────────────────────────────────


class TestDriverReportFoundItem:
    @pytest.mark.anyio
    async def test_no_driver_profile_raises_403(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_report_found_item(
                    DriverReportRequest(ride_id="ride-1", item_description="a phone"), current_user=_DRIVER_USER
                )
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_ride_not_found_raises_404(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        patches = _start(
            _patches(**{"backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW])})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_report_found_item(
                    DriverReportRequest(ride_id="ride-1", item_description="a phone"), current_user=_DRIVER_USER
                )
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_not_the_assigned_driver_raises_403(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        ride = {"id": "ride-1", "driver_id": "someone-else", "status": "completed"}
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.get_ride": AsyncMock(return_value=ride),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_report_found_item(
                    DriverReportRequest(ride_id="ride-1", item_description="a phone"), current_user=_DRIVER_USER
                )
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_ride_not_completed_raises_400(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        ride = {"id": "ride-1", "driver_id": "driverrow-1", "status": "in_progress"}
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.get_ride": AsyncMock(return_value=ride),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_report_found_item(
                    DriverReportRequest(ride_id="ride-1", item_description="a phone"), current_user=_DRIVER_USER
                )
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_successful_report_creates_case_and_notifies_rider(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        ride = {"id": "ride-1", "driver_id": "driverrow-1", "status": "completed", "rider_id": "rider-1"}
        insert_one = AsyncMock(return_value={"id": "case-1"})
        push = AsyncMock()
        create_ticket = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.get_ride": AsyncMock(return_value=ride),
                    "backend.routes.lost_and_found.db_supabase.insert_one": insert_one,
                    "backend.routes.lost_and_found.send_push_notification": push,
                    "backend.routes.lost_and_found.create_ticket_for_lost_and_found": create_ticket,
                }
            )
        )
        try:
            result = await driver_report_found_item(
                DriverReportRequest(ride_id="ride-1", item_description="a phone", item_category="electronics"),
                current_user=_DRIVER_USER,
            )
            assert result["success"] is True
            assert result["case"]["id"] == "case-1"
            push.assert_awaited()  # rider notified
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_category_falls_back_to_other(self):
        from backend.routes.lost_and_found import DriverReportRequest, driver_report_found_item

        ride = {"id": "ride-1", "driver_id": "driverrow-1", "status": "completed", "rider_id": None}
        insert_one = AsyncMock(return_value={"id": "case-1"})
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.get_ride": AsyncMock(return_value=ride),
                    "backend.routes.lost_and_found.db_supabase.insert_one": insert_one,
                }
            )
        )
        try:
            await driver_report_found_item(
                DriverReportRequest(ride_id="ride-1", item_description="a phone", item_category="not-a-real-category"),
                current_user=_DRIVER_USER,
            )
            saved = insert_one.await_args_list[0].args[1]
            assert saved["item_category"] == "other"
        finally:
            _stop(patches)


# ── driver_respond ──────────────────────────────────────────────────────


class TestDriverRespond:
    @pytest.mark.anyio
    async def test_no_driver_profile_raises_403(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_respond("case-1", RespondRequest(found=True), current_user=_DRIVER_USER)
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_case_not_found_raises_404(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        patches = _start(
            _patches(**{"backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW])})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_respond("case-1", RespondRequest(found=True), current_user=_DRIVER_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_not_the_case_driver_raises_403(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        case = {"id": "case-1", "driver_id": "someone-else", "status": "reported"}
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_respond("case-1", RespondRequest(found=True), current_user=_DRIVER_USER)
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_case_not_awaiting_response_raises_400(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        case = {"id": "case-1", "driver_id": "driverrow-1", "status": "found"}
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await driver_respond("case-1", RespondRequest(found=True), current_user=_DRIVER_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_found_true_updates_status_and_notifies(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        case = {"id": "case-1", "driver_id": "driverrow-1", "status": "reported", "rider_user_id": "rider-1"}
        update_one = AsyncMock()
        push = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                    "backend.routes.lost_and_found.db_supabase.update_one": update_one,
                    "backend.routes.lost_and_found.send_push_notification": push,
                }
            )
        )
        try:
            result = await driver_respond("case-1", RespondRequest(found=True), current_user=_DRIVER_USER)
            assert result == {"success": True, "status": "found"}
            push.assert_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_found_false_sets_not_found_status(self):
        from backend.routes.lost_and_found import RespondRequest, driver_respond

        case = {"id": "case-1", "driver_id": "driverrow-1", "status": "driver_notified", "rider_user_id": None}
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                }
            )
        )
        try:
            result = await driver_respond("case-1", RespondRequest(found=False), current_user=_DRIVER_USER)
            assert result == {"success": True, "status": "not_found"}
        finally:
            _stop(patches)


# ── list_messages / send_message ───────────────────────────────────────


class TestListMessages:
    @pytest.mark.anyio
    async def test_returns_messages_in_ascending_order(self):
        from backend.routes.lost_and_found import list_messages

        case = {"id": "case-1", "reporter_id": "rider-1", "rider_user_id": None, "driver_id": None}
        newest_first = [{"id": "m2"}, {"id": "m1"}]
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=newest_first),
                }
            )
        )
        try:
            result = await list_messages("case-1", limit=50, before=None, current_user=_RIDER)
            assert [m["id"] for m in result["messages"]] == ["m1", "m2"]
        finally:
            _stop(patches)


class TestSendMessage:
    @pytest.mark.anyio
    async def test_closed_case_raises_400(self):
        from backend.routes.lost_and_found import SendMessageRequest, send_message

        case = {"id": "case-1", "reporter_id": "rider-1", "rider_user_id": None, "driver_id": None, "status": "resolved"}
        patches = _start(_patches(**{"backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await send_message("case-1", SendMessageRequest(message="hi"), current_user=_RIDER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_rider_sends_message_notifies_driver(self):
        from backend.routes.lost_and_found import SendMessageRequest, send_message

        case = {
            "id": "case-1",
            "reporter_id": "rider-1",
            "rider_user_id": "rider-1",
            "driver_id": "driverrow-1",
            "status": "found",
        }
        insert_one = AsyncMock(return_value={"id": "msg-1"})
        push = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                    "backend.routes.lost_and_found.db_supabase.insert_one": insert_one,
                    "backend.routes.lost_and_found.db_supabase.get_rows": AsyncMock(return_value=[_DRIVER_ROW]),
                    "backend.routes.lost_and_found.send_push_notification": push,
                }
            )
        )
        try:
            result = await send_message("case-1", SendMessageRequest(message="Where can we meet?"), current_user=_RIDER)
            assert result["success"] is True
            push.assert_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_driver_sends_message_notifies_rider(self):
        from backend.routes.lost_and_found import SendMessageRequest, send_message

        case = {
            "id": "case-1",
            "reporter_id": "driver-user-1",
            "rider_user_id": "rider-1",
            "driver_id": "driverrow-1",
            "status": "driver_found",
        }
        insert_one = AsyncMock(return_value={"id": "msg-1"})
        push = AsyncMock()
        get_rows = AsyncMock(return_value=[_DRIVER_ROW])
        patches = _start(
            _patches(
                **{
                    "backend.routes.lost_and_found.db_supabase.find_one": AsyncMock(return_value=case),
                    "backend.routes.lost_and_found.db_supabase.insert_one": insert_one,
                    "backend.routes.lost_and_found.db_supabase.get_rows": get_rows,
                    "backend.routes.lost_and_found.send_push_notification": push,
                }
            )
        )
        try:
            result = await send_message("case-1", SendMessageRequest(message="I have it"), current_user=_DRIVER_USER)
            assert result["success"] is True
            push.assert_awaited()
        finally:
            _stop(patches)
