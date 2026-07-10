"""Portal funding endpoints (M5.4): allocate, section wallet, Checkout top-ups."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.corporate_wallet_service import TransferBelowFloor

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}

_ROUTE = "routes.corporate_company_wallet"


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _admin_guard():
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin", "id": "m1"}]),
    )


def _head_guard(section_head="m1"):
    # require_section_head does its own membership + section lookups.
    return (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "member", "id": "m1"}]),
        ),
        patch(
            "db_supabase.get_rows",
            AsyncMock(
                return_value=[{"id": "s1", "company_id": "c1", "head_member_id": section_head, "name": "Service"}]
            ),
        ),
    )


_COMPANY = {"id": "c1", "name": "Acme", "status": "active", "stripe_customer_id": "cus_1"}
_SECTION_ROW = [{"id": "s1", "company_id": "c1", "name": "Service"}]


def _section_lookup(rows=None):
    return patch(
        "routes.corporate_company_wallet.get_rows",
        AsyncMock(return_value=_SECTION_ROW if rows is None else rows),
    )


_MASTER = {"id": "w-master", "company_id": "c1", "section_id": None, "balance": 500, "soft_negative_floor": -50}
_SECTION_W = {"id": "w-sec", "company_id": "c1", "section_id": "s1", "balance": 100}


# ── allocate ─────────────────────────────────────────────────────────────────


def test_allocate_master_to_section(test_client, rider_override):
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        _section_lookup(),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.ensure_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(
            f"{_ROUTE}.transfer_between_wallets",
            AsyncMock(return_value={"from_balance": 400, "to_balance": 200}),
        ) as m_transfer,
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s1", "amount": "100.00"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["from_balance"] == 400 and data["to_balance"] == 200
    kwargs = m_transfer.await_args.kwargs
    assert kwargs["from_wallet_id"] == "w-master"
    assert kwargs["to_wallet_id"] == "w-sec"
    assert kwargs["amount"] == Decimal("100.00")
    assert kwargs["idempotency_key"]  # always idempotent


def test_allocate_clawback_direction(test_client, rider_override):
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        _section_lookup(),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.ensure_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(
            f"{_ROUTE}.transfer_between_wallets",
            AsyncMock(return_value={"from_balance": 0, "to_balance": 600}),
        ) as m_transfer,
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s1", "amount": "100.00", "direction": "to_master"},
        )
    assert resp.status_code == 200, resp.text
    kwargs = m_transfer.await_args.kwargs
    assert kwargs["from_wallet_id"] == "w-sec"
    assert kwargs["to_wallet_id"] == "w-master"


def test_allocate_floor_breach_is_409(test_client, rider_override):
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        _section_lookup(),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.ensure_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(
            f"{_ROUTE}.transfer_between_wallets",
            AsyncMock(side_effect=TransferBelowFloor("wallet_below_floor: new=-100 floor=-50")),
        ),
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s1", "amount": "1000.00"},
        )
    assert resp.status_code == 409
    assert "credit" in resp.json()["detail"].lower()


def test_allocate_requires_admin(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member", "id": "m1"}]),
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s1", "amount": "100.00"},
        )
    assert resp.status_code == 403


def test_allocate_blocked_for_non_active_company(test_client, rider_override):
    with (
        _admin_guard(),
        patch(
            f"{_ROUTE}.get_corporate_account_by_id",
            AsyncMock(return_value={**_COMPANY, "status": "pending_verification"}),
        ),
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s1", "amount": "100.00"},
        )
    assert resp.status_code == 409


# ── section wallet read ──────────────────────────────────────────────────────


def test_section_wallet_head_can_read(test_client, rider_override):
    g1, g2 = _head_guard(section_head="m1")
    with (
        g1,
        g2,
        patch(f"{_ROUTE}.get_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(f"{_ROUTE}.list_wallet_transactions", AsyncMock(return_value=[{"id": "t1"}])),
    ):
        resp = test_client.get("/company/c1/sections/s1/wallet")
    assert resp.status_code == 200, resp.text
    assert resp.json()["wallet"]["id"] == "w-sec"


def test_section_wallet_non_head_member_403(test_client, rider_override):
    g1, g2 = _head_guard(section_head="OTHER")
    with g1, g2:
        resp = test_client.get("/company/c1/sections/s1/wallet")
    assert resp.status_code == 403


# ── Checkout top-up sessions ─────────────────────────────────────────────────


def _checkout_mock():
    session = MagicMock()
    session.url = "https://checkout.stripe.com/pay/cs_123"
    session.id = "cs_123"
    return patch(f"{_ROUTE}.stripe.checkout.Session.create", MagicMock(return_value=session))


def test_master_topup_session_metadata_on_payment_intent(test_client, rider_override):
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        _checkout_mock() as m_create,
    ):
        resp = test_client.post("/company/c1/wallet/topup-session", json={"amount": "500.00"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    kwargs = m_create.call_args.kwargs
    # The webhook reads metadata off the PAYMENT INTENT — Checkout doesn't
    # copy session metadata to the PI automatically.
    pi_meta = kwargs["payment_intent_data"]["metadata"]
    assert pi_meta["scope"] == "corporate_topup"
    assert pi_meta["wallet_id"] == "w-master"
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 50000


def test_section_topup_daily_cap_429(test_client, rider_override):
    g1, g2 = _head_guard(section_head="m1")
    with (
        g1,
        g2,
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        patch(f"{_ROUTE}.ensure_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(f"{_ROUTE}.sum_autotopups_today", AsyncMock(return_value=Decimal("4800"))),
    ):
        resp = test_client.post("/company/c1/sections/s1/wallet/topup-session", json={"amount": "500.00"})
    assert resp.status_code == 429  # 4800 + 500 > 5000 (Q14)


def test_section_topup_under_cap_creates_session(test_client, rider_override):
    g1, g2 = _head_guard(section_head="m1")
    with (
        g1,
        g2,
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        patch(f"{_ROUTE}.ensure_section_wallet", AsyncMock(return_value=_SECTION_W)),
        patch(f"{_ROUTE}.sum_autotopups_today", AsyncMock(return_value=Decimal("0"))),
        patch(f"{_ROUTE}.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        _checkout_mock() as m_create,
    ):
        resp = test_client.post("/company/c1/sections/s1/wallet/topup-session", json={"amount": "500.00"})
    assert resp.status_code == 200, resp.text
    pi_meta = m_create.call_args.kwargs["payment_intent_data"]["metadata"]
    assert pi_meta["wallet_id"] == "w-sec"  # credits the SECTION wallet


def test_topup_amount_bounds_422(test_client, rider_override):
    with _admin_guard():
        resp = test_client.post("/company/c1/wallet/topup-session", json={"amount": "50.00"})
    assert resp.status_code == 422  # below $100 per-txn minimum


# ── transfer_between_wallets service wrapper ─────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_maps_floor_error():
    import services.corporate_wallet_service as svc

    rpc_fail = MagicMock()
    rpc_fail.rpc.return_value.execute.side_effect = RuntimeError("wallet_below_floor: new=-1 floor=0")
    with patch.object(svc, "supabase", rpc_fail):
        with pytest.raises(TransferBelowFloor):
            await svc.transfer_between_wallets(
                from_wallet_id="a", to_wallet_id="b", amount="10.00", idempotency_key="k1"
            )


@pytest.mark.asyncio
async def test_transfer_unique_violation_replays_winner():
    # Reviewer-flagged race: concurrent same-key first attempts — the loser
    # hits the ledger unique index; the wrapper must return the winner's legs
    # instead of surfacing the raw DB error.
    import services.corporate_wallet_service as svc

    sb = MagicMock()
    sb.rpc.return_value.execute.side_effect = RuntimeError(
        'duplicate key value violates unique constraint "corp_wtxn_stripe_pi_unique" (23505)'
    )
    ledger = MagicMock()
    ledger.data = [
        {"id": "t-out", "balance_after": 400, "stripe_payment_intent_id": "k1:out"},
        {"id": "t-in", "balance_after": 200, "stripe_payment_intent_id": "k1:in"},
    ]
    sb.table.return_value.select.return_value.in_.return_value.execute.return_value = ledger
    with patch.object(svc, "supabase", sb):
        result = await svc.transfer_between_wallets(
            from_wallet_id="a", to_wallet_id="b", amount="10.00", idempotency_key="k1"
        )
    assert result == {
        "out_txn_id": "t-out",
        "in_txn_id": "t-in",
        "from_balance": 400,
        "to_balance": 200,
    }


@pytest.mark.asyncio
async def test_transfer_rejects_non_positive_amount():
    import services.corporate_wallet_service as svc

    with pytest.raises(ValueError):
        await svc.transfer_between_wallets(from_wallet_id="a", to_wallet_id="b", amount="0")


@pytest.mark.asyncio
async def test_apply_topup_derives_section_scope():
    # Webhook-credited section top-ups must ledger as 'section:<id>', not
    # a hardcoded 'master' (mislabels per-scope reporting/reconciliation).
    import services.corporate_wallet_service as svc

    sb = MagicMock()
    wallet_lookup = MagicMock()
    wallet_lookup.data = [{"id": "w-sec", "section_id": "s1"}]
    sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = wallet_lookup
    rpc_res = MagicMock()
    rpc_res.data = [{"transaction_id": "t1", "balance_after": 600}]
    sb.rpc.return_value.execute.return_value = rpc_res
    with patch.object(svc, "supabase", sb):
        await svc.apply_topup(wallet_id="w-sec", amount="500.00", stripe_payment_intent_id="pi_1")
    params = sb.rpc.call_args.args[1]
    assert params["p_scope"] == "section:s1"


def test_allocate_foreign_section_is_404(test_client, rider_override):
    # Tenancy (money-audit warning): a section_id from another company must
    # not provision a mislabeled wallet.
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        _section_lookup(rows=[]),
    ):
        resp = test_client.post(
            "/company/c1/wallet/allocate",
            json={"section_id": "s-foreign", "amount": "100.00"},
        )
    assert resp.status_code == 404


def test_checkout_idempotency_key_is_deterministic(test_client, rider_override):
    # Money-audit BLOCKER: a double-submit must reuse the SAME Checkout
    # Session (same idempotency key), never mint a second real charge.
    keys = []
    session = MagicMock()
    session.url = "https://checkout.stripe.com/pay/cs_1"
    session.id = "cs_1"

    def _capture(**kwargs):
        keys.append(kwargs["idempotency_key"])
        return session

    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch(f"{_ROUTE}.stripe.checkout.Session.create", MagicMock(side_effect=_capture)),
    ):
        for _ in range(2):
            resp = test_client.post("/company/c1/wallet/topup-session", json={"amount": "500.00"})
            assert resp.status_code == 200, resp.text
    assert keys[0] == keys[1]  # same minute-bucket → same session, one charge


def test_checkout_client_key_wins(test_client, rider_override):
    session = MagicMock()
    session.url = "https://checkout.stripe.com/pay/cs_1"
    session.id = "cs_1"
    with (
        _admin_guard(),
        patch(f"{_ROUTE}.get_corporate_account_by_id", AsyncMock(return_value=_COMPANY)),
        patch(f"{_ROUTE}.get_master_wallet", AsyncMock(return_value=_MASTER)),
        patch(f"{_ROUTE}.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch(f"{_ROUTE}.stripe.checkout.Session.create", MagicMock(return_value=session)) as m_create,
    ):
        resp = test_client.post(
            "/company/c1/wallet/topup-session",
            json={"amount": "500.00", "client_key": "portal-click-abc"},
        )
    assert resp.status_code == 200
    assert m_create.call_args.kwargs["idempotency_key"] == "portal-click-abc"


@pytest.mark.asyncio
async def test_wallet_scope_missing_wallet_raises():
    # Never silently ledger as 'master' when the wallet row is missing.
    import services.corporate_wallet_service as svc

    sb = MagicMock()
    empty = MagicMock()
    empty.data = []
    sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = empty
    with patch.object(svc, "supabase", sb):
        with pytest.raises(RuntimeError):
            await svc._wallet_scope("w-ghost")
