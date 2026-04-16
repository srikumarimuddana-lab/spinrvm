# Corporate B2B — Plan 2: Master Wallet + Stripe

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fund the master wallet via Stripe top-ups (manual + auto), handle webhook-driven idempotent credits, expose balance + ledger to super-admins, and freeze the wallet when a company is suspended. After this plan, the company has money available to be spent — Plan 3 wires that to employees, Plan 5 to rides.

**Architecture:**
- New `backend/services/corporate_wallet_service.py` owns all wallet arithmetic behind a thin, async-locking interface. Routes never touch ledger rows directly.
- New `backend/routes/corporate_wallet.py` for super-admin endpoints. Mounted under `/admin/corporate-accounts/{id}/wallet`.
- Stripe integration reuses `get_app_settings()` for secrets, and extends `backend/routes/webhooks.py` to handle `payment_intent.succeeded` when the PI has `metadata.scope="corporate_topup"`.
- Auto-top-up runs as a scheduled task (pattern: `backend/utils/scheduled_rides.py`).

**Tech Stack:** FastAPI, Stripe Python SDK, Supabase (Postgres), pytest, Next.js (admin dashboard).

**Spec:** `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md` §4 (wallet), §7 (super-admin wallet view).

**Prerequisite:** Plan 1 is merged (schema, schemas, validators, super-admin foundation).

**Out of scope:** per-employee allowances (Plan 3), policy engine (Plan 4), ride debits (Plan 5), company-facing portal (Plan 7).

---

## ⚠️ Codebase async/sync pattern (read before implementing any Supabase code)

`supabase-py 2.x` in this repo is **synchronous**. `supabase.table(...).select(...).execute()` and `supabase.rpc(name, params).execute()` return an `APIResponse` directly — **do not `await` them**.

The codebase wraps sync calls with `run_sync` (defined at `backend/db_supabase.py:18-32`). Every async helper in `db_supabase.py` follows this shape:

```python
async def some_helper(...) -> ...:
    def _fn():
        res = supabase.table("x").select("*")...execute()    # SYNC
        return _rows_from_res(res)                            # or _single_row_from_res
    return await run_sync(_fn)
```

**For RPC calls** (wallet arithmetic, etc.) the pattern is identical — `.rpc(name, params)` returns a query builder, `.execute()` is sync:

```python
def _fn():
    res = supabase.rpc("my_function", params).execute()
    return _rows_from_res(res)
return await run_sync(_fn)
```

**For test mocks:** `.execute()` is called inside the `_fn` closure which is offloaded to a threadpool — mock it with `MagicMock(return_value=...)`, NOT `AsyncMock`. An `AsyncMock` returns a coroutine that the sync closure can't `await`, and the chained access (`res.data`) will dereference the coroutine instead of the APIResponse.

Every code snippet below that uses Supabase follows this pattern. If a snippet in this plan shows `await q.execute()` or `await supabase.rpc(...)`, treat it as a plan typo and convert to the `run_sync` form. See Plan 1 Task 4 for worked examples.

---

## Task 1: Wallet bootstrap on company activation

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_wallet_bootstrap.py`

- [ ] **Step 1: Write failing test — wallet row is created when status flips to active**

```python
# backend/tests/test_corporate_wallet_bootstrap.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_wallet_created_on_kyb_approval(test_client, auth_headers):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={"id": "c1", "status": "active"}),
    ) as m_kyb, patch(
        "db_supabase.ensure_corporate_wallet", AsyncMock(return_value={"id": "w1", "company_id": "c1", "balance": 0})
    ) as m_wallet, patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    m_kyb.assert_awaited_once()
    m_wallet.assert_awaited_once_with(company_id="c1")


@pytest.mark.asyncio
async def test_wallet_not_created_on_rejection(test_client, auth_headers):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={"id": "c1", "status": "suspended"}),
    ), patch(
        "db_supabase.ensure_corporate_wallet", AsyncMock()
    ) as m_wallet, patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "bad doc"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    m_wallet.assert_not_awaited()
```

- [ ] **Step 2: Run — expect FAIL (wallet helper not called)**

```bash
pytest backend/tests/test_corporate_wallet_bootstrap.py -v
```

- [ ] **Step 3: Implement `ensure_corporate_wallet` in `db_supabase.py`**

```python
async def ensure_corporate_wallet(*, company_id: str) -> dict:
    """Idempotently create the master wallet for a company. Returns the row."""
    existing = await (
        supabase.table("corporate_wallets")
        .select("*")
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]
    resp = await (
        supabase.table("corporate_wallets")
        .insert({"company_id": company_id, "balance": 0, "currency": "CAD"})
        .execute()
    )
    return (resp.data or [{}])[0]
```

- [ ] **Step 4: Hook into KYB approval handler**

Edit the `kyb_review` route in `backend/routes/corporate_accounts.py`:

```python
from db_supabase import ensure_corporate_wallet  # add to imports

# inside kyb_review, after record_kyb_decision succeeds:
if decision.approve:
    await ensure_corporate_wallet(company_id=company_id)
```

- [ ] **Step 5: Run tests**

```bash
pytest backend/tests/test_corporate_wallet_bootstrap.py -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/db_supabase.py backend/tests/test_corporate_wallet_bootstrap.py
git commit -m "feat(corporate): create master wallet on KYB approval"
```

---

## Task 2: Stripe customer bootstrap

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_stripe_customer.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_stripe_customer.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_stripe_customer_created_on_kyb_approval(test_client, auth_headers):
    fake_cust = MagicMock(id="cus_ABC")
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={
            "id": "c1", "status": "active", "legal_name": "Acme Inc",
            "billing_email": "billing@acme.test",
        }),
    ), patch(
        "db_supabase.ensure_corporate_wallet", AsyncMock(return_value={"id": "w1"})
    ), patch(
        "db_supabase.update_corporate_stripe_customer_id", AsyncMock()
    ) as m_update, patch(
        "stripe.Customer.create", return_value=fake_cust
    ), patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    m_update.assert_awaited_once_with(company_id="c1", stripe_customer_id="cus_ABC")
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_stripe_customer.py -v
```

- [ ] **Step 3: Add `update_corporate_stripe_customer_id` helper**

Append to `backend/db_supabase.py`:

```python
async def update_corporate_stripe_customer_id(*, company_id: str, stripe_customer_id: str) -> None:
    await (
        supabase.table("corporate_accounts")
        .update({"stripe_customer_id": stripe_customer_id})
        .eq("id", company_id)
        .execute()
    )
```

- [ ] **Step 4: Extend KYB approval handler**

```python
# backend/routes/corporate_accounts.py — inside kyb_review, after ensure_corporate_wallet:
if decision.approve and not row.get("stripe_customer_id"):
    import stripe
    from settings_loader import get_app_settings
    from db_supabase import update_corporate_stripe_customer_id

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if stripe_secret:
        customer = stripe.Customer.create(
            email=row.get("billing_email"),
            name=row.get("legal_name") or row.get("name"),
            metadata={"corporate_account_id": company_id},
            api_key=stripe_secret,
        )
        await update_corporate_stripe_customer_id(
            company_id=company_id, stripe_customer_id=customer.id
        )
```

- [ ] **Step 5: Run tests**

```bash
pytest backend/tests/test_corporate_stripe_customer.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/db_supabase.py backend/tests/test_corporate_stripe_customer.py
git commit -m "feat(corporate): create Stripe customer on KYB approval"
```

---

## Task 3: Wallet service — credit / debit with row-level locking

**Files:**
- Create: `backend/services/corporate_wallet_service.py`
- Create: `backend/tests/services/test_corporate_wallet_service.py`

**Design note:** All money moves go through this service. Each movement is a single DB transaction that locks the wallet row (`SELECT ... FOR UPDATE`), applies the delta, inserts a ledger row, commits. The Supabase client does not expose row-level locking directly; we use a Postgres function (RPC) wrapper.

- [ ] **Step 1: Add the Postgres function as a migration**

Create `backend/migrations/28_corporate_wallet_rpc.sql`:

```sql
-- Atomic wallet movement with row-lock + ledger insert
CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id          UUID,
    p_scope              TEXT,
    p_type               TEXT,
    p_delta              NUMERIC(12,2),
    p_ride_id            UUID DEFAULT NULL,
    p_member_id          UUID DEFAULT NULL,
    p_stripe_pi          TEXT DEFAULT NULL,
    p_actor_user_id      UUID DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2))
LANGUAGE plpgsql
AS $$
DECLARE
    v_current   NUMERIC(12,2);
    v_new       NUMERIC(12,2);
    v_txn_id    UUID;
BEGIN
    -- Idempotency short-circuit: if stripe_payment_intent_id already in ledger, no-op
    IF p_stripe_pi IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.stripe_payment_intent_id = p_stripe_pi
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    SELECT balance INTO v_current
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_current IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    v_new := v_current + p_delta;

    -- Enforce soft-negative floor (master-scope only; member scope checked by caller)
    IF p_scope = 'master' AND p_floor IS NOT NULL AND v_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_new, p_floor;
    END IF;

    UPDATE corporate_wallets
    SET balance = v_new, updated_at = now()
    WHERE id = p_wallet_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, ride_id, member_id,
         stripe_payment_intent_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, p_scope, p_type, p_delta, v_new, p_ride_id, p_member_id,
         p_stripe_pi, p_actor_user_id, p_notes)
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    RETURN NEXT;
END
$$;
```

Run the migration in Supabase SQL editor.

- [ ] **Step 2: Write failing service tests**

```python
# backend/tests/services/test_corporate_wallet_service.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_topup_calls_rpc_with_positive_delta():
    rpc_resp = MagicMock(data=[{"transaction_id": "t1", "balance_after": "100.00"}])
    mock_sb = MagicMock()
    mock_sb.rpc = AsyncMock(return_value=rpc_resp)
    with patch("services.corporate_wallet_service.supabase", mock_sb):
        from services.corporate_wallet_service import apply_topup

        result = await apply_topup(
            wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123", actor_user_id=None
        )
    assert result["balance_after"] == "100.00"
    mock_sb.rpc.assert_awaited_once()
    args = mock_sb.rpc.call_args
    assert args.args[0] == "corporate_wallet_apply_delta"
    params = args.args[1]
    assert params["p_delta"] == 100
    assert params["p_type"] == "topup"
    assert params["p_scope"] == "master"


@pytest.mark.asyncio
async def test_idempotent_on_duplicate_stripe_pi():
    # First call and second call return the same ledger row because the RPC
    # short-circuits on the existing stripe_payment_intent_id.
    rpc_resp = MagicMock(data=[{"transaction_id": "t1", "balance_after": "100.00"}])
    mock_sb = MagicMock()
    mock_sb.rpc = AsyncMock(return_value=rpc_resp)
    with patch("services.corporate_wallet_service.supabase", mock_sb):
        from services.corporate_wallet_service import apply_topup

        a = await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123")
        b = await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123")
    assert a == b


@pytest.mark.asyncio
async def test_adjustment_routes_through_rpc():
    rpc_resp = MagicMock(data=[{"transaction_id": "t2", "balance_after": "-25.00"}])
    mock_sb = MagicMock()
    mock_sb.rpc = AsyncMock(return_value=rpc_resp)
    with patch("services.corporate_wallet_service.supabase", mock_sb):
        from services.corporate_wallet_service import apply_adjustment

        await apply_adjustment(
            wallet_id="w1", amount=-25, notes="manual correction", actor_user_id="admin_1",
            floor=-50,
        )
    params = mock_sb.rpc.call_args.args[1]
    assert params["p_type"] == "adjustment"
    assert params["p_delta"] == -25
    assert params["p_floor"] == -50
```

- [ ] **Step 3: Run — expect FAIL**

```bash
pytest backend/tests/services/test_corporate_wallet_service.py -v
```

- [ ] **Step 4: Implement the service**

Create `backend/services/corporate_wallet_service.py`:

```python
"""Money movements for corporate wallets.

All deltas (top-ups, ride debits, adjustments, refunds) go through this
service. It wraps the Postgres function `corporate_wallet_apply_delta`
which enforces row-level locking, idempotency on stripe_payment_intent_id,
and optional soft-negative-floor enforcement.
"""
from __future__ import annotations

from typing import Optional

try:
    from ..db_supabase import supabase
except ImportError:
    from db_supabase import supabase  # type: ignore


async def _apply(
    *,
    wallet_id: str,
    scope: str,
    type_: str,
    delta: float,
    ride_id: Optional[str] = None,
    member_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[float] = None,
) -> dict:
    params = {
        "p_wallet_id": wallet_id,
        "p_scope": scope,
        "p_type": type_,
        "p_delta": delta,
        "p_ride_id": ride_id,
        "p_member_id": member_id,
        "p_stripe_pi": stripe_payment_intent_id,
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": floor,
    }
    resp = await supabase.rpc("corporate_wallet_apply_delta", params)
    rows = resp.data or []
    if not rows:
        raise RuntimeError("wallet RPC returned no row")
    return rows[0]


async def apply_topup(
    *,
    wallet_id: str,
    amount: float,
    stripe_payment_intent_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    if amount <= 0:
        raise ValueError("top-up amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="topup",
        delta=amount,
        stripe_payment_intent_id=stripe_payment_intent_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )


async def apply_adjustment(
    *,
    wallet_id: str,
    amount: float,
    notes: str,
    actor_user_id: str,
    floor: Optional[float] = None,
) -> dict:
    """Positive or negative adjustment to the master wallet (support/refund).

    Signed. Notes required so the audit trail is useful.
    """
    if amount == 0:
        raise ValueError("adjustment amount cannot be zero")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="adjustment",
        delta=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=floor,
    )


async def apply_refund(
    *,
    wallet_id: str,
    amount: float,
    ride_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    if amount <= 0:
        raise ValueError("refund amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="refund",
        delta=amount,
        ride_id=ride_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest backend/tests/services/test_corporate_wallet_service.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/corporate_wallet_service.py backend/tests/services/test_corporate_wallet_service.py backend/migrations/28_corporate_wallet_rpc.sql
git commit -m "feat(corporate): wallet service + Postgres RPC with lock/idempotency"
```

---

## Task 4: Super-admin top-up endpoint (manual funding by ops)

**Files:**
- Create: `backend/routes/corporate_wallet.py`
- Modify: `backend/server.py` (or wherever routers are mounted — match existing pattern)
- Test: `backend/tests/test_corporate_wallet_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_corporate_wallet_routes.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_admin_manual_topup_creates_payment_intent(test_client, auth_headers):
    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "active", "stripe_customer_id": "cus_A"}),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "balance": "0.00"}),
    ), patch(
        "stripe.PaymentIntent.create",
        return_value=MagicMock(id="pi_x", client_secret="pi_x_secret"),
    ), patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_secret"] == "pi_x_secret"
    assert body["payment_intent_id"] == "pi_x"


@pytest.mark.asyncio
async def test_topup_rejects_below_minimum(test_client, auth_headers):
    with patch("dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 50},
            headers=auth_headers,
        )
    assert resp.status_code == 422  # Pydantic validation


@pytest.mark.asyncio
async def test_topup_rejects_if_company_not_active(test_client, auth_headers):
    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "pending_verification"}),
    ), patch("dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
            headers=auth_headers,
        )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run — expect 404**

```bash
pytest backend/tests/test_corporate_wallet_routes.py -v
```

- [ ] **Step 3: Add helpers**

In `backend/db_supabase.py`:

```python
async def get_corporate_wallet_by_company(company_id: str) -> dict | None:
    resp = await (
        supabase.table("corporate_wallets")
        .select("*")
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Create the wallet route file**

Create `backend/routes/corporate_wallet.py`:

```python
"""Super-admin corporate wallet endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..dependencies import get_admin_user
    from ..db_supabase import (
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
    )
    from ..services.corporate_wallet_service import apply_adjustment
    from ..settings_loader import get_app_settings
    from ..validators import validate_id
except ImportError:
    from dependencies import get_admin_user  # type: ignore
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
    )
    from services.corporate_wallet_service import apply_adjustment  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from validators import validate_id  # type: ignore

import stripe


router = APIRouter(prefix="/admin/corporate-accounts", tags=["Corporate Wallet"])


class TopUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(..., ge=100, le=10000, description="CAD between 100 and 10000")
    payment_method_id: Optional[str] = None


class AdjustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float  # signed
    notes: str = Field(..., min_length=1, max_length=500)


@router.post("/{company_id}/wallet/topup")
async def manual_topup(
    company_id: str,
    body: TopUpRequest,
    current_admin: dict = Depends(get_admin_user),
):
    validate_id(company_id)
    company = await get_corporate_account_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.get("status") != "active":
        raise HTTPException(status_code=409, detail="Company is not active")
    if not company.get("stripe_customer_id"):
        raise HTTPException(status_code=409, detail="Company has no Stripe customer")

    wallet = await get_corporate_wallet_by_company(company_id)
    if not wallet:
        raise HTTPException(status_code=500, detail="Wallet not provisioned")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    intent_kwargs = dict(
        amount=int(round(body.amount * 100)),  # cents
        currency="cad",
        customer=company["stripe_customer_id"],
        metadata={
            "scope": "corporate_topup",
            "company_id": company_id,
            "wallet_id": wallet["id"],
            "initiated_by": current_admin["id"],
        },
        api_key=stripe_secret,
    )
    if body.payment_method_id:
        intent_kwargs.update(
            payment_method=body.payment_method_id,
            off_session=True,
            confirm=True,
        )
    intent = stripe.PaymentIntent.create(**intent_kwargs)
    return {"payment_intent_id": intent.id, "client_secret": intent.client_secret}


@router.post("/{company_id}/wallet/adjust")
async def manual_adjust(
    company_id: str,
    body: AdjustRequest,
    current_admin: dict = Depends(get_admin_user),
):
    validate_id(company_id)
    wallet = await get_corporate_wallet_by_company(company_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    result = await apply_adjustment(
        wallet_id=wallet["id"],
        amount=body.amount,
        notes=body.notes,
        actor_user_id=current_admin["id"],
        floor=float(wallet.get("soft_negative_floor", -50)),
    )
    return result
```

- [ ] **Step 5: Mount the router**

Find where other routers are included in `backend/server.py` (or wherever) and add:

```python
from routes.corporate_wallet import router as corporate_wallet_router
app.include_router(corporate_wallet_router)
```

- [ ] **Step 6: Run tests**

```bash
pytest backend/tests/test_corporate_wallet_routes.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/routes/corporate_wallet.py backend/db_supabase.py backend/server.py backend/tests/test_corporate_wallet_routes.py
git commit -m "feat(corporate): super-admin wallet top-up + adjustment endpoints"
```

---

## Task 5: Stripe webhook for corporate top-up

**Files:**
- Modify: `backend/routes/webhooks.py`
- Test: `backend/tests/test_corporate_webhook.py`

**Design note:** The existing `webhooks.py` already handles `payment_intent.succeeded` for rider payments. We extend the handler: when `metadata.scope == "corporate_topup"`, route to the wallet service. Idempotency is doubly enforced: once by the existing `stripe_events` table (migration 22), once by the wallet RPC's `stripe_payment_intent_id` check.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_webhook.py
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _signed(body: dict) -> bytes:
    # For tests we bypass signature verification — patch construct_event.
    return json.dumps(body).encode()


@pytest.mark.asyncio
async def test_corporate_topup_webhook_credits_wallet(test_client):
    event = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": "pi_123",
            "amount_received": 50000,   # 500.00 CAD
            "currency": "cad",
            "metadata": {
                "scope": "corporate_topup",
                "company_id": "c1",
                "wallet_id": "w1",
                "initiated_by": "admin_1",
            },
        }},
    }

    with patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={
            "stripe_webhook_secret": "whsec_test",
            "stripe_secret_key": "sk_test_x",
        }),
    ), patch(
        "stripe.Webhook.construct_event", return_value=MagicMock(
            to_dict_recursive=lambda: event,
            get=lambda k, d=None: event.get(k, d),
        )
    ), patch(
        "db_supabase.claim_stripe_event", AsyncMock(return_value=True)
    ), patch(
        "db_supabase.mark_stripe_event_processed", AsyncMock()
    ), patch(
        "services.corporate_wallet_service.apply_topup",
        AsyncMock(return_value={"transaction_id": "t1", "balance_after": "500.00"}),
    ) as m_topup:
        resp = test_client.post(
            "/webhooks/stripe",
            data=_signed(event),
            headers={"stripe-signature": "t=1,v1=dummy"},
        )
    assert resp.status_code == 200
    m_topup.assert_awaited_once()
    kwargs = m_topup.call_args.kwargs
    assert kwargs["wallet_id"] == "w1"
    assert kwargs["amount"] == 500
    assert kwargs["stripe_payment_intent_id"] == "pi_123"
```

- [ ] **Step 2: Run — expect FAIL (event unrouted)**

```bash
pytest backend/tests/test_corporate_webhook.py -v
```

- [ ] **Step 3: Extend the webhook handler**

In `backend/routes/webhooks.py`, after the existing idempotency gate (where the event type is dispatched), add:

```python
# ── Corporate top-up branch ─────────────────────────────────────────
if event_type == "payment_intent.succeeded":
    meta = data_object.get("metadata") or {}
    if meta.get("scope") == "corporate_topup":
        try:
            from services.corporate_wallet_service import apply_topup
        except ImportError:
            from backend.services.corporate_wallet_service import apply_topup  # type: ignore

        amount_cents = data_object.get("amount_received") or data_object.get("amount", 0)
        amount_cad = amount_cents / 100
        await apply_topup(
            wallet_id=meta["wallet_id"],
            amount=amount_cad,
            stripe_payment_intent_id=data_object["id"],
            actor_user_id=meta.get("initiated_by"),
            notes=f"Stripe top-up via {event_id}",
        )
        await mark_stripe_event_processed(event_id)
        return {"received": True, "scope": "corporate_topup"}
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_webhook.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/webhooks.py backend/tests/test_corporate_webhook.py
git commit -m "feat(corporate): stripe webhook credits master wallet on top-up"
```

---

## Task 6: Auto-top-up scheduled job

**Files:**
- Create: `backend/utils/corporate_autotopup.py`
- Modify: `backend/server.py` — wire the scheduled task (follow the pattern in `backend/utils/scheduled_rides.py`)
- Test: `backend/tests/test_corporate_autotopup.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_autotopup.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_TODAY_TOPUPS_ZERO = AsyncMock(return_value=0)


@pytest.mark.asyncio
async def test_triggers_charge_when_balance_below_threshold():
    wallets = [{
        "id": "w1", "company_id": "c1", "balance": "30.00",
        "auto_topup_enabled": True, "auto_topup_threshold": "100.00",
        "auto_topup_amount": "500.00", "auto_topup_daily_cap": "5000.00",
        "soft_negative_floor": "-50.00",
    }]
    company = {"id": "c1", "stripe_customer_id": "cus_X", "status": "active"}
    intent = MagicMock(id="pi_auto")

    with patch(
        "utils.corporate_autotopup.list_wallets_needing_autotopup",
        AsyncMock(return_value=wallets),
    ), patch(
        "utils.corporate_autotopup.get_corporate_account_by_id",
        AsyncMock(return_value=company),
    ), patch(
        "utils.corporate_autotopup.sum_autotopups_today",
        _TODAY_TOPUPS_ZERO,
    ), patch(
        "utils.corporate_autotopup.get_default_payment_method",
        AsyncMock(return_value="pm_1"),
    ), patch(
        "stripe.PaymentIntent.create", return_value=intent
    ) as m_pi, patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()
    m_pi.assert_called_once()


@pytest.mark.asyncio
async def test_skips_when_daily_cap_reached():
    wallets = [{
        "id": "w1", "company_id": "c1", "balance": "30.00",
        "auto_topup_enabled": True, "auto_topup_threshold": "100.00",
        "auto_topup_amount": "500.00", "auto_topup_daily_cap": "500.00",
    }]
    with patch(
        "utils.corporate_autotopup.list_wallets_needing_autotopup",
        AsyncMock(return_value=wallets),
    ), patch(
        "utils.corporate_autotopup.get_corporate_account_by_id",
        AsyncMock(return_value={"status": "active", "stripe_customer_id": "cus_X"}),
    ), patch(
        "utils.corporate_autotopup.sum_autotopups_today",
        AsyncMock(return_value=500),
    ), patch(
        "stripe.PaymentIntent.create"
    ) as m_pi, patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()
    m_pi.assert_not_called()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_autotopup.py -v
```

- [ ] **Step 3: Add DB helpers**

Append to `backend/db_supabase.py`:

```python
async def list_wallets_needing_autotopup() -> list[dict]:
    """Return wallets where auto_topup_enabled and balance < threshold."""
    resp = await (
        supabase.table("corporate_wallets")
        .select("*")
        .eq("auto_topup_enabled", True)
        .execute()
    )
    rows = resp.data or []
    return [
        r for r in rows
        if r.get("auto_topup_threshold") is not None
        and float(r["balance"]) < float(r["auto_topup_threshold"])
    ]


async def sum_autotopups_today(wallet_id: str) -> float:
    """Sum of today's successful top-ups for a wallet, for daily-cap enforcement."""
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    resp = await (
        supabase.table("corporate_wallet_transactions")
        .select("amount")
        .eq("wallet_id", wallet_id)
        .eq("type", "topup")
        .gte("created_at", today_start.isoformat())
        .execute()
    )
    return sum(float(r["amount"]) for r in (resp.data or []))


async def get_default_payment_method(stripe_customer_id: str, stripe_secret: str) -> str | None:
    """Return the Stripe customer's default card payment method, if any."""
    import stripe
    methods = stripe.PaymentMethod.list(
        customer=stripe_customer_id, type="card", api_key=stripe_secret
    )
    if methods and methods.data:
        return methods.data[0].id
    return None
```

- [ ] **Step 4: Implement the tick**

Create `backend/utils/corporate_autotopup.py`:

```python
"""Auto-top-up scheduled task for corporate wallets.

Runs every N minutes (wired from server startup). Each tick:
  1. Finds wallets below threshold with auto_topup_enabled.
  2. Skips any that would exceed today's daily cap.
  3. Creates an off-session Stripe PaymentIntent against the customer's
     default payment method with confirm=True.
  4. The webhook handler (Task 5) credits the wallet when the charge
     clears — no work here beyond kicking off the intent.
"""
from __future__ import annotations

import logging

try:
    from ..db_supabase import (
        list_wallets_needing_autotopup,
        sum_autotopups_today,
        get_default_payment_method,
        get_corporate_account_by_id,
    )
    from ..settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        list_wallets_needing_autotopup,
        sum_autotopups_today,
        get_default_payment_method,
        get_corporate_account_by_id,
    )
    from settings_loader import get_app_settings  # type: ignore

import stripe

logger = logging.getLogger(__name__)


async def run_autotopup_tick() -> None:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.warning("autotopup: no stripe secret configured, skipping tick")
        return

    wallets = await list_wallets_needing_autotopup()
    for w in wallets:
        try:
            await _process_one(w, stripe_secret)
        except Exception as e:  # don't let one failure stop the rest
            logger.exception("autotopup failed for wallet %s: %s", w.get("id"), e)


async def _process_one(wallet: dict, stripe_secret: str) -> None:
    company = await get_corporate_account_by_id(wallet["company_id"])
    if not company or company.get("status") != "active":
        return
    if not company.get("stripe_customer_id"):
        logger.warning("wallet %s has no stripe_customer_id", wallet["id"])
        return

    topup_amount = float(wallet["auto_topup_amount"])
    daily_cap = float(wallet.get("auto_topup_daily_cap") or 5000)
    today_sum = await sum_autotopups_today(wallet["id"])
    if today_sum + topup_amount > daily_cap:
        logger.info(
            "autotopup: wallet %s at daily cap (%s + %s > %s)",
            wallet["id"], today_sum, topup_amount, daily_cap,
        )
        return

    pm_id = await get_default_payment_method(company["stripe_customer_id"], stripe_secret)
    if not pm_id:
        logger.warning("wallet %s has no default payment method", wallet["id"])
        return

    stripe.PaymentIntent.create(
        amount=int(round(topup_amount * 100)),
        currency="cad",
        customer=company["stripe_customer_id"],
        payment_method=pm_id,
        off_session=True,
        confirm=True,
        metadata={
            "scope": "corporate_topup",
            "company_id": company["id"],
            "wallet_id": wallet["id"],
            "initiated_by": "autotopup",
        },
        api_key=stripe_secret,
    )
    logger.info("autotopup: kicked intent for wallet %s (%s CAD)", wallet["id"], topup_amount)
```

- [ ] **Step 5: Wire the task loop**

In `backend/server.py` (match existing pattern from `scheduled_rides`):

```python
import asyncio
from utils.corporate_autotopup import run_autotopup_tick

@app.on_event("startup")
async def _start_autotopup():
    async def loop():
        while True:
            try:
                await run_autotopup_tick()
            except Exception:
                pass
            await asyncio.sleep(600)  # 10 minutes
    asyncio.create_task(loop())
```

(If `server.py` already uses a different scheduler primitive — e.g., APScheduler — adopt that instead.)

- [ ] **Step 6: Run tests**

```bash
pytest backend/tests/test_corporate_autotopup.py -v
```

Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/utils/corporate_autotopup.py backend/db_supabase.py backend/server.py backend/tests/test_corporate_autotopup.py
git commit -m "feat(corporate): auto-topup scheduled task with daily cap"
```

---

## Task 7: Low-balance notification email

**Files:**
- Create: `backend/utils/corporate_low_balance.py`
- Modify: `backend/utils/corporate_autotopup.py` to call the checker in the same tick loop
- Test: `backend/tests/test_corporate_low_balance.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_low_balance.py
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_sends_email_when_below_threshold_and_autotopup_off():
    wallet = {
        "id": "w1", "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    with patch(
        "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
        AsyncMock(return_value=[wallet]),
    ), patch(
        "utils.corporate_low_balance.get_corporate_account_by_id",
        AsyncMock(return_value={"billing_email": "billing@acme.test", "name": "Acme"}),
    ), patch(
        "utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()
    ) as m_mark, patch(
        "utils.corporate_low_balance.send_email", AsyncMock()
    ) as m_send:
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()
    m_send.assert_awaited_once()
    m_mark.assert_awaited_once_with(wallet_id="w1")


@pytest.mark.asyncio
async def test_rate_limited_within_12h():
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    wallet = {
        "id": "w1", "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": recent,
    }
    with patch(
        "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
        AsyncMock(return_value=[wallet]),
    ), patch(
        "utils.corporate_low_balance.send_email", AsyncMock()
    ) as m_send:
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()
    m_send.assert_not_awaited()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_low_balance.py -v
```

- [ ] **Step 3: Add helpers**

Append to `backend/db_supabase.py`:

```python
async def list_wallets_low_balance_no_autotopup() -> list[dict]:
    resp = await (
        supabase.table("corporate_wallets")
        .select("*")
        .eq("auto_topup_enabled", False)
        .execute()
    )
    rows = resp.data or []
    return [
        r for r in rows
        if r.get("auto_topup_threshold") is not None
        and float(r["balance"]) < float(r["auto_topup_threshold"])
    ]


async def mark_low_balance_notified(*, wallet_id: str) -> None:
    from datetime import datetime, timezone
    await (
        supabase.table("corporate_wallets")
        .update({"low_balance_notified_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", wallet_id)
        .execute()
    )
```

- [ ] **Step 4: Implement the tick**

Create `backend/utils/corporate_low_balance.py`:

```python
"""Low-balance email notifications for corporate wallets with auto-topup OFF."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

try:
    from ..db_supabase import (
        list_wallets_low_balance_no_autotopup,
        mark_low_balance_notified,
        get_corporate_account_by_id,
    )
    from ..features import send_email
except ImportError:
    from db_supabase import (  # type: ignore
        list_wallets_low_balance_no_autotopup,
        mark_low_balance_notified,
        get_corporate_account_by_id,
    )
    from features import send_email  # type: ignore

logger = logging.getLogger(__name__)

_RATE_LIMIT = timedelta(hours=12)


async def run_low_balance_tick() -> None:
    wallets = await list_wallets_low_balance_no_autotopup()
    for w in wallets:
        last = w.get("low_balance_notified_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            except ValueError:
                last_dt = None
            if last_dt and datetime.now(timezone.utc) - last_dt < _RATE_LIMIT:
                continue
        try:
            await _notify_one(w)
        except Exception:
            logger.exception("low-balance notify failed for %s", w["id"])


async def _notify_one(wallet: dict) -> None:
    company = await get_corporate_account_by_id(wallet["company_id"])
    if not company or not company.get("billing_email"):
        return
    subject = f"[Spinr Business] Wallet balance low — {company.get('name')}"
    body = (
        f"Your corporate wallet balance is ${wallet['balance']} CAD, which is below\n"
        f"your low-balance threshold of ${wallet['auto_topup_threshold']}.\n\n"
        f"Top up from the admin portal to avoid ride interruptions.\n\n"
        f"— Spinr Business"
    )
    await send_email(to=company["billing_email"], subject=subject, body=body)
    await mark_low_balance_notified(wallet_id=wallet["id"])
```

- [ ] **Step 5: Ensure `send_email` exists in `features.py`**

If it does not, check the existing codebase for the email primitive (likely already present — `send_push_notification` exists per the webhooks file). If `send_email` is missing, stub it to log:

```python
# backend/features.py — add near send_push_notification if absent
async def send_email(*, to: str, subject: str, body: str) -> None:
    import logging
    logging.getLogger(__name__).info("EMAIL to=%s subject=%r", to, subject)
```

- [ ] **Step 6: Wire into the scheduler**

In `server.py`, add to the existing tick loop or a parallel one:

```python
from utils.corporate_low_balance import run_low_balance_tick

# in the loop above:
await run_low_balance_tick()
```

- [ ] **Step 7: Run tests**

```bash
pytest backend/tests/test_corporate_low_balance.py -v
```

Expected: both PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/utils/corporate_low_balance.py backend/db_supabase.py backend/features.py backend/server.py backend/tests/test_corporate_low_balance.py
git commit -m "feat(corporate): low-balance email with 12h rate limit"
```

---

## Task 8: Wallet view endpoint — balance + recent transactions

**Files:**
- Modify: `backend/routes/corporate_wallet.py`
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_wallet_view.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_wallet_view.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_wallet_returns_balance_and_txns(test_client, auth_headers):
    with patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={
            "id": "w1", "company_id": "c1", "balance": "500.00",
            "currency": "CAD", "auto_topup_enabled": False,
            "auto_topup_threshold": None, "auto_topup_amount": None,
            "soft_negative_floor": "-50.00",
        }),
    ), patch(
        "db_supabase.list_wallet_transactions",
        AsyncMock(return_value=[
            {"id": "t1", "type": "topup", "amount": "500.00",
             "balance_after": "500.00", "scope": "master",
             "created_at": "2026-04-16T00:00:00Z"},
        ]),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.get(
            "/admin/corporate-accounts/c1/wallet",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == "500.00"
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["type"] == "topup"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_wallet_view.py -v
```

- [ ] **Step 3: Add the ledger helper + route**

Append to `backend/db_supabase.py`:

```python
async def list_wallet_transactions(
    *, wallet_id: str, skip: int = 0, limit: int = 50
) -> list[dict]:
    resp = await (
        supabase.table("corporate_wallet_transactions")
        .select("*")
        .eq("wallet_id", wallet_id)
        .order("created_at", desc=True)
        .range(skip, skip + limit - 1)
        .execute()
    )
    return resp.data or []
```

In `backend/routes/corporate_wallet.py`:

```python
from db_supabase import list_wallet_transactions  # add at top

@router.get("/{company_id}/wallet")
async def get_wallet(
    company_id: str,
    skip: int = 0,
    limit: int = 50,
    current_admin: dict = Depends(get_admin_user),
):
    validate_id(company_id)
    wallet = await get_corporate_wallet_by_company(company_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    txns = await list_wallet_transactions(
        wallet_id=wallet["id"], skip=skip, limit=min(limit, 200)
    )
    return {**wallet, "transactions": txns}
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_wallet_view.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_wallet.py backend/db_supabase.py backend/tests/test_corporate_wallet_view.py
git commit -m "feat(corporate): wallet view endpoint with ledger"
```

---

## Task 9: Wallet auto-top-up config endpoint

**Files:**
- Modify: `backend/routes/corporate_wallet.py`
- Test: `backend/tests/test_corporate_wallet_config.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_wallet_config.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_update_autotopup_config(test_client, auth_headers):
    updated = {
        "id": "w1", "company_id": "c1", "balance": "0",
        "auto_topup_enabled": True, "auto_topup_threshold": "100.00",
        "auto_topup_amount": "500.00", "auto_topup_daily_cap": "5000.00",
    }
    with patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1"}),
    ), patch(
        "db_supabase.update_corporate_wallet_config", AsyncMock(return_value=updated)
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.put(
            "/admin/corporate-accounts/c1/wallet/config",
            json={
                "auto_topup_enabled": True,
                "auto_topup_threshold": 100,
                "auto_topup_amount": 500,
            },
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["auto_topup_enabled"] is True
```

- [ ] **Step 2: Add the helper + route**

In `backend/db_supabase.py`:

```python
async def update_corporate_wallet_config(*, wallet_id: str, patch: dict) -> dict | None:
    resp = await (
        supabase.table("corporate_wallets").update(patch).eq("id", wallet_id).execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None
```

In `backend/routes/corporate_wallet.py`:

```python
class WalletConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_topup_enabled: Optional[bool] = None
    auto_topup_threshold: Optional[float] = Field(None, ge=0)
    auto_topup_amount: Optional[float] = Field(None, gt=0, le=10000)
    auto_topup_daily_cap: Optional[float] = Field(None, gt=0, le=50000)


@router.put("/{company_id}/wallet/config")
async def update_wallet_config(
    company_id: str,
    body: WalletConfigPatch,
    current_admin: dict = Depends(get_admin_user),
):
    validate_id(company_id)
    wallet = await get_corporate_wallet_by_company(company_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    patch = body.model_dump(exclude_none=True)
    if not patch:
        return wallet
    from db_supabase import update_corporate_wallet_config as upd
    return await upd(wallet_id=wallet["id"], patch=patch)
```

- [ ] **Step 3: Run tests**

```bash
pytest backend/tests/test_corporate_wallet_config.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/routes/corporate_wallet.py backend/db_supabase.py backend/tests/test_corporate_wallet_config.py
git commit -m "feat(corporate): auto-topup config endpoint"
```

---

## Task 10: Admin dashboard — wallet panel on company detail page

**Files:**
- Modify: `admin-dashboard/src/lib/api.ts` — add wallet helpers
- Modify: `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx`

- [ ] **Step 1: Add API helpers**

```ts
// admin-dashboard/src/lib/api.ts
export interface WalletTxn {
  id: string;
  type: string;
  scope: string;
  amount: string;
  balance_after: string;
  created_at: string;
  notes?: string | null;
  ride_id?: string | null;
  member_id?: string | null;
}

export interface CorporateWallet {
  id: string;
  company_id: string;
  balance: string;
  currency: string;
  auto_topup_enabled: boolean;
  auto_topup_threshold: string | null;
  auto_topup_amount: string | null;
  auto_topup_daily_cap: string;
  soft_negative_floor: string;
  transactions: WalletTxn[];
}

export async function getCorporateWallet(companyId: string): Promise<CorporateWallet> {
  return apiFetch(`/admin/corporate-accounts/${companyId}/wallet`);
}

export async function updateWalletConfig(
  companyId: string,
  patch: Partial<Pick<CorporateWallet,
    "auto_topup_enabled" | "auto_topup_threshold" | "auto_topup_amount" | "auto_topup_daily_cap"
  >>
): Promise<CorporateWallet> {
  return apiFetch(`/admin/corporate-accounts/${companyId}/wallet/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function walletTopupIntent(
  companyId: string,
  body: { amount: number; payment_method_id?: string }
): Promise<{ payment_intent_id: string; client_secret: string }> {
  return apiFetch(`/admin/corporate-accounts/${companyId}/wallet/topup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function walletAdjust(
  companyId: string,
  body: { amount: number; notes: string }
): Promise<{ transaction_id: string; balance_after: string }> {
  return apiFetch(`/admin/corporate-accounts/${companyId}/wallet/adjust`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
```

- [ ] **Step 2: Add a wallet panel to the company detail page**

Append to `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` (before the closing `</div>` of the page):

```tsx
import {
  CorporateWallet,
  getCorporateWallet,
  walletAdjust,
  updateWalletConfig,
} from "@/lib/api";

// inside the component, add:
const [wallet, setWallet] = useState<CorporateWallet | null>(null);

const loadWallet = async () => {
  try { setWallet(await getCorporateWallet(id)); } catch {}
};
useEffect(() => { if (c?.status === "active") loadWallet(); }, [c?.status, id]);

const adjust = async () => {
  const raw = prompt("Adjustment amount (signed CAD):");
  if (!raw) return;
  const amount = parseFloat(raw);
  if (Number.isNaN(amount) || amount === 0) return;
  const notes = prompt("Reason (required):") ?? "";
  if (!notes) return;
  await walletAdjust(id, { amount, notes });
  await loadWallet();
};

// render after the status section:
{wallet && (
  <section className="border rounded p-4">
    <header className="flex items-center justify-between">
      <h2 className="text-lg font-semibold">Master wallet</h2>
      <span className="text-2xl font-bold">
        ${parseFloat(wallet.balance).toFixed(2)} {wallet.currency}
      </span>
    </header>

    <div className="mt-3 text-sm space-y-1">
      <div>Auto top-up: <b>{wallet.auto_topup_enabled ? "on" : "off"}</b></div>
      <div>Threshold: {wallet.auto_topup_threshold ?? "—"} · Amount: {wallet.auto_topup_amount ?? "—"} · Daily cap: {wallet.auto_topup_daily_cap}</div>
      <div>Soft-negative floor: {wallet.soft_negative_floor}</div>
    </div>

    <div className="mt-3 flex gap-2">
      <button onClick={adjust} className="px-3 py-1 rounded border">Adjust balance</button>
      <button
        onClick={async () => {
          await updateWalletConfig(id, { auto_topup_enabled: !wallet.auto_topup_enabled });
          await loadWallet();
        }}
        className="px-3 py-1 rounded border"
      >Toggle auto-topup</button>
    </div>

    <h3 className="mt-4 font-semibold">Recent transactions</h3>
    <ul className="mt-2 divide-y text-sm">
      {wallet.transactions.map((t) => (
        <li key={t.id} className="py-2 flex justify-between">
          <span>
            <span className="inline-block w-28">{t.type}</span>
            <span className="text-gray-500">{new Date(t.created_at).toLocaleString()}</span>
          </span>
          <span>
            {parseFloat(t.amount) >= 0 ? "+" : ""}
            ${parseFloat(t.amount).toFixed(2)}
            <span className="text-gray-500 ml-2">bal ${parseFloat(t.balance_after).toFixed(2)}</span>
          </span>
        </li>
      ))}
    </ul>
  </section>
)}
```

- [ ] **Step 3: Smoke test locally**

Dev server, navigate to an active company's detail page, verify the wallet panel shows balance $0.00 with no transactions. Adjust balance to $100 with note "seed"; verify balance and ledger updates. Toggle auto-topup.

- [ ] **Step 4: Commit**

```bash
git add admin-dashboard/src/lib/api.ts admin-dashboard/src/app/dashboard/corporate-accounts/
git commit -m "feat(admin): wallet panel with ledger, adjust, and autotopup toggle"
```

---

## Task 11: Freeze the wallet when company is suspended

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Test: `backend/tests/test_corporate_wallet_freeze.py`

**Design note:** "Frozen" means auto-topup disabled and new top-up/adjust endpoints refuse with 409. The existing `change_company_status` route gets hooks that flip `auto_topup_enabled=false` on suspend/closed.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_wallet_freeze.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_suspend_disables_autotopup(test_client, auth_headers):
    calls = []

    async def _upd_config(*, wallet_id, patch):
        calls.append(patch)
        return {"id": wallet_id, **patch}

    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "active"}),
    ), patch(
        "db_supabase.update_corporate_account_status",
        AsyncMock(return_value={"id": "c1", "status": "suspended"}),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "auto_topup_enabled": True}),
    ), patch(
        "db_supabase.update_corporate_wallet_config", _upd_config
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/status",
            json={"status": "suspended"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert calls == [{"auto_topup_enabled": False}]


@pytest.mark.asyncio
async def test_topup_refused_when_suspended(test_client, auth_headers):
    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "suspended", "stripe_customer_id": "cus_X"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
            headers=auth_headers,
        )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL for the first test**

```bash
pytest backend/tests/test_corporate_wallet_freeze.py -v
```

- [ ] **Step 3: Hook into `change_company_status`**

In `backend/routes/corporate_accounts.py`, inside `change_company_status`, after the status update succeeds:

```python
from schemas.corporate import CompanyStatus

if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    from db_supabase import get_corporate_wallet_by_company, update_corporate_wallet_config
    wallet = await get_corporate_wallet_by_company(company_id)
    if wallet and wallet.get("auto_topup_enabled"):
        await update_corporate_wallet_config(
            wallet_id=wallet["id"], patch={"auto_topup_enabled": False}
        )
```

The second test already passes because `manual_topup` already enforces `status == "active"`.

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_wallet_freeze.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/tests/test_corporate_wallet_freeze.py
git commit -m "feat(corporate): freeze auto-topup on suspend/close"
```

---

## Task 12: End-to-end wallet flow test

**Files:**
- Create: `backend/tests/test_corporate_e2e_wallet.py`

- [ ] **Step 1: Write the smoke test**

```python
# backend/tests/test_corporate_e2e_wallet.py
"""E2E: approve company → Stripe customer created → wallet provisioned →
   manual top-up via webhook → adjustment → suspend freezes autotopup."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_wallet_lifecycle(test_client, auth_headers):
    # --- 1) KYB approve ------------------------------------------------
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={
            "id": "c1", "status": "active", "legal_name": "Acme Inc",
            "billing_email": "billing@acme.test",
        }),
    ), patch(
        "db_supabase.ensure_corporate_wallet",
        AsyncMock(return_value={"id": "w1", "company_id": "c1", "balance": 0}),
    ), patch(
        "db_supabase.update_corporate_stripe_customer_id", AsyncMock()
    ), patch(
        "stripe.Customer.create", return_value=MagicMock(id="cus_A")
    ), patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={
            "stripe_secret_key": "sk_test",
            "stripe_webhook_secret": "whsec_test",
        }),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        r = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True}, headers=auth_headers,
        )
        assert r.status_code == 200

    # --- 2) Manual top-up intent ---------------------------------------
    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={
            "id": "c1", "status": "active", "stripe_customer_id": "cus_A",
        }),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1"}),
    ), patch(
        "stripe.PaymentIntent.create",
        return_value=MagicMock(id="pi_1", client_secret="cs_1"),
    ), patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        r = test_client.post(
            "/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500}, headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["payment_intent_id"] == "pi_1"

    # --- 3) Webhook fires and credits wallet ---------------------------
    event = {
        "id": "evt_1", "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": "pi_1", "amount_received": 50000, "currency": "cad",
            "metadata": {
                "scope": "corporate_topup",
                "company_id": "c1", "wallet_id": "w1",
                "initiated_by": "admin_1",
            },
        }},
    }
    with patch(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={
            "stripe_webhook_secret": "whsec_test",
            "stripe_secret_key": "sk_test",
        }),
    ), patch(
        "stripe.Webhook.construct_event", return_value=MagicMock(
            to_dict_recursive=lambda: event,
            get=lambda k, d=None: event.get(k, d),
        )
    ), patch(
        "db_supabase.claim_stripe_event", AsyncMock(return_value=True)
    ), patch(
        "db_supabase.mark_stripe_event_processed", AsyncMock()
    ), patch(
        "services.corporate_wallet_service.apply_topup",
        AsyncMock(return_value={"transaction_id": "t1", "balance_after": "500.00"}),
    ) as m_topup:
        r = test_client.post(
            "/webhooks/stripe",
            data=json.dumps(event).encode(),
            headers={"stripe-signature": "t=1,v1=dummy"},
        )
        assert r.status_code == 200
        assert m_topup.await_count == 1
        assert m_topup.await_args.kwargs["amount"] == 500

    # --- 4) Suspension disables auto-topup -----------------------------
    calls = []
    async def _upd(*, wallet_id, patch):
        calls.append(patch)
        return {"id": wallet_id, **patch}

    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "active"}),
    ), patch(
        "db_supabase.update_corporate_account_status",
        AsyncMock(return_value={"id": "c1", "status": "suspended"}),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "auto_topup_enabled": True}),
    ), patch(
        "db_supabase.update_corporate_wallet_config", _upd
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        r = test_client.post(
            "/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "test"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert {"auto_topup_enabled": False} in calls
```

- [ ] **Step 2: Run it**

```bash
pytest backend/tests/test_corporate_e2e_wallet.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full Plan-2 test suite**

```bash
pytest backend/tests/test_corporate_wallet_bootstrap.py \
       backend/tests/test_corporate_stripe_customer.py \
       backend/tests/services/test_corporate_wallet_service.py \
       backend/tests/test_corporate_wallet_routes.py \
       backend/tests/test_corporate_webhook.py \
       backend/tests/test_corporate_autotopup.py \
       backend/tests/test_corporate_low_balance.py \
       backend/tests/test_corporate_wallet_view.py \
       backend/tests/test_corporate_wallet_config.py \
       backend/tests/test_corporate_wallet_freeze.py \
       backend/tests/test_corporate_e2e_wallet.py -v
```

Expected: all PASS.

- [ ] **Step 4: Regression — run the existing test suite**

```bash
pytest backend/tests/ -v --ignore=backend/tests/test_corporate_b2b_schema.py
```

Expected: no new failures.

- [ ] **Step 5: Manual Stripe test-mode round-trip**

1. Start backend + dashboard locally with Stripe test keys set in `app_settings`.
2. Create a company in the dashboard, upload a KYB doc, approve. Verify wallet row created and Stripe Customer ID set on the company.
3. On the company detail page, click Top up → $500 → complete the PaymentIntent with Stripe test card `4242 4242 4242 4242`.
4. Verify the Stripe webhook fires (check backend logs), ledger shows a `topup` row, and balance is `$500.00`.
5. Enable auto-topup (threshold $100, amount $500). Use an admin Adjustment to push balance to $50. Wait one tick (or call `run_autotopup_tick()` from a REPL); verify a new top-up intent is created.
6. Suspend the company; verify auto-topup toggles off and the top-up button returns 409.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_corporate_e2e_wallet.py
git commit -m "test(corporate): e2e wallet lifecycle"
```

---

## Done criteria (Plan 2)

- Master wallet row is created idempotently on KYB approval.
- Stripe Customer created + persisted on KYB approval.
- All wallet writes go through the service → Postgres RPC with `SELECT ... FOR UPDATE` and idempotency on `stripe_payment_intent_id`.
- Manual top-up endpoint creates a PaymentIntent; webhook credits the wallet; end-to-end round-trip verified in Stripe test mode.
- Auto-top-up runs on a schedule, respects `auto_topup_daily_cap`, never fires for inactive companies.
- Low-balance emails rate-limited to once per 12h per wallet.
- Wallet view endpoint returns balance + paginated ledger.
- Auto-top-up config endpoint validates amount bounds.
- Suspend/close freezes the wallet (auto-topup disabled, top-up refused).
- Admin dashboard shows the wallet panel with balance, ledger, adjust, and auto-topup toggle.

### Handoff to Plan 3
After this plan: a super-admin can fully fund and manage a corporate master wallet end-to-end. Plan 3 introduces `corporate_members`, invitations, allowance assignment, and the "ask for more" flow — money moves from master wallet into employee allowances using the same `corporate_wallet_apply_delta` RPC (called with `scope=member:<id>`).
