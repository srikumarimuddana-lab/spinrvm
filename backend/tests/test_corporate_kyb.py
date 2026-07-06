# backend/tests/test_corporate_kyb.py
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def test_kyb_reviewed_by_column_is_text_not_uuid():
    """Regression: kyb_reviewed_by must be TEXT.

    Platform-admin ids are strings (e.g. 'admin-001'), not users.id UUIDs, so a
    UUID column rejects the reviewer id with Postgres 22P02 and the approve/reject
    endpoint 503s. Migration 27 shipped it as UUID; migration 208 widens it to TEXT.
    Guard against a re-narrowing.
    """
    # Walk migrations in order; the last DDL that (re)types kyb_reviewed_by wins.
    # Strip `--` comments first so rollback examples inside comment blocks (which
    # legitimately name the UUID type) don't count as live schema.
    patterns = (
        r"kyb_reviewed_by\s+(UUID|TEXT)",  # ADD COLUMN ... kyb_reviewed_by <type>
        r"ALTER COLUMN\s+kyb_reviewed_by\s+TYPE\s+(UUID|TEXT)",  # widening
    )
    def _seq(path: Path) -> tuple:
        head = path.name.split("_", 1)[0]
        return (int(head) if head.isdigit() else 1_000_000, path.name)

    typ = None
    for sql in sorted(_MIGRATIONS_DIR.glob("*.sql"), key=_seq):
        live = "\n".join(line.split("--", 1)[0] for line in sql.read_text().splitlines())
        for pat in patterns:
            for m in re.finditer(pat, live, re.IGNORECASE):
                typ = m.group(1).upper()
    assert typ == "TEXT", f"kyb_reviewed_by resolved to {typ}; admin ids are non-UUID strings"


def test_approve_kyb_flips_status_to_active(test_client, admin_override):
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": ""}),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


def test_reject_kyb_flips_status_to_suspended(test_client, admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=corporate_account_row("suspended")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


def test_kyb_review_404_on_missing_company(test_client, admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/nonexistent/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 404, resp.text
