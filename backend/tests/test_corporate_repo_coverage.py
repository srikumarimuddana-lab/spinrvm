"""Coverage for repositories/corporate_repo.py (A1c, Sub-tier B).

Corporate accounts / members / allowances / wallets / KYB repository layer,
extracted from db_supabase.py during the god-object decomposition. Was at
42.29% coverage — most callers only exercise it indirectly through
routes/corporate_* integration tests, which cover a handful of happy paths
and none of the branch/error edges below.

This module is CORPORATE BILLING / money-adjacent per CLAUDE.md: wallet
balance reads use Decimal (never float) for threshold comparisons, and
DB errors here must surface loudly (never be silently swallowed) except
where the source explicitly documents a caught-and-None fallback (e.g.
`get_corporate_account_by_id`'s "no rows found" catch).

Query-builder mocking: `corporate_repo.supabase` is patched to a
self-chaining `MagicMock` — every chainable method (`.table/.select/.eq/
.insert/.update/.delete/.upsert/.or_/.in_/.order/.range/.limit/...`)
returns the same mock object, so optional filter calls don't break the
chain; only `.execute()` differs per test. This matches the pattern used
in test_zoho_desk_db_coverage.py.

`run_sync` (repositories/_base.py) is NOT mocked — tests let it run for
real against the mocked `supabase` object so that DB-error paths exercise
the actual DatabaseError/DuplicateRecordError translation logic. Since the
injected exceptions in these tests are not "transient" (no HTTP/2 GOAWAY,
no timeout), `run_sync` takes its no-retry break path and raises
immediately — no real sleeping happens.

Bug found, not fixed (test-only scope): `list_corporate_accounts_filtered`
(and every function below it, i.e. everything except the six top-of-file
"Corporate Accounts Functions" plus `get_corporate_account_by_id`) has no
`if not supabase: return ...` guard. When `supabase` is unconfigured, those
functions raise a bare `AttributeError` from `None.table(...)` instead of
the graceful empty-result / DatabaseError behavior the guarded functions
give. Not exercised as a dedicated test here since asserting on an
incidental AttributeError shape would be a brittle lock-in of the bug
rather than of intended behavior; flagging it for a follow-up instead.

Test-only change — no application code modified.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_CHAIN_METHODS = (
    "table",
    "select",
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in_",
    "or_",
    "order",
    "range",
    "limit",
    "insert",
    "update",
    "delete",
    "upsert",
    "single",
)


def _chain(execute_return=None, execute_side_effect=None):
    """A MagicMock whose every chainable query-builder method returns
    itself, so any combination of optional filters still reaches
    `.execute()`."""
    q = MagicMock()
    for method in _CHAIN_METHODS:
        getattr(q, method).return_value = q
    if execute_side_effect is not None:
        q.execute.side_effect = execute_side_effect
    else:
        q.execute.return_value = execute_return
    return q


def _res(data=None, count=None):
    r = MagicMock()
    r.data = data
    r.count = count
    return r


@pytest.fixture
def sb(monkeypatch):
    """Default chaining supabase mock, wired in as corporate_repo.supabase."""
    q = _chain()
    monkeypatch.setattr("backend.repositories.corporate_repo.supabase", q)
    return q


def _set_execute(q, *, data=None, count=None, side_effect=None):
    if side_effect is not None:
        q.execute.side_effect = side_effect
    else:
        q.execute.return_value = _res(data=data, count=count)


# ═══════════════════════ Corporate Accounts (top-level CRUD) ═══════════════════════


class TestGetAllCorporateAccounts:
    @pytest.mark.anyio
    async def test_returns_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("backend.repositories.corporate_repo.supabase", None)
        from backend.repositories.corporate_repo import get_all_corporate_accounts

        assert await get_all_corporate_accounts() == []

    @pytest.mark.anyio
    async def test_returns_rows_no_filters(self, sb):
        from backend.repositories.corporate_repo import get_all_corporate_accounts

        _set_execute(sb, data=[{"id": "a1"}, {"id": "a2"}])
        result = await get_all_corporate_accounts(skip=0, limit=100)
        assert result == [{"id": "a1"}, {"id": "a2"}]
        sb.or_.assert_not_called()
        sb.eq.assert_not_called()

    @pytest.mark.anyio
    async def test_search_term_sanitized_before_or_clause(self, sb):
        """As of the corporate-portal review (#3289), search now goes
        through the shared `_apply_filters`/`_build_or_clause_term` $regex
        path (repositories/_base.py) instead of a hand-rolled strip —
        PostgREST-reserved characters (`,()`) and LIKE wildcards (`%_`) are
        BACKSLASH-ESCAPED, not deleted, so a search term containing them
        still matches literally instead of silently losing characters."""
        from backend.repositories.corporate_repo import get_all_corporate_accounts

        _set_execute(sb, data=[])
        await get_all_corporate_accounts(search="Acme (Inc.), LLC")
        called = sb.or_.call_args.args[0]
        assert called == (
            r"name.ilike.*Acme \(Inc.\)\, LLC*,"
            r"contact_name.ilike.*Acme \(Inc.\)\, LLC*,"
            r"contact_email.ilike.*Acme \(Inc.\)\, LLC*"
        )

    @pytest.mark.anyio
    async def test_is_active_filter_applied(self, sb):
        from backend.repositories.corporate_repo import get_all_corporate_accounts

        _set_execute(sb, data=[])
        await get_all_corporate_accounts(is_active=True)
        sb.eq.assert_any_call("is_active", True)

    @pytest.mark.anyio
    async def test_db_error_surfaces_not_swallowed(self, sb):
        from backend.repositories.corporate_repo import get_all_corporate_accounts
        from backend.utils.error_handling import DatabaseError

        sb.execute.side_effect = RuntimeError("connection reset")
        with pytest.raises(DatabaseError):
            await get_all_corporate_accounts()


class TestGetCorporateAccountById:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("backend.repositories.corporate_repo.supabase", None)
        from backend.repositories.corporate_repo import get_corporate_account_by_id

        assert await get_corporate_account_by_id("x") is None

    @pytest.mark.anyio
    async def test_returns_row_when_found(self, sb):
        from backend.repositories.corporate_repo import get_corporate_account_by_id

        _set_execute(sb, data={"id": "acc-1", "name": "Acme"})
        result = await get_corporate_account_by_id("acc-1")
        assert result == {"id": "acc-1", "name": "Acme"}

    @pytest.mark.anyio
    async def test_missing_row_caught_and_returns_none(self, sb):
        """`.single()` raises when no row matches; this is the one function
        documented to swallow and return None rather than raise."""
        from backend.repositories.corporate_repo import get_corporate_account_by_id

        sb.execute.side_effect = Exception("PGRST116: 0 rows")
        result = await get_corporate_account_by_id("missing")
        assert result is None


class TestInsertCorporateAccount:
    @pytest.mark.anyio
    async def test_raises_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("backend.repositories.corporate_repo.supabase", None)
        from backend.repositories.corporate_repo import insert_corporate_account

        with pytest.raises(RuntimeError):
            await insert_corporate_account({"name": "Acme"})

    @pytest.mark.anyio
    async def test_success_serializes_decimal_before_insert(self, sb):
        from backend.repositories.corporate_repo import insert_corporate_account

        _set_execute(sb, data=[{"id": "acc-1", "name": "Acme"}])
        result = await insert_corporate_account({"name": "Acme", "credit_limit": Decimal("500.00")})
        assert result == {"id": "acc-1", "name": "Acme"}
        inserted_payload = sb.insert.call_args.args[0]
        assert inserted_payload["credit_limit"] == "500.00"
        assert isinstance(inserted_payload["credit_limit"], str)

    @pytest.mark.anyio
    async def test_db_error_surfaces(self, sb):
        from backend.repositories.corporate_repo import insert_corporate_account
        from backend.utils.error_handling import DatabaseError

        sb.execute.side_effect = RuntimeError("insert failed")
        with pytest.raises(DatabaseError):
            await insert_corporate_account({"name": "Acme"})


class TestUpdateCorporateAccount:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("backend.repositories.corporate_repo.supabase", None)
        from backend.repositories.corporate_repo import update_corporate_account

        assert await update_corporate_account("acc-1", {"name": "New"}) is None

    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import update_corporate_account

        _set_execute(sb, data=[{"id": "acc-1", "name": "New"}])
        result = await update_corporate_account("acc-1", {"name": "New"})
        assert result == {"id": "acc-1", "name": "New"}
        sb.eq.assert_any_call("id", "acc-1")


class TestDeleteCorporateAccount:
    @pytest.mark.anyio
    async def test_returns_false_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("backend.repositories.corporate_repo.supabase", None)
        from backend.repositories.corporate_repo import delete_corporate_account

        assert await delete_corporate_account("acc-1") is False

    @pytest.mark.anyio
    async def test_returns_true_when_rows_deleted(self, sb):
        from backend.repositories.corporate_repo import delete_corporate_account

        _set_execute(sb, data=[{"id": "acc-1"}], count=1)
        assert await delete_corporate_account("acc-1") is True

    @pytest.mark.anyio
    async def test_returns_false_when_count_zero(self, sb):
        from backend.repositories.corporate_repo import delete_corporate_account

        _set_execute(sb, data=[], count=0)
        assert await delete_corporate_account("acc-1") is False

    @pytest.mark.anyio
    async def test_returns_false_when_count_none(self, sb):
        from backend.repositories.corporate_repo import delete_corporate_account

        _set_execute(sb, data=[], count=None)
        assert await delete_corporate_account("acc-1") is False


# ═══════════════════════ Corporate Accounts (B2B v1) ═══════════════════════


class TestListCorporateAccountsFiltered:
    @pytest.mark.anyio
    async def test_no_filters(self, sb):
        from backend.repositories.corporate_repo import list_corporate_accounts_filtered

        _set_execute(sb, data=[{"id": "a1"}])
        result = await list_corporate_accounts_filtered(status=None, size_tier=None, search=None, skip=0, limit=10)
        assert result == [{"id": "a1"}]
        sb.eq.assert_not_called()
        sb.or_.assert_not_called()

    @pytest.mark.anyio
    async def test_status_and_size_tier_filters(self, sb):
        from backend.repositories.corporate_repo import list_corporate_accounts_filtered

        _set_execute(sb, data=[])
        await list_corporate_accounts_filtered(status="active", size_tier="enterprise", search=None, skip=0, limit=10)
        sb.eq.assert_any_call("status", "active")
        sb.eq.assert_any_call("size_tier", "enterprise")

    @pytest.mark.anyio
    async def test_search_sanitized(self, sb):
        """Same shared `_apply_filters` $regex escaping as
        get_all_corporate_accounts (see that test's docstring) — reserved
        characters are backslash-escaped, not stripped."""
        from backend.repositories.corporate_repo import list_corporate_accounts_filtered

        _set_execute(sb, data=[])
        await list_corporate_accounts_filtered(status=None, size_tier=None, search="A,B(C)", skip=0, limit=10)
        called = sb.or_.call_args.args[0]
        assert called == r"name.ilike.*A\,B\(C\)*,legal_name.ilike.*A\,B\(C\)*"

    @pytest.mark.anyio
    async def test_range_bounds(self, sb):
        from backend.repositories.corporate_repo import list_corporate_accounts_filtered

        _set_execute(sb, data=[])
        await list_corporate_accounts_filtered(status=None, size_tier=None, search=None, skip=20, limit=10)
        sb.range.assert_any_call(20, 29)


class TestUpdateCorporateAccountStatus:
    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import update_corporate_account_status

        _set_execute(sb, data=[{"id": "c1", "status": "suspended"}])
        result = await update_corporate_account_status("c1", "suspended")
        assert result == {"id": "c1", "status": "suspended"}
        sb.update.assert_called_with({"status": "suspended"})


class TestRecordKybDecision:
    @pytest.mark.anyio
    async def test_approved_sets_active_and_approved_decision(self, sb):
        from backend.repositories.corporate_repo import record_kyb_decision

        _set_execute(sb, data=[{"id": "c1", "status": "active"}])
        await record_kyb_decision(company_id="c1", reviewer_id="rev-1", approved=True, note=None)
        patch = sb.update.call_args.args[0]
        assert patch["status"] == "active"
        assert patch["kyb_last_decision"] == "approved"
        assert patch["kyb_reviewed_by"] == "rev-1"
        assert "kyb_review_note" not in patch

    @pytest.mark.anyio
    async def test_rejected_sets_suspended_and_rejected_decision(self, sb):
        from backend.repositories.corporate_repo import record_kyb_decision

        _set_execute(sb, data=[{"id": "c1", "status": "suspended"}])
        await record_kyb_decision(company_id="c1", reviewer_id="rev-1", approved=False, note=None)
        patch = sb.update.call_args.args[0]
        assert patch["status"] == "suspended"
        assert patch["kyb_last_decision"] == "rejected"

    @pytest.mark.anyio
    async def test_note_included_when_provided(self, sb):
        from backend.repositories.corporate_repo import record_kyb_decision

        _set_execute(sb, data=[{"id": "c1"}])
        await record_kyb_decision(company_id="c1", reviewer_id="rev-1", approved=False, note="missing signature page")
        patch = sb.update.call_args.args[0]
        assert patch["kyb_review_note"] == "missing signature page"


class TestKybObjectExists:
    @pytest.mark.anyio
    async def test_true_when_dict_entry_matches(self, sb):
        from backend.repositories.corporate_repo import kyb_object_exists

        storage = MagicMock()
        bucket = MagicMock()
        bucket.list.return_value = [{"name": "doc.pdf"}, {"name": "other.png"}]
        storage.from_.return_value = bucket
        sb.storage = storage

        assert await kyb_object_exists(path="kyb/c1/doc.pdf") is True
        bucket.list.assert_called_with("kyb/c1")

    @pytest.mark.anyio
    async def test_true_when_object_entry_matches(self, sb):
        from backend.repositories.corporate_repo import kyb_object_exists

        entry = MagicMock()
        entry.name = "doc.pdf"
        # entry is a MagicMock, not a dict, so the getattr(e, "name", None)
        # branch is used rather than e.get("name").
        storage = MagicMock()
        bucket = MagicMock()
        bucket.list.return_value = [entry]
        storage.from_.return_value = bucket
        sb.storage = storage

        assert await kyb_object_exists(path="kyb/c1/doc.pdf") is True

    @pytest.mark.anyio
    async def test_false_when_no_match(self, sb):
        from backend.repositories.corporate_repo import kyb_object_exists

        storage = MagicMock()
        bucket = MagicMock()
        bucket.list.return_value = [{"name": "unrelated.pdf"}]
        storage.from_.return_value = bucket
        sb.storage = storage

        assert await kyb_object_exists(path="kyb/c1/doc.pdf") is False

    @pytest.mark.anyio
    async def test_false_when_list_returns_none(self, sb):
        from backend.repositories.corporate_repo import kyb_object_exists

        storage = MagicMock()
        bucket = MagicMock()
        bucket.list.return_value = None
        storage.from_.return_value = bucket
        sb.storage = storage

        assert await kyb_object_exists(path="kyb/c1/doc.pdf") is False


class TestSetKybDocument:
    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import set_kyb_document

        _set_execute(sb, data=[{"id": "c1", "kyb_document_url": "kyb/c1/x.pdf"}])
        result = await set_kyb_document(company_id="c1", path="kyb/c1/x.pdf")
        assert result["kyb_document_url"] == "kyb/c1/x.pdf"
        patch = sb.update.call_args.args[0]
        assert patch["kyb_document_url"] == "kyb/c1/x.pdf"
        assert "kyb_submitted_at" in patch


class TestGetCorporateWalletByCompany:
    @pytest.mark.anyio
    async def test_returns_first_row(self, sb):
        from backend.repositories.corporate_repo import get_corporate_wallet_by_company

        _set_execute(sb, data=[{"id": "w1", "company_id": "c1"}])
        result = await get_corporate_wallet_by_company("c1")
        assert result == {"id": "w1", "company_id": "c1"}

    @pytest.mark.anyio
    async def test_returns_none_when_no_wallet(self, sb):
        from backend.repositories.corporate_repo import get_corporate_wallet_by_company

        _set_execute(sb, data=[])
        assert await get_corporate_wallet_by_company("c1") is None


class TestUpdateCorporateStripeCustomerId:
    @pytest.mark.anyio
    async def test_calls_update_with_stripe_id(self, sb):
        from backend.repositories.corporate_repo import update_corporate_stripe_customer_id

        _set_execute(sb, data=[{"id": "c1"}])
        await update_corporate_stripe_customer_id(company_id="c1", stripe_customer_id="cus_123")
        sb.update.assert_called_with({"stripe_customer_id": "cus_123"})
        sb.eq.assert_any_call("id", "c1")


class TestEnsureCorporateWallet:
    @pytest.mark.anyio
    async def test_returns_existing_without_insert(self, sb):
        from backend.repositories.corporate_repo import ensure_corporate_wallet

        sb.execute.return_value = _res(data=[{"id": "w1", "company_id": "c1", "balance": "0"}])
        result = await ensure_corporate_wallet(company_id="c1")
        assert result == {"id": "w1", "company_id": "c1", "balance": "0"}
        sb.insert.assert_not_called()

    @pytest.mark.anyio
    async def test_creates_when_missing(self, sb):
        from backend.repositories.corporate_repo import ensure_corporate_wallet

        sb.execute.side_effect = [
            _res(data=[]),  # select finds nothing
            _res(data=[{"id": "w2", "company_id": "c1", "balance": 0, "currency": "CAD"}]),  # insert result
        ]
        result = await ensure_corporate_wallet(company_id="c1")
        assert result["id"] == "w2"
        sb.insert.assert_called_with({"company_id": "c1", "balance": 0, "currency": "CAD"})

    @pytest.mark.anyio
    async def test_returns_empty_dict_when_insert_yields_nothing(self, sb):
        from backend.repositories.corporate_repo import ensure_corporate_wallet

        sb.execute.side_effect = [_res(data=[]), _res(data=[])]
        result = await ensure_corporate_wallet(company_id="c1")
        assert result == {}


class TestGetCorporateMembersForUser:
    @pytest.mark.anyio
    async def test_returns_rows(self, sb):
        from backend.repositories.corporate_repo import get_corporate_members_for_user

        _set_execute(sb, data=[{"id": "m1", "company_id": "c1", "role": "member"}])
        result = await get_corporate_members_for_user("u1")
        assert result == [{"id": "m1", "company_id": "c1", "role": "member"}]
        sb.eq.assert_any_call("status", "active")


class TestCreateKybUploadUrl:
    @pytest.mark.anyio
    async def test_pdf_success_returns_signed_url_and_computed_path(self, sb):
        from backend.repositories.corporate_repo import create_kyb_upload_url

        storage = MagicMock()
        bucket = MagicMock()
        bucket.create_signed_upload_url.return_value = {"signed_url": "https://signed"}
        storage.from_.return_value = bucket
        sb.storage = storage

        result = await create_kyb_upload_url(company_id="c1", content_type="application/pdf")
        assert result["signed_url"] == "https://signed"
        assert result["path"].startswith("kyb/c1/") and result["path"].endswith(".pdf")
        assert "expires_at" in result

    @pytest.mark.anyio
    async def test_signed_response_path_overrides_computed_path(self, sb):
        from backend.repositories.corporate_repo import create_kyb_upload_url

        storage = MagicMock()
        bucket = MagicMock()
        bucket.create_signed_upload_url.return_value = {"signed_url": "https://signed", "path": "override/path.png"}
        storage.from_.return_value = bucket
        sb.storage = storage

        result = await create_kyb_upload_url(company_id="c1", content_type="image/png")
        assert result["path"] == "override/path.png"

    @pytest.mark.anyio
    async def test_unsupported_content_type_raises_keyerror(self, sb):
        from backend.repositories.corporate_repo import create_kyb_upload_url

        with pytest.raises(KeyError):
            await create_kyb_upload_url(company_id="c1", content_type="application/zip")


class TestListWalletsNeedingAutotopup:
    @pytest.mark.anyio
    async def test_filters_below_threshold_using_decimal(self, sb):
        from backend.repositories.corporate_repo import list_wallets_needing_autotopup

        _set_execute(
            sb,
            data=[
                {"id": "w1", "balance": "10.00", "auto_topup_threshold": "20.00"},  # below -> included
                {"id": "w2", "balance": "30.00", "auto_topup_threshold": "20.00"},  # above -> excluded
                {"id": "w3", "balance": "5.00", "auto_topup_threshold": None},  # no threshold -> excluded
            ],
        )
        result = await list_wallets_needing_autotopup()
        assert [r["id"] for r in result] == ["w1"]
        sb.eq.assert_any_call("auto_topup_enabled", True)

    @pytest.mark.anyio
    async def test_decimal_boundary_equal_balance_not_included(self, sb):
        """balance == threshold is NOT '< threshold' — must not top up at parity."""
        from backend.repositories.corporate_repo import list_wallets_needing_autotopup

        _set_execute(sb, data=[{"id": "w1", "balance": "20.00", "auto_topup_threshold": "20.00"}])
        result = await list_wallets_needing_autotopup()
        assert result == []


class TestSumAutotopupsToday:
    @pytest.mark.anyio
    async def test_sums_decimal_amounts(self, sb):
        from backend.repositories.corporate_repo import sum_autotopups_today

        _set_execute(sb, data=[{"amount": "10.50"}, {"amount": "5.25"}])
        result = await sum_autotopups_today("w1")
        assert result == Decimal("15.75")
        assert isinstance(result, Decimal)

    @pytest.mark.anyio
    async def test_empty_rows_returns_zero_decimal(self, sb):
        from backend.repositories.corporate_repo import sum_autotopups_today

        _set_execute(sb, data=[])
        result = await sum_autotopups_today("w1")
        assert result == Decimal("0")


class TestGetDefaultPaymentMethod:
    @pytest.mark.anyio
    async def test_returns_first_card_id(self, monkeypatch):
        import stripe

        from backend.repositories.corporate_repo import get_default_payment_method

        pm = MagicMock()
        pm.id = "pm_123"
        methods = MagicMock()
        methods.data = [pm]
        monkeypatch.setattr(stripe.PaymentMethod, "list", MagicMock(return_value=methods))

        result = await get_default_payment_method("cus_1", "sk_test_123")
        assert result == "pm_123"

    @pytest.mark.anyio
    async def test_returns_none_when_no_cards(self, monkeypatch):
        import stripe

        from backend.repositories.corporate_repo import get_default_payment_method

        methods = MagicMock()
        methods.data = []
        monkeypatch.setattr(stripe.PaymentMethod, "list", MagicMock(return_value=methods))

        result = await get_default_payment_method("cus_1", "sk_test_123")
        assert result is None


class TestListWalletsLowBalanceNoAutotopup:
    @pytest.mark.anyio
    async def test_filters_below_threshold_with_autotopup_disabled(self, sb):
        from backend.repositories.corporate_repo import list_wallets_low_balance_no_autotopup

        _set_execute(
            sb,
            data=[
                {"id": "w1", "balance": "1.00", "auto_topup_threshold": "10.00"},
                {"id": "w2", "balance": "50.00", "auto_topup_threshold": "10.00"},
            ],
        )
        result = await list_wallets_low_balance_no_autotopup()
        assert [r["id"] for r in result] == ["w1"]
        sb.eq.assert_any_call("auto_topup_enabled", False)


class TestMarkLowBalanceNotified:
    @pytest.mark.anyio
    async def test_calls_update_with_timestamp(self, sb):
        from backend.repositories.corporate_repo import mark_low_balance_notified

        _set_execute(sb, data=[{"id": "w1"}])
        await mark_low_balance_notified(wallet_id="w1")
        patch = sb.update.call_args.args[0]
        assert "low_balance_notified_at" in patch
        sb.eq.assert_any_call("id", "w1")


class TestListWalletTransactions:
    @pytest.mark.anyio
    async def test_default_pagination(self, sb):
        from backend.repositories.corporate_repo import list_wallet_transactions

        _set_execute(sb, data=[{"id": "t1"}])
        result = await list_wallet_transactions(wallet_id="w1")
        assert result == [{"id": "t1"}]
        sb.range.assert_any_call(0, 49)

    @pytest.mark.anyio
    async def test_custom_pagination_bounds(self, sb):
        from backend.repositories.corporate_repo import list_wallet_transactions

        _set_execute(sb, data=[])
        await list_wallet_transactions(wallet_id="w1", skip=10, limit=5)
        sb.range.assert_any_call(10, 14)


class TestUpdateCorporateWalletConfig:
    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import update_corporate_wallet_config

        _set_execute(sb, data=[{"id": "w1", "auto_topup_enabled": True}])
        result = await update_corporate_wallet_config(wallet_id="w1", patch={"auto_topup_enabled": True})
        assert result == {"id": "w1", "auto_topup_enabled": True}
        sb.update.assert_called_with({"auto_topup_enabled": True})


# ═══════════════════════ Members ═══════════════════════


class TestInsertCorporateMemberInvite:
    @pytest.mark.anyio
    async def test_success_defaults_policy_override_false(self, sb):
        from backend.repositories.corporate_repo import insert_corporate_member_invite

        _set_execute(sb, data=[{"id": "m1", "invited_email": "a@b.com"}])
        result = await insert_corporate_member_invite(
            company_id="c1", email="a@b.com", role="member", invite_token="tok", invited_by="owner-1"
        )
        assert result["id"] == "m1"
        payload = sb.insert.call_args.args[0]
        assert payload["policy_override"] is False
        assert payload["status"] == "invited"

    @pytest.mark.anyio
    async def test_returns_empty_dict_when_insert_yields_nothing(self, sb):
        from backend.repositories.corporate_repo import insert_corporate_member_invite

        _set_execute(sb, data=[])
        result = await insert_corporate_member_invite(
            company_id="c1", email="a@b.com", role="member", invite_token="tok", invited_by="owner-1"
        )
        assert result == {}


class TestCountPendingSignupsForUser:
    @pytest.mark.anyio
    async def test_uses_count_when_present(self, sb):
        from backend.repositories.corporate_repo import count_pending_signups_for_user

        sb.execute.return_value = _res(data=[{"id": "1"}, {"id": "2"}], count=2)
        result = await count_pending_signups_for_user("u1")
        assert result == 2

    @pytest.mark.anyio
    async def test_falls_back_to_data_length_when_count_none(self, sb):
        from backend.repositories.corporate_repo import count_pending_signups_for_user

        sb.execute.return_value = _res(data=[{"id": "1"}], count=None)
        result = await count_pending_signups_for_user("u1")
        assert result == 1

    @pytest.mark.anyio
    async def test_zero_when_no_data_and_no_count(self, sb):
        from backend.repositories.corporate_repo import count_pending_signups_for_user

        sb.execute.return_value = _res(data=None, count=None)
        result = await count_pending_signups_for_user("u1")
        assert result == 0


class TestCreateActiveMember:
    @pytest.mark.anyio
    async def test_success_with_explicit_invited_by(self, sb):
        from backend.repositories.corporate_repo import create_active_member

        _set_execute(sb, data=[{"id": "m1", "status": "active"}])
        result = await create_active_member(company_id="c1", user_id="u1", email="a@b.com", invited_by="owner-1")
        assert result["status"] == "active"
        payload = sb.insert.call_args.args[0]
        assert payload["invited_by"] == "owner-1"
        assert payload["role"] == "owner"

    @pytest.mark.anyio
    async def test_invited_by_defaults_to_user_id(self, sb):
        from backend.repositories.corporate_repo import create_active_member

        _set_execute(sb, data=[{"id": "m1"}])
        await create_active_member(company_id="c1", user_id="u1", email="a@b.com")
        payload = sb.insert.call_args.args[0]
        assert payload["invited_by"] == "u1"

    @pytest.mark.anyio
    async def test_duplicate_membership_surfaces_not_swallowed(self, sb):
        """The unique index on (company_id, user_id) makes a duplicate insert
        raise; per the source docstring this is a genuine bug if it happens
        and must propagate as DuplicateRecordError, never be swallowed."""
        from backend.repositories.corporate_repo import create_active_member
        from backend.utils.error_handling import DuplicateRecordError

        sb.execute.side_effect = RuntimeError("duplicate key value violates unique constraint")
        with pytest.raises(DuplicateRecordError):
            await create_active_member(company_id="c1", user_id="u1", email="a@b.com")


class TestListCompanyMembers:
    @pytest.mark.anyio
    async def test_no_status_filter(self, sb):
        from backend.repositories.corporate_repo import list_company_members

        _set_execute(sb, data=[{"id": "m1"}])
        result = await list_company_members(company_id="c1")
        assert result == [{"id": "m1"}]
        sb.in_.assert_not_called()

    @pytest.mark.anyio
    async def test_status_filter_applied(self, sb):
        from backend.repositories.corporate_repo import list_company_members

        _set_execute(sb, data=[])
        await list_company_members(company_id="c1", statuses=["active", "invited"])
        sb.in_.assert_any_call("status", ["active", "invited"])


class TestGetCorporateMemberById:
    @pytest.mark.anyio
    async def test_found(self, sb):
        from backend.repositories.corporate_repo import get_corporate_member_by_id

        _set_execute(sb, data=[{"id": "m1"}])
        assert await get_corporate_member_by_id("m1") == {"id": "m1"}

    @pytest.mark.anyio
    async def test_not_found(self, sb):
        from backend.repositories.corporate_repo import get_corporate_member_by_id

        _set_execute(sb, data=[])
        assert await get_corporate_member_by_id("missing") is None


class TestGetMemberByInviteToken:
    @pytest.mark.anyio
    async def test_found(self, sb):
        from backend.repositories.corporate_repo import get_member_by_invite_token

        _set_execute(sb, data=[{"id": "m1", "invite_token": "tok"}])
        assert await get_member_by_invite_token("tok") == {"id": "m1", "invite_token": "tok"}

    @pytest.mark.anyio
    async def test_not_found(self, sb):
        from backend.repositories.corporate_repo import get_member_by_invite_token

        _set_execute(sb, data=[])
        assert await get_member_by_invite_token("bad-tok") is None


class TestListActiveMembershipsForUser:
    @pytest.mark.anyio
    async def test_returns_rows(self, sb):
        from backend.repositories.corporate_repo import list_active_memberships_for_user

        _set_execute(sb, data=[{"id": "m1"}, {"id": "m2"}])
        result = await list_active_memberships_for_user("u1")
        assert len(result) == 2
        sb.eq.assert_any_call("status", "active")


class TestUpdateCorporateMember:
    @pytest.mark.anyio
    async def test_empty_patch_delegates_to_get_by_id(self, sb, monkeypatch):
        from backend.repositories import corporate_repo

        async def _fake_get(member_id):
            return {"id": member_id, "role": "member"}

        monkeypatch.setattr(corporate_repo, "get_corporate_member_by_id", _fake_get)
        result = await corporate_repo.update_corporate_member("m1", {})
        assert result == {"id": "m1", "role": "member"}
        sb.update.assert_not_called()

    @pytest.mark.anyio
    async def test_nonempty_patch_stamps_updated_at(self, sb):
        from backend.repositories.corporate_repo import update_corporate_member

        _set_execute(sb, data=[{"id": "m1", "role": "admin"}])
        result = await update_corporate_member("m1", {"role": "admin"})
        assert result["role"] == "admin"
        payload = sb.update.call_args.args[0]
        assert payload["role"] == "admin"
        assert "updated_at" in payload


class TestAcceptMemberInvite:
    @pytest.mark.anyio
    async def test_success_flips_to_active(self, sb):
        from backend.repositories.corporate_repo import accept_member_invite

        _set_execute(sb, data=[{"id": "m1", "status": "active", "user_id": "u1"}])
        result = await accept_member_invite(member_id="m1", user_id="u1")
        assert result["status"] == "active"
        payload = sb.update.call_args.args[0]
        assert payload["status"] == "active"
        assert payload["user_id"] == "u1"
        assert payload["invite_token"] is None
        sb.eq.assert_any_call("status", "invited")

    @pytest.mark.anyio
    async def test_replay_against_consumed_token_returns_none(self, sb):
        """Zero rows matched status='invited' -> already consumed -> None,
        not an error (mirrors the ride-acceptance race-guard convention)."""
        from backend.repositories.corporate_repo import accept_member_invite

        _set_execute(sb, data=[])
        result = await accept_member_invite(member_id="m1", user_id="u1")
        assert result is None


# ═══════════════════════ Allowances ═══════════════════════


class TestGetMemberAllowance:
    @pytest.mark.anyio
    async def test_found(self, sb):
        from backend.repositories.corporate_repo import get_member_allowance

        _set_execute(sb, data=[{"id": "al1", "member_id": "m1"}])
        assert await get_member_allowance("m1") == {"id": "al1", "member_id": "m1"}

    @pytest.mark.anyio
    async def test_not_found(self, sb):
        from backend.repositories.corporate_repo import get_member_allowance

        _set_execute(sb, data=[])
        assert await get_member_allowance("m1") is None


class TestUpsertMemberAllowance:
    @pytest.mark.anyio
    async def test_updates_existing_allowance(self, sb):
        from backend.repositories.corporate_repo import upsert_member_allowance

        sb.execute.side_effect = [
            _res(data=[{"id": "al1", "member_id": "m1", "amount": "100.00"}]),  # get_member_allowance select
            _res(data=[{"id": "al1", "member_id": "m1", "amount": "200.00"}]),  # update
        ]
        result = await upsert_member_allowance(member_id="m1", patch={"amount": "200.00"})
        assert result["amount"] == "200.00"
        sb.eq.assert_any_call("id", "al1")

    @pytest.mark.anyio
    async def test_update_falls_back_to_existing_when_no_row_returned(self, sb):
        from backend.repositories.corporate_repo import upsert_member_allowance

        existing = {"id": "al1", "member_id": "m1", "amount": "100.00"}
        sb.execute.side_effect = [_res(data=[existing]), _res(data=[])]
        result = await upsert_member_allowance(member_id="m1", patch={"amount": "200.00"})
        assert result == existing

    @pytest.mark.anyio
    async def test_inserts_when_no_existing_allowance(self, sb):
        from backend.repositories.corporate_repo import upsert_member_allowance

        sb.execute.side_effect = [
            _res(data=[]),  # get_member_allowance select -> none
            _res(data=[{"id": "al2", "member_id": "m1", "used": 0, "status": "active", "amount": "50.00"}]),
        ]
        result = await upsert_member_allowance(member_id="m1", patch={"amount": "50.00"})
        assert result["id"] == "al2"
        insert_payload = sb.insert.call_args.args[0]
        assert insert_payload["member_id"] == "m1"
        assert insert_payload["used"] == 0
        assert insert_payload["status"] == "active"

    @pytest.mark.anyio
    async def test_insert_returns_empty_dict_when_row_missing(self, sb):
        from backend.repositories.corporate_repo import upsert_member_allowance

        sb.execute.side_effect = [_res(data=[]), _res(data=[])]
        result = await upsert_member_allowance(member_id="m1", patch={"amount": "50.00"})
        assert result == {}


class TestListCompanyAllowances:
    @pytest.mark.anyio
    async def test_returns_joined_rows(self, sb):
        from backend.repositories.corporate_repo import list_company_allowances

        _set_execute(sb, data=[{"id": "al1", "member": {"id": "m1", "company_id": "c1"}}])
        result = await list_company_allowances("c1")
        assert result[0]["member"]["company_id"] == "c1"
        sb.eq.assert_any_call("member.company_id", "c1")


class TestListAllowancesDueForReset:
    @pytest.mark.anyio
    async def test_filters_active_fixed_recurring(self, sb):
        from backend.repositories.corporate_repo import list_allowances_due_for_reset

        _set_execute(sb, data=[{"id": "al1"}])
        result = await list_allowances_due_for_reset("2026-08-02")
        assert result == [{"id": "al1"}]
        sb.eq.assert_any_call("type", "fixed_recurring")
        sb.eq.assert_any_call("status", "active")


class TestResetAllowancePeriod:
    @pytest.mark.anyio
    async def test_unconditional_update_when_no_expected_value(self, sb):
        from backend.repositories.corporate_repo import reset_allowance_period

        _set_execute(sb, data=[{"id": "al1", "period_start": "2026-08-01", "period_end": "2026-09-01"}])
        result = await reset_allowance_period(allowance_id="al1", period_start="2026-08-01", period_end="2026-09-01")
        assert result["period_end"] == "2026-09-01"

    @pytest.mark.anyio
    async def test_cas_success_when_expected_matches(self, sb):
        from backend.repositories.corporate_repo import reset_allowance_period

        _set_execute(sb, data=[{"id": "al1", "period_end": "2026-09-01"}])
        result = await reset_allowance_period(
            allowance_id="al1",
            period_start="2026-08-01",
            period_end="2026-09-01",
            expected_period_end="2026-08-01",
        )
        assert result is not None
        sb.eq.assert_any_call("period_end", "2026-08-01")

    @pytest.mark.anyio
    async def test_cas_loss_returns_none(self, sb):
        """Another replica already advanced the period -> zero rows -> None,
        not an error (replay-safety per CLAUDE.md background-loop rules)."""
        from backend.repositories.corporate_repo import reset_allowance_period

        _set_execute(sb, data=[])
        result = await reset_allowance_period(
            allowance_id="al1",
            period_start="2026-08-01",
            period_end="2026-09-01",
            expected_period_end="2026-07-01",
        )
        assert result is None

    @pytest.mark.anyio
    async def test_auto_approved_this_period_reset_to_zero(self, sb):
        from backend.repositories.corporate_repo import reset_allowance_period

        _set_execute(sb, data=[{"id": "al1"}])
        await reset_allowance_period(allowance_id="al1", period_start="2026-08-01", period_end="2026-09-01")
        payload = sb.update.call_args.args[0]
        assert payload["auto_approved_this_period"] == 0


# ═══════════════════════ Allowance requests ═══════════════════════


class TestInsertAllowanceRequest:
    @pytest.mark.anyio
    async def test_success_default_status_pending(self, sb):
        from backend.repositories.corporate_repo import insert_allowance_request

        _set_execute(sb, data=[{"id": "req1", "status": "pending"}])
        result = await insert_allowance_request(member_id="m1", amount=25.0, reason="Client dinner")
        assert result["status"] == "pending"
        payload = sb.insert.call_args.args[0]
        assert payload["status"] == "pending"
        assert payload["amount"] == 25.0


class TestListPendingAllowanceRequestsForMember:
    @pytest.mark.anyio
    async def test_filters_pending_only(self, sb):
        from backend.repositories.corporate_repo import list_pending_allowance_requests_for_member

        _set_execute(sb, data=[{"id": "req1", "status": "pending"}])
        result = await list_pending_allowance_requests_for_member("m1")
        assert result == [{"id": "req1", "status": "pending"}]
        sb.eq.assert_any_call("status", "pending")


class TestListCompanyAllowanceRequests:
    @pytest.mark.anyio
    async def test_no_extra_filters(self, sb):
        from backend.repositories.corporate_repo import list_company_allowance_requests

        _set_execute(sb, data=[{"id": "req1"}])
        result = await list_company_allowance_requests("c1")
        assert result == [{"id": "req1"}]
        sb.eq.assert_any_call("member.company_id", "c1")

    @pytest.mark.anyio
    async def test_member_and_status_filters_applied(self, sb):
        from backend.repositories.corporate_repo import list_company_allowance_requests

        _set_execute(sb, data=[])
        await list_company_allowance_requests("c1", statuses=["pending", "approved"], member_id="m1")
        sb.eq.assert_any_call("member_id", "m1")
        sb.in_.assert_any_call("status", ["pending", "approved"])


class TestGetAllowanceRequestById:
    @pytest.mark.anyio
    async def test_found(self, sb):
        from backend.repositories.corporate_repo import get_allowance_request_by_id

        _set_execute(sb, data=[{"id": "req1"}])
        assert await get_allowance_request_by_id("req1") == {"id": "req1"}

    @pytest.mark.anyio
    async def test_not_found(self, sb):
        from backend.repositories.corporate_repo import get_allowance_request_by_id

        _set_execute(sb, data=[])
        assert await get_allowance_request_by_id("missing") is None


class TestUpdateAllowanceRequest:
    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import update_allowance_request

        _set_execute(sb, data=[{"id": "req1", "status": "approved"}])
        result = await update_allowance_request(
            request_id="req1", status="approved", reviewed_by="admin-1", decision_notes="ok"
        )
        assert result["status"] == "approved"
        payload = sb.update.call_args.args[0]
        assert payload["status"] == "approved"
        assert payload["reviewed_by"] == "admin-1"
        assert payload["decision_notes"] == "ok"


# ═══════════════════════ Allowed domains ═══════════════════════


class TestAddAllowedDomain:
    @pytest.mark.anyio
    async def test_success(self, sb):
        from backend.repositories.corporate_repo import add_allowed_domain

        _set_execute(sb, data=[{"company_id": "c1", "domain": "acme.com"}])
        result = await add_allowed_domain(company_id="c1", domain="acme.com")
        assert result == {"company_id": "c1", "domain": "acme.com"}

    @pytest.mark.anyio
    async def test_falls_back_to_input_dict_when_insert_yields_nothing(self, sb):
        from backend.repositories.corporate_repo import add_allowed_domain

        _set_execute(sb, data=[])
        result = await add_allowed_domain(company_id="c1", domain="acme.com")
        assert result == {"company_id": "c1", "domain": "acme.com"}


class TestListAllowedDomains:
    @pytest.mark.anyio
    async def test_returns_rows(self, sb):
        from backend.repositories.corporate_repo import list_allowed_domains

        _set_execute(sb, data=[{"domain": "acme.com"}, {"domain": "acme.ca"}])
        result = await list_allowed_domains("c1")
        assert len(result) == 2


class TestDeleteAllowedDomain:
    @pytest.mark.anyio
    async def test_calls_delete_with_both_filters(self, sb):
        from backend.repositories.corporate_repo import delete_allowed_domain

        _set_execute(sb, data=[])
        await delete_allowed_domain(company_id="c1", domain="acme.com")
        sb.eq.assert_any_call("company_id", "c1")
        sb.eq.assert_any_call("domain", "acme.com")
        sb.delete.assert_called_once()


class TestFindCompaniesByEmailDomain:
    @pytest.mark.anyio
    async def test_returns_active_companies(self, sb):
        from backend.repositories.corporate_repo import find_companies_by_email_domain

        _set_execute(
            sb,
            data=[{"company_id": "c1", "corporate_accounts": {"id": "c1", "name": "Acme", "status": "active"}}],
        )
        result = await find_companies_by_email_domain("acme.com")
        assert result[0]["corporate_accounts"]["status"] == "active"
        sb.eq.assert_any_call("domain", "acme.com")
        sb.eq.assert_any_call("corporate_accounts.status", "active")


# ═══════════════════════ Billing (Plan 6) ═══════════════════════


class TestListCompanyRidePaymentSources:
    @pytest.mark.anyio
    async def test_no_optional_filters(self, sb):
        from backend.repositories.corporate_repo import list_company_ride_payment_sources

        _set_execute(sb, data=[{"id": "s1"}])
        result = await list_company_ride_payment_sources(company_id="c1")
        assert result == [{"id": "s1"}]
        sb.range.assert_any_call(0, 499)

    @pytest.mark.anyio
    async def test_all_optional_filters_applied(self, sb):
        from backend.repositories.corporate_repo import list_company_ride_payment_sources

        _set_execute(sb, data=[])
        await list_company_ride_payment_sources(
            company_id="c1",
            from_iso="2026-07-01T00:00:00Z",
            to_iso="2026-08-01T00:00:00Z",
            member_id="m1",
            limit=50,
            offset=10,
        )
        sb.eq.assert_any_call("member_id", "m1")
        sb.gte.assert_any_call("created_at", "2026-07-01T00:00:00Z")
        sb.lte.assert_any_call("created_at", "2026-08-01T00:00:00Z")
        sb.range.assert_any_call(10, 59)


class TestGetCorporatePolicy:
    @pytest.mark.anyio
    async def test_found(self, sb):
        from backend.repositories.corporate_repo import get_corporate_policy

        _set_execute(sb, data=[{"id": "pol1", "company_id": "c1", "active": True}])
        result = await get_corporate_policy("c1")
        assert result["company_id"] == "c1"
        sb.eq.assert_any_call("active", True)

    @pytest.mark.anyio
    async def test_not_found(self, sb):
        from backend.repositories.corporate_repo import get_corporate_policy

        _set_execute(sb, data=[])
        assert await get_corporate_policy("c1") is None


class TestUpsertCorporatePolicy:
    @pytest.mark.anyio
    async def test_updates_existing_policy(self, sb):
        from backend.repositories.corporate_repo import upsert_corporate_policy

        existing = {"id": "pol1", "company_id": "c1", "active": True, "max_ride_amount": "50.00"}
        sb.execute.side_effect = [
            _res(data=[existing]),  # get_corporate_policy select
            _res(data=[{**existing, "max_ride_amount": "75.00"}]),  # update
        ]
        result = await upsert_corporate_policy("c1", {"max_ride_amount": "75.00"})
        assert result["max_ride_amount"] == "75.00"
        sb.eq.assert_any_call("id", "pol1")

    @pytest.mark.anyio
    async def test_update_falls_back_to_merged_existing_when_no_row_returned(self, sb):
        from backend.repositories.corporate_repo import upsert_corporate_policy

        existing = {"id": "pol1", "company_id": "c1", "active": True, "max_ride_amount": "50.00"}
        sb.execute.side_effect = [_res(data=[existing]), _res(data=[])]
        result = await upsert_corporate_policy("c1", {"max_ride_amount": "75.00"})
        assert result["max_ride_amount"] == "75.00"
        assert result["id"] == "pol1"

    @pytest.mark.anyio
    async def test_inserts_new_policy_when_none_exists(self, sb):
        from backend.repositories.corporate_repo import upsert_corporate_policy

        sb.execute.side_effect = [
            _res(data=[]),  # get_corporate_policy select -> none
            _res(data=[{"id": "pol2", "company_id": "c1", "active": True, "max_ride_amount": "100.00"}]),
        ]
        result = await upsert_corporate_policy("c1", {"max_ride_amount": "100.00"})
        assert result["id"] == "pol2"
        insert_payload = sb.insert.call_args.args[0]
        assert insert_payload["company_id"] == "c1"
        assert insert_payload["active"] is True
        assert insert_payload["max_ride_amount"] == "100.00"

    @pytest.mark.anyio
    async def test_insert_falls_back_to_insert_doc_when_no_row_returned(self, sb):
        from backend.repositories.corporate_repo import upsert_corporate_policy

        sb.execute.side_effect = [_res(data=[]), _res(data=[])]
        result = await upsert_corporate_policy("c1", {"max_ride_amount": "100.00"})
        assert result["company_id"] == "c1"
        assert result["max_ride_amount"] == "100.00"
        assert result["active"] is True
