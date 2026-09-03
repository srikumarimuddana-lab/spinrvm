"""Redis H3 live-location index — in-process Redis fallback."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from utils.h3_location_index import (
    INDEX_TTL_SECONDS,
    REQUIRED_EVICTION_POLICY,
    _policy_blocker,
    health_snapshot,
    is_ready,
    on_driver_offline,
    on_location_written,
    query_driver_ids,
    readiness_reasons,
    recent_events,
    record_event,
    set_ready,
    upsert_driver,
)
from utils.redis_client import redis_get

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

SK_LAT, SK_LNG = 52.1332, -106.6700


@pytest.fixture(autouse=True)
async def _clear_h3_keys():
    from utils.h3_location_index import _local_last_served_slots, _local_notify_until, invalidate_redis_stats_cache
    from utils.redis_client import redis_delete_pattern

    await redis_delete_pattern("spinr:h3:*")
    _local_last_served_slots.clear()
    _local_notify_until.clear()
    invalidate_redis_stats_cache()
    yield
    await redis_delete_pattern("spinr:h3:*")
    _local_last_served_slots.clear()
    _local_notify_until.clear()
    invalidate_redis_stats_cache()


async def test_upsert_then_query_finds_driver():
    assert await upsert_driver("drv-1", SK_LAT, SK_LNG)
    ids = await query_driver_ids(SK_LAT, SK_LNG, 10.0, res=8)
    assert "drv-1" in ids


async def test_move_removes_from_old_cell():
    assert await upsert_driver("drv-1", SK_LAT, SK_LNG)
    # ~50 km east — different res-8 cell.
    assert await upsert_driver("drv-1", SK_LAT, SK_LNG + 0.7)
    near_old = await query_driver_ids(SK_LAT, SK_LNG, 2.0, res=8)
    near_new = await query_driver_ids(SK_LAT, SK_LNG + 0.7, 2.0, res=8)
    assert "drv-1" not in near_old
    assert "drv-1" in near_new


async def test_remove_drops_driver():
    await upsert_driver("drv-1", SK_LAT, SK_LNG)
    await on_driver_offline("drv-1")
    ids = await query_driver_ids(SK_LAT, SK_LNG, 10.0, res=8)
    assert "drv-1" not in ids
    assert await redis_get("spinr:h3:rev:drv-1") is None


async def test_invalid_coords_do_not_index():
    assert await upsert_driver("drv-1", 999.0, 0.0) is False
    ids = await query_driver_ids(SK_LAT, SK_LNG, 10.0, res=8)
    assert "drv-1" not in ids


async def test_in_process_redis_is_never_ready():
    """No REDIS_URL → per-replica dict. Serving that as dispatch would strand rides."""
    await set_ready(generation=1, driver_count=1, incomplete=False)
    ok, blockers = await readiness_reasons()
    assert ok is False
    assert any("redis_not_connected" in b for b in blockers)
    assert await is_ready() is False


async def test_health_snapshot_has_admin_fields():
    snap = await health_snapshot()
    assert "h3_ready" in snap
    assert "blockers" in snap
    assert "redis" in snap
    assert "last_served" in snap
    assert snap["index_ttl_seconds"] == INDEX_TTL_SECONDS


async def test_ops_notify_dedupes_when_redis_is_down():
    from utils.h3_location_index import _local_notify_until, should_broadcast_ops

    _local_notify_until.clear()
    with patch("utils.h3_location_index.redis_set_nx", AsyncMock(side_effect=RuntimeError("down"))):
        assert await should_broadcast_ops("failover|h3|legacy|h3_not_ready") is True
        assert await should_broadcast_ops("failover|h3|legacy|h3_not_ready") is False


async def test_events_are_capped_and_newest_first():
    for i in range(3):
        await record_event("failover", reason=f"r{i}", extra={"n": i})
    events = await recent_events(10)
    assert events[0]["reason"] == "r2"
    assert events[0]["kind"] == "failover"
    assert "lat" not in events[0]


async def test_location_hook_never_raises():
    assert await on_location_written("drv-2", SK_LAT, SK_LNG, force=True) is True
    ids = await query_driver_ids(SK_LAT, SK_LNG, 5.0, res=8)
    assert "drv-2" in ids


async def test_location_hook_skips_when_dark():
    with patch("utils.h3_location_index.h3_index_writes_enabled", AsyncMock(return_value=False)):
        assert await on_location_written("drv-dark", SK_LAT, SK_LNG) is False
    from utils.redis_client import redis_get as _get

    assert await _get("spinr:h3:rev:drv-dark") is None


def test_volatile_ttl_eviction_blocks_h3():
    assert REQUIRED_EVICTION_POLICY == "noeviction"
    assert _policy_blocker("volatile-ttl") == "eviction_policy:volatile-ttl"
    assert _policy_blocker("noeviction") is None


async def test_location_hook_returns_false_on_invalid_coords():
    assert await on_location_written("drv-bad", 999.0, 0.0, force=True) is False


def test_mark_present_does_not_refresh_h3_ttl():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "utils" / "driver_presence.py").read_text(encoding="utf-8")
    assert "on_driver_heartbeat" not in src
    assert "on_driver_offline" in src


async def test_stale_source_ts_does_not_overwrite_newer_fix():
    now = time.time()
    assert await upsert_driver("drv-1", SK_LAT, SK_LNG, source_ts=now)
    assert await upsert_driver("drv-1", SK_LAT, SK_LNG + 0.7, source_ts=now - 10)
    near_old = await query_driver_ids(SK_LAT, SK_LNG, 2.0, res=8)
    near_new = await query_driver_ids(SK_LAT, SK_LNG + 0.7, 2.0, res=8)
    assert "drv-1" in near_old
    assert "drv-1" not in near_new


async def test_expired_zset_member_is_not_served():
    assert await upsert_driver("drv-old", SK_LAT, SK_LNG, source_ts=1.0)
    ids = await query_driver_ids(SK_LAT, SK_LNG, 5.0, res=8)
    assert "drv-old" not in ids


async def test_unknown_eviction_policy_blocks_h3():
    ok, blockers = await readiness_reasons(
        {
            "backend": "redis",
            "connected": True,
            "maxmemory_policy": "",
            "used_memory_percent": 10,
            "maxmemory_bytes": 1024,
        }
    )
    assert ok is False
    assert any("eviction_policy" in b for b in blockers)


async def test_unknown_memory_percent_blocks_h3():
    ok, blockers = await readiness_reasons(
        {
            "backend": "redis",
            "connected": True,
            "maxmemory_policy": "noeviction",
            "used_memory_percent": None,
            "maxmemory_bytes": 1024,
        }
    )
    assert ok is False
    assert any("memory_percent_unknown" in b for b in blockers)


async def test_unhealthy_flag_does_not_ttl_into_ready():
    from utils.h3_location_index import UNHEALTHY_KEY, mark_unhealthy

    await mark_unhealthy("write_failed")
    from utils.redis_client import redis_get as _get

    raw = await _get(UNHEALTHY_KEY)
    assert raw is not None
    # Sticky: no TTL encoded in the payload contract — key survives until clear.
    ok, blockers = await readiness_reasons()
    assert any(b.startswith("unhealthy:") for b in blockers)


async def test_last_served_per_area_keeps_other_area_failover():
    from utils.h3_location_index import get_last_served, remember_last_served

    await remember_last_served(
        provider="legacy", configured="h3", failed_over=True, reason="h3_not_ready", area_id="saskatoon"
    )
    await remember_last_served(provider="h3", configured="h3", failed_over=False, reason="", area_id="regina")
    banner = await get_last_served()
    assert banner and banner.get("failed_over") is True
    assert banner.get("area_id") == "saskatoon"
    recovered = await get_last_served("regina")
    assert recovered and recovered.get("failed_over") is False


async def test_shadow_activation_ignores_index_not_built():
    from utils.h3_location_index import activation_blockers

    with patch(
        "utils.h3_location_index.readiness_reasons",
        AsyncMock(return_value=(False, ["redis_not_connected", "index_not_built"])),
    ):
        shadow = await activation_blockers("shadow")
        h3 = await activation_blockers("h3")
    assert shadow == ["redis_not_connected"]
    assert "index_not_built" in h3


async def test_require_provider_activation_blocks_h3_on_in_process_redis():
    from utils.h3_location_index import require_provider_activation

    with pytest.raises(ValueError, match="Cannot enable h3"):
        await require_provider_activation("h3")
    await require_provider_activation("legacy")
    await require_provider_activation(None)


async def test_unlimited_maxmemory_with_noeviction_is_not_a_memory_block():
    from utils.h3_location_index import _memory_blocker

    assert _memory_blocker({"maxmemory_bytes": 0, "used_memory_percent": None}) is None
