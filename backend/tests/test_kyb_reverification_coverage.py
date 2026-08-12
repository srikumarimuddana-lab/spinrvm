"""
A1c Sub-tier C coverage: backend/utils/kyb_reverification.py (67% -> target 90%+).

`test_kyb_reverification.py` covers `run_kyb_reverification_tick`'s
happy-path flag/metric emission, the kill-switch short-circuit, the
reflag-cooldown skip/elapsed branches, custom-threshold pass-through, and
one-company-failure-doesn't-block-others. This file closes:

- `resolve_kyb_reverify_threshold_months`: the malformed-value `except`
  branch (falls back to the 12-month default).
- `kyb_reverify_cutoff_iso`: a direct pure-function sanity check.
- The malformed `kyb_reverify_flagged_at` timestamp `except ValueError`
  branch inside `run_kyb_reverification_tick` (falls through to
  `last_dt=None`, treated as never-flagged -> re-flags).
- `kyb_reverification_loop`: the happy tick (metrics/heartbeat/sleep) and a
  tick exception being caught/logged/counted via
  `spinr_bgloop_errors_total` (loop survives).

Patch target follows the established pattern in
`test_kyb_reverification.py`: `utils.kyb_reverification.<name>`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

P = "utils.kyb_reverification."


def test_resolve_threshold_malformed_value_falls_back_to_default():
    from utils.kyb_reverification import resolve_kyb_reverify_threshold_months

    assert resolve_kyb_reverify_threshold_months({"corporate_kyb_reverify_after_months": "not-a-number"}) == 12


def test_resolve_threshold_none_falls_back_to_default():
    from utils.kyb_reverification import resolve_kyb_reverify_threshold_months

    assert resolve_kyb_reverify_threshold_months({}) == 12


def test_resolve_threshold_valid_custom_value():
    from utils.kyb_reverification import resolve_kyb_reverify_threshold_months

    assert resolve_kyb_reverify_threshold_months({"corporate_kyb_reverify_after_months": 6}) == 6


def test_kyb_reverify_cutoff_iso_is_in_the_past():
    from datetime import datetime, timezone

    from utils.kyb_reverification import kyb_reverify_cutoff_iso

    cutoff = kyb_reverify_cutoff_iso(12)
    cutoff_dt = datetime.fromisoformat(cutoff)
    assert cutoff_dt < datetime.now(timezone.utc)


async def test_malformed_flagged_at_timestamp_still_reflags():
    from utils.kyb_reverification import run_kyb_reverification_tick

    company = {"id": "c1", "kyb_reviewed_at": "old", "kyb_reverify_flagged_at": "not-a-real-timestamp"}
    with (
        patch(P + "get_app_settings", AsyncMock(return_value={"corporate_kyb_reverification_enabled": True})),
        patch(P + "list_companies_needing_kyb_reverification", AsyncMock(return_value=[company])),
        patch(P + "mark_kyb_reverify_flagged", AsyncMock()) as mark,
        patch(P + "_metric_inc") as mock_inc,
        patch(P + "_metric_gauge") as mock_gauge,
    ):
        await run_kyb_reverification_tick()
    mark.assert_awaited_once_with(company_id="c1")
    mock_inc.assert_called_once()


# ---------------------------------------------------------------------------
# kyb_reverification_loop
# ---------------------------------------------------------------------------


async def test_loop_happy_tick_records_metrics_and_heartbeat():
    from utils import kyb_reverification as m

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "run_kyb_reverification_tick", AsyncMock()),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_metric_gauge") as mock_gauge,
        patch(P + "_metric_inc") as mock_inc,
        patch(P + "_record_heartbeat") as mock_hb,
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.kyb_reverification_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_not_called()
    mock_hb.assert_called_once_with("kyb_reverification (24h)")


async def test_loop_tick_exception_is_caught_and_counted():
    from utils import kyb_reverification as m

    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "run_kyb_reverification_tick", failing_tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_metric_gauge") as mock_gauge,
        patch(P + "_metric_inc") as mock_inc,
        patch(P + "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.kyb_reverification_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "kyb_reverification"})
