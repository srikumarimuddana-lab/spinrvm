"""/health/dependencies endpoint and spinr_dependency_up gauge exposition.

Two things matter here beyond "does it return JSON":

  1. **Plain /health must not change.** It is the Fly/Railway liveness check;
     altering its shape or its auth posture would break rolling deploys.
  2. **The new endpoint must be auth-gated and fail closed in production.**
     "Which vendor is down" is operational intelligence — an open endpoint
     announcing that Stripe is unreachable tells an attacker exactly when
     payment retries are failing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

_HEALTHY = {
    "healthy": True,
    "dependencies": {
        "supabase": {"status": "ok", "latency_ms": 9.0},
        "redis": {"status": "degraded", "reason": "using_in_process_fallback"},
        "stripe": {"status": "ok"},
        "twilio": {"status": "not_configured", "reason": "missing_credentials"},
    },
}

_UNHEALTHY = {
    "healthy": False,
    "dependencies": {"supabase": {"status": "down", "reason": "circuit_open"}},
}


@pytest.fixture
def client():
    import server

    return TestClient(server.app)


def _probe(result):
    return patch("utils.dependency_health.probe_dependencies", AsyncMock(return_value=result))


class TestAuthGate:
    def test_unauthenticated_allowed_outside_production(self, client, monkeypatch):
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        with _probe(_HEALTHY):
            assert client.get("/health/dependencies").status_code == 200

    def test_fails_closed_in_production_without_token(self, client, monkeypatch):
        import server

        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(server.settings, "ENV", "production", raising=False)
        with _probe(_HEALTHY):
            resp = client.get("/health/dependencies")
        # 503, not 200 — an unset token in production must never serve this.
        assert resp.status_code == 503

    def test_wrong_token_rejected(self, client, monkeypatch):
        monkeypatch.setenv("METRICS_AUTH_TOKEN", "right-token")
        with _probe(_HEALTHY):
            resp = client.get("/health/dependencies", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_correct_bearer_token_accepted(self, client, monkeypatch):
        monkeypatch.setenv("METRICS_AUTH_TOKEN", "right-token")
        with _probe(_HEALTHY):
            resp = client.get("/health/dependencies", headers={"Authorization": "Bearer right-token"})
        assert resp.status_code == 200


class TestResponse:
    def test_degraded_dependency_still_returns_200(self, client, monkeypatch):
        """Degraded means serving. A prober must not page for a Redis fallback."""
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        with _probe(_HEALTHY):
            resp = client.get("/health/dependencies")
        assert resp.status_code == 200
        assert resp.json()["dependencies"]["redis"]["status"] == "degraded"

    def test_down_dependency_returns_503(self, client, monkeypatch):
        """So a prober can alert on status code alone, without parsing a body."""
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        with _probe(_UNHEALTHY):
            resp = client.get("/health/dependencies")
        assert resp.status_code == 503
        assert resp.json()["healthy"] is False


class TestPlainHealthUnchanged:
    def test_plain_health_needs_no_token_and_keeps_its_shape(self, client, monkeypatch):
        """Regression: /health is the platform liveness check — do not gate it."""
        monkeypatch.setenv("METRICS_AUTH_TOKEN", "some-token")
        with patch("server._db_ready", AsyncMock(return_value=(True, {"ping_ms": 3.0}))):
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["db"]["status"] == "ok"
        assert "dependencies" not in body


class TestGaugeExposition:
    def test_metrics_exposes_dependency_up_gauge(self, client, monkeypatch):
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        with _probe(_HEALTHY):
            body = client.get("/metrics").text

        assert "# TYPE spinr_dependency_up gauge" in body
        assert 'spinr_dependency_up{dependency="supabase"} 1.0' in body
        assert 'spinr_dependency_up{dependency="redis"} 0.5' in body
        # not_configured maps to 0 — it genuinely cannot serve.
        assert 'spinr_dependency_up{dependency="twilio"} 0.0' in body

    def test_metrics_still_served_when_dependency_probe_fails(self, client, monkeypatch):
        """A broken probe must degrade the scrape, not empty it."""
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)
        with patch(
            "utils.dependency_health.probe_dependencies",
            AsyncMock(side_effect=RuntimeError("probe exploded")),
        ):
            resp = client.get("/metrics")

        assert resp.status_code == 200
        # Other series are still present.
        assert "spinr_" in resp.text

    def test_metrics_auth_gate_still_enforced_after_refactor(self, client, monkeypatch):
        """The gate moved into a shared helper — pin that it did not weaken."""
        monkeypatch.setenv("METRICS_AUTH_TOKEN", "right-token")
        with _probe(_HEALTHY):
            assert client.get("/metrics").status_code == 401
            assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get("/metrics", headers={"Authorization": "Bearer right-token"}).status_code == 200
            # Query-param form is supported for agents that cannot set headers.
            assert client.get("/metrics?token=right-token").status_code == 200
