"""Third-party dependency health probes (utils/dependency_health.py).

The contract these pin, in priority order:
  1. Never raises — a vendor outage must not turn the health endpoint into a
     500, or the prober cannot tell "Stripe is down" from "Spinr is down".
  2. Never leaks — no credentials, hostnames, URLs, or raw upstream error text
     reach the response body (PIPEDA / CLAUDE.md logging rules).
  3. Cached — a 15s Prometheus agent plus a 30s prober must not stampede.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import dependency_health as dh

_MOD = "backend.utils.dependency_health"


@pytest.fixture(autouse=True)
def _clear_cache():
    dh.reset_cache()
    yield
    dh.reset_cache()


def _patch_all(supabase=None, redis=None, settings=None, firebase_json="{}"):
    """Patch every probe's upstream to a healthy default, overridable per test."""
    return (
        patch(
            f"{_MOD}.db_supabase.ping", AsyncMock(return_value=supabase or {"ping_ms": 12.0, "circuit_state": "closed"})
        ),
        patch(
            f"{_MOD}.get_redis_stats", AsyncMock(return_value=redis or {"connected": True, "used_memory_percent": 10})
        ),
        patch(
            f"{_MOD}.get_app_settings",
            AsyncMock(
                return_value=settings
                if settings is not None
                else {"stripe_secret_key": "sk_x", "twilio_account_sid": "AC_x", "google_maps_api_key": "AIza_x"}
            ),
        ),
        patch.dict("os.environ", {"FIREBASE_SERVICE_ACCOUNT_JSON": firebase_json}, clear=False),
    )


async def _probe(**kw):
    ctxs = _patch_all(**kw)
    for c in ctxs:
        c.__enter__()
    try:
        return await dh.probe_dependencies(force=True)
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)


class TestHappyPath:
    @pytest.mark.anyio
    async def test_all_healthy(self):
        res = await _probe()
        assert res["healthy"] is True
        deps = res["dependencies"]
        for name in ("supabase", "redis", "stripe", "twilio", "google_maps", "firebase"):
            assert deps[name]["status"] == dh.STATUS_OK, name
        assert deps["supabase"]["latency_ms"] == 12.0


class TestNeverRaises:
    @pytest.mark.anyio
    async def test_every_probe_exploding_still_returns(self):
        boom = AsyncMock(side_effect=RuntimeError("upstream exploded"))
        with (
            patch(f"{_MOD}.db_supabase.ping", boom),
            patch(f"{_MOD}.get_redis_stats", boom),
            patch(f"{_MOD}.get_app_settings", boom),
        ):
            res = await dh.probe_dependencies(force=True)

        assert res["healthy"] is False
        assert res["dependencies"]["supabase"]["status"] == dh.STATUS_DOWN
        assert res["dependencies"]["redis"]["status"] == dh.STATUS_DOWN

    @pytest.mark.anyio
    async def test_timeout_is_reported_as_down_not_hung(self):
        async def _never(*_a, **_k):
            await asyncio.sleep(60)

        with patch(f"{_MOD}._PROBE_TIMEOUT_SECONDS", 0.05), patch(f"{_MOD}.db_supabase.ping", _never):
            with (
                patch(f"{_MOD}.get_redis_stats", AsyncMock(return_value={"connected": True})),
                patch(f"{_MOD}.get_app_settings", AsyncMock(return_value={})),
            ):
                res = await dh.probe_dependencies(force=True)

        assert res["dependencies"]["supabase"] == {"status": dh.STATUS_DOWN, "reason": "timeout"}


class TestNeverLeaks:
    @pytest.mark.anyio
    async def test_upstream_error_text_never_reaches_the_body(self):
        secret = "postgres://user:hunter2@db.abcdef.supabase.co:5432/postgres"
        with (
            patch(f"{_MOD}.db_supabase.ping", AsyncMock(side_effect=RuntimeError(secret))),
            patch(f"{_MOD}.get_redis_stats", AsyncMock(side_effect=RuntimeError(secret))),
            patch(f"{_MOD}.get_app_settings", AsyncMock(side_effect=RuntimeError(secret))),
        ):
            res = await dh.probe_dependencies(force=True)

        blob = repr(res)
        assert "hunter2" not in blob
        assert "supabase.co" not in blob
        assert "postgres://" not in blob

    @pytest.mark.anyio
    async def test_credential_values_never_reach_the_body(self):
        # Canary values deliberately do NOT use real key prefixes (sk_live_,
        # AC..., AIza...). The pre-commit secret scanner cannot tell a test
        # fixture from a leaked credential and blocks on the prefix, which is
        # the correct behaviour -- and the prefix is irrelevant here anyway,
        # since what is under test is that the *value* never escapes.
        canary = "CANARY-MUST-NOT-APPEAR"
        res = await _probe(
            settings={
                "stripe_secret_key": canary,
                "twilio_account_sid": canary,
                "google_maps_api_key": canary,
            }
        )
        blob = repr(res)
        assert canary not in blob
        # Not even a length, which would leak key format.
        assert res["dependencies"]["stripe"] == {"status": dh.STATUS_OK}


class TestDegradedStates:
    @pytest.mark.anyio
    async def test_redis_fallback_is_degraded_not_ok(self):
        """The in-process fallback silently loses rate-limit and OTP state."""
        res = await _probe(redis={"connected": False})
        assert res["dependencies"]["redis"]["status"] == dh.STATUS_DEGRADED
        assert res["dependencies"]["redis"]["reason"] == "using_in_process_fallback"
        # Degraded must NOT flip overall health — the app still serves.
        assert res["healthy"] is True

    @pytest.mark.anyio
    async def test_redis_memory_pressure_is_degraded(self):
        res = await _probe(redis={"connected": True, "used_memory_percent": 94})
        assert res["dependencies"]["redis"]["status"] == dh.STATUS_DEGRADED
        assert res["dependencies"]["redis"]["reason"] == "memory_pressure"

    @pytest.mark.anyio
    async def test_open_db_circuit_is_down_even_when_ping_succeeds(self):
        res = await _probe(supabase={"ping_ms": 5.0, "circuit_state": "open"})
        assert res["dependencies"]["supabase"]["status"] == dh.STATUS_DOWN
        assert res["healthy"] is False

    @pytest.mark.anyio
    async def test_half_open_circuit_is_degraded(self):
        res = await _probe(supabase={"ping_ms": 5.0, "circuit_state": "half_open"})
        assert res["dependencies"]["supabase"]["status"] == dh.STATUS_DEGRADED
        assert res["healthy"] is True

    @pytest.mark.anyio
    async def test_missing_vendor_credentials_reported_but_not_unhealthy(self):
        """Otherwise every dev and staging environment pages continuously."""
        res = await _probe(settings={}, firebase_json="")
        deps = res["dependencies"]
        for name in ("stripe", "twilio", "google_maps", "firebase"):
            assert deps[name]["status"] == dh.STATUS_NOT_CONFIGURED, name
        assert res["healthy"] is True


class TestCaching:
    @pytest.mark.anyio
    async def test_second_call_within_ttl_does_not_reprobe(self):
        ping = AsyncMock(return_value={"ping_ms": 1.0, "circuit_state": "closed"})
        with (
            patch(f"{_MOD}.db_supabase.ping", ping),
            patch(f"{_MOD}.get_redis_stats", AsyncMock(return_value={"connected": True})),
            patch(f"{_MOD}.get_app_settings", AsyncMock(return_value={})),
        ):
            await dh.probe_dependencies(force=True)
            assert ping.await_count == 1
            await dh.probe_dependencies()
            await dh.probe_dependencies()
            assert ping.await_count == 1  # served from cache

            await dh.probe_dependencies(force=True)
            assert ping.await_count == 2


class TestGaugeMapping:
    def test_status_maps_to_gauge_value(self):
        assert dh.gauge_value(dh.STATUS_OK) == 1.0
        assert dh.gauge_value(dh.STATUS_DEGRADED) == 0.5
        assert dh.gauge_value(dh.STATUS_DOWN) == 0.0
        # not_configured cannot serve, so it is 0 like down; the distinction
        # lives in the `reason`, not the gauge.
        assert dh.gauge_value(dh.STATUS_NOT_CONFIGURED) == 0.0
        assert dh.gauge_value("nonsense") == 0.0
