# Corporate B2B — Plan 3: Employee Allowances & Work Profile

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire master-wallet money to employees. After this plan, a company admin can invite employees (by explicit invite-token OR email-domain auto-match), assign each member an allowance (fixed-recurring / one-time / unlimited), and the employee can see their balance + recent Work rides + request-more from inside the rider app. Plan 5 wires the allowance into ride debit; Plan 4 adds the policy engine.

**Architecture:**
- New `backend/services/corporate_membership_service.py` owns membership state-machine (`invited → active → suspended → removed`) and tokenized-invite issuance.
- New `backend/services/corporate_allowance_service.py` owns allowance grants/resets/rollbacks. All money movements reuse `corporate_wallet_apply_delta` (migration 28) with `scope=member:<uuid>` — no new ledger code.
- New `backend/routes/corporate_company.py` for company-admin endpoints under `/company/**`. Mounted via `main.py`.
- New `backend/routes/corporate_rider.py` for rider-app endpoints under `/rider/work-profile/**`.
- Monthly allowance reset runs as a scheduled task (pattern: `backend/utils/scheduled_rides.py`).

**Tech Stack:** FastAPI, Pydantic v2, Supabase (Postgres), pytest, Next.js (admin dashboard).

**Spec:** `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md` §3 (data model), §4 (allowance debit/reset), §5 (Work Profile), §7 (admin portal Employees + Allowance-requests screens).

**Prerequisite:** Plans 1 and 2 are merged. Tables `corporate_members`, `corporate_member_allowances`, `corporate_allowance_requests`, `corporate_allowed_domains` already exist (migration 27). The `corporate_wallet_apply_delta` RPC already supports `scope=member:<uuid>`.

**Out of scope:** full policy engine rules (Plan 4), ride-debit wiring into ride completion flow (Plan 5), company-facing admin dashboard UI (Plan 7), rider-app UI implementation (Plan 8). This plan is backend + schemas + one admin-dashboard reference page only.

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

**For RPC calls** (allowance arithmetic) the pattern is identical — `.rpc(name, params)` returns a query builder, `.execute()` is sync:

```python
def _fn():
    res = supabase.rpc("my_function", params).execute()
    return _rows_from_res(res)
return await run_sync(_fn)
```

**For test mocks:** `.execute()` is called inside the `_fn` closure which is offloaded to a threadpool — mock it with `MagicMock(return_value=...)`, NOT `AsyncMock`. An `AsyncMock` returns a coroutine that the sync closure can't `await`, and the chained access (`res.data`) will dereference the coroutine instead of the APIResponse.

Every code snippet below that uses Supabase follows this pattern. If a snippet in this plan shows `await q.execute()` or `await supabase.rpc(...)`, treat it as a plan typo and convert to the `run_sync` form. See Plan 1 Task 4 for worked examples.

---

## ⚠️ Route mounting note

All new routers (`corporate_company.py`, `corporate_rider.py`) must be registered in `backend/main.py` alongside the existing corporate routers. Use the **import-with-fallback** pattern already in that file (try `from .routes.x import router` — fall back to absolute import). Don't add new import guards; copy the existing pattern.

---

## Task 1: Allowance RPCs migration

**Files:**
- Create: `backend/migrations/29_corporate_allowance_rpc.sql`

**Design note:** Allowance movements (grant / reset / rollback) need the same row-lock semantics as the master wallet. Rather than overload `corporate_wallet_apply_delta` with allowance-row mutation, add a sibling function `corporate_allowance_apply_delta` that:
1. `SELECT ... FOR UPDATE` on BOTH the allowance row AND the master wallet row (ordered by object oid to prevent deadlock).
2. Decrements master-wallet balance and increments allowance `used` atomically.
3. Writes TWO ledger rows (one `scope=master type=allowance_grant`, one `scope=member:<id> type=allowance_grant`). Both share a `correlation_id` via the `notes` field so the two halves reconcile.
4. Supports `type IN ('allowance_grant','allowance_reset','allowance_rollback')` — `reset` zeroes `used` (monthly rollover), `rollback` reverses an earlier grant.
5. Enforces floor on the master side (not the allowance side — spec §4 allows allowance soft-negative separately).

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/29_corporate_allowance_rpc.sql
--
-- Atomic allowance movement: moves money from master wallet into / out of
-- a member's allowance (or resets usage). Locks master wallet + allowance
-- rows in deterministic order, writes two paired ledger entries.
-- Safe to re-run (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    p_wallet_id          UUID,       -- master wallet
    p_allowance_id       UUID,       -- allowance row to mutate
    p_member_id          UUID,       -- for ledger scope
    p_type               TEXT,       -- 'allowance_grant' | 'allowance_reset' | 'allowance_rollback'
    p_amount             NUMERIC(12,2),  -- positive, signed semantics encoded in type
    p_actor_user_id      UUID DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL   -- master wallet floor
)
RETURNS TABLE(
    master_txn_id      UUID,
    member_txn_id      UUID,
    master_balance_after NUMERIC(12,2),
    allowance_used_after NUMERIC(12,2)
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_master_balance NUMERIC(12,2);
    v_master_new     NUMERIC(12,2);
    v_used           NUMERIC(12,2);
    v_used_new       NUMERIC(12,2);
    v_master_delta   NUMERIC(12,2);
    v_used_delta     NUMERIC(12,2);
    v_master_txn     UUID;
    v_member_txn     UUID;
BEGIN
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback') THEN
        RAISE EXCEPTION 'invalid allowance type: %', p_type;
    END IF;
    IF p_amount < 0 THEN
        RAISE EXCEPTION 'amount must be non-negative';
    END IF;

    -- Deterministic lock order (prevents deadlock): master wallet first, then allowance.
    SELECT balance INTO v_master_balance
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;
    IF v_master_balance IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    SELECT used INTO v_used
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    -- Map type → (master delta, used delta).
    -- grant:   master -p_amount, used -p_amount (usage counter decreases → more available)
    -- reset:   master 0,         used -v_used   (zero out current usage, no master move)
    -- rollback: master +p_amount, used +p_amount (undo a prior grant)
    IF p_type = 'allowance_grant' THEN
        v_master_delta := -p_amount;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;     -- zero it
    ELSE  -- allowance_rollback
        v_master_delta := p_amount;
        v_used_delta   := p_amount;
    END IF;

    v_master_new := v_master_balance + v_master_delta;
    v_used_new   := v_used + v_used_delta;

    -- Floor check on master only (allowance side may go negative by design — soft-negative floor
    -- lives on the master wallet and is the authoritative limit).
    IF p_floor IS NOT NULL AND v_master_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_master_new, p_floor;
    END IF;

    -- Apply master side (even if delta=0 we still write a ledger row for reset, for audit).
    UPDATE corporate_wallets
    SET balance = v_master_new, updated_at = now()
    WHERE id = p_wallet_id;

    -- Apply allowance side.
    UPDATE corporate_member_allowances
    SET used = v_used_new, updated_at = now()
    WHERE id = p_allowance_id;

    -- Paired ledger rows — one master-scope, one member-scope. Same created_at timestamp
    -- links them for reconciliation.
    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'master', p_type, v_master_delta, v_master_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_master_txn;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'member:' || p_member_id::text, p_type, v_used_delta, v_used_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_member_txn;

    master_txn_id        := v_master_txn;
    member_txn_id        := v_member_txn;
    master_balance_after := v_master_new;
    allowance_used_after := v_used_new;
    RETURN NEXT;
END
$$;
```

- [ ] **Step 2: Apply the migration against the dev database**

```bash
psql "$DATABASE_URL" -f backend/migrations/29_corporate_allowance_rpc.sql
```

Expected: `CREATE FUNCTION` (no error). Re-running must be a no-op (`CREATE OR REPLACE`).

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/29_corporate_allowance_rpc.sql
git commit -m "feat(corporate): allowance apply-delta RPC"
```

---

## Task 2: Pydantic schemas for members, allowances, requests, domains

**Files:**
- Modify: `backend/schemas/corporate.py`
- Test: `backend/tests/test_corporate_membership_schemas.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_membership_schemas.py
from datetime import date

import pytest
from pydantic import ValidationError

from schemas.corporate import (
    AllowanceCreate,
    AllowanceRequestCreate,
    AllowanceResponse,
    AllowanceType,
    AllowanceUpdate,
    AllowedDomainCreate,
    MemberInvite,
    MemberRole,
    MemberStatus,
)


def test_member_invite_normalizes_email():
    m = MemberInvite(email="  Alice@Acme.COM ", role="member")
    assert m.email == "alice@acme.com"
    assert m.role == MemberRole.MEMBER


def test_member_invite_rejects_bad_role():
    with pytest.raises(ValidationError):
        MemberInvite(email="a@b.com", role="superuser")


def test_allowance_create_fixed_recurring_requires_amount_and_period():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.FIXED_RECURRING)
    ok = AllowanceCreate(
        type=AllowanceType.FIXED_RECURRING,
        amount=500,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    assert ok.amount == 500


def test_allowance_create_unlimited_forbids_amount():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.UNLIMITED, amount=100)
    ok = AllowanceCreate(type=AllowanceType.UNLIMITED)
    assert ok.amount is None


def test_allowance_create_one_time_requires_amount():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.ONE_TIME)
    ok = AllowanceCreate(type=AllowanceType.ONE_TIME, amount=200)
    assert ok.amount == 200


def test_allowed_domain_normalizes_and_rejects_at_prefix():
    d = AllowedDomainCreate(domain="  Acme.COM ")
    assert d.domain == "acme.com"
    with pytest.raises(ValidationError):
        AllowedDomainCreate(domain="@acme.com")


def test_allowance_request_caps_amount():
    with pytest.raises(ValidationError):
        AllowanceRequestCreate(amount=0, reason="none")
    with pytest.raises(ValidationError):
        AllowanceRequestCreate(amount=10001, reason="excessive")
    ok = AllowanceRequestCreate(amount=150, reason="client lunch")
    assert ok.amount == 150
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_membership_schemas.py -v
```

- [ ] **Step 3: Extend `backend/schemas/corporate.py`**

Append to the existing file:

```python
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import model_validator


class MemberRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class MemberStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class AllowanceType(str, Enum):
    FIXED_RECURRING = "fixed_recurring"
    ONE_TIME = "one_time"
    UNLIMITED = "unlimited"


class AllowanceStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"


class AllowanceRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    AUTO_APPROVED = "auto_approved"


class MemberInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: MemberRole = MemberRole.MEMBER
    policy_override: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return (v or "").strip().lower()


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    company_id: str
    user_id: Optional[str]
    role: MemberRole
    status: MemberStatus
    invited_email: Optional[str]
    invited_at: Optional[datetime]
    joined_at: Optional[datetime]
    policy_override: bool
    created_at: datetime
    updated_at: datetime


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[MemberRole] = None
    status: Optional[MemberStatus] = None
    policy_override: Optional[bool] = None


class AllowanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AllowanceType
    amount: Optional[float] = Field(None, gt=0, le=100000)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    rollover: bool = False
    auto_approve_topup_amount: Optional[float] = Field(None, gt=0, le=10000)
    auto_approve_monthly_count: Optional[int] = Field(None, ge=0, le=50)

    @model_validator(mode="after")
    def _check_shape(self):
        if self.type == AllowanceType.UNLIMITED:
            if self.amount is not None:
                raise ValueError("unlimited allowance cannot have amount")
            if self.period_start is not None or self.period_end is not None:
                raise ValueError("unlimited allowance cannot have period")
        elif self.type == AllowanceType.FIXED_RECURRING:
            if self.amount is None:
                raise ValueError("fixed_recurring requires amount")
            if self.period_start is None or self.period_end is None:
                raise ValueError("fixed_recurring requires period_start and period_end")
            if self.period_end <= self.period_start:
                raise ValueError("period_end must be after period_start")
        else:  # one_time
            if self.amount is None:
                raise ValueError("one_time requires amount")
        return self


class AllowanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Optional[float] = Field(None, gt=0, le=100000)
    rollover: Optional[bool] = None
    auto_approve_topup_amount: Optional[float] = Field(None, gt=0, le=10000)
    auto_approve_monthly_count: Optional[int] = Field(None, ge=0, le=50)
    status: Optional[AllowanceStatus] = None


class AllowanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    member_id: str
    type: AllowanceType
    amount: Optional[float]
    used: float
    period_start: Optional[date]
    period_end: Optional[date]
    rollover: bool
    auto_approve_topup_amount: Optional[float]
    auto_approve_monthly_count: Optional[int]
    auto_approved_this_period: int
    status: AllowanceStatus


class AllowanceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(..., gt=0, le=10000)
    reason: str = Field(..., min_length=1, max_length=500)


class AllowanceRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: Optional[str] = Field(None, max_length=500)


class AllowanceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    member_id: str
    amount: float
    reason: str
    status: AllowanceRequestStatus
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    decision_notes: Optional[str]
    created_at: datetime


class AllowedDomainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(..., min_length=3, max_length=253)

    @field_validator("domain", mode="before")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v.startswith("@"):
            raise ValueError("domain must not include '@'")
        if not v or "." not in v:
            raise ValueError("domain must contain a dot")
        return v
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_membership_schemas.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/schemas/corporate.py backend/tests/test_corporate_membership_schemas.py
git commit -m "feat(corporate): schemas for members, allowances, requests, domains"
```

---

## Task 3: `db_supabase.py` helpers for members + allowances + domains + requests

**Files:**
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_membership_db_helpers.py`

**Scope:** pure CRUD helpers. Money movement lives in services (Task 4). Every helper follows the `run_sync` pattern.

- [ ] **Step 1: Write failing test (happy path only — helpers are thin)**

```python
# backend/tests/test_corporate_membership_db_helpers.py
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_insert_member_invite_writes_row():
    fake = MagicMock()
    fake.data = [{"id": "m1", "company_id": "c1", "invited_email": "a@b.com", "status": "invited"}]
    with patch("db_supabase.supabase") as mock_sb:
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from db_supabase import insert_corporate_member_invite
        row = await insert_corporate_member_invite(
            company_id="c1", email="a@b.com", role="member",
            invite_token="tok", invited_by="admin1",
        )
    assert row["id"] == "m1"
    mock_sb.table.assert_called_with("corporate_members")


@pytest.mark.asyncio
async def test_list_company_members_filters_status():
    fake = MagicMock()
    fake.data = [{"id": "m1"}, {"id": "m2"}]
    with patch("db_supabase.supabase") as mock_sb:
        chain = (
            mock_sb.table.return_value
                .select.return_value
                .eq.return_value
                .in_.return_value
                .order.return_value
        )
        chain.execute.return_value = fake
        from db_supabase import list_company_members
        rows = await list_company_members(company_id="c1", statuses=["active", "invited"])
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_get_member_by_invite_token_returns_row():
    fake = MagicMock()
    fake.data = [{"id": "m1", "invite_token": "tok"}]
    with patch("db_supabase.supabase") as mock_sb:
        (
            mock_sb.table.return_value
                .select.return_value
                .eq.return_value
                .limit.return_value
                .execute.return_value
        ) = fake
        from db_supabase import get_member_by_invite_token
        row = await get_member_by_invite_token("tok")
    assert row["invite_token"] == "tok"


@pytest.mark.asyncio
async def test_ensure_allowance_inserts_when_absent():
    existing = MagicMock(); existing.data = []
    inserted = MagicMock(); inserted.data = [{"id": "a1", "member_id": "m1", "used": 0}]
    with patch("db_supabase.supabase") as mock_sb:
        (
            mock_sb.table.return_value
                .select.return_value
                .eq.return_value
                .limit.return_value
                .execute.return_value
        ) = existing
        mock_sb.table.return_value.insert.return_value.execute.return_value = inserted
        from db_supabase import upsert_member_allowance
        row = await upsert_member_allowance(
            member_id="m1",
            patch={"type": "fixed_recurring", "amount": 500, "period_start": "2026-04-01", "period_end": "2026-04-30"},
        )
    assert row["id"] == "a1"


@pytest.mark.asyncio
async def test_list_active_pending_requests_orders_created_at():
    fake = MagicMock(); fake.data = [{"id": "r1"}]
    with patch("db_supabase.supabase") as mock_sb:
        chain = (
            mock_sb.table.return_value
                .select.return_value
                .eq.return_value
                .eq.return_value
                .order.return_value
        )
        chain.execute.return_value = fake
        from db_supabase import list_pending_allowance_requests_for_member
        rows = await list_pending_allowance_requests_for_member("m1")
    assert rows[0]["id"] == "r1"
```

- [ ] **Step 2: Run — expect FAIL (imports not yet defined)**

```bash
pytest backend/tests/test_corporate_membership_db_helpers.py -v
```

- [ ] **Step 3: Append helpers to `backend/db_supabase.py`**

Find the block of existing corporate helpers (`ensure_corporate_wallet`, `list_wallet_transactions`, etc.) and append these next to them. Reuse the existing `_rows_from_res` / `_single_row_from_res` helpers.

```python
# ---------- Members ----------
async def insert_corporate_member_invite(
    *,
    company_id: str,
    email: str,
    role: str,
    invite_token: str,
    invited_by: str,
    policy_override: bool = False,
) -> dict:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .insert({
                "company_id": company_id,
                "invited_email": email,
                "role": role,
                "invite_token": invite_token,
                "invited_at": datetime.utcnow().isoformat(),
                "invited_by": invited_by,
                "policy_override": policy_override,
                "status": "invited",
            })
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_fn)


async def list_company_members(
    *,
    company_id: str,
    statuses: list[str] | None = None,
) -> list[dict]:
    def _fn():
        q = supabase.table("corporate_members").select("*").eq("company_id", company_id)
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=False).execute()
        return _rows_from_res(res)
    return await run_sync(_fn)


async def get_corporate_member_by_id(member_id: str) -> dict | None:
    def _fn():
        res = supabase.table("corporate_members").select("*").eq("id", member_id).limit(1).execute()
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def get_member_by_invite_token(token: str) -> dict | None:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("*").eq("invite_token", token).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def list_active_memberships_for_user(user_id: str) -> list[dict]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("*").eq("user_id", user_id).eq("status", "active").execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def update_corporate_member(member_id: str, patch: dict) -> dict | None:
    if not patch:
        return await get_corporate_member_by_id(member_id)
    patch = {**patch, "updated_at": datetime.utcnow().isoformat()}
    def _fn():
        res = supabase.table("corporate_members").update(patch).eq("id", member_id).execute()
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def accept_member_invite(*, member_id: str, user_id: str) -> dict | None:
    """Atomically flip invited → active and stamp user_id + joined_at."""
    patch = {
        "status": "active",
        "user_id": user_id,
        "joined_at": datetime.utcnow().isoformat(),
        "invite_token": None,   # consume token
        "updated_at": datetime.utcnow().isoformat(),
    }
    def _fn():
        res = (
            supabase.table("corporate_members")
            .update(patch)
            .eq("id", member_id)
            .eq("status", "invited")   # guard: only if still pending
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowances ----------
async def get_member_allowance(member_id: str) -> dict | None:
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*").eq("member_id", member_id).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def upsert_member_allowance(*, member_id: str, patch: dict) -> dict:
    """Insert if no allowance exists, else update. Returns the row."""
    existing = await get_member_allowance(member_id)
    if existing:
        def _upd():
            res = (
                supabase.table("corporate_member_allowances")
                .update({**patch, "updated_at": datetime.utcnow().isoformat()})
                .eq("id", existing["id"])
                .execute()
            )
            return _single_row_from_res(res) or existing
        return await run_sync(_upd)
    def _ins():
        res = (
            supabase.table("corporate_member_allowances")
            .insert({"member_id": member_id, "used": 0, "status": "active", **patch})
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_ins)


async def list_company_allowances(company_id: str) -> list[dict]:
    """Join allowances with their members, scoped to a company."""
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*, member:corporate_members!inner(id,company_id,user_id,invited_email,status,role)")
            .eq("member.company_id", company_id)
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def list_allowances_due_for_reset(as_of: str) -> list[dict]:
    """Fixed-recurring allowances whose period_end < as_of (UTC date ISO)."""
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*")
            .eq("type", "fixed_recurring")
            .eq("status", "active")
            .lt("period_end", as_of)
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def reset_allowance_period(
    *, allowance_id: str, period_start: str, period_end: str
) -> dict | None:
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .update({
                "period_start": period_start,
                "period_end": period_end,
                "auto_approved_this_period": 0,
                "updated_at": datetime.utcnow().isoformat(),
            })
            .eq("id", allowance_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowance requests ----------
async def insert_allowance_request(
    *, member_id: str, amount: float, reason: str, status: str = "pending"
) -> dict:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .insert({
                "member_id": member_id, "amount": amount,
                "reason": reason, "status": status,
            })
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_fn)


async def list_pending_allowance_requests_for_member(member_id: str) -> list[dict]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .select("*")
            .eq("member_id", member_id).eq("status", "pending")
            .order("created_at", desc=True).execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def list_company_allowance_requests(
    company_id: str, statuses: list[str] | None = None
) -> list[dict]:
    def _fn():
        q = (
            supabase.table("corporate_allowance_requests")
            .select("*, member:corporate_members!inner(id,company_id,invited_email,user_id)")
            .eq("member.company_id", company_id)
        )
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=True).execute()
        return _rows_from_res(res)
    return await run_sync(_fn)


async def get_allowance_request_by_id(request_id: str) -> dict | None:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .select("*").eq("id", request_id).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def update_allowance_request(
    *, request_id: str, status: str, reviewed_by: str | None,
    decision_notes: str | None,
) -> dict | None:
    patch = {
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.utcnow().isoformat(),
        "decision_notes": decision_notes,
    }
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .update(patch).eq("id", request_id).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowed domains ----------
async def add_allowed_domain(*, company_id: str, domain: str) -> dict:
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .insert({"company_id": company_id, "domain": domain}).execute()
        )
        return _single_row_from_res(res) or {"company_id": company_id, "domain": domain}
    return await run_sync(_fn)


async def list_allowed_domains(company_id: str) -> list[dict]:
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .select("*").eq("company_id", company_id).execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def delete_allowed_domain(*, company_id: str, domain: str) -> None:
    def _fn():
        supabase.table("corporate_allowed_domains").delete().eq(
            "company_id", company_id
        ).eq("domain", domain).execute()
    await run_sync(_fn)


async def find_companies_by_email_domain(domain: str) -> list[dict]:
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .select("company_id, corporate_accounts:corporate_accounts!inner(id,name,status)")
            .eq("domain", domain).eq("corporate_accounts.status", "active").execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_membership_db_helpers.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/db_supabase.py backend/tests/test_corporate_membership_db_helpers.py
git commit -m "feat(corporate): db helpers for members, allowances, requests, domains"
```

---

## Task 4: Allowance service (RPC wrapper)

**Files:**
- Create: `backend/services/corporate_allowance_service.py`
- Test: `backend/tests/services/test_corporate_allowance_service.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/services/test_corporate_allowance_service.py
from unittest.mock import MagicMock, patch

import pytest


def _rpc_ok():
    r = MagicMock()
    r.data = [{
        "master_txn_id": "t_m",
        "member_txn_id": "t_u",
        "master_balance_after": 900,
        "allowance_used_after": -100,  # granted $100 → used decreases by $100
    }]
    return r


@pytest.mark.asyncio
async def test_apply_grant_calls_rpc_with_correct_params():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_grant
        out = await apply_grant(
            wallet_id="w1", allowance_id="a1", member_id="m1",
            amount=100, actor_user_id="admin1", notes="monthly topup",
            floor=-50,
        )
    assert out["master_balance_after"] == 900
    called_name, called_params = mock_sb.rpc.call_args[0]
    assert called_name == "corporate_allowance_apply_delta"
    assert called_params["p_type"] == "allowance_grant"
    assert called_params["p_amount"] == 100
    assert called_params["p_floor"] == -50


@pytest.mark.asyncio
async def test_apply_grant_rejects_non_positive():
    from services.corporate_allowance_service import apply_grant
    with pytest.raises(ValueError):
        await apply_grant(
            wallet_id="w1", allowance_id="a1", member_id="m1",
            amount=0, actor_user_id="admin1",
        )


@pytest.mark.asyncio
async def test_apply_reset_uses_zero_amount():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_reset
        await apply_reset(
            wallet_id="w1", allowance_id="a1", member_id="m1",
            actor_user_id="system",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "allowance_reset"
    assert params["p_amount"] == 0


@pytest.mark.asyncio
async def test_apply_rollback_positive_delta():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_rollback
        await apply_rollback(
            wallet_id="w1", allowance_id="a1", member_id="m1",
            amount=50, actor_user_id="admin1", notes="refund grant",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "allowance_rollback"
    assert params["p_amount"] == 50
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/services/test_corporate_allowance_service.py -v
```

- [ ] **Step 3: Implement the service**

```python
# backend/services/corporate_allowance_service.py
"""Allowance movement — wrapper around `corporate_allowance_apply_delta` RPC.

Every allowance grant, reset, or rollback goes through this service. The RPC
locks the master wallet + allowance rows atomically and writes paired ledger
entries. Callers pass the master `wallet_id` and the target `allowance_id`;
the RPC validates they exist before mutating anything.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from ..db_supabase import run_sync  # type: ignore
    from ..supabase_client import supabase  # type: ignore
except ImportError:
    from db_supabase import run_sync  # type: ignore
    from supabase_client import supabase  # type: ignore


async def _apply(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    type_: str,
    amount: float,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[float] = None,
) -> Dict[str, Any]:
    params = {
        "p_wallet_id": wallet_id,
        "p_allowance_id": allowance_id,
        "p_member_id": member_id,
        "p_type": type_,
        "p_amount": amount,
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": floor,
    }

    def _fn():
        return supabase.rpc("corporate_allowance_apply_delta", params).execute()

    resp = await run_sync(_fn)
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise RuntimeError("allowance RPC returned no row")
    return rows[0]


async def apply_grant(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: float,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[float] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    return await _apply(
        wallet_id=wallet_id, allowance_id=allowance_id, member_id=member_id,
        type_="allowance_grant", amount=amount,
        actor_user_id=actor_user_id, notes=notes, floor=floor,
    )


async def apply_reset(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Zero out the `used` counter at the start of a new period."""
    return await _apply(
        wallet_id=wallet_id, allowance_id=allowance_id, member_id=member_id,
        type_="allowance_reset", amount=0,
        actor_user_id=actor_user_id, notes=notes,
    )


async def apply_rollback(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: float,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("rollback amount must be positive")
    return await _apply(
        wallet_id=wallet_id, allowance_id=allowance_id, member_id=member_id,
        type_="allowance_rollback", amount=amount,
        actor_user_id=actor_user_id, notes=notes,
    )
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest backend/tests/services/test_corporate_allowance_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/services/corporate_allowance_service.py backend/tests/services/test_corporate_allowance_service.py
git commit -m "feat(corporate): allowance service wrapping apply-delta RPC"
```

---

## Task 5: Membership service — invite tokens + domain auto-match

**Files:**
- Create: `backend/services/corporate_membership_service.py`
- Test: `backend/tests/services/test_corporate_membership_service.py`

**Scope:**
- `invite_member(company_id, email, role, invited_by)` — generates a cryptographically random token, stores via `insert_corporate_member_invite`, returns `(member_row, deep_link_url)`.
- `accept_invite(token, user_id)` — looks up member by token, flips to `active`, stamps `user_id`. Returns `(company, member)` or raises `InviteNotFound` / `InviteAlreadyConsumed`.
- `auto_match_by_email(user_id, email)` — finds active companies with `corporate_allowed_domains` matching the domain part of `email`; returns a list of `{company, existing_member_status}`. Does NOT create the membership — the rider app confirms.
- `join_via_domain(company_id, user_id, email)` — idempotently creates/activates a member row for an auto-matched company.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/test_corporate_membership_service.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_invite_member_generates_token_and_calls_insert():
    with patch(
        "services.corporate_membership_service.insert_corporate_member_invite",
        AsyncMock(return_value={"id": "m1"}),
    ) as m_ins:
        from services.corporate_membership_service import invite_member
        member, url = await invite_member(
            company_id="c1", email="a@b.com", role="member", invited_by="admin1",
        )
    assert member["id"] == "m1"
    assert "token=" in url
    args = m_ins.await_args.kwargs
    assert args["email"] == "a@b.com"
    assert len(args["invite_token"]) >= 32   # cryptographic length


@pytest.mark.asyncio
async def test_accept_invite_activates_and_stamps_user_id():
    with patch(
        "services.corporate_membership_service.get_member_by_invite_token",
        AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "invited"}),
    ), patch(
        "services.corporate_membership_service.accept_member_invite",
        AsyncMock(return_value={"id": "m1", "status": "active", "user_id": "u1"}),
    ) as m_accept, patch(
        "services.corporate_membership_service.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "name": "Acme"}),
    ):
        from services.corporate_membership_service import accept_invite
        company, member = await accept_invite(token="tok", user_id="u1")
    assert member["status"] == "active"
    assert company["id"] == "c1"
    m_accept.assert_awaited_once_with(member_id="m1", user_id="u1")


@pytest.mark.asyncio
async def test_accept_invite_raises_on_missing_token():
    with patch(
        "services.corporate_membership_service.get_member_by_invite_token",
        AsyncMock(return_value=None),
    ):
        from services.corporate_membership_service import InviteNotFound, accept_invite
        with pytest.raises(InviteNotFound):
            await accept_invite(token="tok", user_id="u1")


@pytest.mark.asyncio
async def test_accept_invite_raises_if_already_consumed():
    with patch(
        "services.corporate_membership_service.get_member_by_invite_token",
        AsyncMock(return_value={"id": "m1", "status": "active"}),
    ):
        from services.corporate_membership_service import (
            InviteAlreadyConsumed,
            accept_invite,
        )
        with pytest.raises(InviteAlreadyConsumed):
            await accept_invite(token="tok", user_id="u1")


@pytest.mark.asyncio
async def test_auto_match_filters_active_companies_by_domain():
    with patch(
        "services.corporate_membership_service.find_companies_by_email_domain",
        AsyncMock(return_value=[
            {"company_id": "c1", "corporate_accounts": {"id": "c1", "name": "Acme", "status": "active"}},
        ]),
    ):
        from services.corporate_membership_service import auto_match_by_email
        matches = await auto_match_by_email(user_id="u1", email="bob@acme.com")
    assert len(matches) == 1
    assert matches[0]["company"]["name"] == "Acme"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/services/test_corporate_membership_service.py -v
```

- [ ] **Step 3: Implement the service**

```python
# backend/services/corporate_membership_service.py
"""Membership state machine — invite issuance, acceptance, domain auto-match.

Invite flow:
    admin calls invite_member → row (status='invited', token=<32b>)
    user opens deep link → accept_invite(token) → row (status='active', user_id=<uid>)

Domain auto-match flow:
    rider app calls auto_match_by_email → returns companies where the
    rider's email domain is in corporate_allowed_domains AND the company
    is active. The rider app surfaces a confirm prompt; confirmation
    routes to join_via_domain.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..db_supabase import (  # type: ignore
        accept_member_invite,
        find_companies_by_email_domain,
        get_corporate_account_by_id,
        get_member_by_invite_token,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
    )
except ImportError:
    from db_supabase import (  # type: ignore
        accept_member_invite,
        find_companies_by_email_domain,
        get_corporate_account_by_id,
        get_member_by_invite_token,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
    )


class InviteNotFound(Exception):
    pass


class InviteAlreadyConsumed(Exception):
    pass


_DEEP_LINK_BASE = "app://join"


def _generate_token() -> str:
    # 32 random bytes → 43-char urlsafe string. Plenty for anti-guess and
    # short enough for a deep link.
    return secrets.token_urlsafe(32)


async def invite_member(
    *,
    company_id: str,
    email: str,
    role: str = "member",
    invited_by: str,
    policy_override: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """Create an invited membership + return (row, deep-link url)."""
    token = _generate_token()
    row = await insert_corporate_member_invite(
        company_id=company_id,
        email=email,
        role=role,
        invite_token=token,
        invited_by=invited_by,
        policy_override=policy_override,
    )
    return row, f"{_DEEP_LINK_BASE}?token={token}"


async def accept_invite(*, token: str, user_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Look up token, flip invited→active. Returns (company, member)."""
    member = await get_member_by_invite_token(token)
    if not member:
        raise InviteNotFound("invite token not found")
    if member.get("status") != "invited":
        raise InviteAlreadyConsumed("invite already accepted or cancelled")
    updated = await accept_member_invite(member_id=member["id"], user_id=user_id)
    if not updated:
        # Race: someone flipped it between our fetch and write.
        raise InviteAlreadyConsumed("invite was just consumed")
    company = await get_corporate_account_by_id(member["company_id"])
    return company or {}, updated


async def auto_match_by_email(
    *, user_id: str, email: str
) -> List[Dict[str, Any]]:
    """Return active companies that allow this rider's email domain AND
    haven't already enrolled them.
    """
    at = (email or "").rfind("@")
    if at < 0:
        return []
    domain = email[at + 1 :].strip().lower()
    if not domain:
        return []
    raw = await find_companies_by_email_domain(domain)
    # Filter out companies where the user is already an active member (dedupe).
    existing = {m["company_id"] for m in await list_active_memberships_for_user(user_id)}
    matches = []
    for r in raw:
        company = r.get("corporate_accounts") or {}
        cid = company.get("id") or r.get("company_id")
        if cid and cid not in existing:
            matches.append({"company": company})
    return matches


async def join_via_domain(
    *,
    company_id: str,
    user_id: str,
    email: str,
) -> Dict[str, Any]:
    """Create an active membership in one shot (no token round-trip).

    Reused on the rider app's in-app "Join Acme Corp?" confirmation. We still
    write a corporate_members row, but skip the invited-status step because
    the domain match is itself proof of employment.
    """
    # Reuse insert + accept to keep the state-machine invariant (invited→active).
    token = _generate_token()
    member = await insert_corporate_member_invite(
        company_id=company_id,
        email=email,
        role="member",
        invite_token=token,
        invited_by=user_id,  # self-initiated
    )
    updated = await accept_member_invite(member_id=member["id"], user_id=user_id)
    return updated or member
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest backend/tests/services/test_corporate_membership_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/services/corporate_membership_service.py backend/tests/services/test_corporate_membership_service.py
git commit -m "feat(corporate): membership service — invite/accept/domain match"
```

---

## Task 6: Company-admin endpoints — members, invites, allowances, domains

**Files:**
- Create: `backend/routes/corporate_company.py`
- Modify: `backend/main.py`
- Create: `backend/dependencies/company_guard.py`
- Test: `backend/tests/test_corporate_company_routes.py`

**Guard design:** `/company/**` endpoints are called by an authenticated user. The guard resolves `current_user → corporate_members (role in [owner,admin]) → company_id` and rejects if the path's `company_id` doesn't match or the caller is only a `member`. Write it as a FastAPI dependency factory so tests can inject.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_company_routes.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_invite_member_requires_admin_role(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member"}]),
    ):
        resp = test_client.post(
            "/company/c1/members/invite",
            json={"email": "a@b.com", "role": "member"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invite_member_success(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "services.corporate_membership_service.invite_member",
        AsyncMock(return_value=({"id": "m1", "status": "invited"}, "app://join?token=xyz")),
    ) as m_invite:
        resp = test_client.post(
            "/company/c1/members/invite",
            json={"email": "a@b.com", "role": "member"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"]["id"] == "m1"
    assert body["invite_url"].startswith("app://join?token=")
    m_invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_members_filters_active_by_default(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.list_company_members",
        AsyncMock(return_value=[{"id": "m1"}, {"id": "m2"}]),
    ):
        resp = test_client.get(
            "/company/c1/members",
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_set_member_allowance_calls_upsert(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "db_supabase.upsert_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": 0,
        }),
    ) as m_upsert:
        resp = test_client.put(
            "/company/c1/members/m1/allowance",
            json={
                "type": "fixed_recurring",
                "amount": 500,
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            },
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    m_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_member_sets_status_removed(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active"}),
    ), patch(
        "db_supabase.update_corporate_member",
        AsyncMock(return_value={"id": "m1", "status": "removed"}),
    ) as m_upd:
        resp = test_client.delete(
            "/company/c1/members/m1",
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    m_upd.assert_awaited_once_with("m1", {"status": "removed"})


@pytest.mark.asyncio
async def test_add_allowed_domain_lowercases(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.add_allowed_domain",
        AsyncMock(return_value={"company_id": "c1", "domain": "acme.com"}),
    ) as m_add:
        resp = test_client.post(
            "/company/c1/allowed-domains",
            json={"domain": "Acme.COM"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    m_add.assert_awaited_once()
    # Pydantic normalized to lowercase before calling.
    assert m_add.await_args.kwargs["domain"] == "acme.com"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_company_routes.py -v
```

- [ ] **Step 3: Implement the guard**

```python
# backend/dependencies/company_guard.py
"""Resolve caller → company admin role. Mount as FastAPI dependency."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Path

try:
    from ..db_supabase import list_active_memberships_for_user  # type: ignore
    from ..dependencies import get_current_user  # type: ignore
except ImportError:
    from db_supabase import list_active_memberships_for_user  # type: ignore
    from dependencies import get_current_user  # type: ignore


_ADMIN_ROLES = {"owner", "admin"}


async def require_company_admin(
    company_id: str = Path(..., description="Corporate account ID"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id and m.get("role") in _ADMIN_ROLES:
            return {"user": current_user, "company_id": company_id, "role": m["role"]}
    raise HTTPException(status_code=403, detail="not a company admin")


async def require_company_member(
    company_id: str = Path(..., description="Corporate account ID"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id:
            return {"user": current_user, "company_id": company_id, "role": m["role"]}
    raise HTTPException(status_code=403, detail="not a company member")
```

If `backend/dependencies/` doesn't exist as a package, create `backend/dependencies/__init__.py` that re-exports the existing `get_current_user`, `get_admin_user`, etc. from whatever file they're currently defined in.

- [ ] **Step 4: Implement the route module**

```python
# backend/routes/corporate_company.py
"""Company-admin endpoints (`/company/**`). Consumed by the company portal
and used by the rider app for read paths (balances).

Separation: writes requiring admin role use require_company_admin.
Reads available to any active member use require_company_member.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

try:
    from ..db_supabase import (  # type: ignore
        add_allowed_domain,
        delete_allowed_domain,
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        update_corporate_member,
        upsert_member_allowance,
    )
    from ..dependencies.company_guard import (  # type: ignore
        require_company_admin,
        require_company_member,
    )
    from ..schemas.corporate import (  # type: ignore
        AllowanceCreate,
        AllowanceResponse,
        AllowanceUpdate,
        AllowedDomainCreate,
        MemberInvite,
        MemberResponse,
        MemberUpdate,
    )
    from ..services.corporate_membership_service import invite_member  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        add_allowed_domain,
        delete_allowed_domain,
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        update_corporate_member,
        upsert_member_allowance,
    )
    from dependencies.company_guard import (  # type: ignore
        require_company_admin,
        require_company_member,
    )
    from schemas.corporate import (  # type: ignore
        AllowanceCreate,
        AllowanceResponse,
        AllowanceUpdate,
        AllowedDomainCreate,
        MemberInvite,
        MemberResponse,
        MemberUpdate,
    )
    from services.corporate_membership_service import invite_member  # type: ignore


router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company"])


# ---------- Members ----------
@router.get("/members")
async def list_members(
    company_id: str,
    status: Optional[str] = None,
    guard=Depends(require_company_admin),
):
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    return await list_company_members(company_id=company_id, statuses=statuses)


@router.post("/members/invite")
async def invite(
    company_id: str,
    body: MemberInvite,
    guard=Depends(require_company_admin),
):
    member, url = await invite_member(
        company_id=company_id,
        email=body.email,
        role=body.role.value,
        invited_by=guard["user"]["id"],
        policy_override=body.policy_override,
    )
    return {"member": member, "invite_url": url}


@router.patch("/members/{member_id}")
async def update_member(
    company_id: str,
    member_id: str,
    body: MemberUpdate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump(exclude_none=True)
    if "role" in patch:
        patch["role"] = patch["role"].value if hasattr(patch["role"], "value") else patch["role"]
    if "status" in patch:
        patch["status"] = patch["status"].value if hasattr(patch["status"], "value") else patch["status"]
    return await update_corporate_member(member_id, patch) or existing


@router.delete("/members/{member_id}")
async def remove_member(
    company_id: str,
    member_id: str,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    return await update_corporate_member(member_id, {"status": "removed"}) or existing


# ---------- Allowances ----------
@router.get("/allowances")
async def list_allowances(company_id: str, guard=Depends(require_company_admin)):
    return await list_company_allowances(company_id)


@router.get("/members/{member_id}/allowance")
async def get_allowance(
    company_id: str,
    member_id: str,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    return await get_member_allowance(member_id) or {}


@router.put("/members/{member_id}/allowance")
async def set_allowance(
    company_id: str,
    member_id: str,
    body: AllowanceCreate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump()
    patch["type"] = patch["type"].value if hasattr(patch["type"], "value") else patch["type"]
    # Serialize dates to ISO for Supabase JSON body.
    for k in ("period_start", "period_end"):
        if patch.get(k) is not None:
            patch[k] = patch[k].isoformat()
    return await upsert_member_allowance(member_id=member_id, patch=patch)


@router.patch("/members/{member_id}/allowance")
async def patch_allowance(
    company_id: str,
    member_id: str,
    body: AllowanceUpdate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump(exclude_none=True)
    if "status" in patch:
        patch["status"] = patch["status"].value if hasattr(patch["status"], "value") else patch["status"]
    if not patch:
        return await get_member_allowance(member_id) or {}
    return await upsert_member_allowance(member_id=member_id, patch=patch)


# ---------- Allowance requests (admin side) ----------
@router.get("/allowance-requests")
async def list_requests(
    company_id: str,
    status: Optional[str] = "pending",
    guard=Depends(require_company_admin),
):
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    return await list_company_allowance_requests(company_id, statuses=statuses)


# ---------- Allowed domains ----------
@router.get("/allowed-domains")
async def list_domains(company_id: str, guard=Depends(require_company_admin)):
    return await list_allowed_domains(company_id)


@router.post("/allowed-domains")
async def add_domain(
    company_id: str,
    body: AllowedDomainCreate,
    guard=Depends(require_company_admin),
):
    return await add_allowed_domain(company_id=company_id, domain=body.domain)


@router.delete("/allowed-domains/{domain}")
async def remove_domain(
    company_id: str,
    domain: str,
    guard=Depends(require_company_admin),
):
    await delete_allowed_domain(company_id=company_id, domain=domain.lower())
    return {"status": "ok"}
```

- [ ] **Step 5: Mount the router in `main.py`**

Follow the existing pattern (try relative import, fall back to absolute). Add just below the `corporate_wallet` router mount:

```python
try:
    from .routes.corporate_company import router as corporate_company_router
except ImportError:
    from routes.corporate_company import router as corporate_company_router
app.include_router(corporate_company_router)
```

- [ ] **Step 6: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_company_routes.py -v
```

- [ ] **Step 7: Commit**

```bash
git add backend/routes/corporate_company.py backend/dependencies/ backend/main.py backend/tests/test_corporate_company_routes.py
git commit -m "feat(corporate): company-admin endpoints for members, allowances, domains"
```

---

## Task 7: Rider-app Work Profile endpoints

**Files:**
- Create: `backend/routes/corporate_rider.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_corporate_rider_routes.py`

**Endpoints (all auth'd as the rider):**

| Path | Purpose |
|---|---|
| `GET  /rider/work-profile` | All active memberships for the rider (for the Personal/Work picker). |
| `GET  /rider/work-profile/auto-match?email=…` | Companies whose allowed-domains match the rider's email. Used on signup + Settings → "Join my company". |
| `POST /rider/work-profile/accept-invite` | Body: `{token}`. Flips invited → active. |
| `POST /rider/work-profile/join-domain` | Body: `{company_id, email}`. Creates an active member row (domain confirmed). |
| `GET  /rider/work-profile/{company_id}/balance` | `{allowance, used, remaining, company_name, period_end}`. |
| `GET  /rider/work-profile/{company_id}/rides?from=&to=` | Work rides for the caller in date range. Stubbed for Plan 5 — returns `[]` in v1 of this plan, but the route exists and is auth-guarded. |
| `POST /rider/work-profile/{company_id}/allowance-requests` | Body `AllowanceRequestCreate`. Creates pending request OR immediately auto-approves (see Task 8). |
| `GET  /rider/work-profile/{company_id}/allowance-requests` | History of caller's own requests. |

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_rider_routes.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_work_profile_lists_active_memberships(test_client, rider_auth_headers):
    with patch(
        "db_supabase.list_active_memberships_for_user",
        AsyncMock(return_value=[
            {"id": "m1", "company_id": "c1", "role": "member"},
            {"id": "m2", "company_id": "c2", "role": "admin"},
        ]),
    ), patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(side_effect=[
            {"id": "c1", "name": "Acme"},
            {"id": "c2", "name": "Beta"},
        ]),
    ):
        resp = test_client.get("/rider/work-profile", headers=rider_auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["company"]["name"] == "Acme"


@pytest.mark.asyncio
async def test_auto_match_returns_matches(test_client, rider_auth_headers):
    with patch(
        "services.corporate_membership_service.auto_match_by_email",
        AsyncMock(return_value=[{"company": {"id": "c1", "name": "Acme"}}]),
    ):
        resp = test_client.get(
            "/rider/work-profile/auto-match?email=alice@acme.com",
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()[0]["company"]["name"] == "Acme"


@pytest.mark.asyncio
async def test_accept_invite_route_returns_company_and_member(test_client, rider_auth_headers):
    with patch(
        "services.corporate_membership_service.accept_invite",
        AsyncMock(return_value=(
            {"id": "c1", "name": "Acme"},
            {"id": "m1", "status": "active"},
        )),
    ):
        resp = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["company"]["name"] == "Acme"


@pytest.mark.asyncio
async def test_accept_invite_returns_404_when_token_not_found(test_client, rider_auth_headers):
    from services.corporate_membership_service import InviteNotFound
    with patch(
        "services.corporate_membership_service.accept_invite",
        AsyncMock(side_effect=InviteNotFound("nope")),
    ):
        resp = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_balance_returns_remaining(test_client, rider_auth_headers):
    with patch(
        "db_supabase.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": -120,  # granted 500, spent 380 so far (used goes from 0 ↓ as grants post)
            "period_end": "2026-04-30",
        }),
    ), patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "name": "Acme"}),
    ):
        resp = test_client.get(
            "/rider/work-profile/c1/balance",
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_name"] == "Acme"
    assert body["period_end"] == "2026-04-30"
    # remaining = amount + used for fixed_recurring (used is negative after grants, positive after debits)
    # v1 convention (this plan): "remaining" = amount - max(used, 0); if Plan 5 changes semantics, revisit.


@pytest.mark.asyncio
async def test_allowance_request_rate_limit_returns_409(test_client, rider_auth_headers):
    with patch(
        "db_supabase.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "auto_approve_topup_amount": None}),
    ), patch(
        "db_supabase.list_pending_allowance_requests_for_member",
        AsyncMock(return_value=[{"id": "r0", "status": "pending"}]),
    ):
        resp = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_rider_routes.py -v
```

- [ ] **Step 3: Implement the route module**

```python
# backend/routes/corporate_rider.py
"""Rider-app Work Profile endpoints (`/rider/work-profile/**`)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_member_allowance,
        insert_allowance_request,
        list_active_memberships_for_user,
        list_pending_allowance_requests_for_member,
    )
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas.corporate import AllowanceRequestCreate  # type: ignore
    from ..services.corporate_membership_service import (  # type: ignore
        InviteAlreadyConsumed,
        InviteNotFound,
        accept_invite,
        auto_match_by_email,
        join_via_domain,
    )
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_member_allowance,
        insert_allowance_request,
        list_active_memberships_for_user,
        list_pending_allowance_requests_for_member,
    )
    from dependencies import get_current_user  # type: ignore
    from schemas.corporate import AllowanceRequestCreate  # type: ignore
    from services.corporate_membership_service import (  # type: ignore
        InviteAlreadyConsumed,
        InviteNotFound,
        accept_invite,
        auto_match_by_email,
        join_via_domain,
    )


router = APIRouter(prefix="/rider/work-profile", tags=["Corporate Rider"])


class AcceptInviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class JoinDomainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    email: str


def _compute_remaining(allowance: dict) -> Optional[float]:
    """v1 convention: for fixed_recurring / one_time → amount minus debited portion.

    `used` is negative after grants post (grants decrement used by the grant amount)
    and positive after ride debits post. Remaining for the rider UI is:

        remaining = amount - max(used, 0)

    Unlimited allowances return None (infinite).
    """
    if allowance.get("type") == "unlimited":
        return None
    amt = allowance.get("amount")
    used = allowance.get("used") or 0
    if amt is None:
        return None
    return float(amt) - max(float(used), 0.0)


async def _ensure_member(current_user: dict, company_id: str) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id:
            return m
    raise HTTPException(status_code=403, detail="not a company member")


@router.get("")
async def list_work_profiles(current_user: dict = Depends(get_current_user)):
    memberships = await list_active_memberships_for_user(current_user["id"])
    out = []
    for m in memberships:
        company = await get_corporate_account_by_id(m["company_id"]) or {}
        out.append({
            "membership": m,
            "company": {"id": company.get("id"), "name": company.get("name")},
        })
    return out


@router.get("/auto-match")
async def auto_match(
    email: str,
    current_user: dict = Depends(get_current_user),
):
    return await auto_match_by_email(user_id=current_user["id"], email=email)


@router.post("/accept-invite")
async def do_accept_invite(
    body: AcceptInviteBody,
    current_user: dict = Depends(get_current_user),
):
    try:
        company, member = await accept_invite(token=body.token, user_id=current_user["id"])
    except InviteNotFound:
        raise HTTPException(status_code=404, detail="invite not found")
    except InviteAlreadyConsumed:
        raise HTTPException(status_code=409, detail="invite already used")
    return {"company": company, "member": member}


@router.post("/join-domain")
async def do_join_domain(
    body: JoinDomainBody,
    current_user: dict = Depends(get_current_user),
):
    member = await join_via_domain(
        company_id=body.company_id, user_id=current_user["id"], email=body.email,
    )
    company = await get_corporate_account_by_id(body.company_id) or {}
    return {"company": company, "member": member}


@router.get("/{company_id}/balance")
async def my_balance(
    company_id: str,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    allowance = await get_member_allowance(membership["id"]) or {}
    company = await get_corporate_account_by_id(company_id) or {}
    return {
        "company_name": company.get("name"),
        "type": allowance.get("type"),
        "amount": allowance.get("amount"),
        "used": allowance.get("used"),
        "remaining": _compute_remaining(allowance),
        "period_start": allowance.get("period_start"),
        "period_end": allowance.get("period_end"),
        "status": allowance.get("status"),
    }


@router.get("/{company_id}/rides")
async def my_rides(
    company_id: str,
    from_: Optional[str] = None,
    to: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Plan-3 stub. Plan 5 wires ride_payment_sources → rides join."""
    await _ensure_member(current_user, company_id)
    return []


@router.post("/{company_id}/allowance-requests")
async def submit_request(
    company_id: str,
    body: AllowanceRequestCreate,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    pending = await list_pending_allowance_requests_for_member(membership["id"])
    if pending:
        raise HTTPException(status_code=409, detail="a request is already pending")
    # Attempt auto-approval (Task 8 hook point — imported lazily to avoid cycles).
    try:
        from services.corporate_allowance_service import apply_grant  # type: ignore
    except ImportError:
        from ..services.corporate_allowance_service import apply_grant  # type: ignore
    allowance = await get_member_allowance(membership["id"]) or {}
    auto_cap = allowance.get("auto_approve_topup_amount")
    auto_monthly = allowance.get("auto_approve_monthly_count")
    used_auto = allowance.get("auto_approved_this_period") or 0
    if (
        auto_cap is not None
        and body.amount <= float(auto_cap)
        and auto_monthly is not None
        and used_auto < int(auto_monthly)
    ):
        # Auto-approve: (1) create record with status=auto_approved, (2) grant via RPC.
        row = await insert_allowance_request(
            member_id=membership["id"], amount=body.amount,
            reason=body.reason, status="auto_approved",
        )
        try:
            from db_supabase import get_corporate_wallet_by_company  # type: ignore
        except ImportError:
            from ..db_supabase import get_corporate_wallet_by_company  # type: ignore
        wallet = await get_corporate_wallet_by_company(company_id)
        if wallet:
            await apply_grant(
                wallet_id=wallet["id"],
                allowance_id=allowance["id"],
                member_id=membership["id"],
                amount=body.amount,
                actor_user_id=current_user["id"],
                notes=f"auto_approved request {row.get('id','')}",
                floor=float(wallet.get("soft_negative_floor", -50)),
            )
        return row
    # Manual review path.
    return await insert_allowance_request(
        member_id=membership["id"], amount=body.amount,
        reason=body.reason, status="pending",
    )


@router.get("/{company_id}/allowance-requests")
async def my_requests(
    company_id: str,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    try:
        from db_supabase import list_company_allowance_requests  # type: ignore
    except ImportError:
        from ..db_supabase import list_company_allowance_requests  # type: ignore
    # Filter app-side by this member_id since the query is company-scoped.
    rows = await list_company_allowance_requests(company_id, statuses=None)
    return [r for r in rows if r.get("member_id") == membership["id"]]
```

- [ ] **Step 4: Mount the router in `main.py`**

```python
try:
    from .routes.corporate_rider import router as corporate_rider_router
except ImportError:
    from routes.corporate_rider import router as corporate_rider_router
app.include_router(corporate_rider_router)
```

- [ ] **Step 5: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_rider_routes.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/routes/corporate_rider.py backend/main.py backend/tests/test_corporate_rider_routes.py
git commit -m "feat(corporate): rider work-profile endpoints"
```

---

## Task 8: Admin approve/deny for allowance requests

**Files:**
- Modify: `backend/routes/corporate_company.py`
- Test: `backend/tests/test_corporate_allowance_requests.py`

**Semantics:**
- Approve → flips request to `approved`, then calls `apply_grant` on the allowance for the request amount. Writes an admin actor into the ledger.
- Deny → flips to `denied`, stores decision_notes. No money moves.
- Already-reviewed requests return 409.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_allowance_requests.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_approve_request_grants_amount(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_allowance_request_by_id",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 200, "status": "pending",
        }),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "member_id": "m1"}),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
    ), patch(
        "services.corporate_allowance_service.apply_grant",
        AsyncMock(return_value={"master_balance_after": 800, "allowance_used_after": -200}),
    ) as m_grant, patch(
        "db_supabase.update_allowance_request",
        AsyncMock(return_value={"id": "r1", "status": "approved"}),
    ) as m_upd:
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "ok"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    m_grant.assert_awaited_once()
    m_upd.assert_awaited_once()
    kwargs = m_grant.await_args.kwargs
    assert kwargs["amount"] == 200
    assert kwargs["wallet_id"] == "w1"


@pytest.mark.asyncio
async def test_deny_request_skips_grant(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_allowance_request_by_id",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 200, "status": "pending",
        }),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "services.corporate_allowance_service.apply_grant",
        AsyncMock(),
    ) as m_grant, patch(
        "db_supabase.update_allowance_request",
        AsyncMock(return_value={"id": "r1", "status": "denied"}),
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": False, "note": "over budget"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 200
    m_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_decided_request_returns_409(test_client, rider_auth_headers):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_allowance_request_by_id",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 200, "status": "approved",
        }),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "dupe"},
            headers=rider_auth_headers,
        )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_allowance_requests.py -v
```

- [ ] **Step 3: Add the decide endpoint to `corporate_company.py`**

```python
# Append inside corporate_company.py imports:
try:
    from ..db_supabase import (  # type: ignore
        get_allowance_request_by_id,
        get_corporate_wallet_by_company,
        update_allowance_request,
    )
    from ..schemas.corporate import AllowanceRequestDecision  # type: ignore
    from ..services.corporate_allowance_service import apply_grant  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_allowance_request_by_id,
        get_corporate_wallet_by_company,
        update_allowance_request,
    )
    from schemas.corporate import AllowanceRequestDecision  # type: ignore
    from services.corporate_allowance_service import apply_grant  # type: ignore


@router.post("/allowance-requests/{request_id}/decide")
async def decide_allowance_request(
    company_id: str,
    request_id: str,
    body: AllowanceRequestDecision,
    guard=Depends(require_company_admin),
):
    request = await get_allowance_request_by_id(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    member = await get_corporate_member_by_id(request["member_id"])
    if not member or member.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if request.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Request already decided")

    new_status = "approved" if body.approve else "denied"
    if body.approve:
        allowance = await get_member_allowance(request["member_id"])
        wallet = await get_corporate_wallet_by_company(company_id)
        if not allowance or not wallet:
            raise HTTPException(status_code=409, detail="missing allowance or wallet")
        await apply_grant(
            wallet_id=wallet["id"],
            allowance_id=allowance["id"],
            member_id=request["member_id"],
            amount=float(request["amount"]),
            actor_user_id=guard["user"]["id"],
            notes=f"approved request {request_id}",
            floor=float(wallet.get("soft_negative_floor", -50)),
        )
    return await update_allowance_request(
        request_id=request_id,
        status=new_status,
        reviewed_by=guard["user"]["id"],
        decision_notes=body.note,
    )
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_allowance_requests.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_company.py backend/tests/test_corporate_allowance_requests.py
git commit -m "feat(corporate): admin approve/deny for allowance requests"
```

---

## Task 9: Monthly allowance reset job

**Files:**
- Create: `backend/utils/allowance_reset.py`
- Modify: `backend/main.py` (register the loop next to other scheduled tasks)
- Test: `backend/tests/test_corporate_allowance_reset.py`

**Behavior:**
- Every hour (configurable), scan `corporate_member_allowances` WHERE `type='fixed_recurring' AND status='active' AND period_end < today_utc`.
- For each: compute new period (`period_start = old period_end`, `period_end = new_period_start + 1 month`). If `rollover=false`, call `apply_reset` (zero `used`). Update the period dates.
- Idempotent: rely on `period_end >= today` as the short-circuit.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_corporate_allowance_reset.py
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_reset_runs_for_stale_allowances():
    stale = {
        "id": "a1", "member_id": "m1", "type": "fixed_recurring", "status": "active",
        "period_start": "2026-03-01", "period_end": "2026-03-31",
        "rollover": False, "used": -100,  # $100 unspent, $0 spent
    }
    with patch(
        "utils.allowance_reset.list_allowances_due_for_reset",
        AsyncMock(return_value=[stale]),
    ), patch(
        "utils.allowance_reset.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "utils.allowance_reset.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
    ), patch(
        "utils.allowance_reset.apply_reset",
        AsyncMock(return_value={"master_balance_after": 0, "allowance_used_after": 0}),
    ) as m_reset, patch(
        "utils.allowance_reset.reset_allowance_period",
        AsyncMock(return_value={"id": "a1"}),
    ) as m_period:
        from utils.allowance_reset import run_allowance_reset_tick
        await run_allowance_reset_tick(now=date(2026, 4, 1))
    m_reset.assert_awaited_once()
    m_period.assert_awaited_once()
    period_args = m_period.await_args.kwargs
    assert period_args["period_start"] == "2026-03-31"  # old period_end
    assert period_args["period_end"].startswith("2026-04-")


@pytest.mark.asyncio
async def test_reset_skips_rollover_flag():
    rollover = {
        "id": "a2", "member_id": "m2", "type": "fixed_recurring", "status": "active",
        "period_start": "2026-03-01", "period_end": "2026-03-31",
        "rollover": True, "used": -100,
    }
    with patch(
        "utils.allowance_reset.list_allowances_due_for_reset",
        AsyncMock(return_value=[rollover]),
    ), patch(
        "utils.allowance_reset.apply_reset",
        AsyncMock(),
    ) as m_reset, patch(
        "utils.allowance_reset.reset_allowance_period",
        AsyncMock(return_value={"id": "a2"}),
    ) as m_period:
        from utils.allowance_reset import run_allowance_reset_tick
        await run_allowance_reset_tick(now=date(2026, 4, 1))
    m_reset.assert_not_awaited()   # rollover = no reset
    m_period.assert_awaited_once() # but period still rolls forward
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest backend/tests/test_corporate_allowance_reset.py -v
```

- [ ] **Step 3: Implement the job**

```python
# backend/utils/allowance_reset.py
"""Monthly allowance reset — rolls fixed_recurring periods forward and
zeroes `used` for non-rollover allowances.

Runs as a scheduled loop (pattern: utils/scheduled_rides.py). Idempotent:
if period_end is already >= today, nothing happens.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

try:
    from ..db_supabase import (  # type: ignore
        get_corporate_member_by_id,
        get_corporate_wallet_by_company,
        list_allowances_due_for_reset,
        reset_allowance_period,
    )
    from ..services.corporate_allowance_service import apply_reset  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_member_by_id,
        get_corporate_wallet_by_company,
        list_allowances_due_for_reset,
        reset_allowance_period,
    )
    from services.corporate_allowance_service import apply_reset  # type: ignore


logger = logging.getLogger(__name__)


def _add_one_month(d: date) -> date:
    """Crude month-add: last day of month stays on last day of next month."""
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    # Clamp day to new month's length.
    for day in range(d.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)   # shouldn't happen


async def run_allowance_reset_tick(now: Optional[date] = None) -> int:
    """One sweep. Returns count of allowances processed."""
    today = now or date.today()
    rows = await list_allowances_due_for_reset(as_of=today.isoformat())
    processed = 0
    for r in rows:
        try:
            old_end = date.fromisoformat(r["period_end"])
            new_start = old_end
            new_end = _add_one_month(old_end)
            member = await get_corporate_member_by_id(r["member_id"])
            if not member:
                continue
            wallet = await get_corporate_wallet_by_company(member["company_id"])
            if not wallet:
                continue
            if not r.get("rollover"):
                await apply_reset(
                    wallet_id=wallet["id"],
                    allowance_id=r["id"],
                    member_id=r["member_id"],
                    actor_user_id=None,
                    notes=f"period reset {new_start} → {new_end}",
                )
            await reset_allowance_period(
                allowance_id=r["id"],
                period_start=new_start.isoformat(),
                period_end=new_end.isoformat(),
            )
            processed += 1
        except Exception:  # noqa: BLE001
            logger.exception("allowance reset failed for %s", r.get("id"))
    return processed


async def allowance_reset_loop(interval_seconds: int = 3600) -> None:
    while True:
        try:
            await run_allowance_reset_tick()
        except Exception:  # noqa: BLE001
            logger.exception("allowance reset tick raised")
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: Register the loop in `main.py`**

Find where other background loops are registered (near `startup`/`lifespan` → look for `payment_retry` or similar). Add:

```python
try:
    from .utils.allowance_reset import allowance_reset_loop
except ImportError:
    from utils.allowance_reset import allowance_reset_loop

asyncio.create_task(allowance_reset_loop())
```

Stay consistent with how the existing loops (`scheduled_rides`, etc.) are launched — if they use `app.on_event("startup")`, match that.

- [ ] **Step 5: Run — expect PASS**

```bash
pytest backend/tests/test_corporate_allowance_reset.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/utils/allowance_reset.py backend/main.py backend/tests/test_corporate_allowance_reset.py
git commit -m "feat(corporate): monthly allowance reset job"
```

---

## Task 10: Admin-dashboard — company members page (reference)

**Files:**
- Create: `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx`
- Create: `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx`

**Scope:** this is a minimal super-admin-facing view so Plan 3 delivers something clickable. The customer-facing portal under `/company/**` is Plan 7.

- [ ] **Step 1: Members table + inline actions**

Render a table of members from `GET /admin/corporate-accounts/{id}/members` (reuse the existing admin pattern for company detail). Columns: email, role, status, allowance used/total, actions (invite, edit allowance, remove).

Follow the table style from `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` (list page). Reuse the existing `api.ts` client.

- [ ] **Step 2: Allowance edit dialog**

Modal with the same three-radio shape as `AllowanceCreate`:
- `fixed_recurring` → amount + period_start + period_end + rollover + optional auto_approve fields
- `one_time` → amount only
- `unlimited` → no amount

Submits `PUT /company/{id}/members/{memberId}/allowance`.

- [ ] **Step 3: Manual QA**

1. Navigate to a company detail page.
2. Open Members tab.
3. Invite an email, verify a row appears with status `invited`.
4. Open the invite URL as a second user → verify status flips to `active`.
5. Set an allowance → verify the ledger shows two rows (master debit + member credit) via the wallet tab.
6. Submit an allowance request as the member → verify the admin sees it and can approve, granting more money via the ledger.

- [ ] **Step 4: Commit**

```bash
git add admin-dashboard/src/app/dashboard/corporate-accounts/\[id\]/members/
git commit -m "feat(admin): corporate members + allowance dialog"
```

---

## Task 11: e2e test — member lifecycle with money movement

**Files:**
- Create: `backend/tests/test_corporate_e2e_members.py`

**Scope:** exercise the full Plan-3 happy path against mocked DB + service layer. Catches integration drift between routes / services / RPC contract.

- [ ] **Step 1: Write the e2e test**

```python
# backend/tests/test_corporate_e2e_members.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_full_member_lifecycle(test_client, rider_auth_headers, admin_auth_headers):
    """
    1) Admin invites member (token issued).
    2) Rider accepts invite (status invited → active).
    3) Admin sets fixed_recurring allowance → grant RPC fires, ledger records it.
    4) Rider views balance → sees amount + zero used.
    5) Rider submits a manual allowance request (no auto-approve rule set) → pending.
    6) Admin approves → grant fires, status→approved.
    7) Admin removes the member → status=removed.
    """
    # ---- 1) Invite ----
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "services.corporate_membership_service.invite_member",
        AsyncMock(return_value=(
            {"id": "m1", "status": "invited", "invite_token": "tok"},
            "app://join?token=tok",
        )),
    ):
        r = test_client.post(
            "/company/c1/members/invite",
            json={"email": "alice@acme.com", "role": "member"},
            headers=admin_auth_headers,
        )
    assert r.status_code == 200

    # ---- 2) Accept ----
    with patch(
        "services.corporate_membership_service.accept_invite",
        AsyncMock(return_value=(
            {"id": "c1", "name": "Acme"},
            {"id": "m1", "status": "active", "user_id": "u1"},
        )),
    ):
        r = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
            headers=rider_auth_headers,
        )
    assert r.status_code == 200

    # ---- 3) Set allowance ----
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "db_supabase.upsert_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": 0,
        }),
    ):
        r = test_client.put(
            "/company/c1/members/m1/allowance",
            json={
                "type": "fixed_recurring", "amount": 500,
                "period_start": "2026-04-01", "period_end": "2026-04-30",
            },
            headers=admin_auth_headers,
        )
    assert r.status_code == 200

    # ---- 4) Rider views balance ----
    with patch(
        "db_supabase.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": 0, "period_end": "2026-04-30",
            "status": "active",
        }),
    ), patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "name": "Acme"}),
    ):
        r = test_client.get(
            "/rider/work-profile/c1/balance",
            headers=rider_auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["remaining"] == 500

    # ---- 5) Rider submits manual request (no auto-approve) ----
    with patch(
        "db_supabase.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "auto_approve_topup_amount": None}),
    ), patch(
        "db_supabase.list_pending_allowance_requests_for_member",
        AsyncMock(return_value=[]),
    ), patch(
        "db_supabase.insert_allowance_request",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 100,
            "reason": "client dinner", "status": "pending",
        }),
    ):
        r = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
            headers=rider_auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

    # ---- 6) Admin approves ----
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_allowance_request_by_id",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 100, "status": "pending",
        }),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "db_supabase.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "member_id": "m1"}),
    ), patch(
        "db_supabase.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
    ), patch(
        "services.corporate_allowance_service.apply_grant",
        AsyncMock(return_value={"master_balance_after": 400, "allowance_used_after": -100}),
    ), patch(
        "db_supabase.update_allowance_request",
        AsyncMock(return_value={"id": "r1", "status": "approved"}),
    ):
        r = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "ok"},
            headers=admin_auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # ---- 7) Admin removes member ----
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "db_supabase.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active"}),
    ), patch(
        "db_supabase.update_corporate_member",
        AsyncMock(return_value={"id": "m1", "status": "removed"}),
    ):
        r = test_client.delete(
            "/company/c1/members/m1",
            headers=admin_auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["status"] == "removed"
```

- [ ] **Step 2: Fixture `admin_auth_headers`**

If it doesn't already exist in `conftest.py`, add it — it's a JWT with an admin user id. Inspect other corporate tests for how they stub auth.

- [ ] **Step 3: Run the full Plan-3 test suite**

```bash
pytest backend/tests/test_corporate_membership_schemas.py \
       backend/tests/test_corporate_membership_db_helpers.py \
       backend/tests/services/test_corporate_allowance_service.py \
       backend/tests/services/test_corporate_membership_service.py \
       backend/tests/test_corporate_company_routes.py \
       backend/tests/test_corporate_rider_routes.py \
       backend/tests/test_corporate_allowance_requests.py \
       backend/tests/test_corporate_allowance_reset.py \
       backend/tests/test_corporate_e2e_members.py -v
```

Expected: all PASS.

- [ ] **Step 4: Regression — run the full existing test suite**

```bash
pytest backend/tests/ -v --ignore=backend/tests/test_corporate_b2b_schema.py
```

Expected: no new failures. (The schema-probe test is skipped because it requires a live DB.)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_corporate_e2e_members.py backend/tests/conftest.py
git commit -m "test(corporate): e2e member lifecycle with money movement"
```

---

## Done criteria (Plan 3)

- Companies can invite members via email; invite-token deep link activates membership.
- Email-domain auto-match returns eligible active companies for a signed-in rider.
- Each active member has exactly one allowance row; admins can PUT `fixed_recurring`, `one_time`, or `unlimited` shape.
- Allowance grants lock the master wallet + allowance rows atomically (migration 29 RPC) and write paired ledger entries.
- Rider can view their balance, request more, and see request history.
- Rate-limit: a single pending request per member at any time (DB-enforced via unique partial index from migration 27).
- Auto-approve path fires grants immediately when request ≤ `auto_approve_topup_amount` AND `auto_approved_this_period < auto_approve_monthly_count`.
- Admin approve/deny endpoints fire the grant and update the request in one transaction.
- Monthly reset job rolls `fixed_recurring` periods forward; non-rollover allowances get `used` zeroed.
- Admin dashboard shows a minimal members table + allowance dialog (super-admin-facing; customer portal is Plan 7).

### Handoff to Plan 4 (policy engine)
After this plan: employees have budgets but there are no rules constraining how those budgets get spent. Plan 4 introduces `corporate_policies` CRUD + the `evaluate_policy(policy, ride_context)` pure function, and stubs the `corporate_policy_evaluations` audit log. Plan 5 then wires both allowance debit and policy evaluation into the ride completion flow.
