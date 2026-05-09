"""
Tests for claim_ride_atomic and the accept_ride endpoint.
"""

import sys
from unittest.mock import MagicMock

import pytest


class TestClaimRideAtomic:
    """Tests for db_supabase.claim_ride_atomic."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self, mock_supabase_client):
        """Successful claim (1 row updated) returns True."""
        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_1", "status": "driver_accepted"}]

        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.or_.return_value = mock_table
        mock_table.execute.return_value = mock_response

        mock_supabase_client.table.return_value = mock_table

        from backend.db_supabase import claim_ride_atomic

        result = await claim_ride_atomic("ride_1", "driver_1")
        assert result is True

    @pytest.mark.asyncio
    async def test_already_taken_returns_false(self, mock_supabase_client):
        """Race loser (0 rows updated) returns False."""
        mock_response = MagicMock()
        mock_response.data = []

        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.or_.return_value = mock_table
        mock_table.execute.return_value = mock_response

        mock_supabase_client.table.return_value = mock_table

        from backend.db_supabase import claim_ride_atomic

        result = await claim_ride_atomic("ride_1", "driver_1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_supabase_returns_false(self):
        """No supabase client configured returns False."""
        from backend.db_supabase import claim_ride_atomic

        _mod = sys.modules[claim_ride_atomic.__module__]
        original = _mod.supabase
        _mod.supabase = None
        try:
            result = await claim_ride_atomic("ride_1", "driver_1")
            assert result is False
        finally:
            _mod.supabase = original
