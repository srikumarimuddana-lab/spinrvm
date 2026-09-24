from __future__ import annotations

import ast
import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import redis.asyncio as redis_async


def _merge_script() -> str:
    source_path = Path(__file__).resolve().parents[2] / "utils" / "driver_presence.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_MERGE_SCOPED_PRESENCE" for target in node.targets
        ):
            script = ast.literal_eval(node.value)
            assert isinstance(script, str)
            return script
    raise AssertionError("production scoped-presence Lua script was not found")


@pytest.mark.asyncio
async def test_scoped_presence_lua_is_atomic_expiring_and_exactly_scoped():
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("real Redis Lua test requires REDIS_TEST_URL")
    redis = redis_async.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    script = _merge_script()
    identity = uuid.uuid4().hex
    old_key = f"spinr:presence:v2:driver:test-{identity}:session:old-{identity}:epoch:41"
    new_key = f"spinr:presence:v2:driver:test-{identity}:session:new-{identity}:epoch:42"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    async def merge(key, received_ms, contact_until_ms, location_until_ms):
        return await redis.eval(script, 1, key, received_ms, contact_until_ms, location_until_ms)

    try:
        await merge(old_key, now_ms, now_ms + 62_000, now_ms + 62_000)
        # Reordered clients/replicas race; Lua keeps the newest contact and GPS.
        await asyncio.gather(
            merge(old_key, now_ms - 1_000, now_ms + 20_000, now_ms + 30_000),
            merge(old_key, now_ms + 1_000, now_ms + 62_000, now_ms + 60_000),
        )
        before_pong = await redis.hgetall(old_key)
        assert before_pong["contact_received_ms"] == str(now_ms + 1_000)
        assert before_pong["location_valid_until_ms"] == str(now_ms + 62_000)

        # A pong renews contact without replacing or shortening GPS authority.
        await merge(old_key, now_ms + 2_000, now_ms + 62_000, 0)
        after_pong = await redis.hgetall(old_key)
        assert after_pong["location_valid_until_ms"] == before_pong["location_valid_until_ms"]

        await merge(new_key, now_ms + 3_000, now_ms + 62_000, now_ms + 62_000)
        await redis.delete(old_key)  # an old disconnect owns only its captured key
        assert await redis.exists(old_key) == 0
        assert await redis.exists(new_key) == 1
        ttl_ms = await redis.pttl(new_key)
        assert 0 < ttl_ms <= 62_000
    finally:
        await redis.delete(old_key, new_key)
        await redis.aclose()
