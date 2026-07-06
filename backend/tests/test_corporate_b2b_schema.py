# backend/tests/test_corporate_b2b_schema.py
"""Smoke test: the nine new B2B tables + new corporate_accounts columns exist.

Uses the project's sync Supabase client directly — `supabase-py` 2.x exposes
a synchronous `.execute()` that returns an APIResponse. See
`backend/db_supabase.py:run_sync` for the async wrapper used in app code;
these marker-gated integration tests don't need the threadpool hop.
"""

import re
from pathlib import Path

import pytest

from db_supabase import supabase

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _migration_seq(path: Path) -> tuple:
    head = path.name.split("_", 1)[0]
    return (int(head) if head.isdigit() else 1_000_000, path.name)


def test_actor_user_id_is_text_not_uuid():
    """Regression: corporate_wallet_transactions.actor_user_id must be TEXT.

    The admin manual wallet-adjust flow records a PLATFORM admin id ('admin-001',
    a string) as the actor. A UUID column / UUID RPC parameter rejects it with
    Postgres 22P02 (same failure class as kyb_reviewed_by). Migration 27 shipped
    both as UUID; migration 209 widens the column and the p_actor_user_id RPC
    parameter to TEXT. Guard against a re-narrowing of either.

    Comments are stripped so rollback examples inside comment blocks (which name
    the UUID type) don't count as live schema.
    """
    col_type = None
    param_type = None
    for sql in sorted(_MIGRATIONS_DIR.glob("*.sql"), key=_migration_seq):
        live = "\n".join(line.split("--", 1)[0] for line in sql.read_text().splitlines())
        for m in re.finditer(r"actor_user_id\s+(UUID|TEXT)", live, re.IGNORECASE):
            col_type = m.group(1).upper()
        for m in re.finditer(r"ALTER COLUMN\s+actor_user_id\s+TYPE\s+(UUID|TEXT)", live, re.IGNORECASE):
            col_type = m.group(1).upper()
        for m in re.finditer(r"p_actor_user_id\s+(UUID|TEXT)", live, re.IGNORECASE):
            param_type = m.group(1).upper()

    assert col_type == "TEXT", f"actor_user_id column resolved to {col_type}; admin ids are non-UUID strings"
    assert param_type == "TEXT", f"p_actor_user_id RPC param resolved to {param_type}; admin ids are non-UUID strings"

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
    # Guest booking (migration 206)
    "corporate_sections",
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
@pytest.mark.skip(reason="Integration test requires live Supabase connection — run manually against staging/prod")
def test_b2b_tables_exist():
    for t in REQUIRED_TABLES:
        col = _PROBE_COLUMN.get(t, "id")
        # Reading zero rows is enough — table absence raises APIError.
        resp = supabase.table(t).select(col).limit(1).execute()
        assert resp.data is not None, f"table {t} missing"


@pytest.mark.integration
@pytest.mark.skip(reason="Integration test requires live Supabase connection — run manually against staging/prod")
def test_corporate_accounts_has_new_columns():
    resp = supabase.table("corporate_accounts").select(",".join(REQUIRED_CORP_COLS)).limit(1).execute()
    assert resp.data is not None
