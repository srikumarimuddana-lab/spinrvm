"""Coverage-closure tests for routes/drivers/subscriptions.py (A1c Sub-tier A).

Scoped separately from test_spinr_pass_subscription.py (which owns the
checkout/webhook/verify-session happy-path flows) to focus on the parts of
this module that were previously untested:

- _send_subscription_invoice_email (the HTML+PDF invoice mailer)
- _activate_subscription error/degrade branches (driver lookup failure, area
  timezone failure, prior-subscription cancel failure -> cancel_pending)
- resend_subscription_invoice endpoint
- check_expiring_subscriptions background loop (warn + enforce + cancel_pending
  retry), including its many best-effort except-and-continue branches
- assorted small error-handling branches in get_subscription_plans,
  get_current_subscription, _cancel_stripe_subscription,
  _compute_subscription_tax, and subscribe_to_plan

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md):
- `db_supabase` is a *module reference* shared by every importer, so
  `patch("backend.db_supabase.<fn>")` affects both `db_supabase.<fn>(...)`
  and `_deps.db.<fn>(...)` call sites everywhere in this file.
- `_deps.send_push_notification`, `_deps.manager`, `_deps.clear_presence`,
  `_deps.record_period_transition` are *bound names* copied into the _deps
  namespace at import time, so they must be patched at
  `backend.routes.drivers._deps.<name>`.
- `area_timezone` / `compute_pass_expiry` / `pass_duration_label` and
  `redis_set_nx` are imported *inside* the function bodies on every call
  (dual-import pattern), so patching the *source* module
  (`backend.utils.spinr_pass.<fn>` / `backend.utils.redis_client.redis_set_nx`)
  is sufficient and is what all these tests do.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


# ============================================================
# get_subscription_plans
# ============================================================


class TestGetSubscriptionPlansFreeMode:
    async def test_area_disabled_returns_free_mode_message(self):
        from backend.routes.drivers import get_subscription_plans

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {
                "drivers": [driver],
                "service_areas": [{"spinr_pass_enabled": False}],
            }.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_subscription_plans(current_user={"id": "u1"})

        assert result["plans"] == []
        assert result["free_mode"] is True
        assert "free" in result["message"].lower()

    async def test_filters_by_area_and_vehicle_type(self):
        """Normal (not-disabled) path: plans are filtered down to those whose
        service_areas/vehicle_types cover the driver."""
        from backend.routes.drivers import get_subscription_plans

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1", "vehicle_type_id": "vt-sedan"}
        plans = [
            {"id": "p-all-areas", "is_active": True, "service_areas": None, "vehicle_types": None},
            {"id": "p-wrong-area", "is_active": True, "service_areas": ["area-2"], "vehicle_types": None},
            {"id": "p-right-area", "is_active": True, "service_areas": ["area-1"], "vehicle_types": None},
            {"id": "p-empty-areas", "is_active": True, "service_areas": [], "vehicle_types": []},
            {"id": "p-wrong-vt", "is_active": True, "service_areas": None, "vehicle_types": ["vt-suv"]},
        ]

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": plans}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_subscription_plans(current_user={"id": "u1"})

        ids = {p["id"] for p in result["plans"]}
        assert ids == {"p-all-areas", "p-right-area", "p-empty-areas"}
        assert result["free_mode"] is False

    async def test_no_driver_profile_returns_unfiltered_plans(self):
        """A caller with no drivers row yet (edge case) still gets the raw
        active-plan list back rather than erroring."""
        from backend.routes.drivers import get_subscription_plans

        plans = [{"id": "p1", "is_active": True}]

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [], "subscription_plans": plans}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_subscription_plans(current_user={"id": "u-nodriver"})

        assert result["plans"] == plans


# ============================================================
# subscription_checkout_return (public https bounce)
# ============================================================


class TestSubscriptionCheckoutReturn:
    async def test_allowlisted_scheme_redirects_with_session_id(self):
        from backend.routes.drivers import subscription_checkout_return

        response = await subscription_checkout_return(session_id="cs_test_abc123", to="spinr-driver://subscription/success")
        assert response.status_code == 302
        assert response.headers["location"] == "spinr-driver://subscription/success?session_id=cs_test_abc123"

    async def test_disallowed_scheme_falls_back_to_default_target(self):
        from backend.routes.drivers import subscription_checkout_return

        response = await subscription_checkout_return(session_id="cs_x", to="https://evil.example/phish")
        assert response.status_code == 302
        assert response.headers["location"].startswith("spinr-driver://subscription/success")

    async def test_unsafe_session_id_is_dropped(self):
        from backend.routes.drivers import subscription_checkout_return

        response = await subscription_checkout_return(
            session_id="cs_x'; DROP TABLE--", to="spinr-driver://subscription/success"
        )
        assert response.status_code == 302
        # The unsafe session id is stripped -> no ?session_id= query param.
        assert "session_id=" not in response.headers["location"]

    async def test_no_session_id_no_query_string(self):
        from backend.routes.drivers import subscription_checkout_return

        response = await subscription_checkout_return(session_id="", to="exp://192.168.1.5:19000/--/success")
        assert response.status_code == 302
        assert response.headers["location"] == "exp://192.168.1.5:19000/--/success"


# ============================================================
# get_current_subscription
# ============================================================


class TestGetCurrentSubscriptionGaps:
    async def test_no_driver_profile_returns_no_subscription(self):
        from backend.routes.drivers import get_current_subscription

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await get_current_subscription(current_user={"id": "u-nodriver"})
        assert result == {"has_subscription": False, "subscription": None}

    async def test_no_active_subscription_row(self):
        from backend.routes.drivers import get_current_subscription

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [{"id": "d1"}], "driver_subscriptions": []}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_current_subscription(current_user={"id": "u1"})
        assert result == {"has_subscription": False, "subscription": None}

    async def test_past_expiry_flips_row_and_reports_expired(self):
        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1", "user_id": "u1"}
        sub = {"id": "sub-1", "driver_id": "d1", "status": "active", "expires_at": "2020-01-01T00:00:00+00:00"}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "driver_subscriptions": [sub]}.get(table, [])

        update_mock = AsyncMock()
        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            result = await get_current_subscription(current_user={"id": "u1"})

        assert result == {"has_subscription": False, "subscription": None, "expired": True}
        update_mock.assert_awaited_once_with("driver_subscriptions", {"id": "sub-1"}, {"status": "expired"})

    async def test_unparseable_expires_at_returns_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        sub = {"id": "sub-1", "driver_id": "d1", "status": "active", "expires_at": "not-a-real-timestamp"}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "driver_subscriptions": [sub]}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            with pytest.raises(HTTPException) as exc:
                await get_current_subscription(current_user={"id": "u1"})

        assert exc.value.status_code == 503

    async def test_area_timezone_lookup_failure_degrades_to_default(self):
        """A transient area-timezone lookup error must not 500 the display
        endpoint — it degrades to the Regina default (comment on line ~166)."""
        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "driver_subscriptions": [sub]}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.utils.spinr_pass.area_timezone", AsyncMock(side_effect=Exception("geo down"))),
        ):
            result = await get_current_subscription(current_user={"id": "u1"})

        assert result["has_subscription"] is True
        assert result["subscription"]["id"] == "sub-1"


# ============================================================
# _cancel_stripe_subscription: raise_on_error paths
# ============================================================


class TestCancelStripeSubscriptionRaiseOnError:
    async def test_raises_when_no_secret_and_raise_on_error(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})):
            with pytest.raises(RuntimeError, match="stripe_secret_key"):
                await _cancel_stripe_subscription("sub_123", raise_on_error=True)

    async def test_reraises_stripe_failure_when_raise_on_error(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.Subscription.delete", side_effect=Exception("stripe down")),
        ):
            with pytest.raises(Exception, match="stripe down"):
                await _cancel_stripe_subscription("sub_123", raise_on_error=True)


# ============================================================
# _compute_subscription_tax
# ============================================================


class TestComputeSubscriptionTax:
    async def test_no_driver_defaults_to_zero_tax_sk(self):
        from backend.routes.drivers import _compute_subscription_tax

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            result = await _compute_subscription_tax("driver-none", Decimal("49.99"))

        assert result["gst_amount"] == Decimal("0")
        assert result["pst_amount"] == Decimal("0")
        assert result["total"] == Decimal("49.99")
        assert result["province"] == "SK"

    async def test_area_config_disabled_zero_tax(self):
        from backend.routes.drivers import _compute_subscription_tax

        driver = {"id": "d1", "service_area_id": "area-1"}
        area = {"subscription_tax_config": {"enabled": False}}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "service_areas": area}.get(table)

        with patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)):
            result = await _compute_subscription_tax("d1", Decimal("100.00"))

        assert result["tax_total"] == Decimal("0")
        assert result["total"] == Decimal("100.00")

    async def test_full_gst_pst_hst_calc(self):
        from backend.routes.drivers import _compute_subscription_tax

        driver = {"id": "d1", "service_area_id": "area-1"}
        area = {
            "subscription_tax_config": {
                "enabled": True,
                "province": "SK",
                "gst_rate": 5,
                "pst_rate": 6,
                "hst_rate": 1,
            }
        }

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "service_areas": area}.get(table)

        with patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)):
            result = await _compute_subscription_tax("d1", Decimal("100.00"))

        assert result["subtotal"] == Decimal("100.00")
        assert result["gst_amount"] == Decimal("5.00")
        assert result["pst_amount"] == Decimal("6.00")
        assert result["hst_amount"] == Decimal("1.00")
        assert result["tax_total"] == Decimal("12.00")
        assert result["total"] == Decimal("112.00")
        assert result["province"] == "SK"


# ============================================================
# subscribe_to_plan: error / edge branches
# ============================================================


class TestSubscribeToPlanErrorBranches:
    async def test_driver_profile_not_found_404(self):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-1"})

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "user-nobody"})
        assert exc.value.status_code == 404
        assert "Driver" in exc.value.detail

    async def test_plan_not_found_404(self):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "does-not-exist"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": []}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 404
        assert "Plan" in exc.value.detail

    async def test_vehicle_type_mismatch_422(self):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-suv-only"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": "vt-sedan"}
        plan = {"id": "plan-suv-only", "is_active": True, "vehicle_types": ["vt-suv"]}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan]}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 422
        assert "vehicle" in exc.value.detail.lower()

    async def test_service_area_mismatch_no_covering_parent_422(self):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-area-x"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-b", "vehicle_type_id": None}
        plan = {"id": "plan-area-x", "is_active": True, "service_areas": ["area-a"]}
        driver_area = {"id": "area-b", "parent_service_area_id": None}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan]}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver_area)),
        ):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 422
        assert "service area" in exc.value.detail.lower()

    async def test_service_area_mismatch_but_parent_covers_proceeds(self, mock_settings=None):
        """A plan scoped to the PARENT area still covers a child service area."""
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-parent-scoped"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-child", "vehicle_type_id": None}
        plan = {
            "id": "plan-parent-scoped",
            "is_active": True,
            "service_areas": ["area-parent"],
            "name": "Parent Plan",
            "price": 0,
            "duration_days": 30,
            "rides_per_day": -1,
        }
        driver_area = {"id": "area-child", "parent_service_area_id": "area-parent"}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver_area)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        ):
            result = await subscribe_to_plan(request, {"id": "u1"})

        # No Stripe configured + free plan -> dev-mode immediate activation.
        assert result["success"] is True
        assert result["mode"] == "dev"

    async def test_service_area_mismatch_driver_has_no_area_at_all_422(self):
        """Driver has no service_area_id at all (not just a non-matching one) —
        the parent-area lookup is skipped entirely (_checkout_parent_id=None)."""
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-area-x"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {"id": "plan-area-x", "is_active": True, "service_areas": ["area-a"]}

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan]}.get(table, [])

        find_one_mock = AsyncMock()  # must never be called — no area to look up
        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.find_one", find_one_mock),
        ):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 422
        find_one_mock.assert_not_awaited()

    async def test_area_timezone_exception_falls_back_to_default_expiry(self, mock_driver=None):
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-free"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1", "vehicle_type_id": None}
        plan = {
            "id": "plan-free",
            "is_active": True,
            "name": "Free Plan",
            "price": 0,
            "duration_days": 7,
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
            patch("backend.utils.spinr_pass.area_timezone", AsyncMock(side_effect=Exception("geo down"))),
        ):
            result = await subscribe_to_plan(request, {"id": "u1"})

        assert result["success"] is True

    async def test_client_return_url_with_allowed_scheme_is_used(self, mock_driver=None):
        """A client-supplied success_url matching an allowed app scheme is used
        verbatim as the deep-link target (line ~466)."""
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(
            return_value={"plan_id": "plan-premium", "success_url": "exp://192.168.1.5:19000/--/subscription/success"}
        )
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-premium",
            "is_active": True,
            "name": "Premium Pass",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.checkout.Session.create") as mock_create,
        ):
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_x"
            mock_create.return_value = session

            await subscribe_to_plan(request, {"id": "u1"})

        call_kwargs = mock_create.call_args[1]
        from urllib.parse import quote

        assert quote("exp://192.168.1.5:19000/--/subscription/success", safe="") in call_kwargs["success_url"]

    async def test_price_retrieve_exception_returns_502(self):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-recurring"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-recurring",
            "is_active": True,
            "name": "Pro",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
            "stripe_price_id": "price_abc",
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.Price.retrieve", side_effect=Exception("stripe unreachable")),
        ):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 502

    async def test_generic_exception_in_checkout_block_returns_502(self):
        """An unexpected error anywhere in the Stripe checkout block (here:
        tax computation for the one-off flow) is caught by the broad handler
        and surfaced as a clean 502, not a raw 500."""
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-oneoff"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-oneoff",
            "is_active": True,
            "name": "One-off",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers.subscriptions._compute_subscription_tax",
                AsyncMock(side_effect=Exception("tax engine exploded")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})
        assert exc.value.status_code == 502

    async def test_stale_session_expire_failure_is_swallowed(self):
        """Failing to expire an already-completed/expired stale Checkout
        Session must not block superseding the DB row."""
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-premium",
            "is_active": True,
            "name": "Premium Pass",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            if table == "driver_subscriptions" and filters.get("status") == "pending":
                return [{"id": "stale-1", "stripe_session_id": "cs_stale"}]
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        update_mock = AsyncMock()
        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.checkout.Session.create") as mock_create,
            patch("stripe.checkout.Session.expire", side_effect=Exception("already completed")),
        ):
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_new"
            mock_create.return_value = session

            result = await subscribe_to_plan(request, {"id": "u1"})

        assert result.get("checkout_url")
        superseded = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[0] == "driver_subscriptions" and c.args[2].get("status") == "superseded"
        ]
        assert superseded

    async def test_race_loser_session_expire_failure_is_swallowed(self):
        """Insert conflict (409) path: failing to expire our own just-created
        session must not mask the 409."""
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-premium",
            "is_active": True,
            "name": "Premium Pass",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
        }

        def fake_get_rows(table, filters, columns=None, limit=None):
            if table == "driver_subscriptions" and filters.get("status") == "pending":
                return [{"id": "other-pending"}]
            return {"drivers": [driver], "subscription_plans": [plan], "driver_subscriptions": []}.get(table, [])

        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock(side_effect=Exception("unique violation"))),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.checkout.Session.create") as mock_create,
            patch("stripe.checkout.Session.expire", side_effect=Exception("already gone")),
        ):
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_new"
            mock_create.return_value = session

            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "u1"})

        assert exc.value.status_code == 409

    async def test_dev_mode_cancels_existing_active_subscription(self):
        """Dev/no-Stripe activation must cancel the driver's prior active pass
        and bump the subscriber count inline (no webhook to do it later)."""
        from backend.routes.drivers import subscribe_to_plan
        from fastapi import Request

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-free"})
        driver = {"id": "d1", "user_id": "u1", "service_area_id": None, "vehicle_type_id": None}
        plan = {
            "id": "plan-free",
            "is_active": True,
            "name": "Free Plan",
            "price": 0,
            "duration_days": 30,
            "rides_per_day": -1,
            "subscriber_count": 3,
        }
        existing = {"id": "old-sub", "stripe_subscription_id": None}

        def fake_get_rows(table, filters, columns=None, limit=None):
            if table == "driver_subscriptions":
                if filters.get("status") == "active":
                    return [existing]
                return []
            return {"drivers": [driver], "subscription_plans": [plan]}.get(table, [])

        cancel_mock = AsyncMock()
        update_mock = AsyncMock()
        with (
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", cancel_mock),
        ):
            result = await subscribe_to_plan(request, {"id": "u1"})

        assert result["mode"] == "dev"
        cancel_mock.assert_awaited_once_with(None)
        cancelled = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[0] == "driver_subscriptions" and c.args[1] == {"id": "old-sub"}
        ]
        assert cancelled


# ============================================================
# cancel_subscription: missing driver / missing sub
# ============================================================


class TestCancelSubscriptionMissingRows:
    async def test_no_driver_profile_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import cancel_subscription

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await cancel_subscription({"id": "u-nobody"})
        assert exc.value.status_code == 404

    async def test_no_active_subscription_400(self):
        from fastapi import HTTPException

        from backend.routes.drivers import cancel_subscription

        def fake_get_rows(table, filters, columns=None, limit=None):
            return {"drivers": [{"id": "d1"}], "driver_subscriptions": []}.get(table, [])

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            with pytest.raises(HTTPException) as exc:
                await cancel_subscription({"id": "u1"})
        assert exc.value.status_code == 400


# ============================================================
# get_subscription_payment_history: stored-tax-columns branch
# ============================================================


class TestPaymentHistoryStoredTaxColumns:
    async def test_uses_stored_subtotal_and_tax_columns_when_present(self):
        """Rows written after migration 186 carry their own subtotal/tax
        columns and must use them verbatim rather than back-computing."""
        from backend.routes.drivers import get_subscription_payment_history

        driver = {"id": "d1", "user_id": "u1"}
        payments = [
            {
                "id": "pay-1",
                "driver_id": "d1",
                "plan_id": "plan-1",
                "plan_name": "Premium Pass",
                "amount": "52.17",
                "subtotal": "47.00",
                "gst_amount": "2.35",
                "pst_amount": "2.82",
                "hst_amount": "0",
                "province": "SK",
                "currency": "cad",
                "billing_reason": "one_off",
                "stripe_invoice_id": None,
                "created_at": "2025-06-01T10:00:00+00:00",
            }
        ]

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], payments])),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u1"})

        row = result["payments"][0]
        assert row["subtotal"] == "47.00"
        assert row["gst_amount"] == "2.35"
        assert row["pst_amount"] == "2.82"


# ============================================================
# _send_subscription_invoice_email
# ============================================================


class TestSendSubscriptionInvoiceEmail:
    def _kwargs(self, **overrides):
        base = dict(
            driver_id="d1",
            plan_name="Premium Pass",
            duration_label="30 days",
            subtotal=Decimal("47.00"),
            gst_amount=Decimal("2.35"),
            pst_amount=Decimal("2.82"),
            hst_amount=Decimal("0.00"),
            tax_total=Decimal("5.17"),
            total=Decimal("52.17"),
            province="SK",
            billing_reason="one_off",
            payment_date="August 02, 2026",
        )
        base.update(overrides)
        return base

    async def test_returns_false_when_driver_not_found(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            result = await _send_subscription_invoice_email(**self._kwargs())
        assert result is False

    async def test_returns_false_when_user_has_no_email(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "first_name": "Sam", "last_name": "D", "email": ""}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "users": user}.get(table)

        with patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)):
            result = await _send_subscription_invoice_email(**self._kwargs())
        assert result is False

    async def test_success_generates_pdf_and_sends_email(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "first_name": "Sam", "last_name": "Driver", "email": "sam@example.com"}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "users": user}.get(table)

        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                return_value=b"%PDF-1.4 fake",
            ),
            patch("backend.utils.email_provider.send_transactional_email", send_mock),
        ):
            result = await _send_subscription_invoice_email(
                **self._kwargs(hst_amount=Decimal("1.00"), stripe_invoice_url="https://stripe.example/inv/1")
            )

        assert result is True
        send_mock.assert_awaited_once()
        call_kwargs = send_mock.call_args.kwargs
        assert call_kwargs["to"] == "sam@example.com"
        assert call_kwargs["attachments"] is not None
        assert call_kwargs["attachments"][0]["mime"] == "application/pdf"
        # HST tax row was included since hst_amount > 0
        assert "HST" in call_kwargs["html"]
        assert "stripe.example" in call_kwargs["html"]

    async def test_pdf_generation_failure_still_sends_html_only_email(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "first_name": "Sam", "last_name": "Driver", "email": "sam@example.com"}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "users": user}.get(table)

        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                side_effect=Exception("pdf lib broke"),
            ),
            patch("backend.utils.email_provider.send_transactional_email", send_mock),
        ):
            result = await _send_subscription_invoice_email(**self._kwargs())

        assert result is True
        assert send_mock.call_args.kwargs["attachments"] is None

    async def test_email_delivery_failure_returns_false(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "first_name": "Sam", "last_name": "Driver", "email": "sam@example.com"}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "users": user}.get(table)

        with (
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                return_value=b"%PDF-1.4 fake",
            ),
            patch("backend.utils.email_provider.send_transactional_email", AsyncMock(return_value=False)),
        ):
            result = await _send_subscription_invoice_email(**self._kwargs())

        assert result is False

    async def test_pct_label_guards_against_zero_or_negative_base(self):
        """A subtotal of zero (shouldn't normally happen, but tax could still
        be non-zero on a legacy/edge-case row) must not divide-by-zero when
        rendering the tax-row percentage label."""
        from backend.routes.drivers import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "first_name": "Sam", "last_name": "Driver", "email": "sam@example.com"}

        def fake_find_one(table, filters, **kw):
            return {"drivers": driver, "users": user}.get(table)

        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                return_value=b"%PDF-1.4 fake",
            ),
            patch("backend.utils.email_provider.send_transactional_email", send_mock),
        ):
            result = await _send_subscription_invoice_email(
                **self._kwargs(subtotal=Decimal("0"), gst_amount=Decimal("2.00"), total=Decimal("2.00"))
            )

        assert result is True
        html = send_mock.call_args.kwargs["html"]
        assert "GST" in html and "GST (" not in html  # no "(x.xx%)" suffix rendered

    async def test_unexpected_exception_is_caught_and_returns_false(self):
        from backend.routes.drivers import _send_subscription_invoice_email

        with patch("backend.db_supabase.find_one", AsyncMock(side_effect=RuntimeError("db exploded"))):
            result = await _send_subscription_invoice_email(**self._kwargs())
        assert result is False


# ============================================================
# _activate_subscription: degrade / failure branches
# ============================================================


class TestActivateSubscriptionDegradeBranches:
    async def test_driver_lookup_exception_continues_with_no_driver(self):
        """A DB error looking up the driver during activation must not abort
        activation (the driver already paid) — it degrades to no push/email."""
        from backend.routes.drivers import _activate_subscription

        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},  # sub lookup (no plan_id -> no plan lookup)
                Exception("db down"),  # drivers lookup raises
            ]
        )
        update_mock = AsyncMock(return_value={"id": "s1", "status": "active"})
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            # Should not raise despite the driver lookup blowing up.
            await _activate_subscription("s1", plan_id=None)

        # The atomic claim still ran.
        claims = [c for c in update_mock.await_args_list if c.args and c.args[1] == {"id": "s1", "status": "pending"}]
        assert claims

    async def test_area_timezone_exception_falls_back_to_default_expiry(self):
        from backend.routes.drivers import _activate_subscription

        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "p1", "duration_days": 7, "subscriber_count": 0},
                {"id": "d1", "service_area_id": "area-1"},
                # _compute_subscription_tax's own drivers lookup (its own
                # independent find_one call, not a reuse of the row above) —
                # no service_area_id -> short-circuits to zero tax/SK.
                {"id": "d1"},
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", AsyncMock(return_value={"id": "s1"})),
            patch("backend.utils.spinr_pass.area_timezone", AsyncMock(side_effect=Exception("geo down"))),
        ):
            await _activate_subscription("s1", "p1")
        # No exception -> the fallback path was exercised successfully.

    async def test_prior_subscription_cancel_failure_marks_cancel_pending(self):
        """If cancelling the driver's prior Stripe subscription fails during a
        plan-switch, the old row must be marked cancel_pending (not silently
        lost) rather than raising (the new pass is already paid for)."""
        from backend.routes.drivers import _activate_subscription

        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "p1", "duration_days": 30, "subscriber_count": 0},
                {"id": "d1"},
                # _compute_subscription_tax's own drivers lookup.
                {"id": "d1"},
            ]
        )
        existing_active = {"id": "old-sub", "driver_id": "d1", "stripe_subscription_id": "sub_old"}
        update_mock = AsyncMock(return_value={"id": "s1"})
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[existing_active, {"id": "s1"}])),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.routes.drivers.subscriptions._cancel_stripe_subscription",
                AsyncMock(side_effect=Exception("stripe unreachable")),
            ),
        ):
            await _activate_subscription("s1", "p1")

        cancel_pending_calls = [
            c
            for c in update_mock.await_args_list
            if c.args
            and c.args[0] == "driver_subscriptions"
            and c.args[1] == {"id": "old-sub"}
            and c.args[2].get("$set", {}).get("status") == "cancel_pending"
        ]
        assert cancel_pending_calls

    async def test_full_happy_path_records_ledger_swallows_push_failure_sends_invoice(self):
        """One combined happy-path test exercising: prior-sub cancel success,
        subscriber count bump, one-off ledger recording, push-notification
        failure swallow, and the invoice email call for a one-off plan."""
        from backend.routes.drivers import _activate_subscription

        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1", "plan_name": "Pro", "stripe_session_id": "cs1"},
                {"id": "p1", "duration_days": 30, "price": 49.99, "subscriber_count": 2, "name": "Pro Pass"},
                {"id": "d1", "user_id": "u1"},
                # _compute_subscription_tax's own drivers lookup.
                {"id": "d1"},
            ]
        )
        existing_active = {"id": "old-sub", "driver_id": "d1", "stripe_subscription_id": "sub_old"}
        update_mock = AsyncMock(return_value={"id": "s1"})
        record_mock = AsyncMock()
        email_mock = AsyncMock()
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[existing_active, {"id": "s1"}])),
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", email_mock),
            patch(
                "backend.routes.drivers._deps.send_push_notification",
                AsyncMock(side_effect=Exception("push provider down")),
            ),
        ):
            await _activate_subscription("s1", "p1", "payment")

        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["billing_reason"] == "one_off"
        email_mock.assert_awaited_once()
        assert email_mock.await_args.kwargs["plan_name"] == "Pro Pass"


# ============================================================
# resend_subscription_invoice
# ============================================================


class TestResendSubscriptionInvoice:
    async def test_no_driver_profile_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-1", current_user={"id": "u-nobody"})
        assert exc.value.status_code == 404

    async def test_payment_not_found_or_not_owned_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "d1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value={"id": "pay-1", "driver_id": "OTHER"})),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-1", current_user={"id": "u1"})
        assert exc.value.status_code == 404

    async def test_legacy_row_without_tax_columns_resends_successfully(self):
        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-1",
            "driver_id": "d1",
            "plan_id": "p1",
            "plan_name": "Legacy Pass",
            "amount": "29.99",
            "billing_reason": "one_off",
            "created_at": "2025-01-01T00:00:00+00:00",
            # no "subtotal" -> legacy row path
        }
        plan = {"id": "p1", "duration_days": 30}

        def fake_find_one(table, filters, **kw):
            return {"subscription_payments": payment, "subscription_plans": plan}.get(table)

        email_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "d1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", email_mock),
        ):
            result = await resend_subscription_invoice("pay-1", current_user={"id": "u1"})

        assert result == {"success": True}
        call_kwargs = email_mock.await_args.kwargs
        assert call_kwargs["subtotal"] == call_kwargs["total"]
        assert call_kwargs["gst_amount"] == Decimal("0.00")
        assert call_kwargs["invoice_number"] == "SPX-PAY-1".upper() or call_kwargs["invoice_number"].startswith("SPX-")

    async def test_unparseable_created_at_falls_back_to_now(self):
        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-2",
            "driver_id": "d1",
            "plan_id": None,
            "plan_name": "Legacy Pass",
            "amount": "10.00",
            "billing_reason": "one_off",
            "created_at": "not-a-real-date",
        }

        def fake_find_one(table, filters, **kw):
            return {"subscription_payments": payment}.get(table)

        email_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "d1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", email_mock),
        ):
            result = await resend_subscription_invoice("pay-2", current_user={"id": "u1"})

        assert result == {"success": True}

    async def test_delivery_failure_returns_502(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-3",
            "driver_id": "d1",
            "plan_id": None,
            "plan_name": "Pass",
            "amount": "10.00",
            "subtotal": "9.50",
            "gst_amount": "0.25",
            "pst_amount": "0.25",
            "hst_amount": "0",
            "tax_total": "0.50",
            "province": "SK",
            "billing_reason": "one_off",
            "created_at": "2025-01-01T00:00:00+00:00",
        }

        def fake_find_one(table, filters, **kw):
            return {"subscription_payments": payment}.get(table)

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "d1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch(
                "backend.routes.drivers.subscriptions._send_subscription_invoice_email",
                AsyncMock(return_value=False),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-3", current_user={"id": "u1"})

        assert exc.value.status_code == 502


# ============================================================
# _record_subscription_payment: ledger error handling
# ============================================================


class TestRecordSubscriptionPaymentErrorHandling:
    async def test_duplicate_insert_is_swallowed_as_debug(self):
        from backend.routes.drivers import _record_subscription_payment

        with patch(
            "backend.db_supabase.insert_one",
            AsyncMock(side_effect=Exception("duplicate key value violates unique constraint")),
        ):
            result = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=Decimal("49.99"),
                billing_reason="subscription_cycle",
                stripe_invoice_id="in_123",
            )
        assert result is None

    async def test_non_duplicate_insert_failure_is_logged_as_error(self):
        from backend.routes.drivers import _record_subscription_payment

        with patch("backend.db_supabase.insert_one", AsyncMock(side_effect=Exception("network timeout"))):
            result = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=Decimal("49.99"),
                billing_reason="one_off",
            )
        assert result is None

    async def test_stripe_invoice_url_column_written_when_present(self):
        from backend.routes.drivers import _record_subscription_payment

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            row_id = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=Decimal("49.99"),
                billing_reason="subscription_cycle",
                stripe_invoice_url="https://stripe.example/inv/9",
            )
        assert row_id is not None
        row = insert_mock.await_args.args[1]
        assert row["stripe_invoice_url"] == "https://stripe.example/inv/9"


# ============================================================
# check_expiring_subscriptions: background loop
# ============================================================


def _stop_sleep():
    """asyncio.sleep is patched to blow up so the `while True:` loop exits
    after exactly one iteration; the surrounding test unwraps the sentinel
    exception. Matches the convention already used in
    test_spinr_pass_subscription.py::TestExpiryWarning3Day."""
    return AsyncMock(side_effect=Exception("stop"))


async def _run_once(**extra_patches):
    from backend.routes import drivers as drv

    patches = {
        "backend.routes.drivers._deps.asyncio.sleep": _stop_sleep(),
        "backend.utils.redis_client.redis_set_nx": AsyncMock(return_value=True),
        "backend.settings_loader.get_app_settings": AsyncMock(return_value={"require_driver_subscription": False}),
        "backend.db_supabase.get_rows": AsyncMock(return_value=[]),
        "backend.db_supabase.find_one": AsyncMock(return_value=None),
        "backend.db_supabase.update_one": AsyncMock(),
        "backend.db_supabase.insert_one": AsyncMock(),
    }
    patches.update(extra_patches)

    import contextlib

    with contextlib.ExitStack() as stack:
        mocks = {}
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, mock_obj))
            mocks[target] = mock_obj
        try:
            await drv.check_expiring_subscriptions()
        except Exception as e:
            if "stop" not in str(e):
                raise
    return mocks


class TestCheckExpiringSubscriptionsLockAndSweep:
    async def test_lock_not_acquired_skips_all_processing(self):
        get_rows_mock = AsyncMock(return_value=[])
        await _run_once(
            **{
                "backend.utils.redis_client.redis_set_nx": AsyncMock(return_value=False),
                "backend.db_supabase.get_rows": get_rows_mock,
            }
        )
        get_rows_mock.assert_not_awaited()

    async def test_lock_not_acquired_continues_to_next_iteration(self):
        """Covers the `continue` after the lock-not-acquired sleep: the first
        sleep call returns normally (letting the loop go around once more)
        and the second call is used as the stop sentinel."""
        get_rows_mock = AsyncMock(return_value=[])
        sleep_mock = AsyncMock(side_effect=[None, Exception("stop")])
        await _run_once(
            **{
                "backend.routes.drivers._deps.asyncio.sleep": sleep_mock,
                "backend.utils.redis_client.redis_set_nx": AsyncMock(return_value=False),
                "backend.db_supabase.get_rows": get_rows_mock,
            }
        )
        assert sleep_mock.await_count == 2
        get_rows_mock.assert_not_awaited()

    async def test_cancel_pending_sweep_success_marks_cancelled(self):
        pending_row = {"id": "pc1", "stripe_subscription_id": "sub_x"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "cancel_pending":
                return [pending_row]
            return []

        update_mock = AsyncMock()
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": update_mock,
                "backend.routes.drivers.subscriptions._cancel_stripe_subscription": AsyncMock(),
            }
        )
        resolved = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[1] == {"id": "pc1"} and c.args[2].get("$set", {}).get("status") == "cancelled"
        ]
        assert resolved

    async def test_cancel_pending_sweep_failure_leaves_row_pending(self):
        pending_row = {"id": "pc1", "stripe_subscription_id": "sub_x"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "cancel_pending":
                return [pending_row]
            return []

        update_mock = AsyncMock()
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": update_mock,
                "backend.routes.drivers.subscriptions._cancel_stripe_subscription": AsyncMock(
                    side_effect=Exception("stripe still unreachable")
                ),
            }
        )
        resolved = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[1] == {"id": "pc1"} and c.args[2].get("$set", {}).get("status") == "cancelled"
        ]
        assert not resolved

    async def test_cancel_pending_query_exception_is_caught_and_sweep_skipped(self):
        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "cancel_pending":
                raise Exception("db down")
            return []

        # Must not raise despite the sweep query failing.
        await _run_once(**{"backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows)})

    async def test_get_app_settings_exception_defaults_require_sub_false(self):
        expired_sub = {"id": "sub-1", "driver_id": "d1", "expires_at": "2020-01-01T00:00:00+00:00"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "active":
                return [expired_sub]
            return []

        update_mock = AsyncMock()
        find_mock = AsyncMock()  # should never be called for "drivers" (require_sub defaults False -> continue)
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": update_mock,
                "backend.db_supabase.find_one": find_mock,
                "backend.settings_loader.get_app_settings": AsyncMock(side_effect=Exception("settings down")),
            }
        )
        marked_expired = [c for c in update_mock.await_args_list if c.args and c.args[2] == {"status": "expired"}]
        assert marked_expired
        find_mock.assert_not_awaited()

    async def test_mark_expired_update_exception_is_caught_and_continues(self):
        expired_sub = {"id": "sub-1", "driver_id": "d1", "expires_at": "2020-01-01T00:00:00+00:00"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "active":
                return [expired_sub]
            return []

        async def fake_update_one(table, filters, updates, **kw):
            if table == "driver_subscriptions" and updates == {"status": "expired"}:
                raise Exception("write failed")
            return {}

        # Must not raise despite the mark-expired write failing.
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": AsyncMock(side_effect=fake_update_one),
            }
        )


class TestCheckExpiringSubscriptionsMainLoopBranches:
    async def test_skip_branches_and_full_enforcement_happy_path(self):
        """Combines several independent per-row branches of the main
        active_subs loop into a single run (each row hits exactly one
        branch, so they don't interact):
          - no expires_at -> skipped
          - unparseable expires_at string -> skipped
          - already expiry_warned -> skipped
          - expired + require_sub True + driver already offline -> skip after mark-expired
          - expired + require_sub True + driver online -> full enforcement
          - non-string (naive datetime) expires_at handled without crashing
          - within-24h window, not yet warned -> warning push + flag set
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        subs = [
            {"id": "no-exp", "driver_id": "d-no-exp", "expires_at": None},
            {"id": "bad-date", "driver_id": "d-bad-date", "expires_at": "not-a-date"},
            {
                "id": "already-warned",
                "driver_id": "d-warned",
                "expires_at": (now + timedelta(hours=5)).isoformat(),
                "expiry_warned": True,
            },
            {
                "id": "expired-offline",
                "driver_id": "d-offline",
                "expires_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "expired-online",
                "driver_id": "d-online",
                "plan_name": "Premium Pass",
                "expires_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "naive-datetime",
                "driver_id": "d-naive",
                "expires_at": now.replace(tzinfo=None) - timedelta(days=100),
            },
            {
                # A naive (no-offset) ISO string, as opposed to the `datetime`
                # object above — exercises the isinstance(str)+tzinfo-None
                # branch specifically (as distinct from the non-string branch).
                "id": "naive-string-date",
                "driver_id": "d-naive-str",
                "expires_at": (now.replace(tzinfo=None) - timedelta(days=200)).isoformat(),
            },
            {
                "id": "warn-24h",
                "driver_id": "d-warn24",
                "plan_name": "Weekly Pass",
                "expires_at": (now + timedelta(hours=10)).isoformat(),
                "expiry_warned": False,
            },
        ]
        drivers_by_id = {
            "d-offline": {"id": "d-offline", "user_id": "u-offline", "is_online": False},
            "d-online": {"id": "d-online", "user_id": "u-online", "is_online": True},
            "d-warn24": {"id": "d-warn24", "user_id": "u-warn24"},
        }

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions":
                if filters.get("status") == "cancel_pending":
                    return []
                if filters.get("status") == "active":
                    return subs
                return []
            return []

        def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return drivers_by_id.get(filters.get("id"))
            return None

        manager_mock = MagicMock()
        manager_mock.broadcast_to_admins = AsyncMock()
        manager_mock.disconnect = MagicMock()

        update_mock = AsyncMock()
        push_mock = AsyncMock()

        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.find_one": AsyncMock(side_effect=fake_find_one),
                "backend.db_supabase.update_one": update_mock,
                "backend.db_supabase.insert_one": AsyncMock(),
                "backend.settings_loader.get_app_settings": AsyncMock(
                    return_value={"require_driver_subscription": True}
                ),
                "backend.routes.drivers._deps.manager": manager_mock,
                "backend.routes.drivers._deps.clear_presence": AsyncMock(),
                "backend.routes.drivers._deps.record_period_transition": AsyncMock(),
                "backend.routes.drivers._deps.send_push_notification": push_mock,
            }
        )

        # expired-offline: only marked expired, driver never flipped.
        expired_calls = [c for c in update_mock.await_args_list if c.args and c.args[2] == {"status": "expired"}]
        expired_ids = {c.args[1]["id"] for c in expired_calls}
        assert {"expired-offline", "expired-online"} <= expired_ids

        # expired-online: driver flipped offline.
        offline_flip = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[0] == "drivers" and c.args[1] == {"id": "d-online"} and c.args[2].get("is_online") is False
        ]
        assert offline_flip
        manager_mock.disconnect.assert_any_call("driver_u-online")
        manager_mock.broadcast_to_admins.assert_awaited()

        # 24h warning: flag set + push fired for d-warn24 only (not d-offline/d-online).
        warn_flag_calls = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[1] == {"id": "warn-24h"} and c.args[2].get("$set", {}).get("expiry_warned") is True
        ]
        assert warn_flag_calls
        push_titles = [str(c.args[1]) for c in push_mock.await_args_list]
        assert any("Expiring Soon" in t for t in push_titles)

        # Skipped rows never got an expired/warn write.
        no_exp_touch = [c for c in update_mock.await_args_list if c.args and c.args[1] == {"id": "no-exp"}]
        bad_date_touch = [c for c in update_mock.await_args_list if c.args and c.args[1] == {"id": "bad-date"}]
        warned_touch = [c for c in update_mock.await_args_list if c.args and c.args[1] == {"id": "already-warned"}]
        assert not no_exp_touch
        assert not bad_date_touch
        assert not warned_touch

    async def test_enforcement_side_effects_all_swallow_errors(self):
        """Every best-effort side effect in the online-driver enforcement path
        (clear_presence, activity-log insert, push, admin broadcast) can fail
        independently without aborting the sweep or crashing the loop."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_name": "Premium Pass",
            "expires_at": (now - timedelta(hours=1)).isoformat(),
        }
        driver = {"id": "d1", "user_id": "u1", "is_online": True}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "active":
                return [sub]
            return []

        def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            return None

        manager_mock = MagicMock()
        manager_mock.broadcast_to_admins = AsyncMock(side_effect=Exception("broadcast down"))
        manager_mock.disconnect = MagicMock()

        # Must complete without raising despite every side effect failing.
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.find_one": AsyncMock(side_effect=fake_find_one),
                "backend.db_supabase.update_one": AsyncMock(),
                "backend.db_supabase.insert_one": AsyncMock(side_effect=Exception("activity log insert failed")),
                "backend.settings_loader.get_app_settings": AsyncMock(
                    return_value={"require_driver_subscription": True}
                ),
                "backend.routes.drivers._deps.manager": manager_mock,
                "backend.routes.drivers._deps.clear_presence": AsyncMock(side_effect=Exception("presence down")),
                "backend.routes.drivers._deps.record_period_transition": AsyncMock(),
                "backend.routes.drivers._deps.send_push_notification": AsyncMock(
                    side_effect=Exception("push down")
                ),
            }
        )
        manager_mock.disconnect.assert_any_call("driver_u1")

    async def test_driver_offline_flip_failure_logged_and_skipped(self):
        """If flipping the driver's `is_online` row fails, enforcement must
        stop for that driver (no period-transition/push/etc) but not crash
        the sweep."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        sub = {"id": "sub-1", "driver_id": "d1", "expires_at": (now - timedelta(hours=1)).isoformat()}
        driver = {"id": "d1", "user_id": "u1", "is_online": True}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "active":
                return [sub]
            return []

        def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            return None

        async def fake_update_one(table, filters, updates, **kw):
            if table == "drivers":
                raise Exception("write conflict")
            return {}

        period_mock = AsyncMock()
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.find_one": AsyncMock(side_effect=fake_find_one),
                "backend.db_supabase.update_one": AsyncMock(side_effect=fake_update_one),
                "backend.settings_loader.get_app_settings": AsyncMock(
                    return_value={"require_driver_subscription": True}
                ),
                "backend.routes.drivers._deps.record_period_transition": period_mock,
            }
        )
        # Enforcement aborted before the insurance-period transition ran.
        period_mock.assert_not_awaited()

    async def test_24h_warning_push_failure_is_swallowed_flag_still_set(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_name": "Premium Pass",
            "expires_at": (now + timedelta(hours=5)).isoformat(),
            "expiry_warned": False,
        }
        driver = {"id": "d1", "user_id": "u1"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and filters.get("status") == "active":
                return [sub]
            return []

        def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            return None

        update_mock = AsyncMock()
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.find_one": AsyncMock(side_effect=fake_find_one),
                "backend.db_supabase.update_one": update_mock,
                "backend.routes.drivers._deps.send_push_notification": AsyncMock(
                    side_effect=Exception("push provider down")
                ),
            }
        )
        warn_flag_calls = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[1] == {"id": "sub-1"} and c.args[2].get("$set", {}).get("expiry_warned") is True
        ]
        assert warn_flag_calls


class TestCheckExpiringSubscriptions3DayWarning:
    async def test_missing_or_unparseable_expiry_rows_are_skipped(self):
        from datetime import datetime, timedelta, timezone

        naive_str = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)).isoformat()
        subs_3d = [
            {"id": "3d-no-exp", "driver_id": "d1", "expires_at": None},
            {"id": "3d-bad-date", "driver_id": "d1", "expires_at": "not-a-date"},
            # Naive (no-offset) ISO string — exercises the tzinfo-None
            # normalization branch; claim will still be attempted (returns
            # None here, i.e. not claimed, so no push either).
            {"id": "3d-naive", "driver_id": "d1", "expires_at": naive_str},
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and "$and" in (filters or {}):
                return subs_3d
            return []

        update_mock = AsyncMock(return_value=None)
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": update_mock,
            }
        )
        # Only the naive-date row reaches the atomic-claim update_one call
        # (the other two `continue` before ever calling update_one).
        assert all(c.args[1].get("id") == "3d-naive" for c in update_mock.await_args_list if c.args)

    async def test_lost_claim_race_is_skipped(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        sub_3d = {
            "id": "3d-1",
            "driver_id": "d1",
            "expires_at": (now + timedelta(days=2)).isoformat(),
        }

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and "$and" in (filters or {}):
                return [sub_3d]
            return []

        find_mock = AsyncMock()  # driver lookup must never happen — claim was lost
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.update_one": AsyncMock(return_value=None),  # lost the atomic claim
                "backend.db_supabase.find_one": find_mock,
            }
        )
        find_mock.assert_not_awaited()

    async def test_push_failure_swallowed_flag_already_claimed(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        sub_3d = {
            "id": "3d-1",
            "driver_id": "d1",
            "plan_name": "Premium Pass",
            "expires_at": (now + timedelta(days=2)).isoformat(),
        }
        driver = {"id": "d1", "user_id": "u1"}

        def fake_get_rows(table, filters, **kw):
            if table == "driver_subscriptions" and "$and" in (filters or {}):
                return [sub_3d]
            return []

        def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            return None

        update_mock = AsyncMock(return_value={"id": "3d-1"})
        await _run_once(
            **{
                "backend.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows),
                "backend.db_supabase.find_one": AsyncMock(side_effect=fake_find_one),
                "backend.db_supabase.update_one": update_mock,
                "backend.routes.drivers._deps.send_push_notification": AsyncMock(
                    side_effect=Exception("push down")
                ),
            }
        )
        claimed = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[1].get("id") == "3d-1" and c.args[2].get("$set", {}).get("expiry_warned_3d") is True
        ]
        assert claimed
