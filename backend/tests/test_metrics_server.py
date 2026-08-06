"""Private metrics listener (backend/metrics_server.py).

Fly's Prometheus cannot send an auth header, so it cannot scrape the token-gated
public /metrics. This listener answers on a port absent from [http_service],
which fly-proxy therefore never routes — private by construction rather than by
authentication.

What these tests pin:
  1. Opt-in only — unset/invalid METRICS_PORT must leave everything unchanged.
  2. Only /metrics exists. Binding the main app to an unauthenticated port
     would expose every authenticated route.
  3. It never returns normally, because lifespan._restartable would busy-loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

import metrics_server


class TestPortResolution:
    def test_unset_disables(self, monkeypatch):
        monkeypatch.delenv("METRICS_PORT", raising=False)
        assert metrics_server.metrics_port() == 0

    def test_blank_disables(self, monkeypatch):
        monkeypatch.setenv("METRICS_PORT", "   ")
        assert metrics_server.metrics_port() == 0

    def test_valid_port_parsed(self, monkeypatch):
        monkeypatch.setenv("METRICS_PORT", "9091")
        assert metrics_server.metrics_port() == 9091

    @pytest.mark.parametrize("bad", ["not-a-number", "0", "-1", "70000"])
    def test_invalid_disables_rather_than_crashing(self, monkeypatch, bad):
        """A typo in a deploy env var must not stop the API booting."""
        monkeypatch.setenv("METRICS_PORT", bad)
        assert metrics_server.metrics_port() == 0


class TestMetricsApp:
    def test_serves_prometheus_exposition(self):
        client = TestClient(metrics_server.build_metrics_app())
        with patch("utils.scrape_gauges.refresh_all", AsyncMock()):
            resp = client.get("/metrics")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")

    def test_refreshes_gauges_like_the_public_endpoint(self):
        """Both endpoints must report identical freshness."""
        client = TestClient(metrics_server.build_metrics_app())
        refresh = AsyncMock()
        with patch("utils.scrape_gauges.refresh_all", refresh):
            client.get("/metrics")

        refresh.assert_awaited_once()

    def test_no_auth_required(self):
        """Safe only because the port is unroutable — see module docstring."""
        client = TestClient(metrics_server.build_metrics_app())
        with patch("utils.scrape_gauges.refresh_all", AsyncMock()):
            assert client.get("/metrics").status_code == 200

    def test_exposes_nothing_but_metrics(self):
        """The main API must not be reachable on the unauthenticated port."""
        app = metrics_server.build_metrics_app()
        paths = {r.path for r in app.routes}
        assert paths == {"/metrics"}

        client = TestClient(app)
        for path in ("/health", "/api/v1/rides", "/health/dependencies", "/"):
            assert client.get(path).status_code == 404, path

    def test_only_get_is_allowed(self):
        client = TestClient(metrics_server.build_metrics_app())
        with patch("utils.scrape_gauges.refresh_all", AsyncMock()):
            assert client.post("/metrics").status_code == 405


class TestNeverReturnsNormally:
    """lifespan._restartable is `while True: await coro_factory()`.

    A normal return re-invokes immediately, so a returning serve_metrics would
    busy-loop on a bind that is already known to fail.
    """

    @pytest.mark.anyio
    async def test_bind_failure_parks_instead_of_returning(self):
        class _Server:
            async def serve(self):
                raise OSError("address already in use")

        with patch("uvicorn.Server", return_value=_Server()), patch("uvicorn.Config"):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(metrics_server.serve_metrics(9091), timeout=0.2)

    @pytest.mark.anyio
    async def test_clean_exit_also_parks(self):
        class _Server:
            async def serve(self):
                return None

        with patch("uvicorn.Server", return_value=_Server()), patch("uvicorn.Config"):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(metrics_server.serve_metrics(9091), timeout=0.2)

    @pytest.mark.anyio
    async def test_unexpected_crash_propagates_for_restartable_backoff(self):
        """A real crash SHOULD restart — that is what _restartable is for."""

        class _Server:
            async def serve(self):
                raise RuntimeError("boom")

        with patch("uvicorn.Server", return_value=_Server()), patch("uvicorn.Config"):
            with pytest.raises(RuntimeError, match="boom"):
                await metrics_server.serve_metrics(9091)

    @pytest.mark.anyio
    async def test_cancellation_propagates_for_clean_shutdown(self):
        class _Server:
            async def serve(self):
                raise asyncio.CancelledError()

        with patch("uvicorn.Server", return_value=_Server()), patch("uvicorn.Config"):
            with pytest.raises(asyncio.CancelledError):
                await metrics_server.serve_metrics(9091)
