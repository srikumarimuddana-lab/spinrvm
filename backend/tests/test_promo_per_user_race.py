"""P1: per-user promo redemption cap must be enforced atomically.

The rule-3 count_documents() check is a TOCTOU race — N concurrent redemptions
all read user_uses=0 and pass. _record_promo_application now calls the atomic
claim_promo_user_slot RPC (migration 257) as the authoritative gate.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
class TestRecordPromoApplicationAtomicGate:
    async def test_cap_reached_raises_400_and_skips_insert(self):
        from backend.routes import promotions as promo

        insert = AsyncMock()
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[{"max_uses_per_user": 1, "max_uses": 0}]),
            ),
            patch("backend.routes.promotions.db_supabase.claim_promo_user_slot", AsyncMock(return_value=False)),
            patch("backend.routes.promotions.db_supabase.insert_one", insert),
            pytest.raises(Exception) as exc,
        ):
            await promo._record_promo_application("p1", "SAVE5", "u1", Decimal("5.00"))

        assert getattr(exc.value, "status_code", None) == 400
        insert.assert_not_awaited()  # never wrote a promo_applications row

    async def test_successful_claim_inserts_and_increments(self):
        from backend.routes import promotions as promo

        insert = AsyncMock()
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[{"max_uses_per_user": 1, "max_uses": 100}]),
            ),
            patch("backend.routes.promotions.db_supabase.claim_promo_user_slot", AsyncMock(return_value=True)) as claim,
            patch("backend.routes.promotions.db_supabase.insert_one", insert),
            patch("backend.routes.promotions.increment_promo_uses", AsyncMock(return_value=True)),
        ):
            app_id = await promo._record_promo_application("p1", "SAVE5", "u1", Decimal("5.00"))

        claim.assert_awaited_once_with("p1", "u1", 1)
        insert.assert_awaited_once()
        assert app_id

    async def test_global_cap_exhausted_releases_user_slot(self):
        """If the global-uses increment fails after we claimed the user's slot,
        the slot must be released so the user isn't charged a phantom redemption."""
        from backend.routes import promotions as promo

        release = AsyncMock()
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[{"max_uses_per_user": 2, "max_uses": 50}]),
            ),
            patch("backend.routes.promotions.db_supabase.claim_promo_user_slot", AsyncMock(return_value=True)),
            patch("backend.routes.promotions.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.promotions.increment_promo_uses", AsyncMock(return_value=False)),
            patch("backend.routes.promotions.db_supabase.release_promo_user_slot", release),
            pytest.raises(Exception) as exc,
        ):
            await promo._record_promo_application("p1", "SAVE5", "u1", Decimal("5.00"))

        assert getattr(exc.value, "status_code", None) == 409
        release.assert_awaited_once_with("p1", "u1")

    async def test_multi_use_promo_passes_max_per_user_through(self):
        from backend.routes import promotions as promo

        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[{"max_uses_per_user": 3, "max_uses": 0}]),
            ),
            patch("backend.routes.promotions.db_supabase.claim_promo_user_slot", AsyncMock(return_value=True)) as claim,
            patch("backend.routes.promotions.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.promotions.increment_promo_uses", AsyncMock(return_value=True)),
        ):
            await promo._record_promo_application("p1", "SAVE5", "u1", Decimal("5.00"))

        claim.assert_awaited_once_with("p1", "u1", 3)
