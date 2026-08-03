"""Coverage gap-closer for backend/server.py (app factory / router mounting).

Test-only, additive: no application code in server.py is touched. This file
exercises the pieces of server.py that are pure runtime logic reachable
without re-importing the module (the app singleton is already built by
conftest.py's preload) — `_db_ready`'s cache/success/failure branches, the
`/health` endpoint's healthy/unhealthy responses, the real `_metrics_token()`
env-var reader, `/metrics`'s Redis-connected gauge block and its own
exception-swallow branch, and `DeprecatedRootPathMiddleware`'s root-prefix
(non-`/api/`) deprecated-path branch that `test_deprecated_route_admin_exempt.py`
does not reach.

Not attempted here (documented, not silently skipped): the Sentry-init
module-level block (`if sentry_dsn: ...`, roughly lines 469-538) and the
`if __name__ == "__main__":` entrypoint only run at *import* time, before any
test can patch them — reproducing them would require reloading `backend.server`
mid-suite, which risks re-registering routes/middleware on the single shared
`app` instance and corrupting every other test that imports `backend.server.app`
in the same session. Same class of "structurally near-impossible to reach
through this harness" already accepted elsewhere in this backlog for
dual-import `ImportError` fallback lines.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse, Response

from backend import server


def _req(headers=None, query=None):
    r = MagicMock()
    r.headers = headers or {}
    r.query_params = query or {}
    return r


@pytest.fixture(autouse=True)
def _reset_health_cache():
    """_health_cache is module-global mutable state; isolate each test."""
    original = dict(server._health_cache)
    server._health_cache.update(at=0.0, ok=False, detail={})
    yield
    server._health_cache.clear()
    server._health_cache.update(original)


# --------------------------------------------------------------------------- #
# _db_ready
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
class TestDbReady:
    async def test_cache_hit_skips_ping(self):
        server._health_cache.update(at=time.monotonic(), ok=True, detail={"ping_ms": 5})
        with patch("db_supabase.ping", AsyncMock()) as ping_fn:
            ok, detail = await server._db_ready()
        assert (ok, detail) == (True, {"ping_ms": 5})
        ping_fn.assert_not_awaited()

    async def test_fresh_ping_success_filters_to_safe_fields(self):
        server._health_cache.update(at=0.0, ok=False, detail={})
        with patch(
            "db_supabase.ping",
            AsyncMock(return_value={"ping_ms": 12, "circuit_state": "closed", "secret": "nope"}),
        ):
            ok, detail = await server._db_ready()
        assert ok is True
        assert detail == {"ping_ms": 12, "circuit_state": "closed"}
        assert "secret" not in detail
        # Result is cached for subsequent calls within the TTL.
        assert server._health_cache["ok"] is True

    async def test_fresh_ping_non_dict_result_yields_empty_detail(self):
        server._health_cache.update(at=0.0, ok=False, detail={})
        with patch("db_supabase.ping", AsyncMock(return_value="not-a-dict")):
            ok, detail = await server._db_ready()
        assert ok is True
        assert detail == {}

    async def test_ping_raises_marks_unready(self):
        server._health_cache.update(at=0.0, ok=False, detail={})
        with patch("db_supabase.ping", AsyncMock(side_effect=RuntimeError("db down"))):
            ok, detail = await server._db_ready()
        assert (ok, detail) == (False, {})

    async def test_ping_timeout_marks_unready(self):
        server._health_cache.update(at=0.0, ok=False, detail={})

        async def _hangs():
            await asyncio.sleep(10)

        with (
            patch("db_supabase.ping", _hangs),
            patch("backend.server._HEALTH_PING_TIMEOUT", 0.01),
        ):
            ok, detail = await server._db_ready()
        assert (ok, detail) == (False, {})


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
class TestHealthEndpoint:
    async def test_healthy_returns_200_shape(self):
        with patch("backend.server._db_ready", AsyncMock(return_value=(True, {"ping_ms": 3}))):
            result = await server.health()
        assert result == {"status": "healthy", "db": {"status": "ok", "ping_ms": 3}}

    async def test_unhealthy_returns_503(self):
        with patch("backend.server._db_ready", AsyncMock(return_value=(False, {}))):
            result = await server.health()
        assert isinstance(result, JSONResponse)
        assert result.status_code == 503


# --------------------------------------------------------------------------- #
# _metrics_token — the real env-var reader (other tests always mock it out)
# --------------------------------------------------------------------------- #


class TestMetricsTokenReal:
    def test_empty_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        assert server._metrics_token() == ""

    def test_strips_whitespace_from_env(self, monkeypatch):
        monkeypatch.setenv("METRICS_AUTH_TOKEN", "  secret-token  ")
        assert server._metrics_token() == "secret-token"


# --------------------------------------------------------------------------- #
# /metrics — Redis-connected gauge block + its exception-swallow branch
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
class TestMetricsRedisGauges:
    async def test_redis_connected_sets_all_gauges(self):
        stats = {
            "connected": True,
            "used_memory_bytes": 1000,
            "maxmemory_bytes": 2000,
            "used_memory_percent": 50.0,
            "total_keys": 42,
            "connected_clients": 3,
            "uptime_seconds": 999,
            "keyspace_hits_total": 10,
            "keyspace_misses_total": 1,
            "evicted_keys_total": 0,
            "expired_keys_total": 0,
        }
        gauges: dict = {}
        with (
            patch("backend.server._metrics_token", return_value=""),
            patch("backend.server.settings.ENV", "development"),
            patch("utils.redis_client.get_redis_stats", AsyncMock(return_value=stats), create=True),
            patch(
                "utils.metrics.set_gauge",
                side_effect=lambda name, val, *a, **k: gauges.update({name: val}),
                create=True,
            ),
            patch("utils.metrics.render_prometheus", return_value="# ok", create=True),
        ):
            resp = await server.metrics(_req())
        assert isinstance(resp, Response)
        assert gauges["spinr_redis_connected"] == 1
        assert gauges["spinr_redis_used_memory_bytes"] == 1000
        assert gauges["spinr_redis_used_memory_percent"] == 50.0
        assert gauges["spinr_redis_total_keys"] == 42

    async def test_redis_connected_with_missing_optional_fields_defaults_to_zero(self):
        """used_memory_percent omitted (None) must skip that one gauge but
        still set the rest via the `or 0` defaults."""
        stats = {"connected": True}
        gauges: dict = {}
        with (
            patch("backend.server._metrics_token", return_value=""),
            patch("backend.server.settings.ENV", "development"),
            patch("utils.redis_client.get_redis_stats", AsyncMock(return_value=stats), create=True),
            patch(
                "utils.metrics.set_gauge",
                side_effect=lambda name, val, *a, **k: gauges.update({name: val}),
                create=True,
            ),
            patch("utils.metrics.render_prometheus", return_value="# ok", create=True),
        ):
            await server.metrics(_req())
        assert gauges["spinr_redis_used_memory_bytes"] == 0
        assert "spinr_redis_used_memory_percent" not in gauges

    async def test_get_redis_stats_raising_is_swallowed_and_marks_disconnected(self):
        gauges: dict = {}
        with (
            patch("backend.server._metrics_token", return_value=""),
            patch("backend.server.settings.ENV", "development"),
            patch("utils.redis_client.get_redis_stats", AsyncMock(side_effect=RuntimeError("redis down")), create=True),
            patch(
                "utils.metrics.set_gauge",
                side_effect=lambda name, val, *a, **k: gauges.update({name: val}),
                create=True,
            ),
            patch("utils.metrics.render_prometheus", return_value="# ok", create=True),
        ):
            resp = await server.metrics(_req())
        assert isinstance(resp, Response)
        assert gauges["spinr_redis_connected"] == 0

    async def test_query_param_token_accepted_when_no_auth_header(self):
        with (
            patch("backend.server._metrics_token", return_value="secret"),
            patch("utils.redis_client.get_redis_stats", AsyncMock(return_value={"connected": False}), create=True),
            patch("utils.metrics.render_prometheus", return_value="# ok", create=True),
        ):
            resp = await server.metrics(_req(query={"token": "secret"}))
        assert isinstance(resp, Response)

    async def test_query_param_wrong_token_rejected(self):
        with patch("backend.server._metrics_token", return_value="secret"):
            with pytest.raises(HTTPException) as exc:
                await server.metrics(_req(query={"token": "nope"}))
        assert exc.value.status_code == 401


# --------------------------------------------------------------------------- #
# DeprecatedRootPathMiddleware — root-prefix (non-/api/) deprecated branch
# --------------------------------------------------------------------------- #


class _FakeURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeRequest:
    def __init__(self, path: str) -> None:
        self.url = _FakeURL(path)


async def _call_next(_request):
    return Response(status_code=200)


def _dispatch(path: str) -> Response:
    mw = server.DeprecatedRootPathMiddleware(app=lambda scope, receive, send: None)
    return asyncio.run(mw.dispatch(_FakeRequest(path), _call_next))


class TestDeprecatedRootPrefixBranch:
    def test_settings_root_path_flagged_with_v1_prefix_canonical(self, caplog):
        """/settings/... is a _DEPRECATED_ROOT_PREFIXES hit (not /api/), so the
        canonical-path derivation takes the `"/api/v1" + path` branch (line 102),
        distinct from the /api/-prefixed branch already covered elsewhere."""
        resp = _dispatch("/settings/legal")
        assert resp.headers.get("X-Spinr-Deprecated") == "true"

    def test_company_info_root_path_flagged(self):
        resp = _dispatch("/company-info")
        assert resp.headers.get("X-Spinr-Deprecated") == "true"

    def test_canonical_v1_path_not_flagged(self):
        resp = _dispatch("/api/v1/rides")
        assert "X-Spinr-Deprecated" not in resp.headers

    def test_unrelated_path_not_flagged(self):
        resp = _dispatch("/healthz")
        assert "X-Spinr-Deprecated" not in resp.headers
