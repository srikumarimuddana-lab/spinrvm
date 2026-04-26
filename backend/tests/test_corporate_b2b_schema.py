# backend/tests/test_corporate_b2b_schema.py
"""Smoke test: the nine new B2B tables + new corporate_accounts columns exist.

Uses the project's sync Supabase client directly — `supabase-py` 2.x exposes
a synchronous `.execute()` that returns an APIResponse. See
`backend/db_supabase.py:run_sync` for the async wrapper used in app code;
these marker-gated integration tests don't need the threadpool hop.
"""

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


# ride_payment_sources uses ride_id as its PK instead of a surrogate id column
# (see migration 27 §9) — probe a column that definitely exists on each table.
_PROBE_COLUMN = {
    "ride_payment_sources": "ride_id",
}


@pytest.mark.integration
def test_b2b_tables_exist():
    for t in REQUIRED_TABLES:
        col = _PROBE_COLUMN.get(t, "id")
        # Reading zero rows is enough — table absence raises APIError.
        resp = supabase.table(t).select(col).limit(1).execute()
        assert resp.data is not None, f"table {t} missing"


@pytest.mark.integration
def test_corporate_accounts_has_new_columns():
    resp = supabase.table("corporate_accounts").select(",".join(REQUIRED_CORP_COLS)).limit(1).execute()
    assert resp.data is not None
