import unittest
from unittest.mock import MagicMock, patch
import asyncio
import sys
import os

# Add repo root and backend dir to path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir)) # Repo root
sys.path.insert(0, os.path.join(parent_dir, 'backend'))
sys.path.insert(0, parent_dir)

# Mock external dependencies
sys.modules['supabase'] = MagicMock()
sys.modules['supabase.client'] = MagicMock()
sys.modules['postgrest'] = MagicMock()
sys.modules['gotrue'] = MagicMock()

# Reload modules
if 'backend.db' in sys.modules:
    del sys.modules['backend.db']
if 'backend.db_supabase' in sys.modules:
    del sys.modules['backend.db_supabase']

from backend.db import db, MockCursor

class TestMockCursorIteration(unittest.IsolatedAsyncioTestCase):

    async def test_async_for_iteration(self):
        """Test that MockCursor supports async for iteration."""
        with patch('backend.db_supabase.supabase') as mock_supabase:
            # Setup a fluent mock for table().select().execute()
            mock_query = MagicMock()
            mock_supabase.table.return_value = mock_query
            mock_query.select.return_value = mock_query

            # Setup response data (3 items)
            mock_data = [
                {'id': '1', 'status': 'completed'},
                {'id': '2', 'status': 'completed'},
                {'id': '3', 'status': 'completed'}
            ]

            mock_response = MagicMock()
            mock_response.data = mock_data

            # The query chain ends with execute()
            mock_query.execute.return_value = mock_response
            # Also handle limit() call if it happens
            mock_query.limit.return_value = mock_query
            # And order()
            mock_query.order.return_value = mock_query
            # And _apply_filters chain
            mock_query.eq.return_value = mock_query

            cursor = db.rides.find({'status': 'completed'})

            results = []
            async for ride in cursor:
                results.append(ride)

            self.assertEqual(len(results), 3)
            self.assertEqual(results[0]['id'], '1')
            self.assertEqual(results[1]['id'], '2')
            self.assertEqual(results[2]['id'], '3')

if __name__ == '__main__':
    unittest.main()
