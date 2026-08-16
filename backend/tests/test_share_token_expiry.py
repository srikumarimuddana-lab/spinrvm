"""Share-link expiry (analysis finding F3).

Two writers existed for `rides.shared_trip_token`:

  * ``POST /rides/{id}/share`` stamped ``shared_trip_token_created_at``
  * ``GET  /rides/{id}/share`` did NOT

and ``track_shared_ride`` only enforces the 24h expiry when that timestamp is
present. The rider app's own "Share my trip" button and the driver Safety
overlay both call the **GET**, so the primary user-facing path minted links
that never expired -- indefinitely exposing pickup/dropoff addresses, live
driver coordinates, plate and photo to anyone the link reached.

These pin the fix without revoking links a rider may be actively sharing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

RIDE_ID = "ride_share_expiry_001"
RIDER_ID = "rider_share_expiry"
TOKEN = "tok_abc123"


def _ride(**over):
    base = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,
        "status": "in_progress",
        "pickup_address": "100 Main St",
        "dropoff_address": "200 Broad Ave",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
class TestGetShareLinkStampsTimestamp:
    async def _get_link(self, ride):
        from backend.routes import rides as rides_mod

        updates = []

        async def _update_ride(rid, patch_):
            updates.append((rid, patch_))

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(side_effect=_update_ride)),
        ):
            result = await rides_mod.get_share_trip_link(ride_id=RIDE_ID, current_user={"id": RIDER_ID})
        return result, updates

    async def test_new_token_is_stamped_with_created_at(self):
        result, updates = await self._get_link(_ride(shared_trip_token=None))

        assert result["success"] is True
        assert len(updates) == 1
        _rid, patch_ = updates[0]
        assert patch_["shared_trip_token"]
        assert patch_["shared_trip_token_created_at"], "a token minted without a timestamp can never expire"

    async def test_legacy_token_without_timestamp_is_backfilled_on_read(self):
        result, updates = await self._get_link(_ride(shared_trip_token=TOKEN, shared_trip_token_created_at=None))

        assert result["share_token"] == TOKEN, "must reuse, not rotate, the existing token"
        assert len(updates) == 1
        assert "shared_trip_token_created_at" in updates[0][1]
        assert "shared_trip_token" not in updates[0][1], "the token itself must not change"

    async def test_already_stamped_token_is_not_rewritten(self):
        stamped = datetime.now(timezone.utc).isoformat()
        _result, updates = await self._get_link(_ride(shared_trip_token=TOKEN, shared_trip_token_created_at=stamped))

        assert updates == [], "no write when nothing needs changing"


@pytest.mark.asyncio
class TestTrackRespectsExpiry:
    async def _track(self, ride):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])):
            try:
                return await rides_mod.track_shared_ride(share_token=TOKEN), None
            except HTTPException as exc:
                return None, exc

    async def test_token_older_than_24h_is_rejected(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        result, exc = await self._track(_ride(shared_trip_token_created_at=old))

        assert result is None
        assert exc.status_code == 404

    async def test_fresh_token_still_tracks(self):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result, exc = await self._track(_ride(shared_trip_token_created_at=fresh))

        assert exc is None
        assert result["status"] == "in_progress"

    async def test_legacy_null_timestamp_on_LIVE_ride_still_works(self):
        """The conservative half: we must not revoke a link a rider is
        actively sharing mid-trip just because it predates the fix."""
        result, exc = await self._track(_ride(shared_trip_token_created_at=None, status="in_progress"))

        assert exc is None
        assert result["status"] == "in_progress"

    async def test_legacy_null_timestamp_on_ENDED_ride_is_rejected(self):
        """...but once the ride is over there is no safety purpose left, and
        an immortal link would keep exposing pickup/dropoff forever."""
        result, exc = await self._track(_ride(shared_trip_token_created_at=None, status="completed"))

        assert result is None
        assert exc.status_code == 404
