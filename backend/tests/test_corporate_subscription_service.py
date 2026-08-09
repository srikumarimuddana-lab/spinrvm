# backend/tests/test_corporate_subscription_service.py
"""Covers corporate_subscription_service.py — flat SaaS subscription billing
for corporate accounts (business decision: full Stripe Subscriptions
automation, corporate + admin portal review round 2).

See docs/change-log for the corresponding Change Impact Log entries. Stripe
Subscription objects own the recurring-charge schedule; this service only
creates/cancels them and persists the resulting state — renewal/dunning sync
is covered separately in test_webhooks_corporate_subscription.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import corporate_subscription_service as svc

_SETTINGS = {"stripe_secret_key": "sk_test_123"}


def _company(**extra):
    return {
        "id": "c1",
        "name": "Acme Co",
        "billing_email": "billing@acme.example",
        "legal_name": "Acme Co Ltd",
        "stripe_customer_id": "cus_1",
        **extra,
    }


def _plan(**extra):
    return {
        "id": "plan_pro",
        "name": "Pro",
        "monthly_price": "199.00",
        "stripe_price_id": "price_123",
        "is_active": True,
        **extra,
    }


def _stripe_subscription(sub_id="sub_1", period_end=1_800_000_000):
    obj = MagicMock()
    obj.id = sub_id
    obj.current_period_end = period_end
    return obj


class TestAssignSubscription:
    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_happy_path_creates_subscription_and_persists_row(self):
        created_rows = []

        async def _capture_create(row):
            created_rows.append(row)
            return row

        with (
            patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=_company())),
            patch.object(svc.db_supabase, "get_corporate_subscription_plan", AsyncMock(return_value=_plan())),
            patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=None)),
            patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
            patch.object(svc.db_supabase, "get_default_payment_method", AsyncMock(return_value="pm_1")),
            patch.object(svc.stripe.Subscription, "create", return_value=_stripe_subscription()) as mock_create,
            patch.object(svc.db_supabase, "create_corporate_subscription_row", AsyncMock(side_effect=_capture_create)),
            patch.object(svc, "log_admin_action", AsyncMock(return_value="audit-1")) as mock_audit,
        ):
            row = await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

        assert row["status"] == "active"
        assert row["price"] == "199.00"
        assert row["stripe_subscription_id"] == "sub_1"
        assert row["company_id"] == "c1"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["customer"] == "cus_1"
        assert call_kwargs["items"] == [{"price": "price_123"}]
        assert call_kwargs["idempotency_key"] == "corp-sub-create-c1"
        mock_audit.assert_awaited_once()
        assert created_rows and created_rows[0]["plan_id"] == "plan_pro"

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_unknown_company_rejected(self):
        with patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=None)):
            with pytest.raises(svc.CorporateSubscriptionError, match="company_not_found"):
                await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_inactive_plan_rejected(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=_company())),
            patch.object(
                svc.db_supabase, "get_corporate_subscription_plan", AsyncMock(return_value=_plan(is_active=False))
            ),
        ):
            with pytest.raises(svc.CorporateSubscriptionError, match="plan_not_found_or_inactive"):
                await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_plan_missing_stripe_price_rejected(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=_company())),
            patch.object(
                svc.db_supabase,
                "get_corporate_subscription_plan",
                AsyncMock(return_value=_plan(stripe_price_id=None)),
            ),
        ):
            with pytest.raises(svc.CorporateSubscriptionError, match="plan_missing_stripe_price"):
                await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_existing_active_subscription_blocks_second_assign(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=_company())),
            patch.object(svc.db_supabase, "get_corporate_subscription_plan", AsyncMock(return_value=_plan())),
            patch.object(
                svc.db_supabase,
                "get_active_corporate_subscription",
                AsyncMock(return_value={"id": "sub-row-1", "status": "active"}),
            ),
        ):
            with pytest.raises(svc.CorporateSubscriptionError, match="subscription_already_active"):
                await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_no_payment_method_rejected(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_account_by_id", AsyncMock(return_value=_company())),
            patch.object(svc.db_supabase, "get_corporate_subscription_plan", AsyncMock(return_value=_plan())),
            patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=None)),
            patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
            patch.object(svc.db_supabase, "get_default_payment_method", AsyncMock(return_value=None)),
        ):
            with pytest.raises(svc.CorporateSubscriptionError, match="no_payment_method_on_file"):
                await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_lazily_creates_stripe_customer_when_missing(self):
        customer_obj = MagicMock()
        customer_obj.id = "cus_new"

        with (
            patch.object(
                svc.db_supabase,
                "get_corporate_account_by_id",
                AsyncMock(return_value=_company(stripe_customer_id=None)),
            ),
            patch.object(svc.db_supabase, "get_corporate_subscription_plan", AsyncMock(return_value=_plan())),
            patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=None)),
            patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
            patch.object(svc.stripe.Customer, "create", return_value=customer_obj) as mock_customer_create,
            # The lazy create + persist moved into services/corporate_stripe_identity,
            # shared with the KYB-approval path and the drift-repair paths. It
            # writes the id and its Stripe mode together via update_one.
            patch(
                "services.corporate_stripe_identity.db_supabase.update_one",
                AsyncMock(),
            ) as mock_update_cus,
            patch.object(svc.db_supabase, "get_default_payment_method", AsyncMock(return_value="pm_1")),
            patch.object(svc.stripe.Subscription, "create", return_value=_stripe_subscription()),
            patch.object(svc.db_supabase, "create_corporate_subscription_row", AsyncMock(side_effect=lambda r: r)),
            patch.object(svc, "log_admin_action", AsyncMock()),
        ):
            row = await svc.assign_subscription(company_id="c1", plan_id="plan_pro", admin_id="admin-1")

        mock_customer_create.assert_called_once()
        mock_update_cus.assert_awaited_once()
        table, filters, update = mock_update_cus.await_args.args
        assert (table, filters) == ("corporate_accounts", {"id": "c1"})
        assert update["stripe_customer_id"] == "cus_new"
        assert row["stripe_customer_id"] == "cus_new"


class TestCancelSubscription:
    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_at_period_end_flags_row_but_keeps_active(self):
        existing = {"id": "sub-row-1", "status": "active", "stripe_subscription_id": "sub_1"}
        patched_rows = []

        async def _capture_update(sub_id, patch_dict):
            patched_rows.append((sub_id, patch_dict))
            return {**existing, **patch_dict}

        with (
            patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=existing)),
            patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
            patch.object(svc.stripe.Subscription, "modify") as mock_modify,
            patch.object(svc.db_supabase, "update_corporate_subscription", AsyncMock(side_effect=_capture_update)),
            patch.object(svc, "log_admin_action", AsyncMock()),
        ):
            row = await svc.cancel_subscription(company_id="c1", admin_id="admin-1", at_period_end=True)

        mock_modify.assert_called_once_with("sub_1", cancel_at_period_end=True, api_key="sk_test_123")
        assert patched_rows[0][1] == {"cancel_at_period_end": True}
        assert row["status"] == "active"  # untouched — status flips only when the webhook fires at period end

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_immediate_cancel_flips_status_now(self):
        existing = {"id": "sub-row-1", "status": "active", "stripe_subscription_id": "sub_1"}

        with (
            patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=existing)),
            patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
            patch.object(svc.stripe.Subscription, "delete") as mock_delete,
            patch.object(
                svc.db_supabase,
                "update_corporate_subscription",
                AsyncMock(side_effect=lambda sid, patch_dict: {**existing, **patch_dict}),
            ),
            patch.object(svc, "log_admin_action", AsyncMock()),
        ):
            row = await svc.cancel_subscription(company_id="c1", admin_id="admin-1", at_period_end=False)

        mock_delete.assert_called_once_with("sub_1", api_key="sk_test_123")
        assert row["status"] == "cancelled"
        assert row["cancelled_at"] is not None

    @pytest.mark.unit
    @pytest.mark.anyio
    async def test_no_active_subscription_rejected(self):
        with patch.object(svc.db_supabase, "get_active_corporate_subscription", AsyncMock(return_value=None)):
            with pytest.raises(svc.CorporateSubscriptionError, match="no_active_subscription"):
                await svc.cancel_subscription(company_id="c1", admin_id="admin-1")


@pytest.mark.unit
@pytest.mark.anyio
async def test_list_plans_delegates_to_repo():
    with patch.object(
        svc.db_supabase, "list_corporate_subscription_plans", AsyncMock(return_value=[_plan()])
    ) as mock_list:
        plans = await svc.list_plans(active_only=True)

    mock_list.assert_awaited_once_with(active_only=True)
    assert plans == [_plan()]
