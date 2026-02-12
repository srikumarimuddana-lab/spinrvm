import unittest
from unittest.mock import MagicMock, patch
import asyncio
import sys
import os

# Mock external dependencies before importing backend modules
sys.modules['supabase'] = MagicMock()
sys.modules['supabase.client'] = MagicMock()
sys.modules['postgrest'] = MagicMock()
sys.modules['gotrue'] = MagicMock()

# Add repo root to path to allow importing backend
sys.path.append(os.getcwd())

# Force reload of modules to ensure mocks apply if already imported
if 'backend.db' in sys.modules:
    del sys.modules['backend.db']
if 'backend.db_supabase' in sys.modules:
    del sys.modules['backend.db_supabase']

from backend.db import db
import backend.db_supabase as db_supabase

class TestDBWrapper(unittest.IsolatedAsyncioTestCase):

    async def test_find_one_user(self):
        with patch('backend.db_supabase.supabase') as mock_supabase:
            # Setup a fluent mock for table().select().eq().execute()
            mock_query = MagicMock()
            mock_supabase.table.return_value = mock_query
            mock_query.select.return_value = mock_query
            mock_query.eq.return_value = mock_query

            # Setup response data
            # execute() returns an object with .data
            mock_response = MagicMock()
            mock_response.data = [{'id': 'u1', 'name': 'Test User'}]
            mock_query.execute.return_value = mock_response

            # Test finding user by ID
            user = await db.users.find_one({'id': 'u1'})

            self.assertIsNotNone(user)
            self.assertEqual(user['id'], 'u1')
            self.assertEqual(user['name'], 'Test User')

            # Verify call structure
            mock_supabase.table.assert_called_with('users')
            mock_query.select.assert_called_with('*')
            mock_query.eq.assert_called_with('id', 'u1')

    async def test_find_nearby_drivers(self):
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{'id': 'd1', 'lat': 52.1, 'lng': -106.6}]

            # rpc().execute()
            mock_supabase.rpc.return_value.execute.return_value = mock_response

            drivers = await db_supabase.find_nearby_drivers(52.1, -106.6, 5000)

            self.assertEqual(len(drivers), 1)
            self.assertEqual(drivers[0]['id'], 'd1')

            mock_supabase.rpc.assert_called_with('find_nearby_drivers', {
                'lat': 52.1,
                'lng': -106.6,
                'radius_meters': 5000
            })

    async def test_update_driver_location(self):
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = None
            mock_supabase.rpc.return_value.execute.return_value = mock_response

            # Test update via db.drivers.update_one
            await db.drivers.update_one(
                {'id': 'd1'},
                {'$set': {'lat': 52.2, 'lng': -106.7}}
            )

            mock_supabase.rpc.assert_called_with('update_driver_location', {
                'driver_id': 'd1',
                'lat': 52.2,
                'lng': -106.7
            })

    async def test_claim_driver_atomic(self):
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_query = MagicMock()
            mock_supabase.table.return_value = mock_query
            mock_query.update.return_value = mock_query
            mock_query.eq.return_value = mock_query # chaining .eq().eq()

            mock_response = MagicMock()
            mock_response.data = [{'id': 'd1', 'is_available': False}]
            mock_query.execute.return_value = mock_response

            # Test logic
            res = await db.drivers.update_one(
                {'id': 'd1', 'is_available': True},
                {'$set': {'is_available': False}}
            )

            self.assertEqual(res.modified_count, 1)

            mock_supabase.table.assert_called_with('drivers')
            mock_query.update.assert_called_with({'is_available': False})

if __name__ == '__main__':
    unittest.main()
