"""Rate-limit enforcement + Sentry/metrics instrumentation tests for
routes/admin/compliance.py — gaps G5 (no Sentry/metrics) and G6 (no rate
limiting) from reports/audits/2026-07-28-compliance-reporting-module-
lifecycle-audit-v1.md.

Mirrors test_promo_rate_limit.py's minimal-app-with-real-limiter strategy:
the global conftest fixture disables `default_limiter` for most tests so
they don't trip rate limits incidentally, so these tests locally re-enable
it and reset its storage.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded


def _counter_value(name: str, labels: dict) -> int:
    from utils import metrics

    key = metrics._labels_to_key(labels)
    return metrics.snapshot()["counters"].get(name, {}).get(key, 0)


_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_RIDE_ROW = {
    "id": "r1",
    "ride_completed_at": "2026-07-05T10:00:00Z",
    "tax_breakdown": {"GST": {"amount": 5.0}},
}


def _build_compliance_app() -> FastAPI:
    """Minimal FastAPI app with only the compliance router and the real
    slowapi limiter wired — same approach as test_promo_rate_limit.py."""
    from dependencies import get_admin_user
    from routes.admin.compliance import api_router as compliance_router
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(compliance_router)

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    return app


@pytest.fixture(autouse=True)
def _enable_real_limiter():
    from utils.rate_limiter import default_limiter

    default_limiter.enabled = True
    inner = getattr(default_limiter, "_limiter", None)
    storage = getattr(inner, "storage", None) if inner is not None else None
    if storage is not None and callable(getattr(storage, "reset", None)):
        result = storage.reset()
        if result is not None:
            asyncio.run(result)
    yield
    default_limiter.enabled = False


def _client() -> TestClient:
    return TestClient(_build_compliance_app(), raise_server_exceptions=False)


class TestGstPstRemittanceRateLimit:
    """gst-pst-remittance is limited to 10/minute (matches other admin
    report/export-weight endpoints in routes/admin/rides.py)."""

    def test_under_limit_never_429s(self):
        client = _client()
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_RIDE_ROW])),
            patch("db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        ):
            for i in range(10):
                r = client.get("/compliance/gst-pst-remittance")
                assert r.status_code != 429, f"Request {i + 1}/10 got 429 too early. Body: {r.text}"

    def test_eleventh_request_is_429(self):
        client = _client()
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_RIDE_ROW])),
            patch("db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        ):
            for _ in range(10):
                client.get("/compliance/gst-pst-remittance")
            r = client.get("/compliance/gst-pst-remittance")

        assert r.status_code == 429, f"Expected 429 on 11th request, got {r.status_code}. Body: {r.text}"
        assert r.json().get("error") == "rate_limit_exceeded"
        assert "Retry-After" in r.headers


class TestInsurancePeriodAuditRateLimit:
    def test_eleventh_request_is_429(self):
        client = _client()
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        ):
            for _ in range(10):
                client.get("/compliance/insurance-period-audit")
            r = client.get("/compliance/insurance-period-audit")

        assert r.status_code == 429


class TestComplianceExportInstrumentation:
    """G5: Sentry capture (domain='admin') and the
    spinr_admin_compliance_export_total{report_type,outcome} counter."""

    def test_metrics_counter_increments_on_success(self):
        client = _client()
        before = _counter_value(
            "spinr_admin_compliance_export_total", {"report_type": "gst_pst_remittance", "outcome": "success"}
        )
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_RIDE_ROW])),
            patch("db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        ):
            resp = client.get("/compliance/gst-pst-remittance")
        assert resp.status_code == 200
        after = _counter_value(
            "spinr_admin_compliance_export_total", {"report_type": "gst_pst_remittance", "outcome": "success"}
        )
        assert after == before + 1

    def test_metrics_counter_increments_on_error_and_sentry_is_captured(self):
        client = _client()
        before = _counter_value(
            "spinr_admin_compliance_export_total", {"report_type": "gst_pst_remittance", "outcome": "error"}
        )
        fake_sentry = MagicMock()
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
            patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        ):
            resp = client.get("/compliance/gst-pst-remittance")
        assert resp.status_code == 503
        after = _counter_value(
            "spinr_admin_compliance_export_total", {"report_type": "gst_pst_remittance", "outcome": "error"}
        )
        assert after == before + 1
        fake_sentry.capture_exception.assert_called_once()
        scope = fake_sentry.push_scope.return_value.__enter__.return_value
        scope.set_tag.assert_any_call("domain", "admin")
        scope.set_tag.assert_any_call("report_type", "gst_pst_remittance")
