"""Additional unit-level coverage for backend/routes/admin/messaging.py.

test_messaging_fan_out.py already covers the immediate-send / scheduled-send
happy paths for admin_send_cloud_message. This file focuses on the parts of
the module a bulk-broadcast bug is most likely to hide in:
  - recipient resolution (_resolve_recipients / _count_audience), including
    the service-area filter and the particular_customer/particular_driver
    paths — get this wrong and a broadcast reaches the wrong people or nobody
  - per-channel senders (_send_push_one/_send_email_one/_send_sms_one) and
    their marketing vs. transactional branching
  - _fan_out's DB stats write-back, including the failure-to-persist path
  - the read/management endpoints: audience-preview, list, stats, cancel,
    suppressions add/remove
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import routes.admin.messaging as messaging
except ImportError:
    import backend.routes.admin.messaging as messaging  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# _resolve_recipients
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_recipients_particular_customer_no_ids_returns_empty():
    out = await messaging._resolve_recipients("particular_customer", [], None, need_contact=True)
    assert out == []


@pytest.mark.anyio
async def test_resolve_recipients_particular_ids_push_only_skips_contact_lookup():
    """need_contact=False must avoid hitting the DB — ids are wrapped directly."""
    get_rows_mock = AsyncMock()
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("particular_driver", ["d1", "d2"], None, need_contact=False)
    assert out == [{"id": "d1"}, {"id": "d2"}]
    get_rows_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_recipients_particular_ids_with_contact_hits_users_table():
    rows = [{"id": "d1", "email": "d1@x.com", "phone": "306-555-0100"}]
    get_rows_mock = AsyncMock(return_value=rows)
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("particular_customer", ["d1"], None, need_contact=True)
    assert out == rows
    args, kwargs = get_rows_mock.await_args
    assert args[0] == "users"
    assert args[1] == {"id": {"$in": ["d1"]}}


@pytest.mark.anyio
async def test_resolve_recipients_customers_no_area_queries_all_riders():
    get_rows_mock = AsyncMock(return_value=[{"id": "r1"}])
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("customers", [], None, need_contact=False)
    assert out == [{"id": "r1"}]
    args, _ = get_rows_mock.await_args
    assert args[1] == {"is_rider": True}


@pytest.mark.anyio
async def test_resolve_recipients_customers_with_area_filters_by_recent_rides():
    ride_rows = [{"rider_id": "r1"}, {"rider_id": "r2"}, {"rider_id": None}]
    user_rows = [{"id": "r1"}, {"id": "r2"}]
    get_rows_mock = AsyncMock(side_effect=[ride_rows, user_rows])
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("customers", [], "area-1", need_contact=False)
    assert {r["id"] for r in out} == {"r1", "r2"}
    # Second call must filter users by the ids resolved from rides + is_rider.
    second_call_args = get_rows_mock.await_args_list[1].args
    assert second_call_args[0] == "users"
    assert second_call_args[1]["is_rider"] is True
    assert set(second_call_args[1]["id"]["$in"]) == {"r1", "r2"}


@pytest.mark.anyio
async def test_resolve_recipients_customers_area_with_no_riders_returns_empty_without_second_query():
    get_rows_mock = AsyncMock(return_value=[])
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("customers", [], "empty-area", need_contact=False)
    assert out == []
    assert get_rows_mock.await_count == 1  # only the rides lookup, no users query


@pytest.mark.anyio
async def test_resolve_recipients_drivers_with_area_filters_by_assignment():
    driver_rows = [{"user_id": "u1"}, {"user_id": "u2"}]
    user_rows = [{"id": "u1"}, {"id": "u2"}]
    get_rows_mock = AsyncMock(side_effect=[driver_rows, user_rows])
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("drivers", [], "area-2", need_contact=False)
    assert {r["id"] for r in out} == {"u1", "u2"}


@pytest.mark.anyio
async def test_resolve_recipients_all_audience_queries_every_user():
    get_rows_mock = AsyncMock(return_value=[{"id": "u1"}, {"id": "u2"}])
    with patch.object(messaging.db, "get_rows", get_rows_mock):
        out = await messaging._resolve_recipients("all", [], None, need_contact=False)
    assert len(out) == 2
    args, _ = get_rows_mock.await_args
    assert args[1] == {}


@pytest.mark.anyio
async def test_resolve_recipients_unknown_audience_returns_empty():
    out = await messaging._resolve_recipients("bogus", [], None, need_contact=False)
    assert out == []


# ---------------------------------------------------------------------------
# _count_audience
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_count_audience_particular_uses_len_of_ids():
    out = await messaging._count_audience("particular_customer", ["a", "b", "c"])
    assert out == 3


@pytest.mark.anyio
async def test_count_audience_customers_no_area_uses_count_documents():
    with patch.object(messaging.db_supabase, "count_documents", AsyncMock(return_value=42)):
        out = await messaging._count_audience("customers", [], None)
    assert out == 42


@pytest.mark.anyio
async def test_count_audience_customers_with_area_uses_resolve_recipients_len():
    with patch.object(messaging, "_resolve_recipients", AsyncMock(return_value=[{"id": "1"}, {"id": "2"}])):
        out = await messaging._count_audience("customers", [], "area-1")
    assert out == 2


@pytest.mark.anyio
async def test_count_audience_drivers_no_area():
    with patch.object(messaging.db_supabase, "count_documents", AsyncMock(return_value=7)):
        out = await messaging._count_audience("drivers", [], None)
    assert out == 7


@pytest.mark.anyio
async def test_count_audience_all():
    with patch.object(messaging.db_supabase, "count_documents", AsyncMock(return_value=100)):
        out = await messaging._count_audience("all", [], None)
    assert out == 100


@pytest.mark.anyio
async def test_count_audience_unknown_returns_zero():
    out = await messaging._count_audience("bogus", [], None)
    assert out == 0


# ---------------------------------------------------------------------------
# target-app mapping
# ---------------------------------------------------------------------------


def test_target_app_for_audience_mapping():
    assert messaging._target_app_for_audience("drivers") == "driver"
    assert messaging._target_app_for_audience("particular_driver") == "driver"
    assert messaging._target_app_for_audience("customers") == "rider"
    assert messaging._target_app_for_audience("particular_customer") == "rider"
    assert messaging._target_app_for_audience("all") is None


# ---------------------------------------------------------------------------
# per-channel senders
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_push_one_transactional_success():
    push_mock = AsyncMock(return_value=True)
    fake_module = MagicMock(send_push_notification=push_mock)
    with patch.dict("sys.modules", {"features": fake_module}):
        with patch("builtins.__import__", wraps=__import__):
            ok = await messaging._send_push_one("u1", "Title", "Body", "rider", False, "info")
    assert ok is True


@pytest.mark.anyio
async def test_send_push_one_marketing_uses_marketing_helper():
    marketing_mock = AsyncMock(return_value=True)
    with patch("utils.marketing_push.send_marketing_push", marketing_mock, create=True):
        ok = await messaging._send_push_one("u1", "Title", "Body", "rider", True, "promo")
    assert ok is True
    marketing_mock.assert_awaited_once()
    assert marketing_mock.await_args.kwargs["user_id"] == "u1"


@pytest.mark.anyio
async def test_send_push_one_transactional_exception_returns_false():
    async def _raise(*a, **kw):
        raise RuntimeError("fcm down")

    with patch("features.send_push_notification", _raise, create=True):
        ok = await messaging._send_push_one("u1", "Title", "Body", None, False, "info")
    assert ok is False


@pytest.mark.anyio
async def test_send_email_one_missing_email_returns_false():
    ok = await messaging._send_email_one({"id": "u1", "email": ""}, "T", "D", False)
    assert ok is False


@pytest.mark.anyio
async def test_send_email_one_missing_id_returns_false():
    ok = await messaging._send_email_one({"email": "x@y.com"}, "T", "D", False)
    assert ok is False


@pytest.mark.anyio
async def test_send_email_one_transactional_calls_transactional_sender():
    send_mock = AsyncMock(return_value=True)
    with patch("utils.email_provider.send_transactional_email", send_mock, create=True):
        ok = await messaging._send_email_one({"id": "u1", "email": "u1@x.com"}, "Title", "Body", False)
    assert ok is True
    assert send_mock.await_args.kwargs["email_type"] == "broadcast"


@pytest.mark.anyio
async def test_send_email_one_marketing_calls_marketing_sender():
    send_mock = AsyncMock(return_value=True)
    with patch("utils.marketing_email.send_marketing_email", send_mock, create=True):
        ok = await messaging._send_email_one({"id": "u1", "email": "u1@x.com"}, "Title", "Body", True)
    assert ok is True


@pytest.mark.anyio
async def test_send_sms_one_missing_phone_returns_false():
    ok = await messaging._send_sms_one({"id": "u1", "phone": ""}, "T", "D", False, {})
    assert ok is False


@pytest.mark.anyio
async def test_send_sms_one_transactional_uses_settings_credentials():
    send_mock = AsyncMock(return_value={"success": True})
    settings = {
        "twilio_account_sid": "sid",
        "twilio_auth_token": "tok",
        "twilio_from_number": "+15550100",
    }
    with patch("sms_service.send_sms", send_mock, create=True):
        ok = await messaging._send_sms_one({"id": "u1", "phone": "+15550101"}, "Title", "Body", False, settings)
    assert ok is True
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["twilio_sid"] == "sid"


@pytest.mark.anyio
async def test_send_sms_one_marketing_uses_marketing_sender():
    send_mock = AsyncMock(return_value=True)
    with patch("utils.marketing_sms.send_marketing_sms", send_mock, create=True):
        ok = await messaging._send_sms_one({"id": "u1", "phone": "+15550101"}, "Title", "Body", True, {})
    assert ok is True


# ---------------------------------------------------------------------------
# _fan_out
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fan_out_writes_success_and_failure_counts():
    recipients = [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]
    update_mock = AsyncMock(return_value=None)

    async def _push(uid, *a, **kw):
        return uid != "u3"  # u3 fails

    with (
        patch.object(messaging, "_send_push_one", AsyncMock(side_effect=_push)),
        patch.object(messaging.db_supabase, "update_one", update_mock),
    ):
        await messaging._fan_out(
            "msg-1",
            recipients,
            title="T",
            description="D",
            channels=["push"],
            is_marketing=False,
            target_app="rider",
            msg_type="info",
        )

    update_mock.assert_awaited_once_with("cloud_messages", {"id": "msg-1"}, {"successful": 2, "failed_count": 1})


@pytest.mark.anyio
async def test_fan_out_drops_recipients_without_id():
    recipients = [{"id": "u1"}, {}, "not-a-dict"]
    update_mock = AsyncMock(return_value=None)
    with (
        patch.object(messaging, "_send_push_one", AsyncMock(return_value=True)),
        patch.object(messaging.db_supabase, "update_one", update_mock),
    ):
        await messaging._fan_out(
            "msg-2",
            recipients,
            title="T",
            description="D",
            channels=["push"],
            is_marketing=False,
            target_app=None,
            msg_type="info",
        )
    update_mock.assert_awaited_once_with("cloud_messages", {"id": "msg-2"}, {"successful": 1, "failed_count": 0})


@pytest.mark.anyio
async def test_fan_out_stat_writeback_failure_does_not_raise():
    """A DB error persisting final stats must not propagate — the broadcast
    itself already happened; losing the stat write is logged, not fatal."""
    with (
        patch.object(messaging, "_send_push_one", AsyncMock(return_value=True)),
        patch.object(messaging.db_supabase, "update_one", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        await messaging._fan_out(
            "msg-3",
            [{"id": "u1"}],
            title="T",
            description="D",
            channels=["push"],
            is_marketing=False,
            target_app=None,
            msg_type="info",
        )  # must not raise


@pytest.mark.anyio
async def test_fan_out_loads_sms_settings_only_for_transactional_sms():
    settings_mock = AsyncMock(return_value={"twilio_account_sid": "sid"})
    with (
        patch.object(messaging, "_send_sms_one", AsyncMock(return_value=True)),
        patch.object(messaging.db_supabase, "update_one", AsyncMock()),
        patch("settings_loader.get_app_settings", settings_mock, create=True),
    ):
        await messaging._fan_out(
            "msg-4",
            [{"id": "u1", "phone": "+15550101"}],
            title="T",
            description="D",
            channels=["sms"],
            is_marketing=False,
            target_app=None,
            msg_type="info",
        )
    settings_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_fan_out_skips_settings_load_for_marketing_sms():
    """Marketing SMS goes through the consent-gated marketing sender, which
    doesn't need Twilio creds passed in — settings lookup must be skipped."""
    settings_mock = AsyncMock(return_value={})
    with (
        patch.object(messaging, "_send_sms_one", AsyncMock(return_value=True)),
        patch.object(messaging.db_supabase, "update_one", AsyncMock()),
        patch("settings_loader.get_app_settings", settings_mock, create=True),
    ):
        await messaging._fan_out(
            "msg-5",
            [{"id": "u1", "phone": "+15550101"}],
            title="T",
            description="D",
            channels=["sms"],
            is_marketing=True,
            target_app=None,
            msg_type="info",
        )
    settings_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# admin_send_cloud_message — service-area filter plumbing + failure path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_cloud_message_service_area_ignored_for_all_audience():
    from fastapi import BackgroundTasks
    from fastapi.responses import Response

    background_tasks = BackgroundTasks()
    response = Response()

    with (
        patch.object(messaging.db_supabase, "insert_one", AsyncMock(return_value=None)),
        patch.object(messaging, "_resolve_recipients", AsyncMock(return_value=[{"id": "u1"}])) as resolve_mock,
    ):
        await messaging.admin_send_cloud_message(
            payload=messaging.CloudMessageRequest(title="T", description="D", audience="all", service_area_id="area-x"),
            background_tasks=background_tasks,
            response=response,
        )
    # audience="all" is not in ("customers", "drivers") so service_area_id must be dropped to None
    assert resolve_mock.await_args.args[2] is None


@pytest.mark.anyio
async def test_send_cloud_message_insert_failure_raises_500():
    from fastapi import BackgroundTasks, HTTPException
    from fastapi.responses import Response

    with patch.object(messaging.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(HTTPException) as ei:
            await messaging.admin_send_cloud_message(
                payload=messaging.CloudMessageRequest(title="T", description="D"),
                background_tasks=BackgroundTasks(),
                response=Response(),
            )
    assert ei.value.status_code == 500


# ---------------------------------------------------------------------------
# audience-preview
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_audience_preview_includes_opt_in_counts():
    with (
        patch.object(messaging, "_resolve_recipients", AsyncMock(return_value=[{"id": "1"}, {"id": "2"}])),
        patch.object(messaging.db_supabase, "count_documents", AsyncMock(side_effect=[10, 5, 20])),
    ):
        out = await messaging.admin_cloud_message_audience_preview(audience="customers", service_area_id=None)
    assert out["audience_total"] == 2
    assert out["email_opted_in"] == 10
    assert out["sms_opted_in"] == 5
    assert out["push_opted_in"] == 20


@pytest.mark.anyio
async def test_audience_preview_consent_count_failure_returns_none_not_error():
    with (
        patch.object(messaging, "_resolve_recipients", AsyncMock(return_value=[])),
        patch.object(messaging.db_supabase, "count_documents", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        out = await messaging.admin_cloud_message_audience_preview(audience="all", service_area_id=None)
    assert out["email_opted_in"] is None
    assert out["sms_opted_in"] is None
    assert out["push_opted_in"] is None


@pytest.mark.anyio
async def test_audience_preview_drops_area_filter_for_particular_audience():
    with patch.object(messaging, "_resolve_recipients", AsyncMock(return_value=[])) as resolve_mock:
        with patch.object(messaging.db_supabase, "count_documents", AsyncMock(return_value=0)):
            await messaging.admin_cloud_message_audience_preview(
                audience="particular_customer", service_area_id="area-x"
            )
    assert resolve_mock.await_args.args[2] is None


# ---------------------------------------------------------------------------
# list / stats / cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_cloud_messages_applies_filters():
    get_rows_mock = AsyncMock(return_value=[{"id": "m1"}])
    with patch.object(messaging.db_supabase, "get_rows", get_rows_mock):
        out = await messaging.admin_get_cloud_messages(status="sent", audience="customers", limit=10, offset=0)
    assert out == [{"id": "m1"}]
    args, kwargs = get_rows_mock.await_args
    assert args[1] == {"status": "sent", "audience": "customers"}


@pytest.mark.anyio
async def test_get_cloud_messages_table_missing_returns_empty_list():
    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(side_effect=RuntimeError("no table"))):
        out = await messaging.admin_get_cloud_messages(limit=10, offset=0)
    assert out == []


@pytest.mark.anyio
async def test_get_cloud_message_stats_computes_success_rate():
    all_messages = [
        {"status": "sent", "successful": 8, "total_recipients": 10},
        {"status": "scheduled", "successful": 0, "total_recipients": 0},
        {"status": "failed", "successful": 0, "total_recipients": 5},
    ]
    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(return_value=all_messages)):
        out = await messaging.admin_get_cloud_message_stats()
    assert out["total_messages"] == 3
    assert out["total_sent"] == 1
    assert out["total_scheduled"] == 1
    assert out["total_failed"] == 1
    assert out["total_recipients_reached"] == 8
    assert out["success_rate"] == round(8 / 15 * 100, 1)


@pytest.mark.anyio
async def test_get_cloud_message_stats_no_recipients_zero_rate():
    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(return_value=[])):
        out = await messaging.admin_get_cloud_message_stats()
    assert out["success_rate"] == 0


@pytest.mark.anyio
async def test_delete_cloud_message_not_found_raises_404():
    from fastapi import HTTPException

    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as ei:
            await messaging.admin_delete_cloud_message("missing-id")
    assert ei.value.status_code == 404


@pytest.mark.anyio
async def test_delete_cloud_message_already_sent_rejected():
    from fastapi import HTTPException

    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "m1", "status": "sent"}])):
        with pytest.raises(HTTPException) as ei:
            await messaging.admin_delete_cloud_message("m1")
    assert ei.value.status_code == 400


@pytest.mark.anyio
async def test_delete_cloud_message_cancels_scheduled():
    update_mock = AsyncMock(return_value=None)
    with (
        patch.object(messaging.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "m1", "status": "scheduled"}])),
        patch.object(messaging.db_supabase, "update_one", update_mock),
    ):
        out = await messaging.admin_delete_cloud_message("m1")
    assert out == {"message": "Message cancelled"}
    update_mock.assert_awaited_once_with("cloud_messages", {"id": "m1"}, {"status": "cancelled"})


# ---------------------------------------------------------------------------
# marketing suppressions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_marketing_suppressions_filters_by_channel():
    get_rows_mock = AsyncMock(return_value=[{"id": "s1", "channel": "email"}])
    with patch.object(messaging.db_supabase, "get_rows", get_rows_mock):
        out = await messaging.admin_list_marketing_suppressions(channel="email", limit=50, offset=0)
    assert out == [{"id": "s1", "channel": "email"}]
    args, _ = get_rows_mock.await_args
    assert args[1] == {"channel": "email"}


@pytest.mark.anyio
async def test_list_marketing_suppressions_query_failure_returns_empty():
    with patch.object(messaging.db_supabase, "get_rows", AsyncMock(side_effect=RuntimeError("down"))):
        out = await messaging.admin_list_marketing_suppressions(channel=None, limit=50, offset=0)
    assert out == []


@pytest.mark.anyio
async def test_add_marketing_suppression_delegates_to_service():
    add_mock = AsyncMock(return_value=True)
    with patch("services.marketing_consent.add_marketing_suppression", add_mock, create=True):
        out = await messaging.admin_add_marketing_suppression(
            messaging.ManualSuppressionRequest(channel="email", target="a@b.com", reason="bounce")
        )
    assert out == {"success": True, "added": True}
    add_mock.assert_awaited_once_with("email", "a@b.com", reason="bounce", source="admin")


@pytest.mark.anyio
async def test_delete_marketing_suppression_not_found_raises_404():
    from fastapi import HTTPException

    with patch.object(messaging.db_supabase, "find_one", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as ei:
            await messaging.admin_delete_marketing_suppression("missing")
    assert ei.value.status_code == 404


@pytest.mark.anyio
async def test_delete_marketing_suppression_removes_existing():
    delete_mock = AsyncMock(return_value=None)
    with (
        patch.object(messaging.db_supabase, "find_one", AsyncMock(return_value={"id": "s1"})),
        patch.object(messaging.db_supabase, "delete_many", delete_mock),
    ):
        out = await messaging.admin_delete_marketing_suppression("s1")
    assert out == {"success": True}
    delete_mock.assert_awaited_once_with("marketing_suppressions", {"id": "s1"})
