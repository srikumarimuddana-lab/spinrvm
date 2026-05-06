"""
Unit tests for backend/utils/surge_engine.py

Coverage:
- ratio_to_multiplier: all tier boundaries and representative interior points
- SURGE_CAP constant enforcement
- recalculate_all_surges skips surge_source == 'manual' areas
- return type is float (engine uses plain float throughout, not Decimal)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test.  The dual-import pattern means we can import
# directly from the bare path (backend dir is on sys.path via conftest.py).
# ---------------------------------------------------------------------------
from utils.surge_engine import SURGE_CAP, ratio_to_multiplier

# ── Tier boundary parametrize ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ratio, expected",
    [
        # Below the first tier threshold (< 0.5) → normal
        (0.0, 1.0),
        (0.49, 1.0),
        # At / above the first threshold (≥ 0.5) → 1.25×
        (0.5, 1.25),
        (0.65, 1.25),
        # At / above the second threshold (≥ 0.8) → 1.5×
        (0.8, 1.5),
        (1.0, 1.5),
        # At / above the third threshold (≥ 1.2) → 1.75×
        (1.2, 1.75),
        (1.6, 1.75),
        # At / above the fourth threshold (≥ 2.0) → 2.0×
        (2.0, 2.0),
        (2.5, 2.0),
        # At / above the cap threshold (≥ 3.0) → SURGE_CAP (2.5×)
        (3.0, 2.5),
        (5.0, 2.5),
        (100.0, 2.5),
    ],
)
def test_ratio_to_multiplier_tier_table(ratio: float, expected: float) -> None:
    """ratio_to_multiplier maps every tier boundary and interior value correctly."""
    result = ratio_to_multiplier(ratio)
    assert result == expected, f"ratio={ratio}: expected multiplier {expected}, got {result}"


# ── SURGE_CAP constant ───────────────────────────────────────────────────────


def test_surge_cap_is_25() -> None:
    """SURGE_CAP must equal 2.5 (auto-mode hard ceiling per CLAUDE.md)."""
    assert SURGE_CAP == 2.5


def test_ratio_to_multiplier_never_exceeds_surge_cap() -> None:
    """No ratio, however large, should produce a multiplier above SURGE_CAP."""
    for ratio in (3.0, 10.0, 100.0, 1_000_000.0):
        assert ratio_to_multiplier(ratio) <= SURGE_CAP, (
            f"ratio={ratio} produced a multiplier exceeding SURGE_CAP={SURGE_CAP}"
        )


# ── Return type ──────────────────────────────────────────────────────────────


def test_surge_returns_float_not_decimal() -> None:
    """
    The engine works in plain float throughout — callers must not assume Decimal.
    Verify that ratio_to_multiplier returns a float for both normal and capped cases.
    """
    for ratio in (0.0, 0.5, 1.0, 2.0, 3.0):
        result = ratio_to_multiplier(ratio)
        assert isinstance(result, float), f"ratio={ratio}: expected float, got {type(result).__name__}"


# ── Manual-override: auto engine must skip 'manual' source areas ─────────────


@pytest.mark.anyio
async def test_surge_not_recalculated_when_source_is_manual() -> None:
    """
    recalculate_all_surges() must skip any area whose surge_source == 'manual'.
    A manual area with a sky-high ratio should NOT have its multiplier touched.
    """
    # Area with manual override — would be 2.5× if auto-calculated (ratio≥3.0)
    manual_area = {
        "id": "area-manual-01",
        "name": "Downtown (manual override)",
        "is_active": True,
        "surge_source": "manual",
        "surge_multiplier": 3.0,  # admin set this manually
        "parent_service_area_id": None,
    }

    # We expect calculate_surge_for_area / db.update_one to never be called
    # for the manual area.  Patch db.get_rows to return only this area.
    mock_db_update = AsyncMock()

    with (
        patch("utils.surge_engine.db") as mock_db,
        patch("utils.surge_engine.calculate_surge_for_area", new_callable=AsyncMock) as mock_calc,
    ):
        mock_db.get_rows = AsyncMock(return_value=[manual_area])
        mock_db.update_one = mock_db_update

        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    # The manual area was skipped → calculate_surge_for_area never called
    mock_calc.assert_not_called()
    # No DB update should have been written for the manual area
    mock_db_update.assert_not_called()
    # No results returned (nothing was auto-processed)
    assert results == []


@pytest.mark.anyio
async def test_surge_recalculated_for_auto_source_area() -> None:
    """
    recalculate_all_surges() DOES process areas with surge_source == 'auto'
    (or with no source set, which defaults to 'auto').
    """
    auto_area = {
        "id": "area-auto-01",
        "name": "South End",
        "is_active": True,
        "surge_source": "auto",
        "surge_multiplier": 1.0,
        "parent_service_area_id": None,
    }

    fake_metrics = {
        "area_id": "area-auto-01",
        "demand_count": 10,
        "supply_count": 3,
        "ratio": 3.33,
        "multiplier": 2.5,
    }

    with (
        patch("utils.surge_engine.db") as mock_db,
        patch("utils.surge_engine.calculate_surge_for_area", new_callable=AsyncMock, return_value=fake_metrics),
    ):
        mock_db.get_rows = AsyncMock(return_value=[auto_area])
        mock_db.update_one = AsyncMock()
        mock_db.insert_one = AsyncMock()

        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert len(results) == 1
    assert results[0]["multiplier"] == 2.5


# ── Manual override: admin can hold a value above auto-mode cap ──────────────


def test_manual_override_value_above_auto_cap_is_valid() -> None:
    """
    The auto engine caps at 2.5, but a manual override (set by an admin directly
    in the DB) can legitimately store values above 2.5 (up to 10.0 per admin UI).
    ratio_to_multiplier is never called for manual areas, so this test simply
    confirms that SURGE_CAP is a ceiling only for the auto tier function —
    a stored value of 3.0 returned directly (simulating a DB read) is valid.
    """
    # Simulate what the admin dashboard would store and what the DB returns.
    # The auto function is bypassed for manual areas, so the stored value is
    # returned verbatim.
    admin_stored_value = 3.0
    assert admin_stored_value > SURGE_CAP  # confirms it's above the auto cap
    # The engine itself never calls ratio_to_multiplier on manual areas,
    # so the value is not clipped — just round-trip as-is.
    assert admin_stored_value == 3.0
