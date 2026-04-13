"""
Tests for claim_ride_atomic and the accept_ride endpoint.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestClaimRideAtomic:
    """Tests for db_supabase.claim_ride_atomic."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        """Successful claim (1 row updated) returns True."""
        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_1", "status": "driver_accepted"}]

        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.or_.return_value = mock_table
        mock_table.execute.return_value = mock_response

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("backend.db_supabase.supabase", mock_supabase):
            from backend.db_supabase import claim_ride_atomic

            result = await claim_ride_atomic("ride_1", "driver_1")
            assert result is True

    @pytest.mark.asyncio
    async def test_already_taken_returns_false(self):
        """Race loser (0 rows updated) returns False."""
        mock_response = MagicMock()
        mock_response.data = []

        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.or_.return_value = mock_table
        mock_table.execute.return_value = mock_response

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("backend.db_supabase.supabase", mock_supabase):
            from backend.db_supabase import claim_ride_atomic

            result = await claim_ride_atomic("ride_1", "driver_1")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_supabase_returns_false(self):
        """No supabase client configured returns False."""
        with patch("backend.db_supabase.supabase", None):
            from backend.db_supabase import claim_ride_atomic

            result = await claim_ride_atomic("ride_1", "driver_1")
            assert result is False
