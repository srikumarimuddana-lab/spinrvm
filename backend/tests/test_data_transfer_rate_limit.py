"""Rate-limit enforcement tests for the Data Transfer admin routes — gap H3
from reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md
(only the export route was rate-limited; import/jobs/search had none, and
import/commit is an unthrottled bulk-write path).

Mirrors test_promo_rate_limit.py / test_compliance_rate_limit.py's
minimal-app-with-real-limiter strategy: the global conftest fixture
disables `default_limiter` for most tests so they don't trip rate limits
incidentally, so these tests locally re-enable it and reset its storage.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}


def _build_app(router_module_path: str, router_attr: str = "router") -> FastAPI:
    import importlib

    from dependencies import get_admin_user
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    mod = importlib.import_module(router_module_path)
    router = getattr(mod, router_attr)

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(router)
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


class TestImportCommitRateLimit:
    """import/commit is the tightest limit (10/hour) — it's a bulk-write
    path (creates users/drivers rows), unlike the read-only routes."""

    def _client(self):
        return TestClient(_build_app("routes.admin.data_transfer_import"), raise_server_exceptions=False)

    def test_eleventh_request_is_429(self):
        client = self._client()
        with patch(
            "services.data_transfer.entity_import_service.parse_bundle_zip",
            side_effect=Exception("not a real zip"),
        ):
            for _ in range(10):
                client.post(
                    "/data-transfer/import/commit",
                    files={"bundle_zip": ("b.zip", b"not a zip", "application/zip")},
                )
            r = client.post(
                "/data-transfer/import/commit",
                files={"bundle_zip": ("b.zip", b"not a zip", "application/zip")},
            )
        assert r.status_code == 429, f"Expected 429 on 11th request, got {r.status_code}. Body: {r.text}"
        assert r.json().get("error") == "rate_limit_exceeded"


class TestImportValidateRateLimit:
    """import/validate is read-only (dry-run) — a looser limit (30/hour)."""

    def _client(self):
        return TestClient(_build_app("routes.admin.data_transfer_import"), raise_server_exceptions=False)

    def test_thirtieth_request_still_succeeds_shape(self):
        client = self._client()
        with patch(
            "services.data_transfer.entity_import_service.parse_bundle_zip",
            side_effect=Exception("not a real zip"),
        ):
            for i in range(30):
                r = client.post(
                    "/data-transfer/import/validate",
                    files={"bundle_zip": ("b.zip", b"not a zip", "application/zip")},
                )
                assert r.status_code != 429, f"Request {i + 1}/30 got 429 too early. Body: {r.text}"

    def test_thirty_first_request_is_429(self):
        client = self._client()
        with patch(
            "services.data_transfer.entity_import_service.parse_bundle_zip",
            side_effect=Exception("not a real zip"),
        ):
            for _ in range(30):
                client.post(
                    "/data-transfer/import/validate",
                    files={"bundle_zip": ("b.zip", b"not a zip", "application/zip")},
                )
            r = client.post(
                "/data-transfer/import/validate",
                files={"bundle_zip": ("b.zip", b"not a zip", "application/zip")},
            )
        assert r.status_code == 429


class TestJobsListRateLimit:
    def _client(self):
        return TestClient(_build_app("routes.admin.data_transfer_jobs"), raise_server_exceptions=False)

    def test_sixty_first_request_is_429(self):
        client = self._client()
        with patch("db_supabase.get_rows", AsyncMock(return_value=[])):
            for _ in range(60):
                client.get("/data-transfer/jobs")
            r = client.get("/data-transfer/jobs")
        assert r.status_code == 429


class TestSearchRateLimit:
    def _client(self):
        return TestClient(_build_app("routes.admin.data_transfer_search"), raise_server_exceptions=False)

    def test_sixty_first_request_is_429(self):
        client = self._client()
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            for _ in range(60):
                client.get("/data-transfer/search")
            r = client.get("/data-transfer/search")
        assert r.status_code == 429
