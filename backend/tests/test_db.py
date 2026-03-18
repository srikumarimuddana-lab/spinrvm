"""
Unit tests for database layer (db.py and db_supabase.py).
Tests cover CRUD operations, queries, and database utilities.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMockCursor:
    """Tests for the MockCursor class."""
    
    @pytest.fixture
    def mock_cursor(self):
        from backend.db import MockCursor
        return MockCursor('test_collection', {'status': 'active'})
    
    def test_cursor_initialization(self, mock_cursor):
        """Test cursor is initialized with correct filter."""
        assert mock_cursor.collection_name == 'test_collection'
        assert mock_cursor._filter == {'status': 'active'}
    
    @pytest.mark.asyncio
    async def test_cursor_to_list(self, mock_cursor):
        """Test cursor to_list returns list of documents."""
        # The MockCursor needs proper implementation for to_list
        # This tests the async behavior
        result = await mock_cursor.to_list(limit=10)
        assert isinstance(result, list)
    
    @pytest.mark.asyncio
    async def test_cursor_chain(self, mock_cursor):
        """Test cursor method chaining (sort, skip, limit)."""
        result = mock_cursor.sort('created_at', -1).skip(10).limit(20)
        assert result is mock_cursor


class TestCollection:
    """Tests for the Collection class."""
    
    @pytest.fixture
    def collection(self):
        from backend.db import Collection
        return Collection('test_collection')
    
    def test_collection_initialization(self, collection):
        """Test collection is initialized with correct name."""
        assert collection.name == 'test_collection'
    
    def test_collection_find(self, collection):
        """Test collection find returns a cursor."""
        cursor = collection.find({'status': 'active'})
        assert cursor is not None
        assert cursor._filter == {'status': 'active'}
    
    def test_collection_find_empty_filter(self, collection):
        """Test collection find with empty filter."""
        cursor = collection.find()
        assert cursor is not None
        assert cursor._filter is None


class TestDBWrapper:
    """Tests for the main DB wrapper."""
    
    @pytest.fixture
    def db(self):
        from backend.db import db
        return db
    
    def test_db_collections_exist(self, db):
        """Test all expected collections exist on db object."""
        expected_collections = [
            'users', 'drivers', 'rides', 'otps', 'otp_records',
            'vehicle_types', 'fare_configs', 'service_areas',
            'settings', 'saved_addresses', 'support_tickets',
            'faqs', 'area_fees', 'surge_pricing', 'notifications',
            'disputes', 'payouts', 'bank_accounts', 'promo_codes',
            'promo_applications', 'driver_documents', 'document_requirements',
            'document_files', 'driver_location_history', 'corporate_accounts',
            'ride_messages', 'emergency_contacts', 'emergency',
            'notification_preferences'
        ]
        for collection_name in expected_collections:
            assert hasattr(db, collection_name), f"Missing collection: {collection_name}"


class TestUserCollection:
    """Tests for user-specific database operations."""
    
    @pytest.fixture
    def user_collection(self):
        from backend.db import db
        return db.users
    
    @pytest.mark.asyncio
    async def test_find_one_user_by_id(self, user_collection, mock_supabase_client):
        """Test finding a user by ID."""
        mock_data = {'id': 'user_123', 'phone': '+1234567890', 'email': 'test@example.com'}
        
        # Setup mock response
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await user_collection.find_one({'id': 'user_123'})
        
        assert result is not None
        assert result['id'] == 'user_123'
        assert result['phone'] == '+1234567890'
    
    @pytest.mark.asyncio
    async def test_find_one_user_not_found(self, user_collection, mock_supabase_client):
        """Test finding a user that doesn't exist."""
        mock_response = MagicMock()
        mock_response.data = []
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await user_collection.find_one({'id': 'nonexistent'})
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_find_one_user_by_phone(self, user_collection, mock_supabase_client):
        """Test finding a user by phone number."""
        mock_data = {'id': 'user_123', 'phone': '+1234567890'}
        
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await user_collection.find_one({'phone': '+1234567890'})
        
        assert result is not None
        assert result['phone'] == '+1234567890'


class TestDriverCollection:
    """Tests for driver-specific database operations."""
    
    @pytest.fixture
    def driver_collection(self):
        from backend.db import db
        return db.drivers
    
    @pytest.mark.asyncio
    async def test_find_available_drivers(self, driver_collection, mock_supabase_client):
        """Test finding available drivers."""
        mock_data = [
            {'id': 'driver_1', 'is_available': True, 'is_online': True},
            {'id': 'driver_2', 'is_available': True, 'is_online': True}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await driver_collection.find_one({'is_available': True})
        
        # Verify the mock was called
        mock_supabase_client.table.assert_called_with('drivers')
    
    @pytest.mark.asyncio
    async def test_update_driver_location(self, driver_collection, mock_supabase_client):
        """Test updating driver location."""
        mock_response = MagicMock()
        mock_response.data = [{'lat': 52.2, 'lng': -106.7}]
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        # This uses the RPC function for location update
        result = await driver_collection.update_one(
            {'id': 'driver_123'},
            {'$set': {'lat': 52.2, 'lng': -106.7}}
        )
    
    @pytest.mark.asyncio
    async def test_set_driver_available(self, driver_collection, mock_supabase_client):
        """Test setting driver availability."""
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', 'is_available': True}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await driver_collection.update_one(
            {'id': 'driver_123'},
            {'$set': {'is_available': True}}
        )


class TestRideCollection:
    """Tests for ride-specific database operations."""
    
    @pytest.fixture
    def ride_collection(self):
        from backend.db import db
        return db.rides
    
    @pytest.mark.asyncio
    async def test_find_ride_by_id(self, ride_collection, mock_supabase_client):
        """Test finding a ride by ID."""
        mock_data = {
            'id': 'ride_123',
            'rider_id': 'user_123',
            'status': 'completed'
        }
        
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await ride_collection.find_one({'id': 'ride_123'})
        
        assert result is not None
        assert result['id'] == 'ride_123'
    
    @pytest.mark.asyncio
    async def test_find_rides_by_status(self, ride_collection, mock_supabase_client):
        """Test finding rides by status."""
        mock_data = [
            {'id': 'ride_1', 'status': 'pending'},
            {'id': 'ride_2', 'status': 'pending'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        cursor = ride_collection.find({'status': 'pending'})
        assert cursor is not None
    
    @pytest.mark.asyncio
    async def test_count_rides(self, ride_collection, mock_supabase_client):
        """Test counting rides."""
        mock_response = MagicMock()
        mock_response.count = 100
        
        # Supabase returns count via a different mechanism
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        count = await ride_collection.count_documents({'status': 'completed'})
        # The count handling depends on implementation


class TestOTPRecordOperations:
    """Tests for OTP record operations."""
    
    @pytest.fixture
    def otp_collection(self):
        from backend.db import db
        return db.otps
    
    @pytest.fixture
    def otp_records_collection(self):
        from backend.db import db
        return db.otp_records
    
    @pytest.mark.asyncio
    async def test_insert_otp_record(self, otp_records_collection, mock_supabase_client):
        """Test inserting an OTP record."""
        mock_response = MagicMock()
        mock_response.data = [{'id': 'otp_123'}]
        mock_supabase_client.table.return_value.insert.return_value = MagicMock(
            execute=AsyncMock(return_value=mock_response)
        )
        
        otp_data = {
            'phone': '+1234567890',
            'code': '123456',
            'expires_at': '2024-01-01T00:10:00Z'
        }
        
        result = await otp_records_collection.insert_one(otp_data)
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_find_otp_by_phone_and_code(self, otp_records_collection, mock_supabase_client):
        """Test finding OTP by phone and code."""
        mock_data = {'id': 'otp_123', 'phone': '+1234567890', 'code': '123456', 'verified': False}
        
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await otp_records_collection.find_one({
            'phone': '+1234567890',
            'code': '123456'
        })
        
        assert result is not None
        assert result['code'] == '123456'
    
    @pytest.mark.asyncio
    async def test_verify_otp(self, otp_records_collection, mock_supabase_client):
        """Test verifying an OTP record."""
        mock_response = MagicMock()
        mock_response.data = [{'id': 'otp_123', 'verified': True}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await otp_records_collection.update_one(
            {'id': 'otp_123'},
            {'$set': {'verified': True}}
        )
    
    @pytest.mark.asyncio
    async def test_delete_expired_otp(self, otp_records_collection, mock_supabase_client):
        """Test deleting expired OTP records."""
        mock_response = MagicMock()
        mock_response.count = 1
        
        mock_query = MagicMock()
        mock_query.delete.return_value = mock_query
        mock_query.lt.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await otp_records_collection.delete_one({'id': 'otp_123'})


class TestDatabaseSupabaseFunctions:
    """Tests for db_supabase.py specific functions."""
    
    @pytest.mark.asyncio
    async def test_find_nearby_drivers(self, mock_supabase_client):
        """Test finding nearby drivers using RPC."""
        from backend.db_supabase import find_nearby_drivers
        
        mock_data = [
            {'id': 'driver_1', 'lat': 52.1, 'lng': -106.6, 'distance_meters': 500},
            {'id': 'driver_2', 'lat': 52.2, 'lng': -106.7, 'distance_meters': 1000}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await find_nearby_drivers(52.1333, -106.6667, 5000)
        
        assert len(result) == 2
        mock_supabase_client.rpc.assert_called_once_with(
            'find_nearby_drivers',
            {'lat': 52.1333, 'lng': -106.6667, 'radius_meters': 5000}
        )
    
    @pytest.mark.asyncio
    async def test_update_driver_location_rpc(self, mock_supabase_client):
        """Test updating driver location via RPC."""
        from backend.db_supabase import update_driver_location
        
        mock_response = MagicMock()
        mock_response.data = None
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        await update_driver_location('driver_123', 52.2, -106.7)
        
        mock_supabase_client.rpc.assert_called_once_with(
            'update_driver_location',
            {'driver_id': 'driver_123', 'lat': 52.2, 'lng': -106.7}
        )
    
    @pytest.mark.asyncio
    async def test_claim_driver_atomic(self, mock_supabase_client):
        """Test atomic driver claiming operation."""
        from backend.db_supabase import claim_driver_atomic
        
        # Mock successful claim
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', 'is_available': False}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await claim_driver_atomic('driver_123')
        assert result is True
    
    @pytest.mark.asyncio
    async def test_get_ride(self, mock_supabase_client):
        """Test getting a ride by ID."""
        from backend.db_supabase import get_ride
        
        mock_data = {'id': 'ride_123', 'status': 'in_progress', 'rider_id': 'user_123'}
        
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await get_ride('ride_123')
        
        assert result is not None
        assert result['id'] == 'ride_123'
    
    @pytest.mark.asyncio
    async def test_get_rides_for_user(self, mock_supabase_client):
        """Test getting rides for a specific user."""
        from backend.db_supabase import get_rides_for_user
        
        mock_data = [
            {'id': 'ride_1', 'status': 'completed'},
            {'id': 'ride_2', 'status': 'pending'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await get_rides_for_user('user_123', limit=10)
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_get_rides_for_driver(self, mock_supabase_client):
        """Test getting rides for a specific driver."""
        from backend.db_supabase import get_rides_for_driver
        
        mock_data = [
            {'id': 'ride_1', 'driver_id': 'driver_123', 'status': 'completed'},
            {'id': 'ride_2', 'driver_id': 'driver_123', 'status': 'in_progress'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_data
        mock_supabase_client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await get_rides_for_driver('driver_123', statuses=['completed', 'in_progress'])
        
        assert len(result) == 2


class TestUtilityFunctions:
    """Tests for database utility functions."""
    
    def test_serialize_for_api(self):
        """Test data serialization for API responses."""
        from backend.db_supabase import _serialize_for_api
        
        # Test with datetime (should convert to string)
        from datetime import datetime
        test_data = {'created_at': datetime(2024, 1, 1, 12, 0, 0)}
        result = _serialize_for_api(test_data)
        assert 'created_at' in result
        
        # Test with regular data
        test_data = {'id': '123', 'name': 'test'}
        result = _serialize_for_api(test_data)
        assert result == test_data
    
    def test_single_row_from_res(self):
        """Test extracting single row from response."""
        from backend.db_supabase import _single_row_from_res
        
        mock_response = MagicMock()
        mock_response.data = [{'id': '123', 'name': 'test'}]
        
        result = _single_row_from_res(mock_response)
        assert result == {'id': '123', 'name': 'test'}
        
        # Test with empty response
        mock_response.data = []
        result = _single_row_from_res(mock_response)
        assert result is None
    
    def test_rows_from_res(self):
        """Test extracting rows from response."""
        from backend.db_supabase import _rows_from_res
        
        mock_response = MagicMock()
        mock_response.data = [
            {'id': '1', 'name': 'test1'},
            {'id': '2', 'name': 'test2'}
        ]
        
        result = _rows_from_res(mock_response)
        assert len(result) == 2
        assert result[0]['id'] == '1'


class TestAsyncHelpers:
    """Tests for async helper functions."""
    
    @pytest.mark.asyncio
    async def test_run_sync(self):
        """Test running sync function in async context."""
        from backend.db_supabase import run_sync
        
        def sync_func():
            return 42
        
        result = await run_sync(sync_func)
        assert result == 42
    
    @pytest.mark.asyncio
    async def test_run_sync_with_exception(self):
        """Test run_sync handles exceptions properly."""
        from backend.db_supabase import run_sync
        
        def sync_func_raises():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError, match="Test error"):
            await run_sync(sync_func_raises)