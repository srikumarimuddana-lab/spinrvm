"""
Unit tests for features module.
Tests cover support tickets, FAQs, surge pricing, notifications, and other features.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ADMIN = {"id": "admin-1", "email": "admin@spinr.ca", "role": "super_admin"}


class TestSupportTickets:
    """Tests for support ticket functionality."""

    @pytest.mark.asyncio
    async def test_create_ticket(self, mock_supabase_client):
        """Test creating a support ticket."""
        from backend.db_supabase import insert_one

        ticket_data = {
            "user_id": "user_123",
            "subject": "Payment issue",
            "description": "I was charged twice for my ride",
            "category": "billing",
            "status": "open",
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "ticket_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("support_tickets", ticket_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_user_tickets(self, mock_supabase_client):
        """Test getting tickets for a user."""
        from backend.db_supabase import get_rows

        mock_tickets = [
            {"id": "ticket_1", "status": "open", "subject": "Payment issue"},
            {"id": "ticket_2", "status": "closed", "subject": "Driver behavior"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_tickets
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rows("support_tickets", {"user_id": "user_123"})

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_ticket_status(self, mock_supabase_client):
        """Test updating ticket status."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "ticket_123", "status": "closed"}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await update_one("support_tickets", {"id": "ticket_123"}, {"status": "closed"})

        assert result["status"] == "closed"

    @pytest.mark.asyncio
    async def test_reply_to_ticket(self, mock_supabase_client):
        """Test adding a reply to a ticket."""
        from backend.db_supabase import insert_one

        reply_data = {
            "ticket_id": "ticket_123",
            "user_id": "admin_1",
            "message": "We have reviewed your case and issued a refund.",
            "is_admin_reply": True,
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "reply_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("ticket_replies", reply_data)

        assert result is not None


class TestFAQs:
    """Tests for FAQ functionality."""

    @pytest.mark.asyncio
    async def test_create_faq(self, mock_supabase_client):
        """Test creating an FAQ."""
        from backend.db_supabase import insert_one

        faq_data = {
            "question": "How do I request a ride?",
            "answer": "Open the app and enter your destination...",
            "category": "rides",
            "order": 1,
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "faq_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("faqs", faq_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_faqs_by_category(self, mock_supabase_client):
        """Test getting FAQs by category."""
        from backend.db_supabase import get_rows

        mock_faqs = [
            {"id": "faq_1", "question": "How do I request a ride?", "category": "rides"},
            {"id": "faq_2", "question": "How do I pay?", "category": "rides"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_faqs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute = (
            MagicMock(return_value=mock_response)
        )

        result = await get_rows("faqs", {"category": "rides"})

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_faq(self, mock_supabase_client):
        """Test updating an FAQ."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "faq_123", "answer": "Updated answer..."}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await update_one("faqs", {"id": "faq_123"}, {"answer": "Updated answer..."})

        assert result["answer"] == "Updated answer..."

    @pytest.mark.asyncio
    async def test_get_faqs_route_orders_by_sort_order_ascending(self):
        """features.get_faqs is the only GET /faqs handler (the formerly-
        shadowed duplicate at routes/faqs.py was removed 2026-08-18). A lower
        sort_order must sort first regardless of DB fetch order, since neither
        app re-sorts this list client-side."""
        from backend import features

        rows = [
            {"id": "third", "sort_order": 2},
            {"id": "first", "sort_order": 0},
            {"id": "second", "sort_order": 1},
        ]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            result = await features.get_faqs()
        assert [r["id"] for r in result] == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_get_faqs_route_missing_sort_order_treated_as_zero(self):
        from backend import features

        rows = [{"id": "explicit-zero", "sort_order": 0}, {"id": "no-field"}, {"id": "positive", "sort_order": 1}]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            result = await features.get_faqs()
        assert [r["id"] for r in result] == ["explicit-zero", "no-field", "positive"]

    @pytest.mark.asyncio
    async def test_get_faqs_audience_filter_passed_to_query(self):
        """The live handler's own docstring says this filter MUST apply —
        without it, driver-only FAQs would surface in the rider app. Assert
        the $in:[audience, 'both'] filter is actually built and passed to
        the DB call, not just documented as a requirement."""
        from backend import features

        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows,
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            await features.get_faqs(audience="driver")
        _, kwargs = mock_get_rows.call_args
        query = mock_get_rows.call_args.args[1] if len(mock_get_rows.call_args.args) > 1 else kwargs.get("query")
        assert query["audience"] == {"$in": ["both", "driver"]}

    @pytest.mark.asyncio
    async def test_get_faqs_category_filter_passed_to_query(self):
        from backend import features

        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows,
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            await features.get_faqs(category="billing")
        query = mock_get_rows.call_args.args[1]
        assert query["category"] == "billing"

    @pytest.mark.asyncio
    async def test_get_faqs_global_rows_always_included_area_tagged_rows_scoped(self):
        """Global FAQs (no service_area_ids) always show. Area-tagged FAQs
        only show when the caller's resolved scope overlaps their tags."""
        from backend import features

        rows = [
            {"id": "global", "service_area_ids": None},
            {"id": "in-scope", "service_area_ids": ["area-yxe"]},
            {"id": "out-of-scope", "service_area_ids": ["area-other"]},
        ]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value={"area-yxe"})),
        ):
            result = await features.get_faqs(service_area_id="area-yxe")
        assert {r["id"] for r in result} == {"global", "in-scope"}

    @pytest.mark.asyncio
    async def test_get_faqs_lat_lng_resolves_service_area_then_scopes(self):
        from backend import features

        rows = [{"id": "a1", "service_area_ids": ["area-5"]}]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch(
                "backend.routes.fares.resolve_service_area_for_point",
                new=AsyncMock(return_value={"id": "area-5"}),
            ) as mock_point,
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value={"area-5"})) as mock_scope,
        ):
            result = await features.get_faqs(lat=52.1332, lng=-106.6700)
        mock_point.assert_awaited_once_with(52.1332, -106.6700)
        mock_scope.assert_awaited_once_with("area-5")
        assert [r["id"] for r in result] == ["a1"]

    @pytest.mark.asyncio
    async def test_get_faqs_no_location_context_hides_area_tagged_rows(self):
        from backend import features

        rows = [{"id": "area-only", "service_area_ids": ["area-1"]}, {"id": "global"}]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            result = await features.get_faqs()
        assert [r["id"] for r in result] == ["global"]

    @pytest.mark.asyncio
    async def test_get_faqs_returns_empty_list_when_db_returns_none(self):
        from backend import features

        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=None)),
            patch("backend.routes.fares.resolve_area_scope", new=AsyncMock(return_value=set())),
        ):
            result = await features.get_faqs()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_faqs_area_resolution_failure_degrades_to_empty_scope(self):
        """Must never 500 an unauthenticated public endpoint on a service-area
        lookup failure — degrade to global-FAQs-only instead (CLAUDE.md
        'no silent swallow' still requires the error be logged, not hidden)."""
        from backend import features

        rows = [{"id": "global"}, {"id": "area-tagged", "service_area_ids": ["area-1"]}]
        with (
            patch("backend.features.db_supabase.get_rows", new=AsyncMock(return_value=rows)),
            patch(
                "backend.routes.fares.resolve_area_scope",
                new=AsyncMock(side_effect=RuntimeError("service_areas lookup exploded")),
            ),
            patch("backend.features.logger.opt", new=MagicMock(return_value=MagicMock(error=MagicMock()))),
        ):
            result = await features.get_faqs(service_area_id="area-1")
        assert [r["id"] for r in result] == ["global"]


class TestSurgePricing:
    """Tests for surge pricing functionality."""

    @pytest.mark.asyncio
    async def test_update_surge_multiplier(self, mock_supabase_client):
        """Test updating surge pricing multiplier."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "surge_123", "multiplier": 1.5}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await update_one("surge_pricing", {"id": "surge_123"}, {"multiplier": 1.5})

        assert result["multiplier"] == 1.5

    @pytest.mark.asyncio
    async def test_get_surge_for_area(self, mock_supabase_client):
        """Test getting surge pricing for an area."""
        from backend.db_supabase import get_rows

        mock_surge = [{"id": "surge_123", "area_id": "area_1", "multiplier": 1.25}]

        mock_response = MagicMock()
        mock_response.data = mock_surge
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rows("surge_pricing", {"area_id": "area_1"})

        assert len(result) == 1
        assert result[0]["multiplier"] == 1.25

    def test_calculate_surge_price(self):
        """Test calculating price with surge multiplier."""
        base_price = 15.00
        surge_multiplier = 1.5

        final_price = base_price * surge_multiplier

        assert final_price == 22.50


class TestNotifications:
    """Tests for notification functionality."""

    @pytest.mark.asyncio
    async def test_send_push_notification(self, mock_supabase_client):
        """Test sending a push notification."""
        from backend.db_supabase import insert_one

        notification_data = {
            "user_id": "user_123",
            "title": "Your driver has arrived",
            "body": "John is waiting in a white Toyota Camry",
            "type": "ride_update",
            "data": {"ride_id": "ride_123"},
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "notif_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("notifications", notification_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_user_notifications(self, mock_supabase_client):
        """Test getting notifications for a user."""
        from backend.db_supabase import get_rows

        mock_notifications = [
            {"id": "notif_1", "title": "Ride confirmed", "read": False},
            {"id": "notif_2", "title": "Driver arrived", "read": True},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_notifications
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rows("notifications", {"user_id": "user_123"})

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_mark_notification_read(self, mock_supabase_client):
        """Test marking a notification as read."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "notif_123", "read": True}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await update_one("notifications", {"id": "notif_123"}, {"read": True})

        assert result["read"] is True

    @pytest.mark.asyncio
    async def test_register_fcm_token(self, mock_supabase_client):
        """Test registering FCM token for push notifications."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "user_123", "fcm_token": "token_abc"}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await update_one("users", {"id": "user_123"}, {"fcm_token": "token_abc"})

        assert result["fcm_token"] == "token_abc"


class TestServiceAreas:
    """Tests for service area functionality."""

    @pytest.mark.asyncio
    async def test_get_service_areas(self, mock_supabase_client):
        """Test getting all service areas."""
        from backend.db_supabase import get_rows

        mock_areas = [
            {"id": "area_1", "name": "Downtown", "active": True},
            {"id": "area_2", "name": "Airport", "active": True},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_areas
        mock_supabase_client.table.return_value.select.return_value.execute = MagicMock(return_value=mock_response)

        result = await get_rows("service_areas")

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_assign_driver_to_area(self, mock_supabase_client):
        """Test assigning a driver to a service area."""
        from backend.db_supabase import insert_one

        assignment_data = {"driver_id": "driver_123", "area_id": "area_1"}

        mock_response = MagicMock()
        mock_response.data = [{"id": "assignment_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("driver_areas", assignment_data)

        assert result is not None

    def test_point_in_polygon(self):
        """Test point in polygon check for service area."""
        from backend.features import point_in_polygon

        # Simple square polygon
        polygon = [
            {"lat": 52.1, "lng": -106.7},
            {"lat": 52.1, "lng": -106.6},
            {"lat": 52.2, "lng": -106.6},
            {"lat": 52.2, "lng": -106.7},
        ]

        # Point inside polygon
        assert point_in_polygon(52.15, -106.65, polygon) is True

        # Point outside polygon
        assert point_in_polygon(52.0, -106.65, polygon) is False


class TestAreaTaxJustification:
    """A29 (ACTION_ITEMS.md): `pricing_router`'s dedicated PUT /areas/{id}/tax
    (not currently reachable from any frontend, but hardened for consistency)
    requires a written justification, same as the live admin-dashboard tax
    editor at routes/admin/service_areas.py."""

    @pytest.mark.asyncio
    async def test_tax_change_without_justification_rejected(self):
        from backend.features import UpdateTaxConfigRequest, update_area_tax

        req = UpdateTaxConfigRequest(pst_enabled=True, pst_rate=6.0)
        with pytest.raises(HTTPException) as exc_info:
            await update_area_tax("area-1", req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "justification" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_tax_change_with_justification_logs_and_succeeds(self):
        from backend.features import UpdateTaxConfigRequest, update_area_tax

        log_admin_action = AsyncMock()
        updated_row = {"gst_enabled": True, "gst_rate": 5.0, "pst_enabled": True, "pst_rate": 6.0}
        with (
            patch("backend.features.db_supabase.update_one", AsyncMock()) as update_one,
            patch("backend.features.db_supabase.get_rows", AsyncMock(return_value=[updated_row])),
            patch("backend.features.log_admin_action", log_admin_action),
        ):
            req = UpdateTaxConfigRequest(
                pst_enabled=True, pst_rate=6.0, tax_justification="SK PST enablement, approved by finance"
            )
            result = await update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_awaited_once()
        log_admin_action.assert_awaited_once()
        assert log_admin_action.call_args.args[1] == "tax_config_updated"
        assert result["pst_enabled"] is True
        assert result["pst_rate"] == 6.0

    @pytest.mark.asyncio
    async def test_empty_payload_skips_write_and_justification_check(self):
        from backend.features import UpdateTaxConfigRequest, update_area_tax

        with (
            patch("backend.features.db_supabase.update_one", AsyncMock()) as update_one,
            patch("backend.features.db_supabase.get_rows", AsyncMock(return_value=[{"gst_rate": 5.0}])),
        ):
            req = UpdateTaxConfigRequest()
            result = await update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_not_awaited()
        assert result["gst_rate"] == 5.0


class TestSavedAddresses:
    """Tests for saved addresses functionality."""

    @pytest.mark.asyncio
    async def test_save_address(self, mock_supabase_client):
        """Test saving an address."""
        from backend.db_supabase import insert_one

        address_data = {
            "user_id": "user_123",
            "label": "Home",
            "address": "123 Main St",
            "lat": 52.1333,
            "lng": -106.6667,
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "addr_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("saved_addresses", address_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_user_addresses(self, mock_supabase_client):
        """Test getting saved addresses for a user."""
        from backend.db_supabase import get_rows

        mock_addresses = [
            {"id": "addr_1", "label": "Home", "address": "123 Main St"},
            {"id": "addr_2", "label": "Work", "address": "456 Office Blvd"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_addresses
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rows("saved_addresses", {"user_id": "user_123"})

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_delete_saved_address(self, mock_supabase_client):
        """Test deleting a saved address."""
        from backend.db_supabase import delete_one

        mock_response = MagicMock()
        mock_response.count = 1

        mock_query = MagicMock()
        mock_query.delete.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await delete_one("saved_addresses", {"id": "addr_123"})

        assert result is not None


class TestEmergencyContacts:
    """Tests for emergency contact functionality."""

    @pytest.mark.asyncio
    async def test_add_emergency_contact(self, mock_supabase_client):
        """Test adding an emergency contact."""
        from backend.db_supabase import insert_one

        contact_data = {"user_id": "user_123", "name": "John Doe", "phone": "+1234567890", "relationship": "spouse"}

        mock_response = MagicMock()
        mock_response.data = [{"id": "contact_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("emergency_contacts", contact_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_emergency_contacts(self, mock_supabase_client):
        """Test getting emergency contacts for a user."""
        from backend.db_supabase import get_rows

        mock_contacts = [
            {"id": "contact_1", "name": "John Doe", "phone": "+1234567890"},
            {"id": "contact_2", "name": "Jane Doe", "phone": "+0987654321"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_contacts
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rows("emergency_contacts", {"user_id": "user_123"})

        assert len(result) == 2


class TestCorporateAccounts:
    """Tests for corporate account functionality."""

    @pytest.mark.asyncio
    async def test_create_corporate_account(self, mock_supabase_client):
        """Test creating a corporate account."""
        from backend.db_supabase import insert_one

        corporate_data = {
            "company_name": "Acme Corp",
            "admin_user_id": "user_123",
            "billing_email": "billing@acme.com",
            "status": "active",
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "corp_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("corporate_accounts", corporate_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_add_employee_to_corporate(self, mock_supabase_client):
        """Test adding employee to corporate account."""
        from backend.db_supabase import insert_one

        employee_data = {"corporate_id": "corp_123", "user_id": "user_456", "role": "employee"}

        mock_response = MagicMock()
        mock_response.data = [{"id": "emp_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("corporate_employees", employee_data)

        assert result is not None
