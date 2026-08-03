"""Corporate accounts repository — B2B CRUD, members, allowances, wallets, domains.

Extracted from db_supabase.py (Phase 2 of god-object decomposition).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from ._base import (
        _apply_filters,
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        run_sync,
        supabase,
    )
except ImportError:
    from repositories._base import (
        _apply_filters,
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        run_sync,
        supabase,
    )


# ============ Corporate Accounts Functions ============


async def get_all_corporate_accounts(
    skip: int = 0, limit: int = 100, search: Optional[str] = None, is_active: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """
    Get all corporate accounts with optional filtering and pagination.

    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        search: Search term for company name, contact name, or email
        is_active: Filter by active status

    Returns:
        List of corporate accounts
    """
    if not supabase:
        return []

    def _fn():
        query = supabase.table("corporate_accounts").select("*").range(skip, skip + limit - 1)

        if search:
            # Shared $regex/$or escaping (repositories/_base.py._apply_filters)
            # instead of a hand-rolled escape+strip — see corporate module
            # review gap #42. _build_or_clause_term's $regex branch escapes
            # LIKE wildcards (_escape_like) and PostgREST or()-group reserved
            # characters (_postgrest_pattern) rather than silently stripping
            # them, so a search term containing a comma or parenthesis still
            # matches instead of having that character dropped.
            query = _apply_filters(
                query,
                {
                    "$or": [
                        {"name": {"$regex": search, "$options": "i"}},
                        {"contact_name": {"$regex": search, "$options": "i"}},
                        {"contact_email": {"$regex": search, "$options": "i"}},
                    ]
                },
            )

        if is_active is not None:
            query = query.eq("is_active", is_active)

        query = query.order("created_at", desc=True)
        return _rows_from_res(query.execute())

    return await run_sync(_fn)


async def get_corporate_account_by_id(validated_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a corporate account by ID.

    Args:
        validated_id: Validated corporate account ID

    Returns:
        Corporate account data or None if not found
    """
    if not supabase:
        return None

    def _fn():
        try:
            res = supabase.table("corporate_accounts").select("*").eq("id", validated_id).single().execute()
            return _single_row_from_res(res)
        except Exception as e:
            # If no rows found, Supabase raises an exception
            logger.debug(f"No corporate account found with ID {validated_id}: {e}")
            return None

    return await run_sync(_fn)


async def insert_corporate_account(account_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Insert a new corporate account.

    Args:
        account_data: Corporate account data to insert

    Returns:
        Created corporate account data or None if failed
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    account_data = _serialize_for_api(account_data)

    def _fn():
        res = supabase.table("corporate_accounts").insert(account_data).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def update_corporate_account(account_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Update an existing corporate account.

    Args:
        account_id: ID of the account to update
        update_data: Data to update

    Returns:
        Updated corporate account data or None if failed
    """
    if not supabase:
        return None

    update_data = _serialize_for_api(update_data)

    def _fn():
        res = supabase.table("corporate_accounts").update(update_data).eq("id", account_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def delete_corporate_account(account_id: str) -> bool:
    """
    Delete a corporate account.

    Args:
        account_id: ID of the account to delete

    Returns:
        True if successful, False otherwise
    """
    if not supabase:
        return False

    def _fn():
        res = supabase.table("corporate_accounts").delete().eq("id", account_id).execute()
        # If deletion was successful, affected rows will be > 0
        return res.count > 0 if res.count is not None else False

    return await run_sync(_fn)


# ── Corporate Accounts (B2B v1) ──────────────────────────────────────


async def list_corporate_accounts_filtered(
    *,
    status: Optional[str],
    size_tier: Optional[str],
    search: Optional[str],
    skip: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """List corporate accounts with optional status / size-tier / name-search filters."""

    def _fn():
        q = supabase.table("corporate_accounts").select("*")
        if status:
            q = q.eq("status", status)
        if size_tier:
            q = q.eq("size_tier", size_tier)
        if search:
            # Shared $regex/$or escaping — see get_all_corporate_accounts above
            # and corporate module review gap #42.
            q = _apply_filters(
                q,
                {
                    "$or": [
                        {"name": {"$regex": search, "$options": "i"}},
                        {"legal_name": {"$regex": search, "$options": "i"}},
                    ]
                },
            )
        q = q.order("created_at", desc=True).range(skip, skip + limit - 1)
        return _rows_from_res(q.execute())

    return await run_sync(_fn)


async def update_corporate_account_status(company_id: str, status: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_accounts").update({"status": status}).eq("id", company_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def record_kyb_decision(
    *,
    company_id: str,
    reviewer_id: str,
    approved: bool,
    note: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Record a KYB approve/reject decision. Approval flips status to active.

    Rejection flips status to suspended so the company can re-upload and be
    re-reviewed without creating a fresh account.
    """
    new_status = "active" if approved else "suspended"
    patch = {
        "status": new_status,
        "kyb_reviewed_at": datetime.now(timezone.utc).isoformat(),
        "kyb_reviewed_by": reviewer_id,
        # 'approved' | 'rejected' (migration 225) — lets the portal distinguish
        # KYB-rejected (may resubmit) from staff-suspended (may not).
        "kyb_last_decision": "approved" if approved else "rejected",
    }
    if note:
        patch["kyb_review_note"] = note  # column exists since migration 225

    def _fn():
        res = supabase.table("corporate_accounts").update(patch).eq("id", company_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def kyb_object_exists(*, path: str) -> bool:
    """True if the object was actually uploaded to the private kyb-documents
    bucket. Guards /kyb/submit: a client must not be able to point
    kyb_document_url at a path that was never uploaded (or someone else's)."""
    folder, _, filename = path.rpartition("/")

    def _fn():
        return supabase.storage.from_("kyb-documents").list(folder)

    entries = await run_sync(_fn) or []
    for e in entries:
        name = e.get("name") if isinstance(e, dict) else getattr(e, "name", None)
        if name == filename:
            return True
    return False


async def set_kyb_document(*, company_id: str, path: str) -> Optional[Dict[str, Any]]:
    """Persist the uploaded KYB document's storage key + submission time.

    FIXES the never-persisted-URL bug: create_kyb_upload_url returned a path
    but nothing ever wrote it to corporate_accounts.kyb_document_url, so
    GET /{id}/kyb/view read a column no code populated. Stores the RAW
    storage key (kyb/{company_id}/{uuid}.ext) — the private-bucket object is
    only reachable via the backend's signed streaming endpoint, never a
    public URL.
    """
    patch = {
        "kyb_document_url": path,
        "kyb_submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    def _fn():
        res = supabase.table("corporate_accounts").update(patch).eq("id", company_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def get_corporate_wallet_by_company(company_id: str) -> Optional[Dict[str, Any]]:
    """Return the master wallet row for a company, or None."""

    def _fn():
        res = supabase.table("corporate_wallets").select("*").eq("company_id", company_id).limit(1).execute()
        return _rows_from_res(res)

    rows = await run_sync(_fn)
    return rows[0] if rows else None


async def update_corporate_stripe_customer_id(*, company_id: str, stripe_customer_id: str) -> None:
    """Persist the Stripe customer id for a corporate account."""

    def _fn():
        supabase.table("corporate_accounts").update({"stripe_customer_id": stripe_customer_id}).eq(
            "id", company_id
        ).execute()

    await run_sync(_fn)


async def ensure_corporate_wallet(*, company_id: str) -> Dict[str, Any]:
    """Idempotently create the master wallet for a company. Returns the row."""

    def _select():
        res = supabase.table("corporate_wallets").select("*").eq("company_id", company_id).limit(1).execute()
        return _rows_from_res(res)

    existing = await run_sync(_select)
    if existing:
        return existing[0]

    def _insert():
        res = (
            supabase.table("corporate_wallets")
            .insert({"company_id": company_id, "balance": 0, "currency": "CAD"})
            .execute()
        )
        return _single_row_from_res(res)

    created = await run_sync(_insert)
    return created or {}


async def get_corporate_members_for_user(user_id: str) -> List[Dict[str, Any]]:
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


_KYB_CONTENT_EXT = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
}


async def create_kyb_upload_url(*, company_id: str, content_type: str, ttl_seconds: int = 3600) -> Dict[str, Any]:
    """Return a short-lived signed upload URL for a KYB document.

    The bucket 'kyb-documents' is private; the caller uploads with the
    returned URL and we later record the object path on the corporate
    account when review completes.
    """
    import uuid

    ext = _KYB_CONTENT_EXT[content_type]
    path = f"kyb/{company_id}/{uuid.uuid4()}.{ext}"

    def _fn():
        return supabase.storage.from_("kyb-documents").create_signed_upload_url(path)

    signed = await run_sync(_fn)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    return {
        "signed_url": signed["signed_url"],
        "path": signed.get("path", path),
        "expires_at": expires_at,
    }


async def list_wallets_needing_autotopup() -> List[Dict[str, Any]]:
    """Return wallets where auto_topup_enabled and balance < threshold.

    Threshold is filtered in Python because supabase-py doesn't support
    cross-column comparisons in .filter().
    """

    def _fn():
        return supabase.table("corporate_wallets").select("*").eq("auto_topup_enabled", True).execute()

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return [
        r
        for r in rows
        if r.get("auto_topup_threshold") is not None
        and Decimal(str(r["balance"])) < Decimal(str(r["auto_topup_threshold"]))
    ]


async def sum_autotopups_today(wallet_id: str) -> Decimal:
    """Sum of today's successful top-ups for a wallet (for daily-cap enforcement)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def _fn():
        return (
            supabase.table("corporate_wallet_transactions")
            .select("amount")
            .eq("wallet_id", wallet_id)
            .eq("type", "topup")
            .gte("created_at", today_start.isoformat())
            .execute()
        )

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return sum((Decimal(str(r["amount"])) for r in rows), Decimal("0"))


async def get_default_payment_method(stripe_customer_id: str, stripe_secret: str) -> Optional[str]:
    """Return the Stripe customer's first card payment method, if any."""
    import stripe

    def _fn():
        return stripe.PaymentMethod.list(customer=stripe_customer_id, type="card", api_key=stripe_secret)

    methods = await run_sync(_fn)
    data = getattr(methods, "data", None) or []
    return data[0].id if data else None


async def list_wallets_low_balance_no_autotopup() -> List[Dict[str, Any]]:
    """Wallets with auto-topup disabled whose balance has dipped below threshold."""

    def _fn():
        return supabase.table("corporate_wallets").select("*").eq("auto_topup_enabled", False).execute()

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return [
        r
        for r in rows
        if r.get("auto_topup_threshold") is not None
        and Decimal(str(r["balance"])) < Decimal(str(r["auto_topup_threshold"]))
    ]


async def mark_low_balance_notified(*, wallet_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    def _fn():
        supabase.table("corporate_wallets").update({"low_balance_notified_at": now_iso}).eq("id", wallet_id).execute()

    await run_sync(_fn)


async def list_wallet_risk_portfolio() -> List[Dict[str, Any]]:
    """Every corporate wallet annotated with risk flags, for the admin
    portfolio-risk view (Corporate + admin portal review, round 2: "no
    portfolio-level view of corporate wallet risk"). Reuses the same
    "filter cross-column comparisons in Python" pattern as
    list_wallets_needing_autotopup / list_wallets_low_balance_no_autotopup
    (PostgREST can't compare balance to a sibling threshold column
    server-side).

    Company name/status is resolved via a second query on the returned
    company_ids, not a PostgREST embed -- see CLAUDE.md's convention for
    a name/email lookup spanning two tables: resolve IDs first, filter
    the second query with $in. Guards the empty-wallets case so the
    second query is never issued with an empty id list.
    """

    def _fn():
        return supabase.table("corporate_wallets").select("*").execute()

    res = await run_sync(_fn)
    wallets = _rows_from_res(res)
    if not wallets:
        return []

    company_ids = [w["company_id"] for w in wallets if w.get("company_id")]
    accounts_by_id: Dict[str, Dict[str, Any]] = {}
    if company_ids:

        def _fn2():
            return supabase.table("corporate_accounts").select("id,name,status").in_("id", company_ids).execute()

        accounts_res = await run_sync(_fn2)
        accounts_by_id = {a["id"]: a for a in _rows_from_res(accounts_res)}

    out: List[Dict[str, Any]] = []
    for w in wallets:
        balance = Decimal(str(w.get("balance") or 0))
        floor = Decimal(str(w.get("soft_negative_floor") if w.get("soft_negative_floor") is not None else -50))
        threshold = w.get("auto_topup_threshold")
        auto_topup_enabled = bool(w.get("auto_topup_enabled"))

        flags: List[str] = []
        if balance < 0:
            flags.append("negative_balance")
        if balance <= floor:
            flags.append("at_floor")
        if threshold is not None and balance < Decimal(str(threshold)):
            flags.append("below_autotopup_threshold" if auto_topup_enabled else "low_balance_no_autotopup")

        account = accounts_by_id.get(w.get("company_id"), {})
        out.append(
            {
                "wallet_id": w.get("id"),
                "company_id": w.get("company_id"),
                "company_name": account.get("name"),
                "company_status": account.get("status"),
                "balance": str(balance),
                "soft_negative_floor": str(floor),
                "auto_topup_enabled": auto_topup_enabled,
                "risk_flags": flags,
            }
        )

    # Riskiest first: any flag beats none, most-negative balance beats less-negative.
    out.sort(key=lambda r: (0 if r["risk_flags"] else 1, Decimal(r["balance"])))
    return out


async def list_wallet_transactions(*, wallet_id: str, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent ledger entries for a wallet, newest first."""
    upper = skip + max(limit, 1) - 1

    def _fn():
        return (
            supabase.table("corporate_wallet_transactions")
            .select("*")
            .eq("wallet_id", wallet_id)
            .order("created_at", desc=True)
            .range(skip, upper)
            .execute()
        )

    res = await run_sync(_fn)
    return _rows_from_res(res)


async def update_corporate_wallet_config(*, wallet_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Patch one or more configuration columns on a corporate_wallets row."""

    def _fn():
        res = supabase.table("corporate_wallets").update(patch).eq("id", wallet_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ============================================================
# Corporate B2B Plan 3 — members, allowances, requests, domains
# ============================================================


# ---------- Members ----------
async def insert_corporate_member_invite(
    *,
    company_id: str,
    email: str,
    role: str,
    invite_token: str,
    invited_by: str,
    policy_override: bool = False,
) -> Dict[str, Any]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .insert(
                {
                    "company_id": company_id,
                    "invited_email": email,
                    "role": role,
                    "invite_token": invite_token,
                    "invited_at": datetime.now(timezone.utc).isoformat(),
                    "invited_by": invited_by,
                    "policy_override": policy_override,
                    "status": "invited",
                }
            )
            .execute()
        )
        return _single_row_from_res(res) or {}

    return await run_sync(_fn)


async def count_pending_signups_for_user(user_id: str) -> int:
    """How many pending_verification companies this user has self-registered.

    Backs the self-serve signup abuse cap (max 3 pending per user); served by
    the partial index corp_accounts_signup_pending_idx (migration 224).
    """

    def _fn():
        res = (
            supabase.table("corporate_accounts")
            .select("id", count="exact")
            .eq("signup_user_id", user_id)
            .eq("status", "pending_verification")
            .execute()
        )
        count = getattr(res, "count", None)
        return count if count is not None else len(res.data or [])

    return await run_sync(_fn)


async def create_active_member(
    *,
    company_id: str,
    user_id: str,
    email: str,
    role: str = "owner",
    invited_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a directly-ACTIVE membership (no invite round-trip).

    Used by owner bootstrap: the self-serve signup creator is already an
    authenticated, email-verified user, so there is nothing to invite — they
    become the company's first (owner) member immediately. The partial unique
    index corp_members_company_user_unique makes a duplicate insert raise;
    callers pre-check membership (bootstrap_owner), so a violation here is a
    genuine bug and must surface, not be swallowed.
    """
    now = datetime.now(timezone.utc).isoformat()

    def _fn():
        res = (
            supabase.table("corporate_members")
            .insert(
                {
                    "company_id": company_id,
                    "user_id": user_id,
                    "invited_email": email,
                    "role": role,
                    "status": "active",
                    "joined_at": now,
                    "invited_at": now,
                    "invited_by": invited_by or user_id,
                }
            )
            .execute()
        )
        return _single_row_from_res(res) or {}

    return await run_sync(_fn)


async def list_company_members(
    *,
    company_id: str,
    statuses: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    def _fn():
        q = supabase.table("corporate_members").select("*").eq("company_id", company_id)
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=False).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def get_corporate_member_by_id(member_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_members").select("*").eq("id", member_id).limit(1).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def get_member_by_invite_token(token: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_members").select("*").eq("invite_token", token).limit(1).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def list_active_memberships_for_user(user_id: str) -> List[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_members").select("*").eq("user_id", user_id).eq("status", "active").execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def update_corporate_member(member_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not patch:
        return await get_corporate_member_by_id(member_id)
    patch = {**patch, "updated_at": datetime.now(timezone.utc).isoformat()}

    def _fn():
        res = supabase.table("corporate_members").update(patch).eq("id", member_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def accept_member_invite(*, member_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Atomically flip invited -> active and stamp user_id + joined_at.

    Guarded by `.eq("status", "invited")` so we only flip pending invites,
    preventing replay against an already-consumed token.
    """
    patch = {
        "status": "active",
        "user_id": user_id,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "invite_token": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    def _fn():
        res = supabase.table("corporate_members").update(patch).eq("id", member_id).eq("status", "invited").execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ---------- Allowances ----------
async def get_member_allowance(member_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_member_allowances").select("*").eq("member_id", member_id).limit(1).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def upsert_member_allowance(*, member_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Insert if no allowance row exists, else update. Returns the row."""
    existing = await get_member_allowance(member_id)
    if existing:

        def _upd():
            res = (
                supabase.table("corporate_member_allowances")
                .update({**patch, "updated_at": datetime.now(timezone.utc).isoformat()})
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


async def list_company_allowances(company_id: str) -> List[Dict[str, Any]]:
    """Join allowances with their members, scoped to one company."""

    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*, member:corporate_members!inner(id,company_id,user_id,invited_email,status,role)")
            .eq("member.company_id", company_id)
            .execute()
        )
        return _rows_from_res(res)

    return await run_sync(_fn)


async def list_allowances_due_for_reset(as_of: str) -> List[Dict[str, Any]]:
    """Active fixed_recurring allowances whose period_end < as_of (ISO date)."""

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
    *,
    allowance_id: str,
    period_start: str,
    period_end: str,
    expected_period_end: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Advance an allowance's billing period.

    When ``expected_period_end`` is provided this becomes an atomic
    compare-and-swap: the row is updated only if its current ``period_end``
    still equals the expected value, so exactly one replica can claim a given
    period roll-forward (replay-safety F8). Returns the updated row, or None
    when the CAS lost (another replica already advanced it). With
    ``expected_period_end=None`` it behaves as before (unconditional update).
    """

    def _fn():
        q = (
            supabase.table("corporate_member_allowances")
            .update(
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "auto_approved_this_period": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", allowance_id)
        )
        if expected_period_end is not None:
            q = q.eq("period_end", expected_period_end)
        res = q.execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ---------- Allowance requests ----------
async def insert_allowance_request(
    *, member_id: str, amount: float, reason: str, status: str = "pending"
) -> Dict[str, Any]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .insert(
                {
                    "member_id": member_id,
                    "amount": amount,
                    "reason": reason,
                    "status": status,
                }
            )
            .execute()
        )
        return _single_row_from_res(res) or {}

    return await run_sync(_fn)


async def list_pending_allowance_requests_for_member(
    member_id: str,
) -> List[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .select("*")
            .eq("member_id", member_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
        )
        return _rows_from_res(res)

    return await run_sync(_fn)


async def list_company_allowance_requests(
    company_id: str,
    statuses: Optional[List[str]] = None,
    member_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    def _fn():
        q = (
            supabase.table("corporate_allowance_requests")
            .select("*, member:corporate_members!inner(id,company_id,invited_email,user_id)")
            .eq("member.company_id", company_id)
        )
        if member_id:
            q = q.eq("member_id", member_id)
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=True).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def get_allowance_request_by_id(
    request_id: str,
) -> Optional[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_allowance_requests").select("*").eq("id", request_id).limit(1).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def update_allowance_request(
    *,
    request_id: str,
    status: str,
    reviewed_by: Optional[str],
    decision_notes: Optional[str],
) -> Optional[Dict[str, Any]]:
    patch = {
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "decision_notes": decision_notes,
    }

    def _fn():
        res = supabase.table("corporate_allowance_requests").update(patch).eq("id", request_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ---------- Allowed domains ----------
async def add_allowed_domain(*, company_id: str, domain: str) -> Dict[str, Any]:
    def _fn():
        res = supabase.table("corporate_allowed_domains").insert({"company_id": company_id, "domain": domain}).execute()
        return _single_row_from_res(res) or {"company_id": company_id, "domain": domain}

    return await run_sync(_fn)


async def list_allowed_domains(company_id: str) -> List[Dict[str, Any]]:
    def _fn():
        res = supabase.table("corporate_allowed_domains").select("*").eq("company_id", company_id).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def delete_allowed_domain(*, company_id: str, domain: str) -> None:
    def _fn():
        supabase.table("corporate_allowed_domains").delete().eq("company_id", company_id).eq("domain", domain).execute()

    await run_sync(_fn)


async def find_companies_by_email_domain(domain: str) -> List[Dict[str, Any]]:
    """Active companies that whitelist this email domain for auto-match."""

    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .select("company_id, corporate_accounts:corporate_accounts!inner(id,name,status)")
            .eq("domain", domain)
            .eq("corporate_accounts.status", "active")
            .execute()
        )
        return _rows_from_res(res)

    return await run_sync(_fn)


# ---------- Billing (Plan 6) ----------
async def list_company_ride_payment_sources(
    *,
    company_id: str,
    from_iso: Optional[str] = None,
    to_iso: Optional[str] = None,
    member_id: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return ride_payment_sources rows for a company, newest first.

    Each row is the source of truth for a work ride's billing split:
    allowance_debit_amount + master_fallback_amount = total billed to company.
    """
    upper = offset + max(limit, 1) - 1

    def _fn():
        q = supabase.table("ride_payment_sources").select("*").eq("company_id", company_id)
        if member_id:
            q = q.eq("member_id", member_id)
        if from_iso:
            q = q.gte("created_at", from_iso)
        if to_iso:
            q = q.lte("created_at", to_iso)
        res = q.order("created_at", desc=True).range(offset, upper).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def get_corporate_policy(company_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the active corporate_policies row for a company."""

    def _fn():
        res = (
            supabase.table("corporate_policies")
            .select("*")
            .eq("company_id", company_id)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def upsert_corporate_policy(company_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or update the company's policy row.

    The table has a UNIQUE constraint on company_id so we upsert on that
    column.  Callers pass only the fields they want to change; for a full
    replace they pass the complete desired state.
    """
    existing = await get_corporate_policy(company_id)
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        update_patch = {**patch, "updated_at": now}

        def _upd():
            res = supabase.table("corporate_policies").update(update_patch).eq("id", existing["id"]).execute()
            return _single_row_from_res(res) or {**existing, **update_patch}

        return await run_sync(_upd) or existing

    insert_doc = {
        "company_id": company_id,
        "active": True,
        "created_at": now,
        "updated_at": now,
        **patch,
    }

    def _ins():
        res = supabase.table("corporate_policies").insert(insert_doc).execute()
        return _single_row_from_res(res) or insert_doc

    return await run_sync(_ins) or insert_doc


# ============ Corporate Subscription (flat SaaS billing) Functions ============


async def list_corporate_subscription_plans(active_only: bool = True) -> List[Dict[str, Any]]:
    """Return the admin-managed catalog of flat SaaS subscription plans."""

    def _fn():
        q = supabase.table("corporate_subscription_plans").select("*")
        if active_only:
            q = q.eq("is_active", True)
        return q.order("monthly_price").execute()

    res = await run_sync(_fn)
    return _rows_from_res(res)


async def get_corporate_subscription_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        return supabase.table("corporate_subscription_plans").select("*").eq("id", plan_id).limit(1).execute()

    rows = _rows_from_res(await run_sync(_fn))
    return rows[0] if rows else None


async def get_active_corporate_subscription(company_id: str) -> Optional[Dict[str, Any]]:
    """Return the company's current active/past_due subscription row, if any.

    At most one such row exists per company (enforced by a partial unique
    index — see migration 281), so `.limit(1)` is a defensive belt, not the
    source of the invariant.
    """

    def _fn():
        return (
            supabase.table("corporate_subscriptions")
            .select("*")
            .eq("company_id", company_id)
            .in_("status", ["active", "past_due"])
            .limit(1)
            .execute()
        )

    rows = _rows_from_res(await run_sync(_fn))
    return rows[0] if rows else None


async def get_corporate_subscription_by_stripe_id(stripe_subscription_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        return (
            supabase.table("corporate_subscriptions")
            .select("*")
            .eq("stripe_subscription_id", stripe_subscription_id)
            .limit(1)
            .execute()
        )

    rows = _rows_from_res(await run_sync(_fn))
    return rows[0] if rows else None


async def list_corporate_subscriptions_for_company(company_id: str) -> List[Dict[str, Any]]:
    """Full subscription history for a company (admin detail view), newest first."""

    def _fn():
        return (
            supabase.table("corporate_subscriptions")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .execute()
        )

    return _rows_from_res(await run_sync(_fn))


async def create_corporate_subscription_row(row: Dict[str, Any]) -> Dict[str, Any]:
    def _fn():
        res = supabase.table("corporate_subscriptions").insert(row).execute()
        return _single_row_from_res(res) or row

    return await run_sync(_fn)


async def update_corporate_subscription(subscription_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    patch = {**patch, "updated_at": datetime.now(timezone.utc).isoformat()}

    def _fn():
        res = supabase.table("corporate_subscriptions").update(patch).eq("id", subscription_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ============ Corporate Section Budgets (visibility-only) Functions ============


async def record_section_spend(*, section_id: str, month: str, amount: Decimal) -> Decimal:
    """Atomically add `amount` to a section's running month-to-date spend
    total via the corporate_section_spend_add RPC (migration 282) — a
    single-statement upsert-increment, safe under concurrency without a
    row lock. Visibility only: never raises for "over budget," there is no
    budget-cap enforcement here. Returns the new running total.
    """

    def _fn():
        return supabase.rpc(
            "corporate_section_spend_add",
            {"p_section_id": section_id, "p_month": month, "p_delta": str(amount)},
        ).execute()

    res = await run_sync(_fn)
    data = getattr(res, "data", None)
    return Decimal(str(data)) if data is not None else Decimal("0")


async def get_section_spend_map(section_ids: List[str], month: str) -> Dict[str, Decimal]:
    """Batch-read current month-to-date spend for a set of sections.

    Returns {section_id: used}; a section with no rows for `month` (no
    settled rides yet, or the column predates this feature) is simply
    absent from the dict — callers should default missing keys to 0.
    """
    if not section_ids:
        return {}

    def _fn():
        return (
            supabase.table("corporate_section_spend")
            .select("section_id,used")
            .in_("section_id", section_ids)
            .eq("month", month)
            .execute()
        )

    rows = _rows_from_res(await run_sync(_fn))
    return {r["section_id"]: Decimal(str(r.get("used") or 0)) for r in rows}


# ============ Corporate KYB Re-verification (staleness reminder) Functions ============


async def list_companies_needing_kyb_reverification(*, reviewed_before_iso: str) -> List[Dict[str, Any]]:
    """Active, KYB-approved companies whose last review predates the given
    cutoff. Does not filter on kyb_reverify_flagged_at — the background
    loop applies its own cooldown check in Python over the result,
    mirroring list_wallets_low_balance_no_autotopup's established pattern
    (see utils/corporate_low_balance.py) rather than building an $or
    filter for it.
    """

    def _fn():
        return (
            supabase.table("corporate_accounts")
            .select("*")
            .eq("status", "active")
            .eq("kyb_last_decision", "approved")
            .lt("kyb_reviewed_at", reviewed_before_iso)
            .execute()
        )

    return _rows_from_res(await run_sync(_fn))


async def mark_kyb_reverify_flagged(*, company_id: str) -> None:
    """Replay-safety claim flag only (migration 283) — not the source of
    truth for staleness, which the admin filter computes live from
    kyb_reviewed_at."""

    def _fn():
        supabase.table("corporate_accounts").update(
            {"kyb_reverify_flagged_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", company_id).execute()

    await run_sync(_fn)
