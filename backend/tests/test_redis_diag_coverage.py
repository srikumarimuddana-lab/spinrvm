"""Additional coverage for utils/redis_diag.py.

test_redis_diag.py already exercises probe_redis_url's "unset", "REST URL
rejected", "plaintext warns", and "dedupe" branches by dialing real
(unreachable) sockets. This file fills in the branches that need a mocked
`redis.asyncio` client to reach at all:

- `_classify_error`'s per-substring hint branches (TLS / connection-limit /
  auth / DNS / timeout / generic fallback).
- `_pubsub_roundtrip` end to end: successful round-trip, the "no message
  echoed back" timeout path, and the exception path (including the
  best-effort `except: pass` cleanup in its `finally` block).
- `probe_redis_url`'s "redis package not installed" ImportError branch and
  its full success path (ping + pubsub -> status "ok"/"degraded").
- `log_diagnosis`'s "ok" and "degraded" log lines.

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.redis_diag import (
    _classify_error,
    _pubsub_roundtrip,
    diagnose_redis,
    log_diagnosis,
    probe_redis_url,
)

pytestmark = pytest.mark.unit


def _patch_redis_asyncio_module(monkeypatch, fake_aioredis):
    """Make `import redis.asyncio as redis_asyncio` (inside probe_redis_url)
    resolve to ``fake_aioredis``.

    Same mechanism documented in test_redis_client_coverage.py: `import a.b
    as x` resolves via IMPORT_FROM — it imports `a`, then does
    `getattr(sys.modules['a'], 'b')` — so both the sys.modules entry and the
    attribute on the parent `redis` package must be patched together.
    """
    import redis as redis_pkg  # ensures sys.modules["redis"] exists before we patch its attribute

    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_aioredis)
    monkeypatch.setattr(redis_pkg, "asyncio", fake_aioredis, raising=False)


def _make_fake_client(*, ping_ok=True, pubsub_client=None):
    fake_client = MagicMock()
    fake_client.ping = AsyncMock() if ping_ok else AsyncMock(side_effect=ConnectionError("refused"))
    fake_client.aclose = AsyncMock()
    fake_client.publish = AsyncMock()
    fake_client.pubsub = MagicMock(return_value=pubsub_client)
    return fake_client


# ── _classify_error ─────────────────────────────────────────────────────────


class TestClassifyError:
    def test_tls_handshake_hint(self):
        msg = _classify_error(Exception("SSL: WRONG_VERSION_NUMBER"))
        assert "TLS handshake failed" in msg
        assert "rediss://" in msg

    def test_tls_handshake_hint_via_eof(self):
        msg = _classify_error(Exception("EOF occurred in violation of protocol"))
        assert "TLS handshake failed" in msg

    def test_connection_limit_hint(self):
        msg = _classify_error(Exception("ERR max number of clients reached"))
        assert "connection/request limit hit" in msg

    def test_connection_limit_hint_via_request(self):
        msg = _classify_error(Exception("max requests per second exceeded"))
        assert "connection/request limit hit" in msg

    def test_auth_hint(self):
        msg = _classify_error(Exception("NOAUTH Authentication required"))
        assert "auth rejected" in msg

    def test_dns_hint(self):
        msg = _classify_error(Exception("Name or service not known"))
        assert "DNS resolution failed" in msg

    def test_dns_hint_via_getaddrinfo(self):
        msg = _classify_error(Exception("[Errno -2] getaddrinfo failed"))
        assert "DNS resolution failed" in msg

    def test_timeout_hint_via_message(self):
        msg = _classify_error(Exception("Connection timed out"))
        assert "timed out" in msg

    def test_timeout_hint_via_exception_type(self):
        """asyncio.TimeoutError often stringifies to "" — the isinstance()
        check must still catch it even when the message text is empty."""
        msg = _classify_error(asyncio.TimeoutError())
        assert "timed out" in msg

    def test_generic_fallback(self):
        exc = RuntimeError("something completely unrelated")
        msg = _classify_error(exc)
        assert msg == "RuntimeError: something completely unrelated"


# ── _pubsub_roundtrip ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pubsub_roundtrip_success_after_a_subscribe_confirmation():
    """A subscribe-confirmation message (type != "message") must be skipped
    before the real echoed publish is picked up — exercises both sides of
    `if msg and msg.get("type") == "message"`."""
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock()
    fake_ps.unsubscribe = AsyncMock()
    fake_ps.aclose = AsyncMock()
    fake_ps.get_message = AsyncMock(side_effect=[{"type": "subscribe"}, {"type": "message"}])
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    result = await _pubsub_roundtrip(fake_client, timeout=5.0)

    assert result == {"ok": True}
    fake_client.publish.assert_awaited_once()
    fake_ps.unsubscribe.assert_awaited_once()
    fake_ps.aclose.assert_awaited_once()


@pytest.mark.anyio
async def test_pubsub_roundtrip_no_message_echoed_back():
    """Nothing ever arrives (Upstash REST tier / proxy silently drops
    pub/sub) -> the loop runs out the deadline and reports the specific
    "unsupported or blocked" error rather than a generic exception."""
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock()
    fake_ps.unsubscribe = AsyncMock()
    fake_ps.aclose = AsyncMock()
    fake_ps.get_message = AsyncMock(return_value=None)
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    result = await _pubsub_roundtrip(fake_client, timeout=0.01)

    assert result["ok"] is False
    assert "no message echoed back" in result["error"]


@pytest.mark.anyio
async def test_pubsub_roundtrip_get_message_respects_caller_timeout():
    """Fixed (2026-08-03): `ps.get_message(...)`'s per-poll timeout is now
    bounded by the time actually remaining until the caller's deadline
    (capped at 1s per iteration), not a fixed 1.0s — previously a caller
    asking for a fast 0.01s probe could still have an individual
    get_message() call block for up to a full second when nothing arrived.
    Pinned here via call-args assertion (no real sleep needed since
    get_message is mocked)."""
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock()
    fake_ps.unsubscribe = AsyncMock()
    fake_ps.aclose = AsyncMock()
    fake_ps.get_message = AsyncMock(return_value=None)
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    result = await _pubsub_roundtrip(fake_client, timeout=0.01)

    assert result["ok"] is False
    # The per-poll timeout must be bounded by the ~0.01s requested budget,
    # never the old hardcoded 1.0s.
    _, kwargs = fake_ps.get_message.call_args
    assert kwargs["timeout"] <= 0.01
    assert kwargs["ignore_subscribe_messages"] is True


@pytest.mark.anyio
async def test_pubsub_roundtrip_exception_path_and_best_effort_cleanup():
    """subscribe() raising must be caught and classified, and the finally
    block's own unsubscribe()/aclose() failures must be swallowed (best
    effort cleanup — noqa: S110 in the source) rather than masking the
    original error."""
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock(side_effect=ConnectionError("NOAUTH bad token"))
    fake_ps.unsubscribe = AsyncMock(side_effect=RuntimeError("cleanup failed too"))
    fake_ps.aclose = AsyncMock(side_effect=RuntimeError("cleanup failed too"))
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    result = await _pubsub_roundtrip(fake_client, timeout=1.0)

    assert result["ok"] is False
    assert "auth rejected" in result["error"]
    # Cleanup was attempted despite raising internally — proves the
    # except-pass branches in the finally block actually ran.
    fake_ps.unsubscribe.assert_awaited_once()
    fake_ps.aclose.assert_awaited_once()


# ── probe_redis_url: package-missing + full success path ───────────────────


@pytest.mark.anyio
async def test_probe_redis_url_reports_missing_redis_package(monkeypatch):
    """Force `import redis.asyncio as redis_asyncio` to raise ImportError by
    putting None in sys.modules["redis"] — the documented way to make an
    import statement fail deterministically without uninstalling anything."""
    monkeypatch.setitem(sys.modules, "redis", None)
    monkeypatch.delitem(sys.modules, "redis.asyncio", raising=False)

    res = await probe_redis_url("REDIS_URL", "rediss://host.upstash.io:6379", timeout=0.01)

    assert res["status"] == "error"
    assert res["error"] == "redis package not installed"


@pytest.mark.anyio
async def test_probe_redis_url_full_success_is_ok(monkeypatch):
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock()
    fake_ps.unsubscribe = AsyncMock()
    fake_ps.aclose = AsyncMock()
    fake_ps.get_message = AsyncMock(return_value={"type": "message"})
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(return_value=fake_client)
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    res = await probe_redis_url("REDIS_URL", "rediss://host.upstash.io:6379", timeout=1.0)

    assert res["status"] == "ok"
    assert res["pubsub"] == {"ok": True}
    assert isinstance(res["ping_ms"], float)
    fake_client.aclose.assert_awaited_once()


@pytest.mark.anyio
async def test_probe_redis_url_full_success_but_pubsub_broken_is_degraded(monkeypatch):
    """PING succeeds (Upstash REST-tier-adjacent proxies often do) but
    pub/sub never echoes back -> "degraded", not "ok" or "error"."""
    fake_ps = MagicMock()
    fake_ps.subscribe = AsyncMock()
    fake_ps.unsubscribe = AsyncMock()
    fake_ps.aclose = AsyncMock()
    fake_ps.get_message = AsyncMock(return_value=None)
    fake_client = _make_fake_client(pubsub_client=fake_ps)

    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(return_value=fake_client)
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    res = await probe_redis_url("WS_REDIS_URL", "rediss://host.upstash.io:6379", timeout=0.01)

    assert res["status"] == "degraded"
    assert res["pubsub"]["ok"] is False


@pytest.mark.anyio
async def test_probe_redis_url_ping_failure_still_closes_client(monkeypatch):
    fake_client = _make_fake_client(ping_ok=False, pubsub_client=MagicMock())
    fake_aioredis = MagicMock()
    fake_aioredis.from_url = MagicMock(return_value=fake_client)
    _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

    res = await probe_redis_url("REDIS_URL", "rediss://host.upstash.io:6379", timeout=0.01)

    assert res["status"] == "error"
    fake_client.aclose.assert_awaited_once()


# ── diagnose_redis (sanity — main dedupe path already covered elsewhere) ───


@pytest.mark.anyio
async def test_diagnose_redis_probes_distinct_unset_urls_independently():
    results = await diagnose_redis({"REDIS_URL": "", "WS_REDIS_URL": ""})
    assert [r["status"] for r in results] == ["unset", "unset"]


# ── log_diagnosis ────────────────────────────────────────────────────────────


def test_log_diagnosis_covers_every_status_branch():
    """No assertions on log content (loguru writes straight to its own sink,
    not captured by capsys/caplog without extra wiring) — this exists purely
    to execute the "ok" and "degraded" branches, which the module's own
    tests never reach since they only ever produce "unset"/"error" results
    against unreachable hosts."""
    results = [
        {"label": "REDIS_URL", "status": "unset"},
        {
            "label": "WS_REDIS_URL",
            "status": "ok",
            "endpoint": "rediss://h:6379",
            "ping_ms": 1.2,
        },
        {
            "label": "WS_REDIS_URL (effective)",
            "status": "ok",
            "endpoint": "rediss://h:6379",
            "ping_ms": 1.2,
            "same_as": "WS_REDIS_URL",
        },
        {
            "label": "RATE_LIMIT_REDIS_URL",
            "status": "degraded",
            "endpoint": "rediss://h2:6379",
            "ping_ms": 3.4,
            "pubsub": {"ok": False, "error": "no message echoed back"},
        },
        {
            "label": "SOME_URL",
            "status": "error",
            "endpoint": "rediss://h3:6379",
            "error": "ConnectionError: refused",
            "warning": "plaintext redis:// — Upstash requires rediss:// (TLS)",
        },
    ]

    log_diagnosis(results)  # must not raise
