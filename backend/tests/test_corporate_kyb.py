# backend/tests/test_corporate_kyb.py
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import corporate_account_row

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def test_kyb_reviewed_by_column_is_text_not_uuid():
    """Regression: kyb_reviewed_by must be TEXT.

    Platform-admin ids are strings (e.g. 'admin-001'), not users.id UUIDs, so a
    UUID column rejects the reviewer id with Postgres 22P02 and the approve/reject
    endpoint 503s. Migration 27 shipped it as UUID; migration 213 widens it to TEXT.
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
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("pending_verification", id="c1")),
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
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=corporate_account_row("suspended")),
        ) as m_dec,
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("pending_verification", id="c1")),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"
    # Compare-and-set on the status read before the write.
    assert m_dec.await_args.kwargs["expected_status"] == "pending_verification"


def test_kyb_review_wallet_failure_is_partial_success(test_client, admin_override):
    """Corporate + admin portal review, gap #40: record_kyb_decision already
    committed status='active' before the wallet step runs, so a wallet
    failure must not raise a 503 that hides the fact the company is already
    approved — same partial-success shape as owner_bootstrap_error."""
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("pending_verification", id="c1")),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(side_effect=RuntimeError("db down")),
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
    data = resp.json()
    assert data["status"] == "active"  # status change already committed
    assert data["wallet_provisioning_error"] is True
    assert data["stripe_customer_creation_error"] is False


def test_kyb_review_success_reports_no_provisioning_errors(test_client, admin_override):
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("pending_verification", id="c1")),
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
    data = resp.json()
    assert data["wallet_provisioning_error"] is False
    assert data["stripe_customer_creation_error"] is False


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


# ── Closed-company guard: a KYB decision must never reopen a closed company ──


def _kyb_guard_patches(*, current, decision_row, settings=None):
    """Pre-read, decision write, provisioning and email — each returned so a
    test can assert on what did (not) run."""
    return (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(side_effect=list(current)),
        ),
        patch("db_supabase.record_kyb_decision", AsyncMock(return_value=decision_row)),
        patch("routes.corporate_accounts.ensure_corporate_wallet", AsyncMock(return_value={"id": "w1"})),
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value=settings if settings is not None else {"stripe_secret_key": ""}),
        ),
        patch("utils.email_provider.send_transactional_email", AsyncMock(return_value=True)),
    )


@pytest.mark.parametrize("approve", [True, False])
def test_kyb_review_on_closed_company_is_refused_without_status_write(test_client, admin_override, approve):
    closed = corporate_account_row("closed", id="c1", contact_email="owner@acme.com")
    p_read, p_dec, p_wallet, p_settings, p_mail = _kyb_guard_patches(current=[closed], decision_row=None)
    with p_read, p_dec as m_dec, p_wallet as m_wallet, p_settings, p_mail as m_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": approve},
        )
    assert resp.status_code == 409, resp.text
    assert "closed" in resp.json()["detail"].lower()
    m_dec.assert_not_awaited()  # no status write, no decision stamp
    m_wallet.assert_not_awaited()
    m_mail.assert_not_awaited()  # a closed company is not told it was "approved"


@pytest.mark.parametrize("current_status", ["pending_verification", "active", "suspended"])
def test_kyb_review_on_open_company_passes_read_status_as_expected(test_client, admin_override, current_status):
    current = corporate_account_row(current_status, id="c1")
    p_read, p_dec, p_wallet, p_settings, p_mail = _kyb_guard_patches(
        current=[current], decision_row=corporate_account_row("active", id="c1")
    )
    with p_read, p_dec as m_dec, p_wallet, p_settings, p_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert m_dec.await_args.kwargs["expected_status"] == current_status


def test_kyb_review_cas_loser_returns_409(test_client, admin_override):
    """Read 'pending_verification', then the company is closed before the
    write: the CAS UPDATE matches zero rows, the re-read shows 'closed' → 409
    and none of the post-decision side effects run."""
    pending = corporate_account_row("pending_verification", id="c1", contact_email="owner@acme.com")
    closed = corporate_account_row("closed", id="c1")
    p_read, p_dec, p_wallet, p_settings, p_mail = _kyb_guard_patches(current=[pending, closed], decision_row=None)
    with p_read, p_dec as m_dec, p_wallet as m_wallet, p_settings, p_mail as m_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 409, resp.text
    assert "'closed'" in resp.json()["detail"]
    assert m_dec.await_args.kwargs["expected_status"] == "pending_verification"
    m_wallet.assert_not_awaited()
    m_mail.assert_not_awaited()


def test_kyb_review_cas_loser_row_gone_returns_404(test_client, admin_override):
    pending = corporate_account_row("pending_verification", id="c1")
    p_read, p_dec, p_wallet, p_settings, p_mail = _kyb_guard_patches(current=[pending, None], decision_row=None)
    with p_read, p_dec, p_wallet, p_settings, p_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 404, resp.text


def test_kyb_review_kill_switch_off_restores_unconditional_write(test_client, admin_override):
    """corporate_kyb_refuses_closed_company=false → no pre-read, no CAS (old behaviour)."""
    p_read, p_dec, p_wallet, p_settings, p_mail = _kyb_guard_patches(
        current=[],
        decision_row=corporate_account_row("suspended", id="c1"),
        settings={"corporate_kyb_refuses_closed_company": False},
    )
    with p_read as m_read, p_dec as m_dec, p_wallet, p_settings, p_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False},
        )
    assert resp.status_code == 200, resp.text
    m_read.assert_not_awaited()
    assert m_dec.await_args.kwargs["expected_status"] is None
