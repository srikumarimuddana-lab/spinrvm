"""Tests for the marketing-aware cloud-message fan-out and audience resolver.

Covers:
- service_area audience resolves riders via rides → users
- marketing email fan-out routes through send_marketing_email (consent-gated),
  NOT send_transactional_email
- non-marketing email fan-out routes through send_transactional_email
- marketing SMS fan-out routes through send_marketing_sms
- marketing push fan-out routes through send_marketing_push (consent-gated),
  NOT the raw features.send_push_notification
- non-marketing push fan-out still uses the raw send path (unchanged)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes.admin import messaging as m
except ImportError:  # pragma: no cover
    from backend.routes.admin import messaging as m  # type: ignore

pytestmark = pytest.mark.anyio


async def test_customers_filtered_by_service_area_via_rides():
    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return [{"rider_id": "u1"}, {"rider_id": "u2"}, {"rider_id": "u1"}]  # dupe u1
        if table == "users":
            assert set(filters["id"]["$in"]) == {"u1", "u2"}
            assert filters["is_rider"] is True  # service-area filter stays scoped to riders
            return [{"id": "u1", "email": "a@b.com"}, {"id": "u2", "email": "c@d.com"}]
        return []

    with patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
        rows = await m._resolve_recipients("customers", [], "area-1", need_contact=True)
    assert {r["id"] for r in rows} == {"u1", "u2"}


async def test_drivers_filtered_by_service_area_via_drivers_table():
    async def _get_rows(table, filters=None, **kw):
        if table == "drivers":
            assert filters == {"service_area_id": "area-9"}
            return [{"user_id": "d1"}, {"user_id": "d2"}]
        if table == "users":
            assert set(filters["id"]["$in"]) == {"d1", "d2"}
            return [{"id": "d1"}, {"id": "d2"}]
        return []

    with patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
        rows = await m._resolve_recipients("drivers", [], "area-9", need_contact=False)
    assert {r["id"] for r in rows} == {"d1", "d2"}


async def test_customers_without_area_filter_targets_all_riders():
    async def _get_rows(table, filters=None, **kw):
        assert table == "users" and filters == {"is_rider": True}
        return [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]

    with patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
        rows = await m._resolve_recipients("customers", [], None, need_contact=False)
    assert len(rows) == 3


async def test_marketing_email_fanout_uses_marketing_sender():
    recipients = [{"id": "u1", "email": "a@b.com", "phone": "+13065551234"}]
    with (
        patch("utils.marketing_email.send_marketing_email", AsyncMock(return_value=True)) as mk,
        patch("utils.email_provider.send_transactional_email", AsyncMock(return_value=True)) as tx,
        patch("db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        await m._fan_out(
            "msg1", recipients, title="T", description="D",
            channels=["email"], is_marketing=True, target_app="rider", msg_type="info",
        )
    mk.assert_awaited_once()
    assert mk.call_args.kwargs["to"] == "a@b.com"
    assert mk.call_args.kwargs["user_id"] == "u1"
    tx.assert_not_called()  # marketing must never use the transactional path


async def test_non_marketing_email_fanout_uses_transactional_sender():
    recipients = [{"id": "u1", "email": "a@b.com"}]
    with (
        patch("utils.marketing_email.send_marketing_email", AsyncMock(return_value=True)) as mk,
        patch("utils.email_provider.send_transactional_email", AsyncMock(return_value=True)) as tx,
        patch("db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        await m._fan_out(
            "msg2", recipients, title="T", description="D",
            channels=["email"], is_marketing=False, target_app=None, msg_type="info",
        )
    tx.assert_awaited_once()
    assert tx.call_args.kwargs["email_type"] == "broadcast"
    mk.assert_not_called()


async def test_marketing_sms_fanout_uses_marketing_sms():
    recipients = [{"id": "u1", "phone": "+13065551234"}]
    with (
        patch("utils.marketing_sms.send_marketing_sms", AsyncMock(return_value=True)) as mk,
        patch("db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        await m._fan_out(
            "msg3", recipients, title="T", description="D",
            channels=["sms"], is_marketing=True, target_app="rider", msg_type="info",
        )
    mk.assert_awaited_once()
    assert mk.call_args.kwargs["to"] == "+13065551234"


async def test_marketing_push_fanout_uses_marketing_push():
    """A promo/marketing push must be gated by consent, same as email/SMS —
    it must NOT reach every rider regardless of push_opt_in."""
    recipients = [{"id": "u1"}]
    with (
        patch("utils.marketing_push.send_marketing_push", AsyncMock(return_value=True)) as mk,
        patch("features.send_push_notification", AsyncMock(return_value=True)) as raw,
        patch("db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        await m._fan_out(
            "msg4", recipients, title="T", description="D",
            channels=["push"], is_marketing=True, target_app="rider", msg_type="promotion",
        )
    mk.assert_awaited_once()
    assert mk.call_args.kwargs["user_id"] == "u1"
    assert mk.call_args.kwargs["data"] == {"type": "promotion"}
    raw.assert_not_called()  # marketing push must never bypass the consent gate


async def test_non_marketing_push_fanout_uses_raw_send():
    """Operational pushes (ride updates, safety) are unaffected by the
    marketing consent gate."""
    recipients = [{"id": "u1"}]
    with (
        patch("utils.marketing_push.send_marketing_push", AsyncMock(return_value=True)) as mk,
        patch("features.send_push_notification", AsyncMock(return_value=True)) as raw,
        patch("db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        await m._fan_out(
            "msg5", recipients, title="T", description="D",
            channels=["push"], is_marketing=False, target_app="rider", msg_type="alert",
        )
    raw.assert_awaited_once()
    assert raw.call_args.kwargs["data"] == {"type": "alert"}
    mk.assert_not_called()
