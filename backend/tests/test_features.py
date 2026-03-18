"""
Unit tests for features module.
Tests cover support tickets, FAQs, surge pricing, notifications, and other features.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSupportTickets:
    """Tests for support ticket functionality."""
    
    @pytest.mark.asyncio
    async def test_create_ticket(self, mock_supabase_client):
        """Test creating a support ticket."""
        from backend.db_supabase import insert_one
        
        ticket_data = {
            'user_id': 'user_123',
            'subject': 'Payment issue',
            'description': 'I was charged twice for my ride',
            'category': 'billing',
            'status': 'open'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ticket_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('support_tickets', ticket_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_user_tickets(self, mock_supabase_client):
        """Test getting tickets for a user."""
        from backend.db_supabase import get_rows
        
        mock_tickets = [
            {'id': 'ticket_1', 'status': 'open', 'subject': 'Payment issue'},
            {'id': 'ticket_2', 'status': 'closed', 'subject': 'Driver behavior'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_tickets
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('support_tickets', {'user_id': 'user_123'})
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_update_ticket_status(self, mock_supabase_client):
        """Test updating ticket status."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ticket_123', 'status': 'closed'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('support_tickets', {'id': 'ticket_123'}, {'status': 'closed'})
        
        assert result['status'] == 'closed'
    
    @pytest.mark.asyncio
    async def test_reply_to_ticket(self, mock_supabase_client):
        """Test adding a reply to a ticket."""
        from backend.db_supabase import insert_one
        
        reply_data = {
            'ticket_id': 'ticket_123',
            'user_id': 'admin_1',
            'message': 'We have reviewed your case and issued a refund.',
            'is_admin_reply': True
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'reply_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('ticket_replies', reply_data)
        
        assert result is not None


class TestFAQs:
    """Tests for FAQ functionality."""
    
    @pytest.mark.asyncio
    async def test_create_faq(self, mock_supabase_client):
        """Test creating an FAQ."""
        from backend.db_supabase import insert_one
        
        faq_data = {
            'question': 'How do I request a ride?',
            'answer': 'Open the app and enter your destination...',
            'category': 'rides',
            'order': 1
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'faq_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('faqs', faq_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_faqs_by_category(self, mock_supabase_client):
        """Test getting FAQs by category."""
        from backend.db_supabase import get_rows
        
        mock_faqs = [
            {'id': 'faq_1', 'question': 'How do I request a ride?', 'category': 'rides'},
            {'id': 'faq_2', 'question': 'How do I pay?', 'category': 'rides'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_faqs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('faqs', {'category': 'rides'})
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_update_faq(self, mock_supabase_client):
        """Test updating an FAQ."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'faq_123', 'answer': 'Updated answer...'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('faqs', {'id': 'faq_123'}, {'answer': 'Updated answer...'})
        
        assert result['answer'] == 'Updated answer...'


class TestSurgePricing:
    """Tests for surge pricing functionality."""
    
    @pytest.mark.asyncio
    async def test_update_surge_multiplier(self, mock_supabase_client):
        """Test updating surge pricing multiplier."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'surge_123', 'multiplier': 1.5}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('surge_pricing', {'id': 'surge_123'}, {'multiplier': 1.5})
        
        assert result['multiplier'] == 1.5
    
    @pytest.mark.asyncio
    async def test_get_surge_for_area(self, mock_supabase_client):
        """Test getting surge pricing for an area."""
        from backend.db_supabase import get_rows
        
        mock_surge = [{'id': 'surge_123', 'area_id': 'area_1', 'multiplier': 1.25}]
        
        mock_response = MagicMock()
        mock_response.data = mock_surge
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('surge_pricing', {'area_id': 'area_1'})
        
        assert len(result) == 1
        assert result[0]['multiplier'] == 1.25
    
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
            'user_id': 'user_123',
            'title': 'Your driver has arrived',
            'body': 'John is waiting in a white Toyota Camry',
            'type': 'ride_update',
            'data': {'ride_id': 'ride_123'}
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'notif_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('notifications', notification_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_user_notifications(self, mock_supabase_client):
        """Test getting notifications for a user."""
        from backend.db_supabase import get_rows
        
        mock_notifications = [
            {'id': 'notif_1', 'title': 'Ride confirmed', 'read': False},
            {'id': 'notif_2', 'title': 'Driver arrived', 'read': True}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_notifications
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('notifications', {'user_id': 'user_123'})
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_mark_notification_read(self, mock_supabase_client):
        """Test marking a notification as read."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'notif_123', 'read': True}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('notifications', {'id': 'notif_123'}, {'read': True})
        
        assert result['read'] is True
    
    @pytest.mark.asyncio
    async def test_register_fcm_token(self, mock_supabase_client):
        """Test registering FCM token for push notifications."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'user_123', 'fcm_token': 'token_abc'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('users', {'id': 'user_123'}, {'fcm_token': 'token_abc'})
        
        assert result['fcm_token'] == 'token_abc'


class TestServiceAreas:
    """Tests for service area functionality."""
    
    @pytest.mark.asyncio
    async def test_get_service_areas(self, mock_supabase_client):
        """Test getting all service areas."""
        from backend.db_supabase import get_rows
        
        mock_areas = [
            {'id': 'area_1', 'name': 'Downtown', 'active': True},
            {'id': 'area_2', 'name': 'Airport', 'active': True}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_areas
        mock_supabase_client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('service_areas')
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_assign_driver_to_area(self, mock_supabase_client):
        """Test assigning a driver to a service area."""
        from backend.db_supabase import insert_one
        
        assignment_data = {
            'driver_id': 'driver_123',
            'area_id': 'area_1'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'assignment_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('driver_areas', assignment_data)
        
        assert result is not None
    
    def test_point_in_polygon(self):
        """Test point in polygon check for service area."""
        from backend.features import point_in_polygon
        
        # Simple square polygon
        polygon = [
            {'lat': 52.1, 'lng': -106.7},
            {'lat': 52.1, 'lng': -106.6},
            {'lat': 52.2, 'lng': -106.6},
            {'lat': 52.2, 'lng': -106.7}
        ]
        
        # Point inside polygon
        assert point_in_polygon(52.15, -106.65, polygon) is True
        
        # Point outside polygon
        assert point_in_polygon(52.0, -106.65, polygon) is False


class TestSavedAddresses:
    """Tests for saved addresses functionality."""
    
    @pytest.mark.asyncio
    async def test_save_address(self, mock_supabase_client):
        """Test saving an address."""
        from backend.db_supabase import insert_one
        
        address_data = {
            'user_id': 'user_123',
            'label': 'Home',
            'address': '123 Main St',
            'lat': 52.1333,
            'lng': -106.6667
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'addr_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('saved_addresses', address_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_user_addresses(self, mock_supabase_client):
        """Test getting saved addresses for a user."""
        from backend.db_supabase import get_rows
        
        mock_addresses = [
            {'id': 'addr_1', 'label': 'Home', 'address': '123 Main St'},
            {'id': 'addr_2', 'label': 'Work', 'address': '456 Office Blvd'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_addresses
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('saved_addresses', {'user_id': 'user_123'})
        
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
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await delete_one('saved_addresses', {'id': 'addr_123'})
        
        assert result is not None


class TestEmergencyContacts:
    """Tests for emergency contact functionality."""
    
    @pytest.mark.asyncio
    async def test_add_emergency_contact(self, mock_supabase_client):
        """Test adding an emergency contact."""
        from backend.db_supabase import insert_one
        
        contact_data = {
            'user_id': 'user_123',
            'name': 'John Doe',
            'phone': '+1234567890',
            'relationship': 'spouse'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'contact_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('emergency_contacts', contact_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_emergency_contacts(self, mock_supabase_client):
        """Test getting emergency contacts for a user."""
        from backend.db_supabase import get_rows
        
        mock_contacts = [
            {'id': 'contact_1', 'name': 'John Doe', 'phone': '+1234567890'},
            {'id': 'contact_2', 'name': 'Jane Doe', 'phone': '+0987654321'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_contacts
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('emergency_contacts', {'user_id': 'user_123'})
        
        assert len(result) == 2


class TestCorporateAccounts:
    """Tests for corporate account functionality."""
    
    @pytest.mark.asyncio
    async def test_create_corporate_account(self, mock_supabase_client):
        """Test creating a corporate account."""
        from backend.db_supabase import insert_one
        
        corporate_data = {
            'company_name': 'Acme Corp',
            'admin_user_id': 'user_123',
            'billing_email': 'billing@acme.com',
            'status': 'active'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'corp_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('corporate_accounts', corporate_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_add_employee_to_corporate(self, mock_supabase_client):
        """Test adding employee to corporate account."""
        from backend.db_supabase import insert_one
        
        employee_data = {
            'corporate_id': 'corp_123',
            'user_id': 'user_456',
            'role': 'employee'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'emp_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('corporate_employees', employee_data)
        
        assert result is not None