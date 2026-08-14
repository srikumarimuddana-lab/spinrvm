"""Coverage for utils/redis_client.py (A1c, Sub-tier A — presence/rate-limit
backbone, worth testing both the Redis-connected path and the documented
silent in-process-dict fallback per CLAUDE.md's "Redis transparency" note).

Existing tests exercise this module only as a side effect of higher-level
callers (dispatch presence, OTP lockout, WS rate limiting) via the `mock_redis`
fixture, which patches `_local` to an empty dict — i.e. everything runs
through the in-process fallback branch. This file adds direct coverage of:

- Every public function's REAL-Redis-connected branch (mocked `redis.asyncio`
  client), not just the in-process fallback.
- The Redis-configured-but-erroring branch, which CLAUDE.md's "Redis
  transparency" convention requires to raise loudly (not silently degrade —
  degrading only happens when REDIS_URL is unset entirely).
- `_get_redis()`'s URL-change reconnect and import-failure-falls-back branches.
- `_humanize_bytes`'s unit-boundary edge cases.
- `get_redis_stats`/`count_keys_by_prefix` in both modes.

Test-only change — no application code modified.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_redis_client_state(monkeypatch):
    """Isolate _redis/_redis_url/_local across tests — mirrors conftest's
    mock_redis fixture but also resets the lazy-init globals so a test that
    forces the Redis-connected branch doesn't leak into a fallback test."""
    from backend.utils import redis_client as rc

    monkeypatch.setattr(rc, "_local", {})
    monkeypatch.setattr(rc, "_redis", None)
    monkeypatch.setattr(rc, "_redis_url", None)
    yield


def _fake_redis_env(monkeypatch, url="redis://test-host:6379/0"):
    monkeypatch.setenv("REDIS_URL", url)


def _patch_get_redis(monkeypatch, fake_client):
    """Patch rc._get_redis() directly to return ``fake_client``.

    `_get_redis()`'s own connection-creation logic (``import redis.asyncio as
    aioredis; aioredis.from_url(...)``) is exercised by its own dedicated
    tests below using monkeypatch.setitem on sys.modules — patching the
    string path "redis.asyncio.from_url" via unittest.mock.patch is NOT
    reliable here: this repo's `redis` package only gains a real `asyncio`
    submodule attribute once something has actually imported it, and a
    prior test elsewhere in the suite (test_coverage_boost.py's
    TestGetRedisDirect) demonstrates that string-path patching can silently
    bind to a stale reference under full-suite ordering. Every OTHER public
    function in this module only cares whether `_get_redis()` returns a
    client or None — not how that client was constructed — so patching the
    seam directly is both simpler and immune to that fragility.
    """
    from backend.utils import redis_client as rc

    monkeypatch.setattr(rc, "_get_redis", AsyncMock(return_value=fake_client))


# ── _get_redis() lazy-init branches ─────────────────────────────────────────
#
# These exercise the real import-and-connect path, so they use the safe
# sys.modules-patching pattern already established in test_coverage_boost.py
# (see its TestGetRedisDirect class) rather than string-path patch(), which
# is unreliable for this particular module per the note above.


def _patch_redis_asyncio_module(monkeypatch, fake_aioredis):
    """Make `import redis.asyncio as aioredis` resolve to ``fake_aioredis``.

    `import a.b as x` resolves via IMPORT_FROM: it imports `a`, then does
    `getattr(sys.modules['a'], 'b')` — NOT a direct sys.modules['a.b'] lookup.
    Both the sys.modules entry and the attribute on the parent `redis`
    package must be patched together for this to take effect reliably, and
    monkeypatch (unlike the hand-rolled try/finally in test_coverage_boost.py)
    correctly restores an attribute that didn't previously exist.
    """
    import redis as redis_pkg  # ensures sys.modules["redis"] exists before we patch its attribute

    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_aioredis)
    monkeypatch.setattr(redis_pkg, "asyncio", fake_aioredis, raising=False)


@pytest.mark.anyio
async def test_get_redis_returns_none_when_url_unset(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await rc._get_redis() is None


@pytest.mark.anyio
async def test_get_redis_connects_and_caches_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake_client = MagicMock()
    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(return_value=fake_client)
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    first = await rc._get_redis()
    second = await rc._get_redis()

    assert first is fake_client
    assert second is fake_client
    fake_aioredis.from_url.assert_called_once()  # cached — second call must not reconnect


@pytest.mark.anyio
async def test_get_redis_reconnects_when_url_changes(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch, "redis://host-a:6379/0")
    client_a = MagicMock()
    client_b = MagicMock()
    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(side_effect=[client_a, client_b])
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    first = await rc._get_redis()
    monkeypatch.setenv("REDIS_URL", "redis://host-b:6379/0")
    second = await rc._get_redis()

    assert first is client_a
    assert second is client_b


@pytest.mark.anyio
async def test_get_redis_falls_back_to_none_on_connect_failure(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(side_effect=RuntimeError("connect refused"))
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    result = await rc._get_redis()
    assert result is None


# ── redis_get / redis_mget ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_redis_get_uses_local_fallback_when_unset(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("k1", "v1")
    assert await rc.redis_get("k1") == "v1"
    assert await rc.redis_get("missing") is None


@pytest.mark.anyio
async def test_redis_get_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.get = AsyncMock(return_value="from-redis")
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_get("k1")
    assert result == "from-redis"
    fake.get.assert_awaited_once_with("k1")


@pytest.mark.anyio
async def test_redis_get_raises_when_configured_but_erroring(monkeypatch):
    """CLAUDE.md: a Redis error when REDIS_URL IS set must surface loudly,
    never silently degrade to the in-process store — degrading only happens
    when REDIS_URL is unset entirely."""
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.get = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_get("k1")


@pytest.mark.anyio
async def test_redis_mget_empty_input_short_circuits(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await rc.redis_mget([]) == []


@pytest.mark.anyio
async def test_redis_mget_local_fallback_aligns_to_keys(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("a", "1")
    await rc.redis_set("c", "3")
    result = await rc.redis_mget(["a", "b", "c"])
    assert result == ["1", None, "3"]


@pytest.mark.anyio
async def test_redis_mget_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.mget = AsyncMock(return_value=["1", None, "3"])
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_mget(["a", "b", "c"])
    assert result == ["1", None, "3"]
    fake.mget.assert_awaited_once_with(["a", "b", "c"])


@pytest.mark.anyio
async def test_redis_mget_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.mget = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_mget(["a"])


# ── redis_set (with/without TTL) ────────────────────────────────────────────


@pytest.mark.anyio
async def test_redis_set_with_ttl_uses_setex_on_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.setex = AsyncMock()
    fake.set = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    await rc.redis_set("k1", "v1", ttl=60)
    fake.setex.assert_awaited_once_with("k1", 60, "v1")
    fake.set.assert_not_awaited()


@pytest.mark.anyio
async def test_redis_set_without_ttl_uses_set_on_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.setex = AsyncMock()
    fake.set = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    await rc.redis_set("k1", "v1")
    fake.set.assert_awaited_once_with("k1", "v1")
    fake.setex.assert_not_awaited()


@pytest.mark.anyio
async def test_redis_set_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.set = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_set("k1", "v1")


@pytest.mark.anyio
async def test_local_set_and_expiry(monkeypatch):
    """TTL-expired local keys behave as a miss (time.monotonic()-based
    best-effort expiry, per the module's documented non-thread-safe design)."""
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("k1", "v1", ttl=100)
    assert await rc.redis_get("k1") == "v1"

    with patch("time.monotonic", return_value=1e12):  # far future
        assert await rc.redis_get("k1") is None


# ── redis_set_nx ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_nx_local_acquires_lock_once(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    first = await rc.redis_set_nx("lock:job", "replica-1", ttl=30)
    second = await rc.redis_set_nx("lock:job", "replica-2", ttl=30)
    assert first is True
    assert second is False


@pytest.mark.anyio
async def test_set_nx_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.set = AsyncMock(return_value=True)
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_set_nx("lock:job", "replica-1", ttl=30)
    assert result is True
    fake.set.assert_awaited_once_with("lock:job", "replica-1", nx=True, ex=30)


@pytest.mark.anyio
async def test_set_nx_real_client_returns_false_when_not_acquired(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.set = AsyncMock(return_value=None)  # NX miss
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_set_nx("lock:job", "replica-1", ttl=30)
    assert result is False


@pytest.mark.anyio
async def test_set_nx_raises_when_configured_but_erroring(monkeypatch):
    """2026-08-11 P1 fix: redis_set_nx used to be the one primitive that
    swallowed a Redis error (logged as a warning) and silently fell through
    to the per-replica local-lock path -- during a real production Redis
    blip, every replica's call independently "won" its own local lock, so
    a leader-election/dedupe guarantee silently became "every replica
    proceeds independently" with no louder signal than a warning log. Now
    matches every other primitive in this module (redis_get/set/incr/
    expire/delete): raises on a real Redis-configured-but-unavailable
    error. Callers now decide explicitly how to degrade (see
    utils/scheduled_rides.py's leader lock or routes/rides/payments.py's
    wallet re-drive lock for two different, deliberate call-site choices)."""
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.set = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_set_nx("lock:job", "replica-1", ttl=30)


# ── redis_incr / redis_incrby ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_incr_and_incrby_local_fallback(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await rc.redis_incr("counter") == 1
    assert await rc.redis_incr("counter") == 2
    assert await rc.redis_incrby("counter", 5) == 7


@pytest.mark.anyio
async def test_incr_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=4)
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_incr("counter")
    assert result == 4
    fake.incr.assert_awaited_once_with("counter")


@pytest.mark.anyio
async def test_incrby_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.incrby = AsyncMock(return_value=9)
    _patch_get_redis(monkeypatch, fake)
    result = await rc.redis_incrby("counter", 5)
    assert result == 9
    fake.incrby.assert_awaited_once_with("counter", 5)


@pytest.mark.anyio
async def test_incrby_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.incrby = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_incrby("counter", 5)


@pytest.mark.anyio
async def test_incr_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_incr("counter")


@pytest.mark.anyio
async def test_local_incrby_preserves_existing_ttl(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("counter", "10", ttl=60)
    await rc.redis_incrby("counter", 5)
    assert rc._local["counter"]["expires_at"] is not None


# ── redis_expire / redis_delete ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_expire_and_delete_local_fallback(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("k1", "v1")
    await rc.redis_expire("k1", 30)
    assert rc._local["k1"]["expires_at"] is not None

    await rc.redis_delete("k1")
    assert await rc.redis_get("k1") is None


@pytest.mark.anyio
async def test_expire_noop_when_local_key_missing(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_expire("never-set", 30)  # must not raise
    assert "never-set" not in rc._local


@pytest.mark.anyio
async def test_expire_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.expire = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    await rc.redis_expire("k1", 30)
    fake.expire.assert_awaited_once_with("k1", 30)


@pytest.mark.anyio
async def test_expire_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.expire = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_expire("k1", 30)


@pytest.mark.anyio
async def test_delete_delegates_to_real_client(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.delete = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    await rc.redis_delete("k1")
    fake.delete.assert_awaited_once_with("k1")


@pytest.mark.anyio
async def test_delete_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.delete = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_delete("k1")


# ── redis_delete_pattern ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_delete_pattern_local_fallback(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("cache:user:1", "a")
    await rc.redis_set("cache:user:2", "b")
    await rc.redis_set("cache:driver:1", "c")

    deleted = await rc.redis_delete_pattern("cache:user:*")
    assert deleted == 2
    assert await rc.redis_get("cache:user:1") is None
    assert await rc.redis_get("cache:driver:1") == "c"


@pytest.mark.anyio
async def test_delete_pattern_delegates_to_real_client_scan(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(pattern):
        for k in ["cache:user:1", "cache:user:2"]:
            yield k

    fake.scan_iter = _scan_iter
    fake.delete = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    deleted = await rc.redis_delete_pattern("cache:user:*")

    assert deleted == 2
    fake.delete.assert_awaited_once_with("cache:user:1", "cache:user:2")


@pytest.mark.anyio
async def test_delete_pattern_no_matches_skips_delete_call(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(pattern):
        return
        yield  # pragma: no cover - makes this an async generator

    fake.scan_iter = _scan_iter
    fake.delete = AsyncMock()
    _patch_get_redis(monkeypatch, fake)
    deleted = await rc.redis_delete_pattern("cache:user:*")

    assert deleted == 0
    fake.delete.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_pattern_raises_when_configured_but_erroring(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(pattern):
        raise ConnectionError("redis down")
        yield  # pragma: no cover

    fake.scan_iter = _scan_iter
    _patch_get_redis(monkeypatch, fake)
    with pytest.raises(ConnectionError):
        await rc.redis_delete_pattern("cache:user:*")


# ── get_redis_stats ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_redis_stats_local_fallback_shape(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("k1", "hello")

    stats = await rc.get_redis_stats()
    assert stats["backend"] == "in_process"
    assert stats["connected"] is False
    assert stats["total_keys"] == 1
    assert stats["used_memory_bytes"] == len(b"hello")
    assert stats["maxmemory_human"] == "unlimited"


@pytest.mark.anyio
async def test_get_redis_stats_real_client_computes_percent(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.info = AsyncMock(
        side_effect=[
            {"used_memory": 500, "maxmemory": 1000, "used_memory_human": "500B", "used_memory_peak": 600},
            {
                "keyspace_hits": 10,
                "keyspace_misses": 2,
                "evicted_keys": 0,
                "expired_keys": 1,
                "total_commands_processed": 100,
            },
            {"connected_clients": 3},
            {"uptime_in_seconds": 3600},
        ]
    )
    fake.dbsize = AsyncMock(return_value=42)
    _patch_get_redis(monkeypatch, fake)
    stats = await rc.get_redis_stats()

    assert stats["backend"] == "redis"
    assert stats["connected"] is True
    assert stats["used_memory_percent"] == 50.0
    assert stats["total_keys"] == 42
    assert stats["keyspace_hits_total"] == 10


@pytest.mark.anyio
async def test_get_redis_stats_real_client_zero_maxmemory_gives_none_percent(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.info = AsyncMock(
        side_effect=[
            {"used_memory": 500, "maxmemory": 0},
            {},
            {},
            {},
        ]
    )
    fake.dbsize = AsyncMock(return_value=1)
    _patch_get_redis(monkeypatch, fake)
    stats = await rc.get_redis_stats()

    assert stats["used_memory_percent"] is None
    assert stats["maxmemory_human"] == "unlimited"


@pytest.mark.anyio
async def test_get_redis_stats_info_failure_returns_error_shape(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()
    fake.info = AsyncMock(side_effect=ConnectionError("redis down"))
    _patch_get_redis(monkeypatch, fake)
    stats = await rc.get_redis_stats()

    assert stats == {"backend": "redis", "connected": False, "error": "redis down"}


# ── count_keys_by_prefix ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_count_keys_by_prefix_local_fallback(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("cache:user:1", "a")
    await rc.redis_set("cache:user:2", "b")
    await rc.redis_set("otp:15550001234", "1234")
    await rc.redis_set("unmatched:key", "x")

    counts = await rc.count_keys_by_prefix()
    assert counts["cache:user:"] == 2
    assert counts["otp:"] == 1
    assert counts["__other__"] == 1
    # Every known prefix present even at zero, for stable dashboard rendering.
    assert counts["session:"] == 0


@pytest.mark.anyio
async def test_count_keys_by_prefix_custom_prefix_list(monkeypatch):
    from backend.utils import redis_client as rc

    monkeypatch.delenv("REDIS_URL", raising=False)
    await rc.redis_set("foo:1", "a")
    await rc.redis_set("bar:1", "b")

    counts = await rc.count_keys_by_prefix(prefixes=["foo:"])
    assert counts == {"foo:": 1, "__other__": 1}


@pytest.mark.anyio
async def test_count_keys_by_prefix_real_client_scan(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(match, count):
        for k in ["cache:user:1", "cache:driver:1", "totally:unmatched"]:
            yield k

    fake.scan_iter = _scan_iter
    _patch_get_redis(monkeypatch, fake)
    counts = await rc.count_keys_by_prefix()

    assert counts["cache:user:"] == 1
    assert counts["cache:driver:"] == 1
    assert counts["__other__"] == 1


@pytest.mark.anyio
async def test_count_keys_by_prefix_decodes_bytes_keys(monkeypatch):
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(match, count):
        yield b"otp:15550001234"

    fake.scan_iter = _scan_iter
    _patch_get_redis(monkeypatch, fake)
    counts = await rc.count_keys_by_prefix()

    assert counts["otp:"] == 1


@pytest.mark.anyio
async def test_count_keys_by_prefix_scan_failure_returns_zeroed_counts(monkeypatch):
    """A SCAN failure mid-count degrades to whatever was counted so far
    (best-effort observability — this is a metrics endpoint, not a
    money/dispatch path, so CLAUDE.md's "surface loudly" bar doesn't
    apply the same way; the function logs a warning and returns)."""
    from backend.utils import redis_client as rc

    _fake_redis_env(monkeypatch)
    fake = MagicMock()

    async def _scan_iter(match, count):
        raise ConnectionError("redis down")
        yield  # pragma: no cover

    fake.scan_iter = _scan_iter
    _patch_get_redis(monkeypatch, fake)
    counts = await rc.count_keys_by_prefix()

    assert counts["__other__"] == 0
    assert all(v == 0 for v in counts.values())


# ── _humanize_bytes ──────────────────────────────────────────────────────────


class TestHumanizeBytes:
    def test_negative_is_zero(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(-5) == "0B"

    def test_bytes_stays_integer(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(500) == "500B"

    def test_kilobytes(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(2048) == "2.0K"

    def test_megabytes(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(12 * 1024 * 1024) == "12.0M"

    def test_gigabytes(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(int(2.1 * 1024 * 1024 * 1024)) == "2.1G"

    def test_terabytes(self):
        from backend.utils.redis_client import _humanize_bytes

        assert _humanize_bytes(3 * 1024 * 1024 * 1024 * 1024) == "3.0T"

    def test_beyond_terabyte_mislabels_as_bytes_not_fixed(self):
        """Bug found, not fixed (test-only pass): `unit` is only ever
        reassigned inside the `if size < 1024` branch, which never fires once
        ``size`` starts >= T-scale — the loop silently keeps dividing by 1024
        through every remaining unit (including "T") without ever setting
        ``unit``, so it stays at its "B" initializer. A petabyte+ value in
        Redis's `used_memory` therefore renders as a tiny, nonsensical "…B"
        string instead of the correct "…P"/"…T" reading. Not a live
        production risk today (no realistic Redis instance holds petabytes),
        but pinned here so a future change to KNOWN_KEY_PREFIXES-scale growth
        doesn't silently inherit a broken dashboard number."""
        from backend.utils.redis_client import _humanize_bytes

        huge = 5 * 1024 * 1024 * 1024 * 1024 * 1024  # 5 exabytes
        result = _humanize_bytes(huge)
        assert result == "5B"  # pins the actual (buggy) behavior
