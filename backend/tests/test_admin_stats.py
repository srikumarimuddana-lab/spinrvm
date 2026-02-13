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
sys.modules['slowapi'] = MagicMock()
sys.modules['slowapi.util'] = MagicMock()
sys.modules['slowapi.errors'] = MagicMock()
sys.modules['firebase_admin'] = MagicMock()
sys.modules['firebase_admin.credentials'] = MagicMock()
sys.modules['firebase_admin.auth'] = MagicMock()
sys.modules['fastapi'] = MagicMock()
sys.modules['fastapi.security'] = MagicMock()
sys.modules['fastapi.staticfiles'] = MagicMock()
sys.modules['fastapi.responses'] = MagicMock()

# Configure FastAPI/APIRouter decorators to return the original function
def route_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator

mock_fastapi_app = MagicMock()
mock_fastapi_app.get.side_effect = route_decorator
mock_fastapi_app.post.side_effect = route_decorator
mock_fastapi_app.put.side_effect = route_decorator
mock_fastapi_app.delete.side_effect = route_decorator
mock_fastapi_app.websocket.side_effect = route_decorator
sys.modules['fastapi'].FastAPI.return_value = mock_fastapi_app

mock_router = MagicMock()
mock_router.get.side_effect = route_decorator
mock_router.post.side_effect = route_decorator
mock_router.put.side_effect = route_decorator
mock_router.delete.side_effect = route_decorator
sys.modules['fastapi'].APIRouter.return_value = mock_router

sys.modules['starlette'] = MagicMock()
sys.modules['starlette.middleware'] = MagicMock()
sys.modules['starlette.middleware.cors'] = MagicMock()
sys.modules['starlette.requests'] = MagicMock()
sys.modules['uvicorn'] = MagicMock()
# sys.modules['pydantic'] = MagicMock()
sys.modules['jwt'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

# Reload modules
if 'backend.server' in sys.modules:
    del sys.modules['backend.server']
if 'backend.db' in sys.modules:
    del sys.modules['backend.db']

# We need to mock db in server explicitly because it imports it
# And we need to ensure admin_get_stats uses our mocked DB
with patch.dict(sys.modules, {'backend.db': MagicMock()}):
    import backend.server
    # But server.py imports db from backend.db, so we need to patch that attribute
    # Actually, let's just import server and patch the db object on it
    from backend.server import admin_get_stats, db as server_db

# We will patch server_db in the test method

class TestAdminStats(unittest.IsolatedAsyncioTestCase):

    async def test_admin_get_stats_calculation(self):
        """Test admin_get_stats correctly iterates over lists from db."""

        # Use the imported server_db which is the one used in admin_get_stats
        db = server_db

        # Mock db.rides.count_documents
        # Note: server_db.rides is a Mock/MagicMock
        # We need to ensure await works on count_documents since it is awaited in server.py

        async def mock_count_docs(*args, **kwargs):
            # We can use side_effect on the mock call itself if we knew order, but since we are mocking the method...
            # The logic in server.py calls count_documents multiple times.
            # 1. {} (total rides)
            # 2. {'status': 'completed'}
            # 3. {'status': 'cancelled'}
            # 4. {'status': ...} (active)
            # 5. drivers {}
            # 6. drivers {'is_online': True}
            # 7. users {}
            return 0 # Default, we will override with side_effect below if possible, or just specific mocks

        # Better approach: Create a side_effect function that checks arguments
        async def side_effect_count(query):
            if query == {}:
                # Could be rides, drivers, or users. Need to check which collection called it.
                # But here we mock db.rides.count_documents separately
                pass
            return 0

        # Since db.rides is a separate object from db.drivers, we can mock them individually

        # Mock awaitable count_documents
        f1, f2, f3, f4 = asyncio.Future(), asyncio.Future(), asyncio.Future(), asyncio.Future()
        f1.set_result(100)
        f2.set_result(50)
        f3.set_result(10)
        f4.set_result(40)

        db.rides.count_documents = MagicMock(side_effect=[f1, f2, f3, f4]) # total, completed, cancelled, active

        d1, d2 = asyncio.Future(), asyncio.Future()
        d1.set_result(20)
        d2.set_result(15)

        db.drivers.count_documents = MagicMock(side_effect=[d1, d2]) # total, online

        db.users.count_documents = MagicMock(return_value=asyncio.Future())
        db.users.count_documents.return_value.set_result(200)

        # Mock db.rides.find().to_list()
        # We need to mock the cursor object returned by find()
        mock_cursor_completed = MagicMock()
        mock_cursor_cancelled = MagicMock()

        # Mock completed rides data
        mock_completed_rides = [
            {'driver_earnings': 10, 'admin_earnings': 2, 'tip_amount': 1},
            {'driver_earnings': 20, 'admin_earnings': 4, 'tip_amount': 2}
        ]
        # Mock cancelled rides data
        mock_cancelled_rides = [
             {'cancellation_fee_admin': 1, 'cancellation_fee_driver': 5}
        ]

        # Configure to_list return values
        # They need to be awaitable
        f_completed = asyncio.Future()
        f_completed.set_result(mock_completed_rides)
        mock_cursor_completed.to_list.return_value = f_completed

        f_cancelled = asyncio.Future()
        f_cancelled.set_result(mock_cancelled_rides)
        mock_cursor_cancelled.to_list.return_value = f_cancelled

        # db.rides.find() needs to return different cursors based on arguments
        def mock_find(query):
            if query.get('status') == 'completed':
                return mock_cursor_completed
            if query.get('status') == 'cancelled':
                return mock_cursor_cancelled
            return MagicMock() # fallback

        db.rides.find = MagicMock(side_effect=mock_find)

        # Execute
        stats = await admin_get_stats()

        # Assertions
        # Total Driver Earnings: (10+1) + (20+2) + 5 = 11 + 22 + 5 = 38
        # Total Admin Earnings: 2 + 4 + 1 = 7
        # Total Tips: 1 + 2 = 3

        self.assertEqual(stats['total_driver_earnings'], 38)
        self.assertEqual(stats['total_admin_earnings'], 7)
        self.assertEqual(stats['total_tips'], 3)

        # Verify to_list was called
        mock_cursor_completed.to_list.assert_called_with(limit=10000)
        mock_cursor_cancelled.to_list.assert_called_with(limit=10000)

if __name__ == '__main__':
    unittest.main()
