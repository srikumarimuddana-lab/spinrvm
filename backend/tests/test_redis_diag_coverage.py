"""Coverage-closure tests for utils/redis_diag.py (A1c Sub-tier C).

Complements the existing tests/test_redis_diag.py (unset URL, REST-URL
rejection, plaintext-TLS warning, dedup-shared-URL, masked endpoint). This
file fills in the branches those don't reach:

- `_classify_error`'s TLS / connection-limit / DNS / timeout branches (the
  default and auth branches are already exercised indirectly).
- `_pubsub_roundtrip` as a standalone unit (echo-received, no-echo-timeout,
  and subscribe-raises paths) — previously exercised only indirectly and
  incompletely through `probe_redis_url`'s network-failure tests.
- `probe_redis_url`'s "redis package not installed" ImportError branch and
  its full successful ping+pubsub-ok / ping+pubsub-degraded paths (the
  existing tests only exercise the pre-dial classification branches and the
  real-network failure branch).
- `log_diagnosis`'s "ok" and "degraded" banner lines (previously only
  exercised via core/lifespan startup with unset URLs, which only reaches
  the "unset" branch).

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import redis_diag
from utils.redis_diag import (
    _classify_error,
    _pubsub_roundtrip,
    diagnose_redis,
    log_diagnosis,
    probe_redis_url,
)

pytestmark = pytest.mark.anyio


# ============================================================
# _classify_error
# ============================================================


class TestClassifyError:
    def test_tls_handshake_failure(self):
        msg = _classify_error(Exception("wrong version number"))
        assert "TLS handshake failed" in msg
        assert "rediss://" in msg

    def test_ssl_keyword_also_classified_as_tls(self):
        assert "TLS handshake failed" in _classify_error(Exception("SSL error occurred"))

    def test_eof_occurred_also_classified_as_tls(self):
        assert "TLS handshake failed" in _classify_error(Exception("EOF occurred in violation of protocol"))

    def test_connection_limit_hit(self):
        msg = _classify_error(Exception("max number of clients reached"))
        assert "connection/request limit hit" in msg

    def test_dns_resolution_failed(self):
        msg = _classify_error(Exception("Name or service not known"))
        assert "DNS resolution failed" in msg

    def test_getaddrinfo_also_classified_as_dns(self):
        assert "DNS resolution failed" in _classify_error(Exception("getaddrinfo failed"))

    def test_timed_out_message(self):
        msg = _classify_error(Exception("Operation timed out"))
        assert "timed out" in msg

    def test_asyncio_timeout_error_instance(self):
        msg = _classify_error(asyncio.TimeoutError())
        assert "timed out" in msg

    def test_default_fallback_includes_type_and_message(self):
        msg = _classify_error(ValueError("something else entirely"))
        assert "ValueError" in msg
        assert "something else entirely" in msg


# ============================================================
# _pubsub_roundtrip
# ============================================================


def _fake_ps(messages=None, *, subscribe_error=None):
    ps = MagicMock()
    ps.subscribe = AsyncMock(side_effect=subscribe_error) if subscribe_error else AsyncMock()
    ps.unsubscribe = AsyncMock()
    ps.aclose = AsyncMock()
    queue = list(messages or [])

    async def _get_message(**kwargs):
        if queue:
            return queue.pop(0)
        return None

    ps.get_message = AsyncMock(side_effect=_get_message)
    return ps


class TestPubsubRoundtrip:
    async def test_echo_received_reports_ok(self):
        client = MagicMock()
        ps = _fake_ps(messages=[{"type": "message", "data": "1"}])
        client.pubsub = MagicMock(return_value=ps)
        client.publish = AsyncMock()

        result = await _pubsub_roundtrip(client, timeout=2.0)

        assert result == {"ok": True}
        ps.unsubscribe.assert_awaited_once()
        ps.aclose.assert_awaited_once()

    async def test_no_echo_times_out_and_reports_not_ok(self):
        client = MagicMock()
        ps = _fake_ps(messages=[])  # never echoes
        client.pubsub = MagicMock(return_value=ps)
        client.publish = AsyncMock()

        result = await _pubsub_roundtrip(client, timeout=0.05)

        assert result["ok"] is False
        assert "no message echoed back" in result["error"]

    async def test_subscribe_exception_is_classified_and_swallowed(self):
        client = MagicMock()
        ps = _fake_ps(subscribe_error=ConnectionError("max connections exceeded for client"))
        client.pubsub = MagicMock(return_value=ps)
        client.publish = AsyncMock()

        result = await _pubsub_roundtrip(client, timeout=1.0)

        assert result["ok"] is False
        assert "connection/request limit hit" in result["error"]
        # cleanup still attempted despite the failure
        ps.unsubscribe.assert_awaited_once()
        ps.aclose.assert_awaited_once()

    async def test_cleanup_exceptions_are_swallowed(self):
        """unsubscribe/aclose raising must not propagate past the roundtrip."""
        client = MagicMock()
        ps = _fake_ps(messages=[{"type": "message", "data": "1"}])
        ps.unsubscribe = AsyncMock(side_effect=RuntimeError("already gone"))
        ps.aclose = AsyncMock(side_effect=RuntimeError("already closed"))
        client.pubsub = MagicMock(return_value=ps)
        client.publish = AsyncMock()

        result = await _pubsub_roundtrip(client, timeout=1.0)

        assert result == {"ok": True}


# ============================================================
# probe_redis_url — import-missing + successful ping/pubsub paths
# ============================================================


class TestProbeRedisUrlImportAndSuccess:
    async def test_redis_package_not_installed(self, monkeypatch: pytest.MonkeyPatch):
        """Force the `import redis.asyncio` inside probe_redis_url to fail."""
        monkeypatch.setitem(sys.modules, "redis.asyncio", None)
        try:
            res = await probe_redis_url("REDIS_URL", "rediss://h:6379")
        finally:
            monkeypatch.delitem(sys.modules, "redis.asyncio", raising=False)

        assert res["status"] == "error"
        assert res["error"] == "redis package not installed"

    async def test_successful_ping_and_pubsub_reports_ok(self):
        import redis.asyncio as real_redis_asyncio

        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        fake_client.aclose = AsyncMock()

        with patch.object(real_redis_asyncio, "from_url", return_value=fake_client):
            with patch.object(redis_diag, "_pubsub_roundtrip", AsyncMock(return_value={"ok": True})):
                res = await probe_redis_url("REDIS_URL", "rediss://user:pw@host.upstash.io:6379", timeout=1.0)

        assert res["status"] == "ok"
        assert res["pubsub"] == {"ok": True}
        assert "ping_ms" in res
        fake_client.aclose.assert_awaited_once()

    async def test_successful_ping_but_broken_pubsub_reports_degraded(self):
        import redis.asyncio as real_redis_asyncio

        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        fake_client.aclose = AsyncMock()

        with patch.object(real_redis_asyncio, "from_url", return_value=fake_client):
            with patch.object(
                redis_diag,
                "_pubsub_roundtrip",
                AsyncMock(return_value={"ok": False, "error": "no message echoed back"}),
            ):
                res = await probe_redis_url("REDIS_URL", "rediss://host.upstash.io:6379", timeout=1.0)

        assert res["status"] == "degraded"
        assert res["pubsub"]["ok"] is False


# ============================================================
# log_diagnosis — ok / degraded banner branches
# ============================================================


class TestLogDiagnosis:
    def test_ok_status_logs_info_banner(self):
        with patch.object(redis_diag.logger, "info") as mock_info:
            log_diagnosis(
                [
                    {
                        "label": "REDIS_URL",
                        "status": "ok",
                        "endpoint": "rediss://host:6379",
                        "ping_ms": 5.2,
                    }
                ]
            )
        joined = " ".join(str(c) for c in mock_info.call_args_list)
        assert "OK" in joined

    def test_degraded_status_logs_warning_banner(self):
        with patch.object(redis_diag.logger, "warning") as mock_warning:
            log_diagnosis(
                [
                    {
                        "label": "WS_REDIS_URL",
                        "status": "degraded",
                        "endpoint": "rediss://host:6379",
                        "ping_ms": 3.1,
                        "pubsub": {"ok": False, "error": "blocked"},
                    }
                ]
            )
        joined = " ".join(str(c) for c in mock_warning.call_args_list)
        assert "DEGRADED" in joined

    def test_error_status_and_warning_note_both_logged(self):
        with (
            patch.object(redis_diag.logger, "error") as mock_error,
            patch.object(redis_diag.logger, "warning") as mock_warning,
        ):
            log_diagnosis(
                [
                    {
                        "label": "REDIS_URL",
                        "status": "error",
                        "endpoint": "redis://host:6379",
                        "error": "boom",
                        "warning": "plaintext redis:// — Upstash requires rediss://",
                    }
                ]
            )
        mock_error.assert_called_once()
        mock_warning.assert_called_once()

    def test_shared_url_same_as_note_included(self):
        with patch.object(redis_diag.logger, "info") as mock_info:
            log_diagnosis(
                [
                    {"label": "A", "status": "ok", "endpoint": "rediss://h:6379", "ping_ms": 1.0},
                    {
                        "label": "B",
                        "status": "ok",
                        "endpoint": "rediss://h:6379",
                        "ping_ms": 1.0,
                        "same_as": "A",
                    },
                ]
            )
        joined = " ".join(str(c) for c in mock_info.call_args_list)
        assert "== A" in joined


# ============================================================
# diagnose_redis — sanity check the dedup path still round-trips real probes
# ============================================================


class TestDiagnoseRedisIntegration:
    async def test_mixed_unset_and_configured_urls(self):
        results = await diagnose_redis({"REDIS_URL": "", "WS_REDIS_URL": "https://x.upstash.io"})
        assert results[0]["status"] == "unset"
        assert results[1]["status"] == "error"
