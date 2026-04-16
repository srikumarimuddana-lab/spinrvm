# backend/tests/test_corporate_b2b_schema.py
"""Smoke test: the nine new B2B tables + new corporate_accounts columns exist."""
import pytest

from db_supabase import supabase


REQUIRED_TABLES = [
    "corporate_wallets",
    "corporate_wallet_transactions",
    "corporate_members",
    "corporate_member_allowances",
    "corporate_allowance_requests",
    "corporate_policies",
    "corporate_allowed_domains",
    "ride_payment_sources",
    "corporate_policy_evaluations",
]

REQUIRED_CORP_COLS = [
    "legal_name",
    "business_number",
    "country_code",
    "currency",
    "tax_region",
    "timezone",
    "locale",
    "billing_email",
    "stripe_customer_id",
    "status",
    "size_tier",
    "kyb_document_url",
    "kyb_reviewed_at",
    "kyb_reviewed_by",
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b2b_tables_exist():
    for t in REQUIRED_TABLES:
        # Reading zero rows is enough — table absence raises APIError.
        resp = await supabase.table(t).select("id").limit(1).execute()
        assert resp.data is not None, f"table {t} missing"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_corporate_accounts_has_new_columns():
    resp = await (
        supabase.table("corporate_accounts")
        .select(",".join(REQUIRED_CORP_COLS))
        .limit(1)
        .execute()
    )
    assert resp.data is not None
