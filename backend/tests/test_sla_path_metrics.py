"""Unit tests for the SLA-path observability metrics (CLAUDE.md Performance
SLAs / ACTION_ITEMS.md N5, OBS-002, A3-004):

- spinr_drivers_location_write_duration_ms  (routes/drivers/location.py)
- spinr_auth_token_refresh_duration_ms      (routes/auth.py)
- spinr_payments_webhook_duration_ms{event_type=...} (routes/webhooks.py)
- spinr_maps_budget_spent_ratio (gauge)     (utils/maps_budget.py)

Different modules under test resolve `backend.utils.metrics` via different
import paths (some tests in this repo import route modules as
`backend.routes.X`, others as top-level `routes.X` — see each module's dual
try/except ImportError block), which can leave two distinct module objects
in sys.modules, each with its own metrics registry. The helpers below
iterate every metrics module object that is actually importable so
assertions don't depend on guessing which one a given code path bound.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request as StarletteRequest


def _iter_metrics_modules():
    mods = {}
    for qualname in ("backend.utils.metrics", "utils.metrics"):
        try:
            mod = importlib.import_module(qualname)
        except ImportError:
            continue
        mods[id(mod)] = mod
    return list(mods.values())


def _clear_metrics_state():
    for mod in _iter_metrics_modules():
        with mod._lock:
            mod._histograms.clear()
            mod._gauges.clear()


def _render_all() -> str:
    return "\n".join(mod.render_prometheus() for mod in _iter_metrics_modules())


def _hist_count(out: str, name: str, label_substr: str | None = None) -> int:
    total = 0
    prefix = f"{name}_count{{"
    for line in out.splitlines():
        if line.startswith(prefix) and (label_substr is None or label_substr in line):
            total += int(line.rsplit(" ", 1)[1])
    return total


def _gauge_value(out: str, name: str) -> float | None:
    prefix = f"{name}{{"
    for line in out.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    return None


@pytest.fixture(autouse=True)
def _clean_metrics():
    _clear_metrics_state()
    yield
    _clear_metrics_state()


# ---------------------------------------------------------------------------
# spinr_drivers_location_write_duration_ms
# ---------------------------------------------------------------------------


class TestDriverLocationWriteDurationMetric:
    async def test_success_path_records_histogram(self, monkeypatch):
        from routes.drivers import location

        async def rows(table, filters, **kwargs):
            return [{"id": "driver-1", "is_online": True}] if table == "drivers" else []

        monkeypatch.setattr(location.db_supabase, "get_rows", rows)
        monkeypatch.setattr("settings_loader.get_app_settings", AsyncMock(return_value={}))
        monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
        monkeypatch.setattr(location._deps, "mark_present", AsyncMock())

        point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
        result = await location.update_live_location(
            point, BackgroundTasks(), current_user={"id": "user-1"}, token_session_id="session-1"
        )
        assert result["accepted"] is True

        out = _render_all()
        assert "spinr_drivers_location_write_duration_ms" in out
        assert _hist_count(out, "spinr_drivers_location_write_duration_ms") >= 1

    async def test_exception_path_still_records(self, monkeypatch):
        """try/finally must record even when the handler raises (no driver profile -> 403)."""
        from routes.drivers import location

        monkeypatch.setattr(location.db_supabase, "get_rows", AsyncMock(return_value=[]))
        monkeypatch.setattr("settings_loader.get_app_settings", AsyncMock(return_value={}))
        monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())

        point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
        with pytest.raises(HTTPException) as exc:
            await location.update_live_location(
                point, BackgroundTasks(), current_user={"id": "user-1"}, token_session_id="session-1"
            )
        assert exc.value.status_code == 403

        out = _render_all()
        assert _hist_count(out, "spinr_drivers_location_write_duration_ms") >= 1


# ---------------------------------------------------------------------------
# spinr_auth_token_refresh_duration_ms
# ---------------------------------------------------------------------------


def _refresh_request(refresh_token: str = "old-refresh-raw") -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "query_string": b"",
            "headers": [(b"cookie", f"refresh_token={refresh_token}".encode())],
        }
    )


class TestAuthTokenRefreshDurationMetric:
    async def test_success_path_records_histogram(self):
        from backend.routes import auth as auth_mod

        refresh_expires = datetime.now(timezone.utc) + timedelta(days=30)
        refresh_row = {
            "id": "rtk-row-001",
            "user_id": "user-sla-1",
            "audience": "rider",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        user_row = {
            "id": "user-sla-1",
            "phone": "+15551234567",
            "role": "rider",
            "profile_complete": True,
            "token_version": 0,
            "current_session_id": "sess-abc",
        }

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=refresh_row)),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=user_row)),
            patch.object(
                auth_mod,
                "issue_refresh_token",
                AsyncMock(return_value=("new-refresh-raw", "hashed", refresh_expires)),
            ),
            patch.object(auth_mod, "create_jwt_token", return_value="new-access-token"),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-refresh-raw"

            result = await auth_mod.refresh_access_token(request=_refresh_request(), response=MagicMock(), body=_Body())

        assert result.token == "new-access-token"

        out = _render_all()
        assert "spinr_auth_token_refresh_duration_ms" in out
        assert _hist_count(out, "spinr_auth_token_refresh_duration_ms") >= 1

    async def test_failure_path_still_records(self):
        """try/finally in metrics.timed must record even on a 401 (invalid token)."""
        from fastapi import HTTPException as FastAPIHTTPException

        from backend.routes import auth as auth_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=None)),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "bad-or-revoked-token"

            with pytest.raises((FastAPIHTTPException, SpinrException)):
                await auth_mod.refresh_access_token(request=_refresh_request(), response=MagicMock(), body=_Body())

        out = _render_all()
        assert _hist_count(out, "spinr_auth_token_refresh_duration_ms") >= 1


# ---------------------------------------------------------------------------
# spinr_payments_webhook_duration_ms{event_type=...}
# ---------------------------------------------------------------------------


def _make_stripe_event(event_type: str, data_object: dict, event_id: str = "evt_sla_test_1") -> dict:
    return {"id": event_id, "type": event_type, "data": {"object": data_object}}


class TestPaymentsWebhookDurationMetric:
    async def test_early_failure_before_event_type_known_labels_other(self):
        """Missing webhook secret -> 500 before the payload is ever parsed;
        no event_type is known yet, so the series must fall back to "other"
        (never an unbounded/empty label)."""
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {}

        with patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings):
            with pytest.raises(Exception) as exc:
                await wh.stripe_webhook(request=req)
        assert exc.value.status_code == 500

        out = _render_all()
        assert "spinr_payments_webhook_duration_ms" in out
        assert _hist_count(out, "spinr_payments_webhook_duration_ms", 'event_type="other"') >= 1

    async def test_known_event_type_is_used_as_the_label(self):
        from backend.routes import webhooks as wh

        event = _make_stripe_event("payment_intent.succeeded", {})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: event.get(k, d)
        event_obj.to_dict_recursive = lambda: event

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "whsec_test", "stripe_secret_key": "sk_test"}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "t=123,v1=sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=False)),
        ):
            result = await wh.stripe_webhook(request=req)

        assert result["duplicate"] is True

        out = _render_all()
        assert _hist_count(out, "spinr_payments_webhook_duration_ms", 'event_type="payment_intent.succeeded"') >= 1


# ---------------------------------------------------------------------------
# spinr_maps_budget_spent_ratio (gauge)
# ---------------------------------------------------------------------------


class TestMapsBudgetSpentRatioGauge:
    async def test_gauge_reflects_spent_over_budget(self):
        from backend.utils import maps_budget as mb

        with (
            patch.object(mb, "estimate_today_usd", AsyncMock(return_value=2.5)),
            patch.object(mb, "_daily_budget_usd", return_value=5.0),
        ):
            allowed, spent, budget = await mb.check_budget()

        assert allowed is True
        out = _render_all()
        assert _gauge_value(out, "spinr_maps_budget_spent_ratio") == pytest.approx(spent / budget)

    async def test_gauge_is_zero_when_budget_unset(self):
        from backend.utils import maps_budget as mb

        with (
            patch.object(mb, "estimate_today_usd", AsyncMock(return_value=0.0)),
            patch.object(mb, "_daily_budget_usd", return_value=0.0),
        ):
            await mb.check_budget()

        out = _render_all()
        assert _gauge_value(out, "spinr_maps_budget_spent_ratio") == 0.0

    async def test_reserve_budget_lua_path_also_records_gauge(self):
        from backend.utils import maps_budget as mb

        eval_mock = AsyncMock(return_value=[1, "1.00"])
        with (
            patch.object(mb, "redis_eval", eval_mock),
            patch.object(mb, "_daily_budget_usd", return_value=4.0),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert allowed is True
        out = _render_all()
        assert _gauge_value(out, "spinr_maps_budget_spent_ratio") == pytest.approx(spent / budget)
