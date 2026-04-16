# Corporate B2B — Plan 1: DB Foundation + Super-admin + KYB Queue

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the B2B schema, expose a complete super-admin CRUD surface for companies with KYB review, and make the admin dashboard able to approve/reject new corporate signups. After this plan, ops can onboard companies manually end-to-end except for wallet/rides (Plans 2+).

**Architecture:**
- Backend: extend `backend/routes/corporate_accounts.py` with new Pydantic schemas, DB helpers in `db_supabase.py`, a KYB workflow, and super-admin-only endpoints under `/admin/corporate-accounts`. Auth reuses the existing `get_admin_user` dependency.
- Frontend: extend `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` with a KYB queue, status filter, and a detail sub-page.
- DB: migration `27_corporate_b2b_v1.sql` already authored — this plan applies and verifies it.

**Tech Stack:** FastAPI, Pydantic v2, Supabase Python client, Next.js 14 (app router, client components), pytest, TypeScript.

**Spec:** `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md` §3, §4 (just data model), §7 (super-admin portion).

**Out of scope for this plan** (later plans): wallet logic, allowance logic, policy engine, ride billing, rider app, company-facing portal, reporting, GTM.

---

## ⚠️ Codebase async/sync pattern (read before implementing any Supabase code)

`supabase-py 2.x` in this repo is **synchronous**. `supabase.table(...).select(...).execute()` and `supabase.rpc(name, params).execute()` return an `APIResponse` directly — **do not `await` them**, you will get `TypeError: object APIResponse can't be used in 'await' expression`.

The codebase offloads these sync calls to a threadpool with the `run_sync` wrapper defined at `backend/db_supabase.py:18-32`. Every async helper in `backend/db_supabase.py` follows this shape:

```python
async def some_helper(...) -> ...:
    def _fn():
        res = supabase.table("x").select("*")...execute()  # SYNC
        return _rows_from_res(res)                          # or _single_row_from_res
    return await run_sync(_fn)
```

When writing tests for helpers that use `run_sync`, the test mocks need to match the sync shape. **Do not use `AsyncMock` on `.execute()`** — it returns a coroutine that the closure won't `await`. Use `MagicMock(return_value=...)` so the chained builder returns a sync APIResponse-like object:

```python
fake_resp = MagicMock(data=[{"id": "c1"}], count=1)
mock_supabase_client.table.return_value.execute = MagicMock(return_value=fake_resp)
```

Every code snippet below that uses Supabase follows this pattern. If you see an `await ...execute()` anywhere in a plan snippet, treat it as a plan typo and convert to the `run_sync` form.

---

## Task 1: Apply migration 27 and verify schema

**Files:**
- Modify: `backend/migrations/27_corporate_b2b_v1.sql` (already authored — only touch if bugs surface)
- Create: `backend/tests/test_corporate_b2b_schema.py`

- [ ] **Step 1: Apply the migration to the local Supabase DB**

Run:
```bash
cd backend && python -c "
import asyncio, os
from db_supabase import supabase
async def run():
    with open('migrations/27_corporate_b2b_v1.sql') as f:
        sql = f.read()
    # Supabase Python client can't run arbitrary DDL; use psql or Supabase SQL editor.
    print('Copy this SQL into Supabase SQL editor, or run with:')
    print('psql \"$DATABASE_URL\" -f migrations/27_corporate_b2b_v1.sql')
asyncio.run(run())
"
```

Then in the Supabase dashboard SQL editor, paste and run `backend/migrations/27_corporate_b2b_v1.sql`. Confirm no errors (notices are fine).

- [ ] **Step 2: Write a schema smoke-test**

```python
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
    resp = (
        supabase.table("corporate_accounts")
        .select(",".join(REQUIRED_CORP_COLS))
        .limit(1)
        .execute()
    )
    assert resp.data is not None
```

- [ ] **Step 3: Run the smoke test**

```bash
pytest backend/tests/test_corporate_b2b_schema.py -v -m integration
```

Expected: 2 tests pass. If any FAILs, re-run the migration or fix columns.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_corporate_b2b_schema.py
git commit -m "test(corporate): schema smoke test for B2B v1 tables and columns"
```

---

## Task 2: Add validators for business number, tax region, domain format

**Files:**
- Modify: `backend/validators.py`
- Test: `backend/tests/test_corporate_validators.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_corporate_validators.py
import pytest

from validators import (
    validate_cra_business_number,
    validate_canadian_tax_region,
    validate_email_domain,
)


class TestBusinessNumber:
    @pytest.mark.parametrize("bn", ["123456789RT0001", "987654321", "123456789-RT-0001"])
    def test_accepts_valid_formats(self, bn):
        assert validate_cra_business_number(bn) == bn.replace("-", "").upper()

    @pytest.mark.parametrize("bad", ["", "abc", "12345", "12345678", "123456789XY9999"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_cra_business_number(bad)


class TestTaxRegion:
    @pytest.mark.parametrize("region", ["ON", "QC", "BC", "AB", "SK", "MB", "NS", "NB", "NL", "PE", "YT", "NT", "NU"])
    def test_accepts_all_provinces_and_territories(self, region):
        assert validate_canadian_tax_region(region) == region

    @pytest.mark.parametrize("bad", ["", "XX", "on", "ontario", "CA"])
    def test_rejects_unknown(self, bad):
        with pytest.raises(ValueError):
            validate_canadian_tax_region(bad)


class TestDomain:
    @pytest.mark.parametrize("raw,expected", [
        ("ACME.com", "acme.com"),
        (" acme.com ", "acme.com"),
        ("@acme.com", "acme.com"),
        ("acme.co.uk", "acme.co.uk"),
    ])
    def test_normalizes(self, raw, expected):
        assert validate_email_domain(raw) == expected

    @pytest.mark.parametrize("bad", ["", "acme", "acme.", ".com", "has spaces.com", "a" * 254 + ".com"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_email_domain(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/test_corporate_validators.py -v
```

Expected: ImportError on the three validators.

- [ ] **Step 3: Implement the validators**

Add to the end of `backend/validators.py`:

```python
import re

_BN_FORMAT = re.compile(r"^\d{9}(?:[A-Z]{2}\d{4})?$")
_CA_TAX_REGIONS = frozenset({
    "ON", "QC", "BC", "AB", "SK", "MB",
    "NS", "NB", "NL", "PE", "YT", "NT", "NU",
})
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def validate_cra_business_number(bn: str) -> str:
    """Validate a CRA Business Number format (9 digits, optional RT/RC/RP + 4 digits).

    Accepts hyphenated and unhyphenated forms; returns the canonical uppercase
    unhyphenated form. Format-only — does not verify against CRA.
    """
    if not isinstance(bn, str):
        raise ValueError("business number must be a string")
    canon = bn.replace("-", "").replace(" ", "").upper()
    if not _BN_FORMAT.match(canon):
        raise ValueError(f"invalid CRA business number format: {bn!r}")
    return canon


def validate_canadian_tax_region(region: str) -> str:
    """Validate a two-letter Canadian province/territory code."""
    if region not in _CA_TAX_REGIONS:
        raise ValueError(f"unknown Canadian tax region: {region!r}")
    return region


def validate_email_domain(domain: str) -> str:
    """Normalize and validate an email domain for allowlist use.

    Strips whitespace, a leading '@', lowercases, and validates against an
    RFC-1035-ish domain regex. Maximum 253 chars per RFC.
    """
    if not isinstance(domain, str):
        raise ValueError("domain must be a string")
    cleaned = domain.strip().lstrip("@").lower()
    if not _DOMAIN_RE.match(cleaned):
        raise ValueError(f"invalid email domain: {domain!r}")
    return cleaned
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest backend/tests/test_corporate_validators.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/validators.py backend/tests/test_corporate_validators.py
git commit -m "feat(corporate): validators for BN, tax region, email domain"
```

---

## Task 3: Expanded Pydantic schemas for corporate accounts

**Files:**
- Create: `backend/schemas/corporate.py`
- Create: `backend/schemas/__init__.py` (if it doesn't exist)
- Test: `backend/tests/test_corporate_schemas.py`

- [ ] **Step 1: Write failing schema tests**

```python
# backend/tests/test_corporate_schemas.py
import pytest
from pydantic import ValidationError

from schemas.corporate import (
    CorporateAccountCreate,
    CorporateAccountUpdate,
    CorporateAccountResponse,
    CompanyStatus,
    SizeTier,
    Locale,
)


def _valid_create() -> dict:
    return {
        "name": "Acme Corp",
        "legal_name": "Acme Corporation Inc.",
        "business_number": "123456789RT0001",
        "tax_region": "ON",
        "billing_email": "billing@acme.com",
        "size_tier": "smb",
        "contact_email": "contact@acme.com",
    }


class TestCorporateAccountCreate:
    def test_accepts_valid_payload(self):
        m = CorporateAccountCreate(**_valid_create())
        assert m.country_code == "CA"
        assert m.currency == "CAD"
        assert m.locale == "en-CA"
        assert m.timezone == "America/Toronto"
        assert m.business_number == "123456789RT0001"

    def test_normalizes_business_number(self):
        p = _valid_create()
        p["business_number"] = "123-456-789-RT-0001"
        m = CorporateAccountCreate(**p)
        assert m.business_number == "123456789RT0001"

    def test_rejects_bad_tax_region(self):
        p = _valid_create()
        p["tax_region"] = "XX"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)

    def test_rejects_bad_locale(self):
        p = _valid_create()
        p["locale"] = "en-US"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)

    def test_rejects_bad_size_tier(self):
        p = _valid_create()
        p["size_tier"] = "tiny"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)


class TestEnums:
    def test_company_status_values(self):
        assert {s.value for s in CompanyStatus} == {
            "pending_verification", "active", "suspended", "closed"
        }

    def test_size_tier_values(self):
        assert {s.value for s in SizeTier} == {"smb", "mid_market", "enterprise"}

    def test_locale_values(self):
        assert {l.value for l in Locale} == {"en-CA", "fr-CA"}


class TestCorporateAccountUpdate:
    def test_all_fields_optional(self):
        CorporateAccountUpdate()  # should not raise

    def test_normalizes_bn_on_update(self):
        m = CorporateAccountUpdate(business_number="987654321")
        assert m.business_number == "987654321"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
pytest backend/tests/test_corporate_schemas.py -v
```

- [ ] **Step 3: Implement the schemas**

Create `backend/schemas/__init__.py`:

```python
# empty on purpose — package marker
```

Create `backend/schemas/corporate.py`:

```python
"""Pydantic v2 schemas for corporate accounts (B2B v1)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

try:
    from ..validators import (
        validate_canadian_tax_region,
        validate_cra_business_number,
    )
except ImportError:
    from validators import (  # type: ignore
        validate_canadian_tax_region,
        validate_cra_business_number,
    )


class CompanyStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class SizeTier(str, Enum):
    SMB = "smb"
    MID_MARKET = "mid_market"
    ENTERPRISE = "enterprise"


class Locale(str, Enum):
    EN_CA = "en-CA"
    FR_CA = "fr-CA"


class CorporateAccountBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    country_code: str = Field("CA", min_length=2, max_length=2)
    currency: str = Field("CAD", min_length=3, max_length=3)
    tax_region: Optional[str] = None
    timezone: str = Field("America/Toronto", max_length=64)
    locale: Locale = Locale.EN_CA
    billing_email: Optional[EmailStr] = None
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(None, max_length=32)
    size_tier: SizeTier = SizeTier.SMB
    is_active: bool = True

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_canadian_tax_region(v)


class CorporateAccountCreate(CorporateAccountBase):
    """Payload for creating a new corporate account (internal use by super-admin).

    The public self-serve signup flow (Plan not in this document) will wrap
    this schema with an outer form that also uploads the KYB document.
    """


class CorporateAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    tax_region: Optional[str] = None
    timezone: Optional[str] = Field(None, max_length=64)
    locale: Optional[Locale] = None
    billing_email: Optional[EmailStr] = None
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(None, max_length=32)
    size_tier: Optional[SizeTier] = None
    is_active: Optional[bool] = None
    credit_limit: Optional[float] = Field(None, ge=0)

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_canadian_tax_region(v)


class CorporateAccountResponse(CorporateAccountBase):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    status: CompanyStatus
    stripe_customer_id: Optional[str] = None
    kyb_document_url: Optional[str] = None
    kyb_reviewed_at: Optional[datetime] = None
    kyb_reviewed_by: Optional[str] = None
    credit_limit: float = 0
    created_at: datetime
    updated_at: datetime


class KYBReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: Optional[str] = Field(None, max_length=500)


class CompanyStatusTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CompanyStatus
    reason: Optional[str] = Field(None, max_length=500)
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_schemas.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/schemas/ backend/tests/test_corporate_schemas.py
git commit -m "feat(corporate): expanded Pydantic v2 schemas for B2B v1"
```

---

## Task 4: Supabase helpers for corporate account reads/writes with new fields

**Files:**
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_db_helpers.py`

- [ ] **Step 1: Write failing tests (using the existing mock_supabase_client)**

Recall from the codebase pattern: `.execute()` is **sync** and is called inside a `def _fn()` closure that `run_sync` offloads. So mock `.execute` with `MagicMock(return_value=...)`, not `AsyncMock`.

```python
# backend/tests/test_corporate_db_helpers.py
from unittest.mock import MagicMock, patch

import pytest


def _fake_resp(data):
    return MagicMock(data=data, count=len(data) if isinstance(data, list) else 0)


@pytest.mark.asyncio
async def test_list_companies_by_status_filter(mock_supabase_client):
    mock_supabase_client.table.return_value.execute = MagicMock(
        return_value=_fake_resp([{"id": "c1", "status": "pending_verification"}])
    )
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import list_corporate_accounts_filtered

        rows = await list_corporate_accounts_filtered(
            status="pending_verification", size_tier=None, search=None, skip=0, limit=50
        )
    assert rows == [{"id": "c1", "status": "pending_verification"}]
    mock_supabase_client.table.assert_called_with("corporate_accounts")


@pytest.mark.asyncio
async def test_update_company_status(mock_supabase_client):
    mock_supabase_client.table.return_value.execute = MagicMock(
        return_value=_fake_resp([{"id": "c1", "status": "active"}])
    )
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import update_corporate_account_status

        row = await update_corporate_account_status("c1", "active")
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_record_kyb_decision(mock_supabase_client):
    mock_supabase_client.table.return_value.execute = MagicMock(
        return_value=_fake_resp([{"id": "c1"}])
    )
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import record_kyb_decision

        await record_kyb_decision(
            company_id="c1",
            reviewer_id="admin_1",
            approved=True,
            note=None,
        )
    # .update(...) was called with status='active' + kyb_reviewed_at + kyb_reviewed_by
    update_call = mock_supabase_client.table.return_value.update.call_args
    assert update_call is not None
    patch_body = update_call.args[0]
    assert patch_body["status"] == "active"
    assert patch_body["kyb_reviewed_by"] == "admin_1"
    assert "kyb_reviewed_at" in patch_body
```

- [ ] **Step 2: Run — expect ImportError**

```bash
pytest backend/tests/test_corporate_db_helpers.py -v
```

- [ ] **Step 3: Implement the helpers**

Append to `backend/db_supabase.py`. Note the `run_sync` pattern: every Supabase call goes inside a `def _fn()` closure, the closure is offloaded to a threadpool, and `.execute()` is **sync** inside it (no `await`).

```python
# ── Corporate Accounts (B2B v1) ──────────────────────────────────────

async def list_corporate_accounts_filtered(
    *,
    status: str | None,
    size_tier: str | None,
    search: str | None,
    skip: int,
    limit: int,
) -> list[dict]:
    """List corporate accounts with optional status / size-tier / name-search filters."""
    def _fn():
        q = supabase.table("corporate_accounts").select("*")
        if status:
            q = q.eq("status", status)
        if size_tier:
            q = q.eq("size_tier", size_tier)
        if search:
            # Escape PostgREST ilike special chars to prevent filter injection
            # (same pattern as get_all_corporate_accounts above).
            safe = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            safe = re.sub(r"[,\.\(\)]", "", safe)
            q = q.or_(f"name.ilike.%{safe}%,legal_name.ilike.%{safe}%")
        q = q.order("created_at", desc=True).range(skip, skip + limit - 1)
        return _rows_from_res(q.execute())
    return await run_sync(_fn)


async def update_corporate_account_status(company_id: str, status: str) -> dict | None:
    def _fn():
        res = (
            supabase.table("corporate_accounts")
            .update({"status": status})
            .eq("id", company_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def record_kyb_decision(
    *,
    company_id: str,
    reviewer_id: str,
    approved: bool,
    note: str | None,
) -> dict | None:
    """Record a KYB approve/reject decision. Approval flips status to active.

    Rejection flips status to suspended so the company can re-upload and be
    re-reviewed without creating a fresh account.
    """
    from datetime import datetime, timezone

    new_status = "active" if approved else "suspended"
    patch = {
        "status": new_status,
        "kyb_reviewed_at": datetime.now(timezone.utc).isoformat(),
        "kyb_reviewed_by": reviewer_id,
    }
    if note:
        patch["kyb_review_note"] = note  # column added in a follow-up migration if desired

    def _fn():
        res = (
            supabase.table("corporate_accounts")
            .update(patch)
            .eq("id", company_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def get_corporate_members_for_user(user_id: str) -> list[dict]:
    """Return all corporate_members rows for a user where status='active'.

    Hot path: called on every work-profile check.
    """
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("id, company_id, role, policy_override")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_db_helpers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db_supabase.py backend/tests/test_corporate_db_helpers.py
git commit -m "feat(corporate): supabase helpers for B2B filter/status/kyb"
```

---

## Task 5: Super-admin endpoints — list + filter + detail (expand existing)

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Test: `backend/tests/test_corporate_admin_routes.py`

- [ ] **Step 1: Write failing route tests**

```python
# backend/tests/test_corporate_admin_routes.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_list_filters_by_status(test_client, auth_headers):
    rows = [
        {"id": "c1", "name": "A", "status": "pending_verification",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
         "country_code": "CA", "currency": "CAD", "locale": "en-CA",
         "size_tier": "smb", "is_active": True, "timezone": "America/Toronto",
         "credit_limit": 0},
    ]
    with patch(
        "db_supabase.list_corporate_accounts_filtered",
        AsyncMock(return_value=rows),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.get(
            "/admin/corporate-accounts?status=pending_verification",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending_verification"


@pytest.mark.asyncio
async def test_status_filter_validates_enum(test_client, auth_headers):
    with patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.get(
            "/admin/corporate-accounts?status=bogus",
            headers=auth_headers,
        )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run — expect FAIL (endpoint not filtering)**

```bash
pytest backend/tests/test_corporate_admin_routes.py -v
```

- [ ] **Step 3: Implement — add status/size_tier filter to existing GET `/admin/corporate-accounts`**

Edit `backend/routes/corporate_accounts.py`'s `get_corporate_accounts` handler:

```python
from schemas.corporate import CompanyStatus, SizeTier  # add near other imports

@router.get("", response_model=List[CorporateAccountResponse])
async def get_corporate_accounts(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[CompanyStatus] = None,
    size_tier: Optional[SizeTier] = None,
    is_active: Optional[bool] = None,
    current_admin: dict = Depends(get_current_admin),
):
    from db_supabase import list_corporate_accounts_filtered

    rows = await list_corporate_accounts_filtered(
        status=status.value if status else None,
        size_tier=size_tier.value if size_tier else None,
        search=search,
        skip=skip,
        limit=min(limit, 500),
    )
    # is_active is a legacy filter; apply in-memory if supplied
    if is_active is not None:
        rows = [r for r in rows if bool(r.get("is_active")) == is_active]
    return rows
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_admin_routes.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/tests/test_corporate_admin_routes.py
git commit -m "feat(corporate): status/size_tier filters on admin list endpoint"
```

---

## Task 6: KYB review endpoint

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Test: `backend/tests/test_corporate_kyb.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_corporate_kyb.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_approve_kyb_flips_status_to_active(test_client, auth_headers):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={"id": "c1", "status": "active"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_reject_kyb_flips_status_to_suspended(test_client, auth_headers):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value={"id": "c1", "status": "suspended"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_kyb_review_404_on_missing_company(test_client, auth_headers):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=None),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/nonexistent/kyb-review",
            json={"approve": True},
            headers=auth_headers,
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run — expect 404 (route not defined)**

```bash
pytest backend/tests/test_corporate_kyb.py -v
```

- [ ] **Step 3: Implement the endpoint**

Append to `backend/routes/corporate_accounts.py`:

```python
from schemas.corporate import KYBReviewDecision  # near other imports


@router.post("/{company_id}/kyb-review", response_model=CorporateAccountResponse)
async def kyb_review(
    company_id: str,
    decision: KYBReviewDecision,
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    """Approve or reject a pending KYB submission.

    Approve → status='active'. Reject → status='suspended' so the company
    can re-upload and be re-reviewed from the queue.
    """
    validate_id(company_id)
    from db_supabase import record_kyb_decision

    row = await record_kyb_decision(
        company_id=company_id,
        reviewer_id=current_admin["id"],
        approved=decision.approve,
        note=decision.note,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    return row
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_kyb.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/tests/test_corporate_kyb.py
git commit -m "feat(corporate): super-admin KYB review endpoint"
```

---

## Task 7: Company status transition endpoint (suspend / reactivate / close)

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Test: `backend/tests/test_corporate_status.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_corporate_status.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_suspend_company(test_client, auth_headers):
    with patch(
        "db_supabase.update_corporate_account_status",
        AsyncMock(return_value={"id": "c1", "status": "suspended"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "overdue balance"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_cannot_reopen_closed_company(test_client, auth_headers):
    with patch(
        "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "status": "closed"}),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/status",
            json={"status": "active"},
            headers=auth_headers,
        )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run — expect 404**

```bash
pytest backend/tests/test_corporate_status.py -v
```

- [ ] **Step 3: Implement the endpoint**

Append to `backend/routes/corporate_accounts.py`:

```python
from schemas.corporate import CompanyStatusTransition, CompanyStatus  # near top


@router.post("/{company_id}/status", response_model=CorporateAccountResponse)
async def change_company_status(
    company_id: str,
    transition: CompanyStatusTransition,
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    validate_id(company_id)
    current = await get_corporate_account_by_id(company_id)
    if not current:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    if current.get("status") == CompanyStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="Account is closed and cannot be reopened")

    from db_supabase import update_corporate_account_status

    row = await update_corporate_account_status(company_id, transition.status.value)
    if not row:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    # TODO (Plan 2): also freeze the wallet on suspend / closed
    return row
```

- [ ] **Step 4: Run tests**

```bash
pytest backend/tests/test_corporate_status.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/corporate_accounts.py backend/tests/test_corporate_status.py
git commit -m "feat(corporate): company status transition endpoint"
```

---

## Task 8: KYB document upload — signed URL + metadata write

**Files:**
- Modify: `backend/routes/corporate_accounts.py`
- Modify: `backend/db_supabase.py`
- Test: `backend/tests/test_corporate_kyb_upload.py`

**Design note:** The rider app / portal uploads the document directly to Supabase Storage via a short-lived signed URL. The backend only writes the resulting object path to `corporate_accounts.kyb_document_url` — this avoids streaming binary data through FastAPI.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_corporate_kyb_upload.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_generates_signed_upload_url(test_client, auth_headers):
    with patch(
        "db_supabase.create_kyb_upload_url",
        AsyncMock(return_value={
            "signed_url": "https://supabase.test/signed?sig=x",
            "path": "kyb/c1/doc.pdf",
            "expires_at": "2026-04-16T01:00:00Z",
        }),
    ), patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-upload-url",
            json={"content_type": "application/pdf"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["signed_url"].startswith("https://")
    assert body["path"] == "kyb/c1/doc.pdf"


@pytest.mark.asyncio
async def test_rejects_non_pdf_or_image(test_client, auth_headers):
    with patch(
        "dependencies.get_admin_user", AsyncMock(return_value={"id": "admin_1"})
    ):
        resp = test_client.post(
            "/admin/corporate-accounts/c1/kyb-upload-url",
            json={"content_type": "application/zip"},
            headers=auth_headers,
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run — expect 404**

```bash
pytest backend/tests/test_corporate_kyb_upload.py -v
```

- [ ] **Step 3: Add `create_kyb_upload_url` to `db_supabase.py`**

```python
async def create_kyb_upload_url(
    *, company_id: str, content_type: str, ttl_seconds: int = 3600
) -> dict:
    """Return a short-lived signed upload URL for a KYB document.

    The bucket 'kyb-documents' is private; the caller uploads with the
    returned URL and we later record the object path on the corporate
    account when review completes.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[content_type]
    path = f"kyb/{company_id}/{uuid.uuid4()}.{ext}"
    # Supabase storage: create a signed upload URL
    signed = await supabase.storage.from_("kyb-documents").create_signed_upload_url(path)
    # `signed` shape: {"signed_url": "...", "token": "...", "path": "..."}
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    return {
        "signed_url": signed["signed_url"],
        "path": signed.get("path", path),
        "expires_at": expires_at,
    }
```

- [ ] **Step 4: Add the route**

```python
class KYBUploadURLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_type: str


_ALLOWED_KYB_CONTENT = {"application/pdf", "image/png", "image/jpeg"}


@router.post("/{company_id}/kyb-upload-url")
async def kyb_upload_url(
    company_id: str,
    body: KYBUploadURLRequest,
    current_admin: dict = Depends(get_current_admin),
):
    validate_id(company_id)
    if body.content_type not in _ALLOWED_KYB_CONTENT:
        raise HTTPException(status_code=400, detail="Unsupported content type for KYB")
    from db_supabase import create_kyb_upload_url

    return await create_kyb_upload_url(company_id=company_id, content_type=body.content_type)
```

- [ ] **Step 5: Run tests**

```bash
pytest backend/tests/test_corporate_kyb_upload.py -v
```

Expected: both PASS.

- [ ] **Step 6: Create the Supabase storage bucket manually (one-time ops step)**

In the Supabase dashboard → Storage → "New bucket" → name `kyb-documents`, **Private** (no public access). Confirm RLS policies set so only `service_role` can read/write.

- [ ] **Step 7: Commit**

```bash
git add backend/db_supabase.py backend/routes/corporate_accounts.py backend/tests/test_corporate_kyb_upload.py
git commit -m "feat(corporate): signed upload URL for KYB documents"
```

---

## Task 9: Admin dashboard — KYB queue page

**Files:**
- Create: `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx`
- Modify: `admin-dashboard/src/lib/api.ts` — add `listCorporateAccounts({status})` and `reviewKyb(id, decision)` helpers

- [ ] **Step 1: Add API helpers**

Edit `admin-dashboard/src/lib/api.ts` — add:

```ts
export interface CorporateAccount {
  id: string;
  name: string;
  legal_name?: string | null;
  business_number?: string | null;
  tax_region?: string | null;
  billing_email?: string | null;
  status: "pending_verification" | "active" | "suspended" | "closed";
  size_tier: "smb" | "mid_market" | "enterprise";
  kyb_document_url?: string | null;
  kyb_reviewed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export async function listCorporateAccounts(opts: {
  status?: CorporateAccount["status"];
  size_tier?: CorporateAccount["size_tier"];
  search?: string;
  skip?: number;
  limit?: number;
} = {}): Promise<CorporateAccount[]> {
  const p = new URLSearchParams();
  if (opts.status) p.set("status", opts.status);
  if (opts.size_tier) p.set("size_tier", opts.size_tier);
  if (opts.search) p.set("search", opts.search);
  if (opts.skip != null) p.set("skip", String(opts.skip));
  if (opts.limit != null) p.set("limit", String(opts.limit));
  return apiFetch(`/admin/corporate-accounts?${p}`);
}

export async function reviewKyb(
  id: string,
  decision: { approve: boolean; note?: string }
): Promise<CorporateAccount> {
  return apiFetch(`/admin/corporate-accounts/${id}/kyb-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
}
```

(`apiFetch` already exists — follow the same pattern as other helpers.)

- [ ] **Step 2: Build the KYB queue page**

Create `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { CorporateAccount, listCorporateAccounts, reviewKyb } from "@/lib/api";

export default function KybQueuePage() {
  const [rows, setRows] = useState<CorporateAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setRows(await listCorporateAccounts({ status: "pending_verification", limit: 100 }));
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const decide = async (id: string, approve: boolean) => {
    const note = approve ? undefined : prompt("Reason for rejection?") ?? undefined;
    setBusyId(id);
    try {
      await reviewKyb(id, { approve, note });
      await load();
    } catch (e: any) {
      alert(e?.message ?? "Review failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">KYB Verification Queue</h1>
        <button onClick={load} className="text-sm underline">Refresh</button>
      </header>

      {loading && <p>Loading…</p>}
      {error && <p className="text-red-600">{error}</p>}
      {!loading && rows.length === 0 && <p className="text-gray-500">Queue is empty.</p>}

      <ul className="divide-y border rounded">
        {rows.map((c) => (
          <li key={c.id} className="p-4 flex items-start gap-4">
            <div className="flex-1">
              <p className="font-medium">{c.legal_name ?? c.name}</p>
              <p className="text-sm text-gray-600">
                BN: {c.business_number ?? "—"} · Region: {c.tax_region ?? "—"} · Tier: {c.size_tier}
              </p>
              <p className="text-sm text-gray-600">Billing: {c.billing_email ?? "—"}</p>
              {c.kyb_document_url && (
                <a className="text-sm text-blue-600 underline" href={c.kyb_document_url} target="_blank" rel="noreferrer">
                  View document
                </a>
              )}
            </div>
            <div className="flex gap-2">
              <button
                disabled={busyId === c.id}
                onClick={() => decide(c.id, true)}
                className="px-3 py-1 rounded bg-green-600 text-white disabled:opacity-50"
              >
                Approve
              </button>
              <button
                disabled={busyId === c.id}
                onClick={() => decide(c.id, false)}
                className="px-3 py-1 rounded bg-red-600 text-white disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Link to it from the existing corporate-accounts page**

Edit `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` — add a button near the top:

```tsx
<Link href="/dashboard/corporate-accounts/kyb-queue" className="px-3 py-1 rounded border">
  KYB Queue
</Link>
```

- [ ] **Step 4: Smoke-test locally**

```bash
cd admin-dashboard && npm run dev
```

Open `http://localhost:3000/dashboard/corporate-accounts/kyb-queue`. Create a test company via the existing corporate-accounts create form (status will default to `pending_verification`). Verify it appears in the queue and approve/reject buttons work.

- [ ] **Step 5: Commit**

```bash
git add admin-dashboard/src/lib/api.ts admin-dashboard/src/app/dashboard/corporate-accounts/
git commit -m "feat(admin): KYB verification queue page"
```

---

## Task 10: Admin dashboard — status filter on existing corporate-accounts list

**Files:**
- Modify: `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx`

- [ ] **Step 1: Add a status filter dropdown**

Locate the existing list UI. Add state:

```tsx
const [statusFilter, setStatusFilter] = useState<CorporateAccount["status"] | "">("");
```

and a `<select>`:

```tsx
<select
  value={statusFilter}
  onChange={(e) => setStatusFilter(e.target.value as any)}
  className="border rounded px-2 py-1"
>
  <option value="">All statuses</option>
  <option value="pending_verification">Pending verification</option>
  <option value="active">Active</option>
  <option value="suspended">Suspended</option>
  <option value="closed">Closed</option>
</select>
```

Update the fetch call to pass `status: statusFilter || undefined` and refetch on change.

- [ ] **Step 2: Add a status pill next to each row's name**

```tsx
function StatusPill({ s }: { s: CorporateAccount["status"] }) {
  const map = {
    pending_verification: "bg-yellow-100 text-yellow-800",
    active: "bg-green-100 text-green-800",
    suspended: "bg-orange-100 text-orange-800",
    closed: "bg-gray-200 text-gray-700",
  };
  return <span className={`text-xs px-2 py-0.5 rounded ${map[s]}`}>{s.replace("_", " ")}</span>;
}
```

Render `<StatusPill s={row.status} />` in the row.

- [ ] **Step 3: Manual QA**

Dev server + filter verification: changing the filter narrows the list.

- [ ] **Step 4: Commit**

```bash
git add admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx
git commit -m "feat(admin): status filter + pill on corporate-accounts list"
```

---

## Task 11: Admin dashboard — company detail page

**Files:**
- Create: `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx`
- Modify: `admin-dashboard/src/lib/api.ts` — add `getCorporateAccount(id)`, `updateCorporateAccount(id, patch)`, `changeCompanyStatus(id, transition)`

- [ ] **Step 1: Add API helpers**

```ts
export async function getCorporateAccount(id: string): Promise<CorporateAccount> {
  return apiFetch(`/admin/corporate-accounts/${id}`);
}

export async function updateCorporateAccount(
  id: string,
  patch: Partial<CorporateAccount>
): Promise<CorporateAccount> {
  return apiFetch(`/admin/corporate-accounts/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function changeCompanyStatus(
  id: string,
  transition: { status: CorporateAccount["status"]; reason?: string }
): Promise<CorporateAccount> {
  return apiFetch(`/admin/corporate-accounts/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(transition),
  });
}
```

- [ ] **Step 2: Build the detail page**

Create `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  CorporateAccount,
  getCorporateAccount,
  updateCorporateAccount,
  changeCompanyStatus,
} from "@/lib/api";

export default function CompanyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [c, setC] = useState<CorporateAccount | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try { setC(await getCorporateAccount(id)); } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, [id]);

  if (err) return <p className="p-6 text-red-600">{err}</p>;
  if (!c) return <p className="p-6">Loading…</p>;

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">{c.legal_name ?? c.name}</h1>
        <p className="text-gray-600">
          Status: <b>{c.status}</b> · Tier: <b>{c.size_tier}</b> · Region: {c.tax_region ?? "—"}
        </p>
      </header>

      <section className="grid grid-cols-2 gap-4 text-sm">
        <div><b>BN</b><div>{c.business_number ?? "—"}</div></div>
        <div><b>Billing email</b><div>{c.billing_email ?? "—"}</div></div>
        <div><b>Created</b><div>{new Date(c.created_at).toLocaleString()}</div></div>
        <div><b>Updated</b><div>{new Date(c.updated_at).toLocaleString()}</div></div>
        <div><b>KYB reviewed</b><div>{c.kyb_reviewed_at ? new Date(c.kyb_reviewed_at).toLocaleString() : "—"}</div></div>
      </section>

      <section className="flex gap-2">
        {c.status !== "suspended" && (
          <button
            onClick={async () => {
              if (!confirm("Suspend this company?")) return;
              setC(await changeCompanyStatus(c.id, { status: "suspended", reason: prompt("Reason?") ?? undefined }));
            }}
            className="px-3 py-1 rounded bg-orange-600 text-white"
          >Suspend</button>
        )}
        {c.status === "suspended" && (
          <button
            onClick={async () => setC(await changeCompanyStatus(c.id, { status: "active" }))}
            className="px-3 py-1 rounded bg-green-600 text-white"
          >Reactivate</button>
        )}
        {c.status !== "closed" && (
          <button
            onClick={async () => {
              if (!confirm("Close this company permanently? This cannot be undone.")) return;
              setC(await changeCompanyStatus(c.id, { status: "closed" }));
            }}
            className="px-3 py-1 rounded bg-red-600 text-white"
          >Close</button>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Link from the list page**

In the list row, wrap the name:

```tsx
<Link href={`/dashboard/corporate-accounts/${row.id}`}>{row.name}</Link>
```

- [ ] **Step 4: Manual QA**

Dev server, navigate to a company detail page, suspend then reactivate a test company.

- [ ] **Step 5: Commit**

```bash
git add admin-dashboard/src/app/dashboard/corporate-accounts/ admin-dashboard/src/lib/api.ts
git commit -m "feat(admin): company detail page with status transitions"
```

---

## Task 12: End-to-end smoke test of the whole plan

**Files:**
- Create: `backend/tests/test_corporate_e2e_foundation.py`

- [ ] **Step 1: Write the smoke test**

```python
# backend/tests/test_corporate_e2e_foundation.py
"""End-to-end: create company → KYB approve → status transitions."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_approve_suspend_reactivate_flow(test_client, auth_headers):
    created = {
        "id": "c_e2e", "name": "E2E Corp", "legal_name": "E2E Corp Inc.",
        "business_number": "123456789RT0001", "tax_region": "ON",
        "billing_email": "billing@e2e.test", "size_tier": "smb",
        "status": "pending_verification",
        "country_code": "CA", "currency": "CAD", "locale": "en-CA",
        "timezone": "America/Toronto", "is_active": True, "credit_limit": 0,
        "created_at": "2026-04-16T00:00:00Z", "updated_at": "2026-04-16T00:00:00Z",
    }

    def _make_response(overrides):
        row = {**created, **overrides}
        return row

    # Sequence: create → KYB approve → suspend → reactivate
    with patch("db_supabase.insert_corporate_account", AsyncMock(return_value=created)), \
         patch("db_supabase.record_kyb_decision",
               AsyncMock(return_value=_make_response({"status": "active"}))), \
         patch("db_supabase.get_corporate_account_by_id",
               AsyncMock(return_value=_make_response({"status": "active"}))), \
         patch("db_supabase.update_corporate_account_status",
               AsyncMock(side_effect=[
                   _make_response({"status": "suspended"}),
                   _make_response({"status": "active"}),
               ])), \
         patch("dependencies.get_admin_user",
               AsyncMock(return_value={"id": "admin_1"})):

        r = test_client.post(
            "/admin/corporate-accounts",
            json={
                "name": "E2E Corp",
                "legal_name": "E2E Corp Inc.",
                "business_number": "123456789RT0001",
                "tax_region": "ON",
                "billing_email": "billing@e2e.test",
                "size_tier": "smb",
            },
            headers=auth_headers,
        )
        assert r.status_code in (200, 201), r.text

        r = test_client.post(
            "/admin/corporate-accounts/c_e2e/kyb-review",
            json={"approve": True},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"

        r = test_client.post(
            "/admin/corporate-accounts/c_e2e/status",
            json={"status": "suspended", "reason": "test"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "suspended"

        r = test_client.post(
            "/admin/corporate-accounts/c_e2e/status",
            json={"status": "active"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"
```

- [ ] **Step 2: Run the e2e test**

```bash
pytest backend/tests/test_corporate_e2e_foundation.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full new-test suite**

```bash
pytest backend/tests/test_corporate_validators.py \
       backend/tests/test_corporate_schemas.py \
       backend/tests/test_corporate_db_helpers.py \
       backend/tests/test_corporate_admin_routes.py \
       backend/tests/test_corporate_kyb.py \
       backend/tests/test_corporate_status.py \
       backend/tests/test_corporate_kyb_upload.py \
       backend/tests/test_corporate_e2e_foundation.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run the existing test suite to ensure no regressions**

```bash
pytest backend/tests/ -v --ignore=backend/tests/test_corporate_b2b_schema.py
```

Expected: no new failures compared to baseline. (Integration tests in `test_corporate_b2b_schema.py` are excluded because they hit a live DB.)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_corporate_e2e_foundation.py
git commit -m "test(corporate): e2e foundation flow — create/kyb/suspend/reactivate"
```

---

## Done criteria (Plan 1)

- Migration 27 applied, 9 new tables + 14 new columns verified present.
- Validators for BN / tax region / email domain tested and in use.
- Pydantic schemas for company create/update/response with enum typing.
- Supabase helpers for filter/list, status update, KYB decision, member lookup.
- Super-admin endpoints: list-with-filters, KYB review, status transition, KYB upload URL.
- Admin dashboard: KYB queue page, status filter, company detail page with status buttons.
- End-to-end test of create → approve → suspend → reactivate.
- All new tests green; existing tests not regressed.

### Handoff to Plan 2
After this plan: a super-admin can onboard a company end-to-end *except* for funding the wallet or booking rides. Plan 2 adds the master wallet, Stripe top-up, webhooks, auto-top-up, and the super-admin wallet-adjustment tool.
