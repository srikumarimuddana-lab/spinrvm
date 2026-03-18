"""
Pytest configuration and fixtures for Spinr backend tests.
This file provides shared fixtures for all test modules.
"""
import os
import sys
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Generator, Any, Dict, Optional
import httpx
from fastapi.testclient import TestClient

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_supabase_client() -> MagicMock:
    """Create a mock Supabase client for testing."""
    mock_client = MagicMock()
    
    # Mock table method with chainable responses
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.gt.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.gte.return_value = mock_table
    mock_table.lte.return_value = mock_table
    mock_table.like.return_value = mock_table
    mock_table.ilike.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.contains.return_value = mock_table
    mock_table.overlap.return_value = mock_table
    mock_table.match.return_value = mock_table
    mock_table.text_search.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.offset.return_value = mock_table
    mock_table.single.return_value = mock_table
    
    # Mock execute to return async response
    async def mock_execute():
        response = MagicMock()
        response.data = []
        response.count = 0
        return response
    
    mock_table.execute = AsyncMock(side_effect=mock_execute)
    mock_client.table.return_value = mock_table
    
    # Mock RPC method
    async def mock_rpc(*args, **kwargs):
        response = MagicMock()
        response.data = None
        return response
    
    mock_client.rpc = AsyncMock(side_effect=mock_rpc)
    
    # Mock auth methods
    mock_client.auth = MagicMock()
    mock_client.auth.sign_in_with_password = AsyncMock(return_value=MagicMock())
    mock_client.auth.sign_up = AsyncMock(return_value=MagicMock())
    mock_client.auth.refresh_session = AsyncMock(return_value=MagicMock())
    mock_client.auth.get_user = AsyncMock(return_value=MagicMock())
    mock_client.auth.admin_get_user = AsyncMock(return_value=MagicMock())
    
    return mock_client


@pytest.fixture
def mock_db_collections() -> Dict[str, MagicMock]:
    """Create mock database collections for testing."""
    collections = {}
    
    for collection_name in [
        'users', 'drivers', 'rides', 'otps', 'otp_records',
        'vehicle_types', 'fare_configs', 'service_areas',
        'settings', 'saved_addresses', 'support_tickets',
        'faqs', 'area_fees', 'surge_pricing', 'notifications',
        'disputes', 'payouts', 'bank_accounts', 'promo_codes'
    ]:
        mock_collection = MagicMock()
        mock_collection.find = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[])
        ))
        mock_collection.find_one = AsyncMock(return_value=None)
        mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id='test_id'))
        mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=[]))
        mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        mock_collection.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_collection.count_documents = AsyncMock(return_value=0)
        collections[collection_name] = mock_collection
    
    return collections


@pytest.fixture
def mock_firebase_admin() -> MagicMock:
    """Mock Firebase Admin SDK."""
    mock_firebase = MagicMock()
    mock_firebase.credentials = MagicMock()
    mock_firebase.cert = MagicMock()
    mock_firebase.initialize_app = MagicMock()
    
    mock_auth = MagicMock()
    mock_auth.create_user = AsyncMock(return_value=MagicMock(uid='test_uid'))
    mock_auth.get_user = AsyncMock(return_value=MagicMock(uid='test_uid', phone_number='+1234567890'))
    mock_auth.update_user = AsyncMock(return_value=MagicMock(uid='test_uid'))
    mock_auth.delete_user = AsyncMock(return_value=MagicMock())
    mock_auth.get_user_by_phone_number = AsyncMock(return_value=MagicMock(uid='test_uid'))
    mock_auth.set_custom_user_claims = AsyncMock(return_value=None)
    
    mock_firebase.auth = mock_auth
    return mock_firebase


@pytest.fixture
def mock_sms_service() -> MagicMock:
    """Mock SMS service for testing."""
    mock_service = MagicMock()
    mock_service.send = AsyncMock(return_value=True)
    mock_service.send_otp = AsyncMock(return_value=True)
    return mock_service


@pytest.fixture
def sample_user_data() -> Dict[str, Any]:
    """Sample user data for testing."""
    return {
        'id': 'user_123',
        'phone': '+1234567890',
        'email': 'test@example.com',
        'first_name': 'Test',
        'last_name': 'User',
        'created_at': '2024-01-01T00:00:00Z',
        'is_admin': False
    }


@pytest.fixture
def sample_driver_data() -> Dict[str, Any]:
    """Sample driver data for testing."""
    return {
        'id': 'driver_123',
        'user_id': 'user_123',
        'phone': '+1234567890',
        'first_name': 'Test',
        'last_name': 'Driver',
        'is_available': True,
        'is_online': False,
        'lat': 52.1333,
        'lng': -106.6667,
        'vehicle_type': 'sedan',
        'license_plate': 'ABC123',
        'rating': 4.8,
        'total_rides': 100,
        'created_at': '2024-01-01T00:00:00Z'
    }


@pytest.fixture
def sample_ride_data() -> Dict[str, Any]:
    """Sample ride data for testing."""
    return {
        'id': 'ride_123',
        'rider_id': 'user_123',
        'driver_id': 'driver_123',
        'pickup_lat': 52.1333,
        'pickup_lng': -106.6667,
        'dropoff_lat': 52.1500,
        'dropoff_lng': -106.6500,
        'pickup_address': '123 Test St',
        'dropoff_address': '456 Main Ave',
        'status': 'requested',
        'fare_amount': 15.50,
        'distance_km': 2.5,
        'duration_minutes': 10,
        'vehicle_type': 'sedan',
        'created_at': '2024-01-01T00:00:00Z'
    }


@pytest.fixture
def sample_otp_data() -> Dict[str, Any]:
    """Sample OTP data for testing."""
    return {
        'id': 'otp_123',
        'phone': '+1234567890',
        'code': '123456',
        'verified': False,
        'expires_at': '2024-01-01T00:10:00Z',
        'created_at': '2024-01-01T00:00:00Z'
    }


@pytest.fixture
def mock_jwt_token() -> str:
    """Return a mock JWT token for testing."""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyIsInBob25lIjoiKzEyMzQ1Njc4OTAiLCJleHAiOjk5OTk5OTk5OTl9.mock_signature"


@pytest.fixture
def auth_headers(mock_jwt_token: str) -> Dict[str, str]:
    """Return authorization headers with mock JWT token."""
    return {
        'Authorization': f'Bearer {mock_jwt_token}',
        'Content-Type': 'application/json'
    }


@pytest.fixture
def mock_rate_limiter() -> MagicMock:
    """Mock rate limiter for testing."""
    mock_limiter = MagicMock()
    mock_limiter._rate_limit_exceeded_handler = MagicMock()
    return mock_limiter


@pytest.fixture(autouse=True)
def patch_external_dependencies(
    mock_supabase_client: MagicMock,
    mock_firebase_admin: MagicMock,
    mock_sms_service: MagicMock
) -> None:
    """Automatically patch external dependencies for all tests."""
    patches = [
        patch('backend.db_supabase.supabase', mock_supabase_client),
        patch('backend.core.security.firebase', mock_firebase_admin),
        patch('backend.sms_service.send_sms', mock_sms_service.send),
        patch('backend.sms_service.send_otp_sms', mock_sms_service.send_otp),
    ]
    
    for p in patches:
        p.start()
    
    yield
    
    for p in patches:
        p.stop()


@pytest.fixture
def test_client() -> TestClient:
    """Create a test client for the FastAPI app."""
    from backend.server import app
    
    with TestClient(app) as client:
        yield client


@pytest.fixture
def async_http_client() -> httpx.AsyncClient:
    """Create an async HTTP client for testing."""
    transport = httpx.AsyncHTTPTransport(app=MagicMock())
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield client
    client.aclose()