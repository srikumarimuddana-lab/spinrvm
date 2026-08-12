"""Unit tests for ``features.calculate_all_fees``'s GST/PST/HST branch.

No prior test exercised this branch directly (existing tests only patch
``calculate_all_fees`` as a whole). Added while enabling PST for the real
Saskatchewan service areas (Saskatoon, Saskatoon Airport, Regina, Regina
Airport) — see docs/change-log/2026-08-11-sk-pst-enable.md. The code
previously carried a comment claiming "PST does NOT apply to rideshare"
which was factually wrong and left those areas GST-only in production
until this fix; pinning both the GST-only and GST+PST cases here so this
class of drift can't recur silently.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


async def _run(matched_area: dict) -> dict:
    from backend import features

    with patch("backend.features.db_supabase.get_rows", AsyncMock(return_value=[])):
        return await features.calculate_all_fees(
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_lat=52.14,
            dropoff_lng=-106.68,
            distance_km=5.0,
            subtotal=100.0,
            ride_time_hour=12,
            _all_areas=[matched_area],
            _matched_area=matched_area,
        )


async def test_gst_only_when_pst_disabled():
    """The pre-fix Saskatoon/Regina config (and the correct config for any
    area that genuinely has no PST): GST only, no PST line at all."""
    area = {"id": "area-1", "name": "Test Area", "gst_enabled": True, "gst_rate": 5.0, "pst_enabled": False}
    result = await _run(area)
    assert result["tax_breakdown"] == {"GST": {"rate": 5.0, "amount": 5.0}}
    assert result["tax_amount"] == 5.0


async def test_gst_and_pst_when_both_enabled():
    """The post-fix Saskatchewan config: GST 5% + PST 6% = 11% combined,
    both lines present and summed correctly."""
    area = {
        "id": "area-1",
        "name": "Saskatoon",
        "gst_enabled": True,
        "gst_rate": 5.0,
        "pst_enabled": True,
        "pst_rate": 6.0,
    }
    result = await _run(area)
    assert result["tax_breakdown"] == {
        "GST": {"rate": 5.0, "amount": 5.0},
        "PST": {"rate": 6.0, "amount": 6.0},
    }
    assert result["tax_amount"] == 11.0


async def test_hst_overrides_gst_and_pst():
    """An HST-enabled area (e.g. a future Ontario/Atlantic market) uses the
    combined rate instead of GST+PST, even if both of those are also set."""
    area = {
        "id": "area-1",
        "name": "Future HST Area",
        "gst_enabled": True,
        "gst_rate": 5.0,
        "pst_enabled": True,
        "pst_rate": 6.0,
        "hst_enabled": True,
        "hst_rate": 13.0,
    }
    result = await _run(area)
    assert result["tax_breakdown"] == {"HST": {"rate": 13.0, "amount": 13.0}}
    assert result["tax_amount"] == 13.0
