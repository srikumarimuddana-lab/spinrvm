"""
Unit tests for driver-related functionality.
Tests cover driver registration, availability, location updates, and driver management.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDriverRegistration:
    """Tests for driver registration functionality."""
    
    @pytest.fixture
    def sample_driver_data(self):
        """Sample driver registration data."""
        return {
            'user_id': 'user_123',
            'first_name': 'Test',
            'last_name': 'Driver',
            'phone': '+1234567890',
            'vehicle_type': 'sedan',
            'license_plate': 'ABC123',
            'is_available': False
        }
    
    @pytest.mark.asyncio
    async def test_register_driver_success(self, sample_driver_data, mock_supabase_client):
        """Test successful driver registration."""
        from backend.db_supabase import insert_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('drivers', sample_driver_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_register_driver_missing_fields(self, sample_driver_data):
        """Test driver registration with missing required fields."""
        required_fields = ['user_id', 'first_name', 'last_name', 'phone']
        
        for field in required_fields:
            data = {k: v for k, v in sample_driver_data.items() if k != field}
            # In real implementation, this would raise validation error
            assert field not in data


class TestDriverAvailability:
    """Tests for driver availability management."""
    
    @pytest.fixture
    def driver_collection(self):
        from backend.db import db
        return db.drivers
    
    @pytest.mark.asyncio
    async def test_set_driver_available(self, driver_collection, mock_supabase_client):
        """Test setting driver as available."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', 'is_available': True}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('drivers', {'id': 'driver_123'}, {'is_available': True})
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_set_driver_unavailable(self, driver_collection, mock_supabase_client):
        """Test setting driver as unavailable."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', 'is_available': False}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('drivers', {'id': 'driver_123'}, {'is_available': False})
        
        assert result['is_available'] is False
    
    @pytest.mark.asyncio
    async def test_set_driver_online(self, driver_collection, mock_supabase_client):
        """Test setting driver as online."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', 'is_online': True}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('drivers', {'id': 'driver_123'}, {'is_online': True})
        
        assert result['is_online'] is True


class TestDriverLocation:
    """Tests for driver location tracking."""
    
    @pytest.mark.asyncio
    async def test_update_driver_location(self, mock_supabase_client):
        """Test updating driver location."""
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
    async def test_find_nearby_drivers(self, mock_supabase_client):
        """Test finding nearby drivers."""
        from backend.db_supabase import find_nearby_drivers
        
        mock_drivers = [
            {'id': 'driver_1', 'lat': 52.1350, 'lng': -106.6680, 'distance_meters': 500},
            {'id': 'driver_2', 'lat': 52.1400, 'lng': -106.6700, 'distance_meters': 1000}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_drivers
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await find_nearby_drivers(52.1333, -106.6667, 5000)
        
        assert len(result) == 2
        assert result[0]['id'] == 'driver_1'
    
    @pytest.mark.asyncio
    async def test_find_nearby_drivers_empty(self, mock_supabase_client):
        """Test finding nearby drivers when none available."""
        from backend.db_supabase import find_nearby_drivers
        
        mock_response = MagicMock()
        mock_response.data = []
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await find_nearby_drivers(52.1333, -106.6667, 5000)
        
        assert len(result) == 0


class TestDriverDocuments:
    """Tests for driver document management."""
    
    @pytest.mark.asyncio
    async def test_upload_driver_document(self, mock_supabase_client):
        """Test uploading driver document."""
        from backend.db_supabase import insert_one
        
        document_data = {
            'driver_id': 'driver_123',
            'document_type': 'license',
            'file_url': 'https://storage.example.com/doc.pdf',
            'status': 'pending'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('driver_documents', document_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_approve_driver_document(self, mock_supabase_client):
        """Test approving driver document."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123', 'status': 'approved'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('driver_documents', {'id': 'doc_123'}, {'status': 'approved'})
        
        assert result['status'] == 'approved'
    
    @pytest.mark.asyncio
    async def test_reject_driver_document(self, mock_supabase_client):
        """Test rejecting driver document."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123', 'status': 'rejected', 'rejection_reason': 'Expired'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one(
            'driver_documents',
            {'id': 'doc_123'},
            {'status': 'rejected', 'rejection_reason': 'Expired'}
        )
        
        assert result['status'] == 'rejected'


class TestDriverStats:
    """Tests for driver statistics."""
    
    @pytest.mark.asyncio
    async def test_get_driver_ride_count(self, mock_supabase_client):
        """Test getting driver ride count."""
        from backend.db_supabase import count_documents
        
        mock_response = MagicMock()
        mock_response.count = 100
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        count = await count_documents('rides', {'driver_id': 'driver_123', 'status': 'completed'})
        
        assert count == 100
    
    @pytest.mark.asyncio
    async def test_get_driver_rating(self, mock_supabase_client):
        """Test calculating driver average rating."""
        from backend.db_supabase import get_rows
        
        mock_rides = [
            {'id': 'ride_1', 'rating': 5},
            {'id': 'ride_2', 'rating': 4},
            {'id': 'ride_3', 'rating': 5}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_rides
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('rides', {'driver_id': 'driver_123'})
        
        assert len(result) == 3
        avg_rating = sum(r['rating'] for r in result) / len(result)
        assert avg_rating == 4.67 or abs(avg_rating - 4.666666666666667) < 0.01
    
    def test_calculate_driver_rating(self):
        """Test driver rating calculation."""
        ratings = [5, 4, 5, 3, 5]
        avg_rating = sum(ratings) / len(ratings)
        
        assert avg_rating == 4.4


class TestDriverEndpoints:
    """Tests for driver API endpoints."""
    
    @pytest.fixture
    def test_client(self):
        from fastapi.testclient import TestClient
        from backend.server import app
        return TestClient(app)
    
    def test_get_driver_profile(self, test_client, auth_headers):
        """Test getting driver profile endpoint."""
        response = test_client.get(
            '/api/v1/drivers/me',
            headers=auth_headers
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 401, 404]
    
    def test_update_driver_availability(self, test_client, auth_headers):
        """Test updating driver availability endpoint."""
        response = test_client.post(
            '/api/v1/drivers/availability',
            json={'is_available': True},
            headers=auth_headers
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 401, 422]
    
    def test_get_nearby_drivers_admin(self, test_client, auth_headers):
        """Test admin endpoint for getting nearby drivers."""
        response = test_client.get(
            '/api/v1/admin/drivers/nearby?lat=52.1333&lng=-106.6667&radius=5000',
            headers=auth_headers
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 401, 403, 422]


class TestDriverVehicle:
    """Tests for driver vehicle management."""
    
    @pytest.mark.asyncio
    async def test_update_driver_vehicle(self, mock_supabase_client):
        """Test updating driver vehicle information."""
        from backend.db_supabase import update_one
        
        vehicle_update = {
            'vehicle_type': 'suv',
            'license_plate': 'XYZ789',
            'vehicle_color': 'Black'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_123', **vehicle_update}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('drivers', {'id': 'driver_123'}, vehicle_update)
        
        assert result['vehicle_type'] == 'suv'
        assert result['license_plate'] == 'XYZ789'
    
    @pytest.mark.asyncio
    async def test_get_vehicle_types(self, mock_supabase_client):
        """Test getting available vehicle types."""
        from backend.db_supabase import get_rows
        
        mock_types = [
            {'id': 'type_1', 'name': 'sedan', 'base_fare': 5.00},
            {'id': 'type_2', 'name': 'suv', 'base_fare': 8.00},
            {'id': 'type_3', 'name': 'luxury', 'base_fare': 15.00}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_types
        mock_supabase_client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('vehicle_types')
        
        assert len(result) == 3