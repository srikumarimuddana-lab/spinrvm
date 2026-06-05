"""Tests for the Redis connectivity diagnostic (Upstash debugging helper)."""

from __future__ import annotations

import pytest

from utils.redis_diag import diagnose_redis, probe_redis_url


@pytest.mark.anyio
async def test_unset_url_reported_as_unset():
    res = await probe_redis_url("REDIS_URL", "")
    assert res["status"] == "unset"
    assert res["configured"] is False


@pytest.mark.anyio
async def test_rest_url_rejected_before_dialing():
    """An Upstash REST URL (https) can't do pub/sub — flag it without a socket."""
    res = await probe_redis_url("WS_REDIS_URL", "https://my-db.upstash.io")
    assert res["status"] == "error"
    assert "REST" in res["error"]
    assert res["scheme"] == "https"
    # Never leak anything resembling the password — only scheme/host/port.
    assert res["endpoint"] == "https://my-db.upstash.io:?"


@pytest.mark.anyio
async def test_plaintext_redis_warns_about_tls():
    res = await probe_redis_url("REDIS_URL", "redis://h:6379", timeout=0.01)
    # Connection will fail/timeout in the test env, but the TLS warning must be
    # attached from the scheme check regardless of reachability.
    assert res.get("warning")
    assert "rediss://" in res["warning"]


@pytest.mark.anyio
async def test_diagnose_dedupes_shared_url():
    """Two labels pointing at the same URL should be marked as shared, not
    dialed twice (matters under Upstash connection caps)."""
    url = "https://same.upstash.io"
    results = await diagnose_redis({"RATE_LIMIT_REDIS_URL": url, "WS_REDIS_URL (effective)": url})
    assert len(results) == 2
    # Second occurrence references the first.
    assert results[1].get("same_as") == "RATE_LIMIT_REDIS_URL"


@pytest.mark.anyio
async def test_masked_endpoint_excludes_credentials():
    res = await probe_redis_url("REDIS_URL", "rediss://default:supersecret@host.upstash.io:6379", timeout=0.01)
    # endpoint is scheme://host:port only — credentials must never appear.
    assert "supersecret" not in str(res)
    assert res["endpoint"] == "rediss://host.upstash.io:6379"
