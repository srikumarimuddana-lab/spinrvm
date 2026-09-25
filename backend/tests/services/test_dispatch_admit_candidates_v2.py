"""
Tests for ``admit_candidates_v2`` (v2 dispatch admission).

The function had no unit tests: its only callers are exercised by the
Postgres-backed ``tests/direct_pool`` tier. It must never fail open — an
unreachable presence store or an exception admits nobody — so each outcome
is pinned here with the presence lookup mocked.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

import services.dispatch_service as ds  # noqa: E402

pytestmark = pytest.mark.anyio

_CONTACT_MS = 1_790_000_000_000
_LOCATION_MS = 1_790_000_060_000


def _evidence(session_id: str = "s-1", epoch: int = 7) -> dict:
    return {
        "session_id": session_id,
        "online_epoch": epoch,
        "contact_valid_until_ms": _CONTACT_MS,
        "location_valid_until_ms": _LOCATION_MS,
    }


def _patch_presence(**kwargs):
    return patch.object(ds, "scoped_driver_presence_evidence", AsyncMock(**kwargs))


async def test_empty_candidates_skip_the_presence_lookup():
    with _patch_presence(return_value=({}, True)) as lookup:
        assert await ds.admit_candidates_v2([]) == ([], "ok")
    lookup.assert_not_awaited()


async def test_lookup_exception_admits_nobody_and_counts_failure():
    with _patch_presence(side_effect=RuntimeError("redis down")), patch.object(ds, "_metric_inc") as inc:
        assert await ds.admit_candidates_v2([{"id": "d1"}]) == ([], "error")
    inc.assert_called_once_with("spinr_dispatch_presence_filter_failed_total", labels={"mode": "v2_closed"})


async def test_unreachable_store_admits_nobody_and_counts_failure():
    with _patch_presence(return_value=({"d1": _evidence()}, False)), patch.object(ds, "_metric_inc") as inc:
        assert await ds.admit_candidates_v2([{"id": "d1"}]) == ([], "store_unavailable")
    inc.assert_called_once_with("spinr_dispatch_presence_filter_failed_total", labels={"mode": "v2_closed"})


async def test_no_evidence_returns_none_present():
    with _patch_presence(return_value=({}, True)):
        assert await ds.admit_candidates_v2([{"id": "d1"}]) == ([], "none_present")


async def test_admits_only_drivers_with_complete_evidence():
    candidates = [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}]
    bad = _evidence("s-3")
    bad["location_valid_until_ms"] = None  # malformed evidence is skipped, not admitted
    with _patch_presence(return_value=({"d1": _evidence(), "d3": bad}, True)) as lookup:
        admitted, outcome = await ds.admit_candidates_v2(candidates)

    lookup.assert_awaited_once_with(["d1", "d2", "d3"])
    assert outcome == "ok"
    assert [d["id"] for d in admitted] == ["d1"]
    assert admitted[0]["_admission"] == {
        "session_id": "s-1",
        "online_epoch": "7",
        "contact_valid_until": datetime.fromtimestamp(_CONTACT_MS / 1000, tz=timezone.utc).isoformat(),
        "location_valid_until": datetime.fromtimestamp(_LOCATION_MS / 1000, tz=timezone.utc).isoformat(),
    }
    assert "_admission" not in candidates[1]
