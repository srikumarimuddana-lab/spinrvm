"""P2: /metrics must fail closed in production without an auth token.

An unset METRICS_AUTH_TOKEN previously only logged a warning and served error
rates, traffic volume, and Redis internals to the public internet. It now
refuses the scrape (503) in production.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


def _req(headers=None, query=None):
    r = MagicMock()
    r.headers = headers or {}
    r.query_params = query or {}
    return r


@pytest.mark.anyio
class TestMetricsAuth:
    async def test_production_without_token_fails_closed(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value=""),
            patch.object(server.settings, "ENV", "production"),
            pytest.raises(HTTPException) as exc,
        ):
            await server.metrics(_req())
        assert exc.value.status_code == 503

    async def test_token_set_but_wrong_returns_401(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value="secret"),
            pytest.raises(HTTPException) as exc,
        ):
            await server.metrics(_req(headers={"authorization": "Bearer wrong"}))
        assert exc.value.status_code == 401

    async def test_correct_token_passes_auth(self):
        """A correct bearer token gets past the gate (render is stubbed)."""
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value="secret"),
            patch("backend.server.settings.ENV", "production"),
            patch("utils.metrics.render_prometheus", return_value="# metrics", create=True),
            patch("utils.redis_client.get_redis_stats", return_value={"connected": False}, create=True),
        ):
            # Should not raise the 503/401 auth errors.
            try:
                await server.metrics(_req(headers={"authorization": "Bearer secret"}))
            except HTTPException as e:
                assert e.status_code not in (401, 503), f"unexpected auth failure: {e.status_code}"

    async def test_non_production_without_token_does_not_fail_closed(self):
        from backend import server

        with (
            patch("backend.server._metrics_token", return_value=""),
            patch("backend.server.settings.ENV", "development"),
            patch("utils.metrics.render_prometheus", return_value="# metrics", create=True),
            patch("utils.redis_client.get_redis_stats", return_value={"connected": False}, create=True),
        ):
            try:
                await server.metrics(_req())
            except HTTPException as e:
                assert e.status_code != 503, "dev must not fail closed"
