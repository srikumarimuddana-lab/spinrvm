import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...features import send_push_notification
    from ...routes.drivers._shared import _encrypt_driver_pii, _vault_decrypt
    from ...routes.users import store_profile_image
    from ...services import lms_service
    from ...utils.audit_logger import log_admin_action
    from ...utils.datetime_utils import parse_iso_utc
    from ...utils.driver_status_notifications import (
        action_message,
        notify_driver_status_change,
        status_message,
        verification_message,
    )
    from ...utils.referral_payout import ReferralClaimNotFound, recredit_failed_claim
    from ...utils.referral_terms import paid_referral_earnings, resolve_referral_terms
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from features import send_push_notification
    from routes.drivers._shared import _encrypt_driver_pii, _vault_decrypt  # type: ignore
    from routes.users import store_profile_image  # type: ignore
    from services import lms_service  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_status_notifications import (  # type: ignore
        action_message,
        notify_driver_status_change,
        status_message,
        verification_message,
    )
    from utils.referral_payout import ReferralClaimNotFound, recredit_failed_claim  # type: ignore
    from utils.referral_terms import paid_referral_earnings, resolve_referral_terms  # type: ignore

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Shared helpers (used by rides.py too via import) ----------


def _user_display_name(user: Optional[Dict]) -> str:
    if not user:
        return ""
    fn = user.get("first_name") or ""
    ln = user.get("last_name") or ""
    return f"{fn} {ln}".strip() or user.get("email") or user.get("phone") or ""


# ---------- Work authorization (single source of truth) ----------
# `drivers.work_authorization_status` is the ONE field an operator picks. The
# older `is_citizen` / `is_permanent_resident` booleans are kept as columns
# (the bulk importer and the drivers export still read them) but they are now
# strictly *derived* from the status — admins no longer set them independently,
# which is what left every driver row showing three separate "Unknown"s.
#
# Categories are mutually exclusive, so exactly one of the derived flags can be
# "yes"; the others report "not_applicable" rather than a misleading "no"/
# "unknown". `unknown` status is the only case where the flags are genuinely
# unknown.
WORK_AUTHORIZATION_CHOICES: Dict[str, str] = {
    "citizen": "Canadian citizen",
    "permanent_resident": "Permanent resident",
    "indefinite": "Work permit — no expiry",
    "expiring": "Work permit — expires",
    "unknown": "Unknown",
}


def normalize_work_authorization_status(value: Any) -> str:
    """Coerce a stored/submitted status to a canonical key. Blank -> 'unknown'."""
    key = str(value or "").strip().lower()
    return key if key in WORK_AUTHORIZATION_CHOICES else "unknown"


def derived_work_authorization_flags(status: str) -> Dict[str, Optional[bool]]:
    """Map a canonical status onto the legacy boolean columns.

    Returns ``None`` for both flags when the status is unknown so an unset
    driver is not silently written as "not a citizen and not a PR".
    """
    status = normalize_work_authorization_status(status)
    if status == "citizen":
        return {"is_citizen": True, "is_permanent_resident": False}
    if status == "permanent_resident":
        return {"is_citizen": False, "is_permanent_resident": True}
    if status in ("indefinite", "expiring"):
        # On a work permit: neither flag applies.
        return {"is_citizen": False, "is_permanent_resident": False}
    return {"is_citizen": None, "is_permanent_resident": None}


def work_authorization_view(driver: Dict[str, Any]) -> Dict[str, Any]:
    """Admin-facing projection of a driver's work authorization.

    One canonical status plus the derived flags rendered as
    ``yes`` / ``not_applicable`` / ``unknown`` so the dashboard never has to
    re-derive the relationship between the three columns.
    """
    status = normalize_work_authorization_status(driver.get("work_authorization_status"))
    # Legacy rows imported before the status column existed only carry the
    # booleans — promote them so those drivers do not read as "Unknown".
    if status == "unknown":
        if driver.get("is_citizen") is True:
            status = "citizen"
        elif driver.get("is_permanent_resident") is True:
            status = "permanent_resident"
    flags = derived_work_authorization_flags(status)

    def _flag(value: Optional[bool]) -> str:
        if value is None:
            return "unknown"
        return "yes" if value else "not_applicable"

    return {
        "status": status,
        "label": WORK_AUTHORIZATION_CHOICES[status],
        "citizen": _flag(flags["is_citizen"]),
        "permanent_resident": _flag(flags["is_permanent_resident"]),
        # Only an `expiring` permit has a meaningful end date.
        "expires_at": driver.get("work_eligibility_expiry_date") if status == "expiring" else None,
    }


def _mask_license_number(plain: Optional[str]) -> Optional[str]:
    """Last-4 mask for a decrypted licence number (never the full value)."""
    if not plain:
        return None
    s = str(plain).strip()
    if not s:
        return None
    return s[-4:] if len(s) > 4 else s


async def _batch_fetch_drivers_and_users(rider_ids: List[str], driver_ids: List[str]) -> tuple:
    """Batch-fetch drivers and users in 2-3 queries instead of N+1 loops."""
    drivers_list = (
        await db_supabase.get_rows("drivers", {"id": {"$in": driver_ids}}, limit=max(len(driver_ids), 1))
        if driver_ids
        else []
    )
    drivers_map = {d["id"]: d for d in drivers_list if d.get("id")}

    all_user_ids = list(
        {
            *rider_ids,
            *(d.get("user_id") for d in drivers_list if d.get("user_id")),
        }
    )
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": all_user_ids}}, limit=max(len(all_user_ids), 1))
        if all_user_ids
        else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    return drivers_map, users_map


# ---------- Driver helper: activity log ----------


async def _log_driver_activity(
    driver_id: str,
    event_type: str,
    title: str,
    description: str = "",
    metadata: dict = None,
    actor: str = "admin",
):
    """Helper to record a driver lifecycle event."""
    try:
        await db_supabase.insert_one(
            "driver_activity_log",
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "event_type": event_type,
                "title": title,
                "description": description,
                "metadata": metadata or {},
                "actor": actor,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.error(f"Failed to log driver activity: {e}", exc_info=True)


# ---------- Pydantic models ----------


class DriverVerifyRequest(BaseModel):
    verified: bool


class DriverPhotoReviewRequest(BaseModel):
    action: Literal["approve", "reject"]


class DriverActionRequest(BaseModel):
    action: Literal["approve", "reject", "suspend", "ban", "unban", "reactivate"]
    reason: Optional[str] = None


class DriverStatusOverride(BaseModel):
    # Must stay in sync with the `valid` set in admin_override_driver_status.
    # They previously disagreed in both directions: `rejected` was in this
    # Literal but not in `valid` (400), and `needs_review` was in `valid` but
    # not here (422) — so neither status was reachable through this endpoint.
    status: Literal["pending", "active", "needs_review", "rejected", "suspended", "banned"]
    is_verified: Optional[bool] = None
    reason: Optional[str] = None


class DriverNoteCreate(BaseModel):
    note: str
    category: str = "general"


# ---------- Drivers list ----------


def _subscription_summary(sub: Optional[Dict[str, Any]], now: datetime) -> tuple:
    """Reduce a driver's latest driver_subscriptions row to a display summary.

    Returns ``(status, plan_name, expires_at)`` where status is one of
    ``"active"`` / ``"expired"`` / ``None``:
      - a past ``expires_at`` reads as expired even if the expiry loop hasn't
        flipped the row yet (so the admin sees reality, not stale state),
      - ``cancelled`` (or no row) reads as None → "no subscription".
    """
    if not sub:
        return None, None, None
    plan = sub.get("plan_name")
    expires = sub.get("expires_at")
    raw = sub.get("status")
    if raw == "cancelled":
        return None, None, None
    exp_dt = parse_iso_utc(expires) if expires else None
    if exp_dt is not None and exp_dt <= now:
        return "expired", plan, expires
    if raw == "expired":
        return "expired", plan, expires
    if raw == "active":
        return "active", plan, expires
    return None, plan, expires


# Whitelist of columns the admin drivers list may be sorted by. Keys are the
# sort tokens the frontend sends; values are real columns on the `drivers`
# table so the ORDER BY happens at the DB (across ALL pages), not per-page in
# the browser. Derived/display columns map to their underlying column:
#   - "name"         -> first_name mirror (display name comes from users, but
#                       the mirror is kept in sync and is what we can order by)
#   - "region"       -> service_area_id (groups drivers by area consistently
#                       across pages; the area NAME lives in a separate table)
#   - "vehicle_type" -> vehicle_type_id (same rationale as region)
# Any token not in this map falls back to created_at so an unexpected value can
# never inject an arbitrary column into the ORDER BY.
_DRIVER_SORT_COLUMNS = {
    "created_at": "created_at",
    "name": "first_name",
    "status": "status",
    "is_online": "is_online",
    "vehicle_type": "vehicle_type_id",
    "vehicle_make": "vehicle_make",
    "rating": "rating",
    "total_rides": "total_rides",
    "total_earnings": "total_earnings",
    "region": "service_area_id",
}


# ── Driver search ───────────────────────────────────────────────────────────
#
# Search runs as two queries because a driver's display name does not live on
# the `drivers` table — it lives on the joined `users` row, and PostgREST cannot
# filter a parent by an embedded child without a foreign-table filter:
#
#   1. Resolve the term against `users` (name / email / phone) -> user IDs.
#   2. Match `drivers` on its own identifier columns OR user_id IN (those IDs).
#
# Step 2's `$in` leaf is why a name search returns anything at all — it was
# silently dropped by the query layer before, which is what made searching a
# driver by name return nothing.

# Whitespace-separated tokens are ANDed, so "Nighil Kumar" matches a driver
# whose first_name is Nighil and last_name is Kumar (neither column contains
# the full string, so a single ILIKE over the whole term never matched).
_DRIVER_SEARCH_MAX_TOKENS = 5
# Bound the term so a pasted wall of text can't build a huge OR clause.
_DRIVER_SEARCH_MAX_TERM = 128
# Cap on the users pre-query. A common surname can exceed this; when it does we
# log it rather than silently returning a truncated driver list.
_DRIVER_SEARCH_USER_LIMIT = 500
# Columns on `users` each token is matched against.
_DRIVER_SEARCH_USER_COLUMNS = ("first_name", "last_name", "email", "phone")
# Columns on `drivers` matched against the FULL term (not per token). These are
# single-token identifiers, plus `name` — the denormalized display-name mirror,
# which lets a name search still hit a driver whose users row is missing or
# stale (e.g. a legacy import).
_DRIVER_SEARCH_DRIVER_COLUMNS = ("name", "phone", "license_plate", "driver_code")
# `id` and `user_id` hold long opaque IDs, so a short term substring-matches a
# large fraction of them purely by chance — searching "ab" returned an arbitrary
# set of drivers whose UUID happened to contain "ab", burying any real match.
# They are only searched when the term is plausibly an ID someone pasted.
_DRIVER_SEARCH_ID_COLUMNS = ("id", "user_id")
_DRIVER_SEARCH_ID_MIN_LEN = 8


def _looks_like_id(term: str) -> bool:
    """True when a term is long enough and shaped like a pasted identifier."""
    return len(term) >= _DRIVER_SEARCH_ID_MIN_LEN and not any(ch.isspace() for ch in term)


def _driver_search_tokens(term: str) -> List[str]:
    """Split a search-box value into the tokens that must ALL match."""
    return [t for t in term.split() if t][:_DRIVER_SEARCH_MAX_TOKENS]


def _phone_digits(term: str) -> Optional[str]:
    """Digits-only form of a term that looks like a phone number, else None.

    Admins paste phone numbers as "(306) 555-1234" but they are stored E.164 as
    "+13065551234", so the literal term never substring-matches. Only applied
    when the term is mostly digits, so a name is never mangled into one.
    """
    digits = "".join(ch for ch in term if ch.isdigit())
    if len(digits) < 4:
        return None
    non_digits = sum(1 for ch in term if not ch.isdigit() and not ch.isspace())
    # Allow the usual separators (+, -, (, ), .) but reject anything wordy.
    if non_digits > 4 or any(ch.isalpha() for ch in term):
        return None
    return digits if digits != term else None


async def _resolve_driver_search_user_ids(tokens: List[str]) -> List[str]:
    """User IDs whose name/email/phone matches EVERY token.

    Each token is ORed across the user columns and the per-token clauses are
    ANDed, which is what makes multi-word name search work. `_apply_filters`
    renders each `$or` as its own PostgREST `or=(...)` param, and repeated
    `or=` params are ANDed server-side.
    """
    per_token = [
        {"$or": [{col: {"$regex": tok, "$options": "i"}} for col in _DRIVER_SEARCH_USER_COLUMNS]} for tok in tokens
    ]

    # Project only `id` so a name search never pulls base64 profile_image rows.
    matching_users = await db_supabase.get_rows(
        "users",
        {"$and": per_token} if len(per_token) > 1 else per_token[0],
        columns="id",
        limit=_DRIVER_SEARCH_USER_LIMIT,
    )
    uids = [u["id"] for u in matching_users if u.get("id")]
    if len(uids) >= _DRIVER_SEARCH_USER_LIMIT:
        # Loud, because the driver the admin is looking for may be the one that
        # got cut. No PII in the log — token count only, never the term itself.
        logger.warning(
            "admin driver search: users pre-query hit the %d-row cap; results may be truncated",
            _DRIVER_SEARCH_USER_LIMIT,
            extra={"domain": "admin", "surface": "backend", "token_count": len(tokens)},
        )
    return uids


@router.get("/drivers")
async def admin_get_drivers(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    is_verified: Optional[bool] = None,
    is_online: Optional[bool] = None,
    is_available: Optional[bool] = None,
    status: Optional[str] = None,
    service_area_id: Optional[str] = None,
    vehicle_type_id: Optional[str] = None,
    photo_status: Optional[str] = None,
    missing_license: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
):
    """Get drivers with filters, enriched with user name/email/phone.

    Search, filtering, sorting and pagination all happen at the DB so the admin
    UI operates over the ENTIRE drivers table, not just the rows already loaded
    into the current page.

    Defense-in-depth dedup: migration 31 adds UNIQUE(drivers.phone) and
    UNIQUE(drivers.user_id) so duplicates can't exist at the DB level.
    We still collapse by phone/user_id here so that if a legacy snapshot
    ever restores old state, the admin UI won't show duplicate rows.
    """

    filters = {}
    if is_verified is not None:
        filters["is_verified"] = is_verified
    if is_online is not None:
        filters["is_online"] = is_online
    if is_available is not None:
        filters["is_available"] = is_available
    if status:
        filters["status"] = status
    if service_area_id:
        filters["service_area_id"] = service_area_id
    if vehicle_type_id:
        filters["vehicle_type_id"] = vehicle_type_id

    # See the "Driver search" block above the route for the two-query design.
    if search:
        term = search.strip()[:_DRIVER_SEARCH_MAX_TERM]
        tokens = _driver_search_tokens(term)
        if tokens:
            # 1. Name/email/phone live on `users`, so resolve them to user IDs
            #    first. Tokens are ANDed here, which is what makes a full name
            #    like "Nighil Kumar" match (first_name and last_name each hold
            #    only one of the tokens).
            matching_uids = await _resolve_driver_search_user_ids(tokens)

            # 2. Match `drivers` on its own columns OR the user IDs from step 1.
            #    Driver-side columns are matched against the whole term: they are
            #    single-token identifiers (plate, code) plus the `name` mirror,
            #    which holds the full display name in one column.
            or_clauses: List[Dict[str, Any]] = [
                {col: {"$regex": term, "$options": "i"}} for col in _DRIVER_SEARCH_DRIVER_COLUMNS
            ]
            # Only search the opaque ID columns for a term shaped like a pasted
            # ID — a short term matches them by coincidence and drowns out the
            # real match.
            if _looks_like_id(term):
                or_clauses += [{col: {"$regex": term, "$options": "i"}} for col in _DRIVER_SEARCH_ID_COLUMNS]
            # "(306) 555-1234" never substring-matches the stored "+13065551234".
            digits = _phone_digits(term)
            if digits:
                or_clauses.append({"phone": {"$regex": digits, "$options": "i"}})
            if matching_uids:
                or_clauses.append({"user_id": {"$in": matching_uids}})
            filters["$or"] = or_clauses

    # Filter to drivers missing licence_number or licence_class (ACTION_ITEMS.md
    # B14 backfill queue — the SGI D00032 form renders these fields blank for
    # any driver where they were never entered; see
    # docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md).
    # $or would collide with the search block's own $or key above, so the two
    # aren't supported together -- this filter is for the dedicated backfill
    # review screen, not the general search UI.
    if missing_license:
        if search:
            raise HTTPException(status_code=400, detail="missing_license cannot be combined with search")
        filters["$or"] = [{"license_number": None}, {"license_class": None}]

    # Filter by profile-photo moderation status (photo lives on users). Used by
    # the admin "Pending photos" queue. No matching users → no drivers.
    if photo_status:
        photo_users = await db_supabase.get_rows("users", {"profile_image_status": photo_status}, limit=1000)
        photo_uids = [u["id"] for u in photo_users if u.get("id")]
        if not photo_uids:
            return []
        filters["user_id"] = {"$in": photo_uids}

    order_col = _DRIVER_SORT_COLUMNS.get((sort_by or "").strip(), "created_at")
    # Default direction is descending (newest-first) — the historical behaviour
    # — unless the caller explicitly asks for ascending.
    desc = (sort_dir or "desc").strip().lower() != "asc"
    drivers = await db_supabase.get_rows("drivers", filters, order=order_col, desc=desc, limit=limit, offset=offset)

    # Defensive dedup — keep the earliest-created row per (user_id, phone) while
    # preserving the DB-returned ORDER BY. We decide which rows to KEEP by
    # scanning oldest-first (so the earliest row wins per dup group), then filter
    # the original list so the requested sort order is not clobbered.
    seen_user_ids: set = set()
    seen_phones: set = set()
    kept_ids: set = set()
    for d in sorted(drivers, key=lambda r: r.get("created_at") or ""):
        uid = d.get("user_id")
        phone = d.get("phone")
        if (uid and uid in seen_user_ids) or (phone and phone in seen_phones):
            continue
        if uid:
            seen_user_ids.add(uid)
        if phone:
            seen_phones.add(phone)
        kept_ids.add(id(d))
    deduped = [d for d in drivers if id(d) in kept_ids]

    user_ids = list({d.get("user_id") for d in deduped if d.get("user_id")})
    # Project only the columns the list renders. users.profile_image is
    # deliberately excluded: for accounts predating the `profile-photos` storage
    # bucket it holds a full base64 data URI, so pulling N of them into a bulk
    # list response bloated the payload (and shipped a face photo — PII — in a
    # list endpoint). The avatar now loads lazily from the per-driver live-stats
    # endpoint when the detail slideout opens. profile_image_status is kept so
    # the status badges and "Pending photos" tab keep working.
    users_list = (
        await db_supabase.get_rows(
            "users",
            {"id": {"$in": user_ids}},
            columns="id,first_name,last_name,email,phone,profile_image_status",
            limit=max(len(user_ids), 1),
        )
        if user_ids
        else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    # Spinr Pass status — one batch query over this page's drivers. Most recent
    # row per driver wins (global created_at DESC, first seen per driver_id).
    driver_ids = [d.get("id") for d in deduped if d.get("id")]
    subs_map: Dict[str, Dict[str, Any]] = {}
    if driver_ids:
        try:
            _subs = await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": {"$in": driver_ids}},
                columns="driver_id,plan_name,status,expires_at,created_at",
                order="created_at",
                desc=True,
                limit=max(len(driver_ids) * 5, 100),
            )
            for s in _subs or []:
                did = s.get("driver_id")
                if did and did not in subs_map:
                    subs_map[did] = s
        except Exception as _sub_err:
            logger.warning(f"admin_get_drivers: subscription enrichment failed: {_sub_err}")
    _sub_now = datetime.now(timezone.utc)

    out = []
    for d in deduped:
        u = users_map.get(d.get("user_id"))
        _sub_status, _sub_plan, _sub_expires = _subscription_summary(subs_map.get(d.get("id")), _sub_now)
        out.append(
            {
                **d,
                "name": _user_display_name(u) or d.get("name"),
                # Prefer the account's first/last over the drivers mirror (which
                # the UI renders). The mirror can hold a stale or placeholder
                # value — e.g. a legacy "Driver" — so a correct account name must
                # win. Mirrors admin_get_driver_stats. Fall back to the mirror,
                # but drop the generic "Driver" placeholder rather than show it.
                "first_name": (u.get("first_name") if u else None)
                or (None if d.get("first_name") == "Driver" else d.get("first_name")),
                "last_name": (u.get("last_name") if u else None) or d.get("last_name"),
                "email": u.get("email") if u else None,
                "phone": u.get("phone") if u else d.get("phone"),
                "profile_image_status": (u.get("profile_image_status") if u else None),
                "subscription_status": _sub_status,
                "subscription_plan": _sub_plan,
                "subscription_expires_at": _sub_expires,
                # Single consolidated work-authorization projection; the raw
                # columns are still spread above for back-compat.
                "work_authorization": work_authorization_view(d),
                # Account deletion cannot change `status` (no 'deleted' value in
                # the set), so a departed driver still reads as status='active'
                # here. Deleted rows stay IN this list on purpose — an admin
                # still has to find them to file the SGI removal — but they must
                # be visibly distinct rather than silently indistinguishable.
                "account_deleted": bool(d.get("deleted_at")),
            }
        )
    return out


class DriverSearchRequest(BaseModel):
    search: str
    limit: int = 5
    is_online: Optional[bool] = None
    is_available: Optional[bool] = None


@router.post("/drivers/search")
async def admin_search_drivers(
    body: DriverSearchRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Typeahead search for drivers via POST body to keep search terms out of server logs."""
    return await admin_get_drivers(
        limit=body.limit,
        search=body.search,
        is_online=body.is_online,
        is_available=body.is_available,
    )


@router.get("/drivers/stats")
async def admin_get_driver_stats(
    service_area_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get driver statistics, optionally filtered by service area and date range.

    Returns overall + per-service-area stats, plus daily chart data for
    driver joins, rides, and earnings.
    """
    from collections import defaultdict

    now = datetime.now(timezone.utc)
    # Default date range: last 30 days. parse_iso_utc always returns a
    # UTC-aware datetime (or None) so comparisons below match `now`.
    range_start = parse_iso_utc(start_date) if start_date else None
    if range_start is None:
        range_start = now - timedelta(days=30)
    range_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    range_end = parse_iso_utc(end_date) if end_date else None
    if range_end is None:
        range_end = now
    else:
        range_end = range_end.replace(hour=23, minute=59, second=59, microsecond=0)

    # Fetch all service areas for lookups
    service_areas = await db_supabase.get_rows("service_areas", order="name", limit=200)
    area_map = {a["id"]: a.get("name", "Unknown") for a in service_areas}

    # ── Fetch drivers ──
    driver_filters: Dict[str, Any] = {}
    if service_area_id:
        driver_filters["service_area_id"] = service_area_id
    all_drivers = await db_supabase.get_rows("drivers", driver_filters, order="created_at", desc=True, limit=5000)

    # Enrich with user info (batch)
    user_ids = list({d.get("user_id") for d in all_drivers if d.get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map: Dict[str, Any] = {u["id"]: u for u in users_list if u.get("id")}

    # Auto-detect needs_review: active drivers with expired docs or pending re-uploads.
    # Capped at 500 for the inline needs_review flag; full paginated list via
    # GET /documents/pending (A-P4-4).
    all_docs = await db_supabase.get_rows("driver_documents", {"status": "pending"}, limit=500)
    pending_doc_driver_ids = {d.get("driver_id") for d in all_docs if d.get("driver_id")}

    now_iso = datetime.now(timezone.utc).isoformat()
    expiry_fields = [
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
    ]

    enriched_drivers = []
    for d in all_drivers:
        u = users_map.get(d.get("user_id"))
        driver_status = d.get("status", "pending")

        # Account deletion soft-deletes the drivers row but cannot change
        # `status` — there is no 'deleted' value in the status set — so a driver
        # who left kept status='active' and went on being counted as an active
        # driver on this page (and as online, for rows whose intent flags
        # predate the deletion hardening in routes/users.py). Classify them into
        # their own bucket instead. Derived for display/counting only; the
        # stored `status` column is untouched.
        if d.get("deleted_at"):
            driver_status = "deleted"

        # Auto-detect needs_review for active drivers
        if driver_status == "active":
            for ef in expiry_fields:
                exp = d.get(ef)
                if exp and str(exp) < now_iso:
                    driver_status = "needs_review"
                    break
            if driver_status == "active" and d.get("id") in pending_doc_driver_ids:
                driver_status = "needs_review"

        enriched_drivers.append(
            {
                **d,
                "status": driver_status,
                "first_name": u.get("first_name") if u else d.get("first_name"),
                "last_name": u.get("last_name") if u else d.get("last_name"),
                "name": _user_display_name(u) or d.get("name"),
                "email": u.get("email") if u else None,
                "phone": u.get("phone") if u else d.get("phone"),
            }
        )

    # ── Compute overall driver stats ──
    total = len(enriched_drivers)
    # A deleted account is never "online", whatever its stale intent flag says.
    online = sum(1 for d in enriched_drivers if d.get("is_online") and d.get("status") != "deleted")
    active_count = sum(1 for d in enriched_drivers if d.get("status") == "active")
    pending_count = sum(1 for d in enriched_drivers if d.get("status") == "pending")
    needs_review_count = sum(1 for d in enriched_drivers if d.get("status") == "needs_review")
    suspended_count = sum(1 for d in enriched_drivers if d.get("status") == "suspended")
    banned_count = sum(1 for d in enriched_drivers if d.get("status") == "banned")
    deleted_count = sum(1 for d in enriched_drivers if d.get("status") == "deleted")
    total_rides_sum = sum(int(d.get("total_rides") or 0) for d in enriched_drivers)
    total_earnings_sum = float(sum(Decimal(str(d.get("total_earnings") or 0)) for d in enriched_drivers))
    avg_rating = 0.0
    rated = [d for d in enriched_drivers if d.get("rating") and float(d.get("rating", 0)) > 0]
    if rated:
        avg_rating = round(sum(float(d["rating"]) for d in rated) / len(rated), 2)

    # ── Per-service-area breakdown ──
    area_stats: Dict[str, Dict[str, Any]] = {}
    for d in enriched_drivers:
        aid = d.get("service_area_id") or "unassigned"
        if aid not in area_stats:
            area_stats[aid] = {
                "service_area_id": aid,
                "service_area_name": area_map.get(aid, "Unassigned"),
                "total": 0,
                "online": 0,
                "verified": 0,
                "unverified": 0,
                "total_rides": 0,
                "total_earnings": 0.0,
            }
        area_stats[aid]["total"] += 1
        if d.get("is_online"):
            area_stats[aid]["online"] += 1
        if d.get("is_verified"):
            area_stats[aid]["verified"] += 1
        else:
            area_stats[aid]["unverified"] += 1
        area_stats[aid]["total_rides"] += int(d.get("total_rides") or 0)
        area_stats[aid]["total_earnings"] = float(
            Decimal(str(area_stats[aid]["total_earnings"])) + Decimal(str(d.get("total_earnings") or 0))
        )

    # ── Daily charts (within date range) ──
    num_days = (range_end - range_start).days + 1
    if num_days > 365:
        num_days = 365

    # Driver joins per day
    daily_joins: Dict[str, int] = defaultdict(int)
    for d in enriched_drivers:
        dt = parse_iso_utc(d.get("created_at"))
        if dt is None:
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_joins[day_key] += 1

    # Rides + earnings per day (for drivers matching the service_area filter)
    driver_ids_set = {d["id"] for d in enriched_drivers}
    ride_filters: Dict[str, Any] = {"created_at": {"$gte": range_start.isoformat()}}
    all_rides = await db_supabase.get_rows("rides", ride_filters, order="created_at", desc=True, limit=5000)

    # Filter rides to only those belonging to our driver set
    relevant_rides = [r for r in all_rides if r.get("driver_id") in driver_ids_set] if service_area_id else all_rides

    daily_rides: Dict[str, int] = defaultdict(int)
    daily_earnings: Dict[str, float] = defaultdict(float)
    for r in relevant_rides:
        dt = parse_iso_utc(r.get("created_at"))
        if dt is None:
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_rides[day_key] += 1
            if r.get("status") == "completed":
                daily_earnings[day_key] = float(
                    Decimal(str(daily_earnings[day_key])) + Decimal(str(r.get("driver_earnings") or 0))
                )

    # Build chart arrays
    joins_chart = []
    rides_chart = []
    earnings_chart = []
    for i in range(num_days):
        day = range_start + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%b %d")
        joins_chart.append(
            {
                "date": day_label,
                "date_raw": day_key,
                "count": daily_joins.get(day_key, 0),
            }
        )
        rides_chart.append(
            {
                "date": day_label,
                "date_raw": day_key,
                "count": daily_rides.get(day_key, 0),
            }
        )
        earnings_chart.append(
            {
                "date": day_label,
                "date_raw": day_key,
                "amount": round(daily_earnings.get(day_key, 0), 2),
            }
        )

    return {
        "stats": {
            "total": total,
            "online": online,
            "active": active_count,
            "pending": pending_count,
            "needs_review": needs_review_count,
            "suspended": suspended_count,
            "banned": banned_count,
            "deleted": deleted_count,
            "total_rides": total_rides_sum,
            "total_earnings": total_earnings_sum,
            "avg_rating": avg_rating,
            # Drivers whose profile photo is awaiting admin approval.
            "pending_photos": sum(1 for u in users_map.values() if u.get("profile_image_status") == "pending_review"),
        },
        "area_stats": list(area_stats.values()),
        "charts": {
            "daily_joins": joins_chart,
            "daily_rides": rides_chart,
            "daily_earnings": earnings_chart,
        },
        "drivers": enriched_drivers,
        "service_areas": [
            {"id": a["id"], "name": a.get("name", "Unknown")}
            for a in service_areas
            if not a.get("parent_service_area_id")
        ],
    }


@router.get("/drivers/approval-queue")
async def admin_get_approval_queue(
    limit: int = Query(50, ge=1, le=200),
    service_area_id: Optional[str] = None,
):
    """Per-driver rollup of pending applications, oldest-first.

    Surfaces drivers that need ops attention right now:
    - drivers.status == "pending" (new applicants)
    - drivers with any driver_documents.status == "pending" (re-uploads
      from suspended/needs_review drivers)
    - drivers whose profile photo awaits review
      (users.profile_image_status == "pending_review")

    Each item carries non-exclusive segment flags (is_new_applicant,
    is_resubmission, has_pending_photo) so the queue UI can split the
    list into tabs; `stats` exposes a count per segment over the full
    result set.

    Each item carries time-in-queue, pending/missing doc counts, and the
    service area + vehicle type names so the queue page doesn't need
    extra round-trips. The header `stats` block exposes SLA signals
    (median wait, oldest, count over 24h) computed over the full result
    set — not the trimmed window — so the dashboard reflects reality even
    when the table is paginated.

    queue_started_at: status_changed at unavailable on `drivers`, so we
    fall back to drivers.created_at for new applicants, or the earliest
    pending-doc upload_at for re-uploaders. This matches what ops cares
    about: "how long has this been waiting on us?" Photo-only rows have
    no photo-upload timestamp, so users.updated_at is the best available
    approximation (any profile update refreshes it), falling back to
    drivers.created_at.
    """
    now = datetime.now(timezone.utc)

    pending_drivers = await db_supabase.get_rows(
        "drivers",
        {"status": "pending", **({"service_area_id": service_area_id} if service_area_id else {})},
        order="created_at",
        limit=1000,
    )

    pending_docs = await db_supabase.get_rows(
        "driver_documents",
        {"status": "pending"},
        order="uploaded_at",
        limit=1000,
    )

    earliest_pending_doc_by_driver: Dict[str, str] = {}
    pending_doc_count_by_driver: Dict[str, int] = {}
    for d in pending_docs:
        did = d.get("driver_id")
        if not did:
            continue
        pending_doc_count_by_driver[did] = pending_doc_count_by_driver.get(did, 0) + 1
        ts = d.get("uploaded_at") or d.get("created_at")
        if ts and (did not in earliest_pending_doc_by_driver or ts < earliest_pending_doc_by_driver[did]):
            earliest_pending_doc_by_driver[did] = ts

    driver_map: Dict[str, Dict[str, Any]] = {d["id"]: d for d in pending_drivers if d.get("id")}
    extra_driver_ids = [did for did in pending_doc_count_by_driver if did not in driver_map]
    if extra_driver_ids:
        extra_filters: Dict[str, Any] = {"id": {"$in": extra_driver_ids}}
        if service_area_id:
            extra_filters["service_area_id"] = service_area_id
        extra_drivers = await db_supabase.get_rows("drivers", extra_filters, limit=len(extra_driver_ids))
        for d in extra_drivers:
            if d.get("id"):
                driver_map[d["id"]] = d

    # Drivers whose only pending item is a profile photo. The join back to
    # `drivers` is what keeps riders out — they share profile_image_status
    # but have no drivers row.
    photo_users = await db_supabase.get_rows(
        "users", {"profile_image_status": "pending_review"}, limit=1000, columns="id"
    )
    known_user_ids = {d.get("user_id") for d in driver_map.values() if d.get("user_id")}
    photo_only_uids = [u["id"] for u in photo_users if u.get("id") and u["id"] not in known_user_ids]
    if photo_only_uids:
        photo_filters: Dict[str, Any] = {
            "user_id": {"$in": photo_only_uids},
            "status": {"$nin": ["banned", "rejected"]},
        }
        if service_area_id:
            photo_filters["service_area_id"] = service_area_id
        photo_drivers = await db_supabase.get_rows("drivers", photo_filters, limit=len(photo_only_uids))
        for d in photo_drivers:
            if d.get("id") and d["id"] not in driver_map and d.get("user_id") not in known_user_ids:
                driver_map[d["id"]] = d
                known_user_ids.add(d.get("user_id"))

    if not driver_map:
        return {
            "stats": {
                "total_pending": 0,
                "oldest_in_queue_hours": 0.0,
                "median_wait_hours": 0.0,
                "over_24h_count": 0,
                "new_applicants": 0,
                "resubmissions": 0,
                "photo_review": 0,
            },
            "items": [],
        }

    user_ids = list({d.get("user_id") for d in driver_map.values() if d.get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    area_ids = list({d.get("service_area_id") for d in driver_map.values() if d.get("service_area_id")})
    areas_list = (
        await db_supabase.get_rows("service_areas", {"id": {"$in": area_ids}}, limit=max(len(area_ids), 1))
        if area_ids
        else []
    )
    areas_map = {a["id"]: a for a in areas_list if a.get("id")}

    vtype_ids = list({d.get("vehicle_type_id") for d in driver_map.values() if d.get("vehicle_type_id")})
    vtypes_list = (
        await db_supabase.get_rows("vehicle_types", {"id": {"$in": vtype_ids}}, limit=max(len(vtype_ids), 1))
        if vtype_ids
        else []
    )
    vtypes_map = {v["id"]: v.get("name") for v in vtypes_list if v.get("id")}

    all_docs = (
        await db_supabase.get_rows(
            "driver_documents",
            {"driver_id": {"$in": list(driver_map.keys())}, "status": {"$in": ["approved", "pending"]}},
            limit=max(len(driver_map) * 10, 100),
        )
        if driver_map
        else []
    )
    docs_by_driver: Dict[str, List[Dict[str, Any]]] = {}
    for d in all_docs:
        docs_by_driver.setdefault(d.get("driver_id"), []).append(d)

    def _missing_count(driver_row: Dict[str, Any]) -> int:
        area = areas_map.get(driver_row.get("service_area_id"))
        if not area:
            return 0
        reqs = area.get("required_documents") or []
        if not isinstance(reqs, list) or not reqs:
            return 0
        driver_docs = docs_by_driver.get(driver_row["id"], [])
        approved_keys = set()
        for dd in driver_docs:
            if dd.get("status") != "approved":
                continue
            k = (
                dd.get("requirement_key")
                or dd.get("requirement_id")
                or (dd.get("document_type") or "").lower().replace(" ", "_")
            )
            if k:
                approved_keys.add(k)
        missing = 0
        for r in reqs:
            if not isinstance(r, dict):
                continue
            key = (r.get("key") or r.get("id") or "").lower()
            if key and key not in approved_keys:
                missing += 1
        return missing

    items: List[Dict[str, Any]] = []
    for did, drow in driver_map.items():
        u = users_map.get(drow.get("user_id"))
        profile_image_status = (u or {}).get("profile_image_status")
        pending_doc_count = pending_doc_count_by_driver.get(did, 0)
        is_new_applicant = drow.get("status") == "pending"
        is_resubmission = not is_new_applicant and pending_doc_count > 0
        has_pending_photo = profile_image_status == "pending_review"

        if is_new_applicant:
            queue_started_at = drow.get("created_at")
        elif pending_doc_count > 0:
            queue_started_at = earliest_pending_doc_by_driver.get(did) or drow.get("created_at")
        else:
            # Photo-only row: users.updated_at approximates when the photo
            # changed; drivers.created_at would overstate the wait for a
            # long-active driver who just swapped their photo.
            queue_started_at = (u or {}).get("updated_at") or drow.get("created_at")

        time_in_queue_seconds = 0
        if queue_started_at:
            qdt = parse_iso_utc(queue_started_at)
            if qdt is not None:
                time_in_queue_seconds = max(0, int((now - qdt).total_seconds()))

        items.append(
            {
                "driver_id": did,
                "user_id": drow.get("user_id"),
                "first_name": (u or {}).get("first_name") or "",
                "last_name": (u or {}).get("last_name") or "",
                "name": _user_display_name(u) or drow.get("name") or "",
                "email": (u or {}).get("email"),
                "phone": (u or {}).get("phone") or drow.get("phone"),
                # Driver photo = users.profile_image (no drivers photo column).
                "profile_photo_url": (u or {}).get("profile_image"),
                "status": drow.get("status", "pending"),
                "created_at": drow.get("created_at"),
                "queue_started_at": queue_started_at,
                "time_in_queue_seconds": time_in_queue_seconds,
                "pending_docs_count": pending_doc_count,
                "missing_docs_count": _missing_count(drow),
                "profile_image_status": profile_image_status,
                "is_new_applicant": is_new_applicant,
                "is_resubmission": is_resubmission,
                "has_pending_photo": has_pending_photo,
                "service_area_id": drow.get("service_area_id"),
                "service_area_name": (areas_map.get(drow.get("service_area_id")) or {}).get("name"),
                "vehicle_type_id": drow.get("vehicle_type_id"),
                "vehicle_type_name": vtypes_map.get(drow.get("vehicle_type_id")),
            }
        )

    items.sort(key=lambda r: r["time_in_queue_seconds"], reverse=True)

    waits = [it["time_in_queue_seconds"] for it in items]
    total = len(items)
    over_24h = sum(1 for w in waits if w >= 86400)
    oldest_hours = round(max(waits) / 3600, 1) if waits else 0.0
    if waits:
        sorted_waits = sorted(waits)
        mid = total // 2
        median_seconds = sorted_waits[mid] if total % 2 == 1 else (sorted_waits[mid - 1] + sorted_waits[mid]) / 2
        median_hours = round(median_seconds / 3600, 1)
    else:
        median_hours = 0.0

    return {
        "stats": {
            "total_pending": total,
            "oldest_in_queue_hours": oldest_hours,
            "median_wait_hours": median_hours,
            "over_24h_count": over_24h,
            "new_applicants": sum(1 for it in items if it["is_new_applicant"]),
            "resubmissions": sum(1 for it in items if it["is_resubmission"]),
            "photo_review": sum(1 for it in items if it["has_pending_photo"]),
        },
        "items": items[:limit],
    }


# Legacy expiry columns on `drivers` — mirrors document_expiry.py so the
# admin queue and the background warner agree on which docs to track.
_EXPIRY_FIELDS: Dict[str, str] = {
    "license_expiry_date": "Driver's License",
    "insurance_expiry_date": "Insurance",
    "vehicle_inspection_expiry_date": "Vehicle Inspection",
    "background_check_expiry_date": "Background Check",
    "work_eligibility_expiry_date": "Work Eligibility",
}


@router.get("/drivers/expiring")
async def admin_get_expiring_documents(
    window_days: int = Query(30, ge=1, le=90),
    service_area_id: Optional[str] = None,
):
    """Drivers with at least one document expiring inside `window_days`.

    Returns one row per (driver, expiring document) so the ops table can
    list each renewal-needed item individually with its own Nudge button.
    Ride volume for the last 30 days is included so ops can prioritize
    high-value drivers. `last_nudged_at` reflects the most recent renewal
    push (manual or automatic) sent to the driver — single field on
    `drivers` shared across all doc types, matching what
    `document_expiry.py` already maintains.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=window_days)

    filters: Dict[str, Any] = {"status": {"$in": ["active", "needs_review"]}}
    if service_area_id:
        filters["service_area_id"] = service_area_id
    drivers = await db_supabase.get_rows("drivers", filters, limit=5000)

    expiring_rows: List[Dict[str, Any]] = []
    affected_driver_ids: set = set()
    for d in drivers:
        for field, label in _EXPIRY_FIELDS.items():
            val = d.get(field)
            if not val:
                continue
            exp_dt = parse_iso_utc(val)
            if exp_dt is None:
                continue
            if now <= exp_dt <= window_end:
                affected_driver_ids.add(d["id"])
                expiring_rows.append(
                    {
                        "driver_row": d,
                        "doc_field": field,
                        "doc_type": field.replace("_expiry_date", ""),
                        "doc_label": label,
                        "expiry_date": val,
                        "days_remaining": max(0, (exp_dt - now).days),
                    }
                )

    if not expiring_rows:
        return {"items": []}

    user_ids = list({d["driver_row"].get("user_id") for d in expiring_rows if d["driver_row"].get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    area_ids = list(
        {d["driver_row"].get("service_area_id") for d in expiring_rows if d["driver_row"].get("service_area_id")}
    )
    areas_list = (
        await db_supabase.get_rows("service_areas", {"id": {"$in": area_ids}}, limit=max(len(area_ids), 1))
        if area_ids
        else []
    )
    areas_map = {a["id"]: a.get("name") for a in areas_list if a.get("id")}

    rides_30d_ago = (now - timedelta(days=30)).isoformat()
    rides = (
        await db_supabase.get_rows(
            "rides",
            {
                "driver_id": {"$in": list(affected_driver_ids)},
                "status": "completed",
                "ride_completed_at": {"$gte": rides_30d_ago},
            },
            limit=10000,
        )
        if affected_driver_ids
        else []
    )
    rides_by_driver: Dict[str, int] = {}
    for r in rides:
        did = r.get("driver_id")
        if did:
            rides_by_driver[did] = rides_by_driver.get(did, 0) + 1

    items: List[Dict[str, Any]] = []
    for row in expiring_rows:
        d = row["driver_row"]
        u = users_map.get(d.get("user_id"))
        items.append(
            {
                "driver_id": d["id"],
                "user_id": d.get("user_id"),
                "name": _user_display_name(u) or d.get("name") or "",
                "first_name": (u or {}).get("first_name") or "",
                "last_name": (u or {}).get("last_name") or "",
                "email": (u or {}).get("email"),
                "phone": (u or {}).get("phone") or d.get("phone"),
                # Driver photo = users.profile_image (no drivers photo column).
                "profile_photo_url": (u or {}).get("profile_image"),
                "status": d.get("status"),
                "service_area_id": d.get("service_area_id"),
                "service_area_name": areas_map.get(d.get("service_area_id")),
                "doc_type": row["doc_type"],
                "doc_label": row["doc_label"],
                "doc_field": row["doc_field"],
                "expiry_date": row["expiry_date"],
                "days_remaining": row["days_remaining"],
                "rides_last_30d": rides_by_driver.get(d["id"], 0),
                "last_nudged_at": d.get("doc_expiry_warned_at"),
            }
        )

    items.sort(key=lambda r: r["days_remaining"])
    return {"items": items}


class DriverNudgeExpiryRequest(BaseModel):
    doc_type: str  # e.g. "license", "insurance" — matches _EXPIRY_FIELDS prefix
    doc_label: Optional[str] = None
    custom_message: Optional[str] = None


@router.post("/drivers/{driver_id}/nudge-expiry")
async def admin_nudge_driver_expiry(
    driver_id: str,
    body: DriverNudgeExpiryRequest,
    admin: dict = Depends(get_admin_user),
):
    """Send a manual renewal-reminder push to a driver.

    The automated warner in `utils/document_expiry.py` already pushes on
    a schedule; this endpoint lets ops nudge a specific high-value driver
    earlier without waiting for the next cron tick. Updates
    `doc_expiry_warned_at` so the automatic loop's 24h throttle doesn't
    re-fire and double-notify. Audit-logs every nudge.
    """
    drv = await db_supabase.get_driver_by_id(driver_id)
    if not drv:
        raise HTTPException(status_code=404, detail="Driver not found")
    user_id = drv.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Driver has no linked user account")

    field = f"{body.doc_type}_expiry_date"
    doc_label = body.doc_label or _EXPIRY_FIELDS.get(field) or body.doc_type.replace("_", " ").title()

    expiry_iso = drv.get(field)
    days_text = ""
    if expiry_iso:
        exp_dt = parse_iso_utc(expiry_iso)
        if exp_dt:
            days_left = max(0, (exp_dt - datetime.now(timezone.utc)).days)
            days_text = f" in {days_left} day{'s' if days_left != 1 else ''}" if days_left > 0 else " today"

    title = f"Renew your {doc_label}"
    body_text = body.custom_message or (
        f"Your {doc_label} expires{days_text}. Please upload a current copy to keep driving."
    )

    try:
        await send_push_notification(
            user_id,
            title,
            body_text,
            data={
                "type": "document_expiry_nudge",
                "driver_id": driver_id,
                "doc_type": body.doc_type,
            },
        )
    except Exception as exc:
        logger.error(
            "Expiry nudge push failed for driver %s doc %s",
            driver_id,
            body.doc_type,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="Notification service unavailable") from exc

    try:
        await db_supabase.update_one(
            "drivers",
            {"id": driver_id},
            {"doc_expiry_warned_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.warning(
            "Could not update doc_expiry_warned_at for driver %s after nudge",
            driver_id,
            exc_info=True,
        )

    await log_admin_action(
        admin,
        "driver_expiry_nudge",
        "drivers",
        driver_id,
        {"doc_type": body.doc_type, "doc_label": doc_label, "has_custom_message": bool(body.custom_message)},
    )
    await _log_driver_activity(
        driver_id,
        "expiry_nudge_sent",
        f"Renewal reminder sent: {doc_label}",
        body.custom_message or "",
        {"doc_type": body.doc_type, "doc_label": doc_label},
    )

    return {"ok": True}


@router.put("/drivers/{driver_id}")
async def admin_update_driver(driver_id: str, updates: Dict[str, Any], admin: dict = Depends(get_admin_user)):
    """Update driver details from admin dashboard.

    Editable identity fields are spread across two tables: account-level
    fields (``email``, ``gender``) live ONLY on ``users``, vehicle/compliance
    fields live ONLY on ``drivers``, and a few (``first_name``, ``last_name``,
    ``phone``) are mirrored on both. The admin dashboard surfaces all of them
    on a single driver row, so this handler must route each field to the table
    it actually exists on — writing ``email`` to ``drivers`` raises
    PGRST204 ("Could not find the 'email' column of 'drivers'") -> 500.
    """
    # Fields that live on the `users` account row.
    user_fields = {"first_name", "last_name", "email", "phone", "gender"}
    # Subset that exists ONLY on `users` (no mirror on `drivers`): these
    # require a linked account row to persist at all.
    user_only_fields = {"email", "gender"}
    # Fields that live on the `drivers` row (`city` is drivers-only; the
    # name/phone columns are mirrored from users for forward-compat — see
    # migration 63_phase3b_field_alignment.sql).
    driver_fields = {
        "first_name",
        "last_name",
        "phone",
        "city",
        "service_area_id",
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
        "date_of_birth",
        "license_class",
        "sgi_approved",
        "sgi_approved_at",
        "regulatory_authority",
        "regulatory_region",
        "regulatory_authority_approved",
        "regulatory_authority_approved_at",
        "work_authorization_status",
        "is_permanent_resident",
        "is_citizen",
        "decals_sent",
        "decals_sent_at",
    }
    allowed = user_fields | driver_fields
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    existing = await db_supabase.get_driver_by_id(driver_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

    user_updates = {k: v for k, v in filtered.items() if k in user_fields}
    driver_updates = {k: v for k, v in filtered.items() if k in driver_fields}

    # These drivers columns are `TEXT NOT NULL DEFAULT ''` (supabase_schema.sql),
    # but the admin form posts an empty/absent vehicle field as JSON null.
    # Writing null violates the not-null constraint (23502) and 500s the whole
    # edit for any driver without full vehicle details (e.g. a pending driver who
    # hasn't entered a vehicle yet). Coalesce an explicit null back to the column
    # default so clearing a field stores '' instead of blowing up the update.
    for _col in ("vehicle_make", "vehicle_model", "vehicle_color", "license_plate"):
        if _col in driver_updates and driver_updates[_col] is None:
            driver_updates[_col] = ""

    # Keep the legacy `drivers.name` atom in sync when either name part changes,
    # since enrichment falls back to it when there is no linked user row.
    # Coalesce explicit JSON nulls to "" so a cleared part never renders as the
    # literal "None" in the rebuilt name.
    if "first_name" in driver_updates or "last_name" in driver_updates:
        new_first = driver_updates.get("first_name", existing.get("first_name")) or ""
        new_last = driver_updates.get("last_name", existing.get("last_name")) or ""
        driver_updates["name"] = f"{new_first} {new_last}".strip()

    # `work_authorization_status` is the single field an admin picks; the
    # `is_citizen` / `is_permanent_resident` columns are strictly derived from
    # it. They are still accepted on their own (bulk-import back-compat, and
    # older clients), but whenever the status is present it WINS — previously
    # this used setdefault, which let a stale explicit boolean contradict the
    # status the operator had just chosen.
    if "work_authorization_status" in driver_updates:
        raw_status = driver_updates.get("work_authorization_status")
        status = str(raw_status or "").strip().lower()
        if status and status not in WORK_AUTHORIZATION_CHOICES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid work_authorization_status. Must be one of: "
                    f"{', '.join(sorted(WORK_AUTHORIZATION_CHOICES))}"
                ),
            )
        # Normalize "" / "unknown" to NULL so the column has one empty spelling.
        driver_updates["work_authorization_status"] = None if status in ("", "unknown") else status
        driver_updates.update(derived_work_authorization_flags(status))

    user_id = existing.get("user_id")
    # email/gender exist ONLY on `users`, so they cannot be persisted without a
    # linked account row — surface loudly rather than silently dropping them.
    # Mirrored fields (first/last name, phone) also live on `drivers`, so an
    # orphaned driver row can still be edited for those via the drivers write.
    user_only_updates = {k: v for k, v in user_updates.items() if k in user_only_fields}
    if user_only_updates and not user_id:
        raise HTTPException(
            status_code=409,
            detail="Driver has no linked user account; cannot update email/gender.",
        )

    try:
        # Write the account row first: list/stats views prefer the user row
        # over the driver mirror, so if the second write fails the surviving
        # state is the canonical one, not a stale mirror.
        if user_updates and user_id:
            await db_supabase.update_one("users", {"id": user_id}, user_updates)
        if driver_updates:
            # license_number is Vault-encrypted at rest (_VAULT_PII_FIELDS,
            # routes/drivers/_shared.py) -- must be encrypted before every
            # write, same as the self-serve profile-update and bulk-import
            # paths. This admin route previously wrote it as plaintext.
            await db_supabase.update_one("drivers", {"id": driver_id}, await _encrypt_driver_pii(driver_updates))
    except HTTPException:
        raise
    except Exception as e:
        # B-P3-leak-cleanup: full traceback to logs, generic detail
        # to client. Supabase / postgrest errors carry table internals.
        logger.exception(f"Failed to update driver {driver_id}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update driver.",
        ) from e
    # Append-only vehicle/identity change history (SGI/insurance audit).
    if driver_updates:
        try:
            from ...utils.vehicle_history import record_vehicle_changes
        except ImportError:
            from utils.vehicle_history import record_vehicle_changes  # type: ignore
        await record_vehicle_changes(
            driver_id, existing, driver_updates, changed_by_user_id=admin.get("id"), role="admin"
        )

    await log_admin_action(
        admin,
        "driver_updated",
        "drivers",
        driver_id,
        {"updated_fields": list(filtered.keys())},
    )
    return {"message": "Driver updated", "updated_fields": list(filtered.keys())}


def _fire_driver_approved(driver: dict) -> None:
    """Queue the Meta DriverApproved send. Never raises into the admin action.

    An admin approving a driver must always succeed and always be audit-logged;
    a Meta problem cannot be allowed to 500 that request or skip the audit
    write that follows it.

    The driver's contact details for Advanced Matching are loaded inside the
    spawned coroutine rather than here, so the admin response is not held up by
    a users lookup.
    """
    try:
        from ...services import meta_conversions_service as _meta
        from ...utils.background import spawn as _spawn
    except ImportError:
        try:
            from services import meta_conversions_service as _meta  # type: ignore
            from utils.background import spawn as _spawn  # type: ignore
        except ImportError:
            logger.error("meta: conversions service unavailable — skipping DriverApproved", exc_info=True)
            return

    async def _send() -> None:
        user: dict = {"id": driver.get("user_id")}
        try:
            fetched = await db_supabase.get_user_by_id(driver.get("user_id")) if driver.get("user_id") else None
            if fetched:
                user = fetched
        except Exception:
            # Send anyway with external_id only — a lower match quality is
            # better than a missing conversion.
            logger.error("meta: could not load user for DriverApproved", exc_info=True)
        await _meta.send_driver_approved(driver, user)

    try:
        _spawn(_send())
    except Exception:
        logger.error("meta: failed to queue DriverApproved for driver %s", driver.get("id"), exc_info=True)


@router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest, admin: dict = Depends(get_admin_user)):
    """Verify or unverify a driver.

    NOTE: the Supabase `drivers` table in production was created from
    supabase_schema.sql, which has no `updated_at` (and no `verified_at`)
    column on `drivers`. Writing either triggers PGRST204 -> 500 (which
    previously escaped CORSMiddleware and surfaced in the browser as a CORS
    error). Only set columns that actually exist on the table.
    """
    try:
        # First check if driver exists
        existing_driver = await db_supabase.get_driver_by_id(driver_id)
        if not existing_driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

        update_fields: Dict[str, Any] = {"is_verified": req.verified}
        # Clear needs_review when admin verifies (re-approves)
        if req.verified:
            update_fields["needs_review"] = False
        await db_supabase.update_one("drivers", {"id": driver_id}, update_fields)
    except HTTPException:
        raise
    except Exception as e:
        # B-P3-leak-cleanup: full traceback to logs, generic detail
        # to client.
        logger.exception(f"Failed to update driver {driver_id} verify flag")
        raise HTTPException(
            status_code=500,
            detail="Failed to update driver.",
        ) from e
    # G4: Notify the driver so they know their verification status changed
    # without having to manually check the Documents screen. Routed through the
    # shared policy (copy unchanged) so it picks up the deleted_at recipient
    # guard it was missing and now also reaches email — this endpoint used to
    # send its own push directly, which is why it was the one documented
    # exception in docs/driver-lifecycle-status-flow.md.
    await notify_driver_status_change(existing_driver, verification_message(req.verified), f"verify:{req.verified}")

    # Meta DriverApproved. CAPI-only: approval happens in the admin dashboard,
    # never on the driver's device, so there is no client event to de-duplicate
    # against and no shared event_id. Fires on approve only — an unverify is
    # not a conversion, and the service's dedup_key means a re-approve after an
    # unverify does not fire a second time.
    if req.verified:
        _fire_driver_approved(existing_driver)

    await log_admin_action(admin, "driver_verified", "drivers", driver_id, {"verified": req.verified})
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


@router.post("/drivers/{driver_id}/photo-review")
async def admin_review_driver_photo(
    driver_id: str, req: DriverPhotoReviewRequest, admin: dict = Depends(get_admin_user)
):
    """Approve or reject a driver's profile photo.

    The photo lives on the user row (users.profile_image, status in
    profile_image_status). Driver photos upload as 'pending_review' and stay
    hidden from riders until an admin approves them here.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")
    user_id = driver.get("user_id")
    if not user_id:
        raise HTTPException(status_code=422, detail="Driver has no linked user account")

    new_status = "approved" if req.action == "approve" else "rejected"
    await db_supabase.update_one("users", {"id": user_id}, {"profile_image_status": new_status})

    # Tell the driver their photo was reviewed (best-effort).
    try:
        if req.action == "approve":
            await send_push_notification(
                user_id, "Photo approved ✅", "Your profile photo is now visible to riders.", {"type": "photo_approved"}
            )
        else:
            await send_push_notification(
                user_id,
                "Photo needs attention ⚠️",
                "Your profile photo wasn't approved. Please upload a clear photo of yourself.",
                {"type": "photo_rejected"},
            )
    except Exception as e:
        logger.warning(f"[ADMIN] photo-review push failed for driver {driver_id}: {e}")

    await log_admin_action(admin, "driver_photo_review", "drivers", driver_id, {"status": new_status})
    return {"message": f"Photo {new_status}", "profile_image_status": new_status}


@router.post("/drivers/{driver_id}/photo")
async def admin_upload_driver_photo(
    driver_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Upload a driver's profile photo on their behalf.

    The photo lives on users.profile_image. Because an admin is uploading it
    (identity already vetted through onboarding), it is stored 'approved'
    directly rather than entering the pending_review moderation queue.
    """
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/gif"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="File must be an image (JPEG, PNG, WebP, or GIF)")

    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")
    user_id = driver.get("user_id")
    if not user_id:
        raise HTTPException(status_code=422, detail="Driver has no linked user account")

    content = await file.read()
    if not isinstance(content, bytes):
        content = bytes(content) if hasattr(content, "__bytes__") else str(content).encode("utf-8")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be smaller than 5MB")

    profile_value = await store_profile_image(user_id, content, file.content_type)
    await db_supabase.update_one(
        "users",
        {"id": user_id},
        {"profile_image": profile_value, "profile_image_status": "approved"},
    )

    await log_admin_action(admin, "driver_photo_upload", "drivers", driver_id, {"status": "approved"})
    return {"message": "Photo uploaded", "profile_image": profile_value, "profile_image_status": "approved"}


@router.get("/drivers/{driver_id}/vehicle-history")
async def admin_driver_vehicle_history(driver_id: str, admin: dict = Depends(get_admin_user)):
    """Append-only before/after history of this driver's vehicle/identity changes."""
    rows = await db_supabase.get_rows(
        "driver_vehicle_history", {"driver_id": driver_id}, order="created_at", desc=True, limit=200
    )
    return {"history": rows or []}


@router.post("/drivers/{driver_id}/action")
async def admin_driver_action(driver_id: str, req: DriverActionRequest, admin: dict = Depends(get_admin_user)):
    """Perform a lifecycle action on a driver.

    Actions: approve, reject, suspend, ban, unban, reactivate.
    Each action transitions the driver to the appropriate state and
    records the reason + timestamp for audit trail.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    current_status = driver.get("status", "pending")
    now = datetime.now(timezone.utc).isoformat()
    updates: Dict[str, Any] = {"updated_at": now}

    if req.action == "approve":
        # Approve → Active: driver can go online
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["rejection_reason"] = None
        updates["verified_at"] = now

    elif req.action == "reject":
        # Reject → Rejected: application declined, driver cannot go online.
        # This branch was missing entirely — `reject` was accepted by
        # DriverActionRequest but fell through to the `else` below and returned
        # 400 "Unknown action: reject", so `rejected` was unreachable via this
        # endpoint (and via status-override, whose `valid` set omitted it).
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when rejecting")
        updates["status"] = "rejected"
        updates["is_verified"] = False
        updates["rejection_reason"] = req.reason
        updates["is_online"] = False
        updates["is_available"] = False

    elif req.action == "suspend":
        # Suspend: temporarily disable, store reason
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when suspending")
        updates["status"] = "suspended"
        updates["suspension_reason"] = req.reason
        updates["suspended_at"] = now
        updates["is_online"] = False
        updates["is_available"] = False

    elif req.action == "ban":
        # Ban: permanently block, store reason
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when banning")
        updates["status"] = "banned"
        updates["is_verified"] = False
        updates["ban_reason"] = req.reason
        updates["banned_at"] = now
        updates["is_online"] = False
        updates["is_available"] = False

    elif req.action == "unban":
        # Unban → Active
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["ban_reason"] = None
        updates["banned_at"] = None
        updates["unban_reason"] = req.reason
        updates["unbanned_at"] = now

    elif req.action == "reactivate":
        # Reactivate from suspended → Active
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["suspension_reason"] = None
        updates["suspended_at"] = None

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, updates)
    except Exception as e:
        logger.error(f"Failed driver action {req.action} on {driver_id}: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.") from e

    logger.info(f"[ADMIN] Driver {driver_id} action={req.action} reason={req.reason}")

    # Auto-log to activity timeline
    action_titles = {
        "approve": "Driver Approved",
        "reject": "Application Rejected",
        "suspend": "Driver Suspended",
        "ban": "Driver Banned",
        "unban": "Driver Unbanned",
        "reactivate": "Driver Reactivated",
    }
    await _log_driver_activity(
        driver_id,
        req.action,
        action_titles.get(req.action, f"Action: {req.action}"),
        req.reason or "",
        {
            "old_status": current_status,
            "new_status": updates.get("status"),
            "reason": req.reason,
        },
    )
    audit_id = await log_admin_action(
        admin,
        f"driver_{req.action}",
        "drivers",
        driver_id,
        {
            "action": req.action,
            "reason": req.reason,
            "old_status": current_status,
            "new_status": updates.get("status"),
        },
    )

    # G4: Notify the driver about their status change. Critical for
    # approve/reject/suspend — without this, drivers wait days not knowing
    # their application was processed. Copy and delivery tier live in
    # utils/driver_status_notifications so the status-override endpoint and the
    # driver-triggered needs_review paths use the same policy.
    await notify_driver_status_change(driver, action_message(req.action, req.reason), req.action)

    return {
        "message": f"Driver {req.action}d successfully",
        "new_status": updates.get("status", current_status),
        "audit_log_id": audit_id,
    }


@router.put("/drivers/{driver_id}/status-override")
async def admin_override_driver_status(
    driver_id: str, req: DriverStatusOverride, admin: dict = Depends(get_admin_user)
):
    """Manually move a driver to any status. Use with caution."""
    valid = {"pending", "active", "needs_review", "rejected", "suspended", "banned"}
    if req.status not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid)}",
        )

    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc).isoformat()
    updates: Dict[str, Any] = {"status": req.status, "updated_at": now}

    # Sync is_verified with status
    updates["is_verified"] = req.status == "active"

    # Take offline if not active
    if req.status != "active":
        updates["is_online"] = False
        updates["is_available"] = False

    if req.reason:
        if req.status == "suspended":
            updates["suspension_reason"] = req.reason
        elif req.status == "banned":
            updates["ban_reason"] = req.reason
        elif req.status == "rejected":
            updates["rejection_reason"] = req.reason

    await db_supabase.update_one("drivers", {"id": driver_id}, updates)
    logger.info(f"[ADMIN] Driver {driver_id} status overridden to {req.status} reason={req.reason}")
    await _log_driver_activity(
        driver_id,
        "status_override",
        f"Status changed to {req.status}",
        req.reason or "Manual admin override",
        {
            "old_status": driver.get("status"),
            "new_status": req.status,
            "reason": req.reason,
        },
    )
    await log_admin_action(
        admin,
        "driver_status_override",
        "drivers",
        driver_id,
        {
            "old_status": driver.get("status"),
            "new_status": req.status,
            "reason": req.reason,
        },
    )

    # This endpoint previously notified nobody: an admin could suspend a driver
    # here and the driver would only find out via a 403 the next time they tried
    # to go online. Same policy as the action endpoint, keyed on the status
    # entered rather than an action name.
    if req.status != driver.get("status"):
        await notify_driver_status_change(
            driver, status_message(req.status, req.reason), f"status_override:{req.status}"
        )

    return {"message": f"Driver status set to {req.status}"}


# ── Driver Notes ──


@router.get("/drivers/{driver_id}/notes")
async def admin_get_driver_notes(driver_id: str):
    """Get all notes for a driver, newest first."""
    notes = await db_supabase.get_rows(
        "driver_notes",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=200,
    )
    return notes or []


@router.post("/drivers/{driver_id}/notes")
async def admin_add_driver_note(driver_id: str, req: DriverNoteCreate):
    """Add a note to a driver's record."""
    if not req.note.strip():
        raise HTTPException(status_code=400, detail="Note cannot be empty")
    doc = {
        "id": str(uuid.uuid4()),
        "driver_id": driver_id,
        "note": req.note.strip(),
        "category": req.category,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("driver_notes", doc)
    await _log_driver_activity(
        driver_id,
        "note_added",
        f"Note added ({req.category})",
        req.note[:100],
        {"category": req.category},
    )
    return doc


@router.delete("/drivers/notes/{note_id}")
async def admin_delete_driver_note(note_id: str):
    """Delete a note."""
    await db_supabase.delete_many("driver_notes", {"id": note_id})
    return {"message": "Note deleted"}


# ── Driver Activity Log ──


@router.get("/drivers/{driver_id}/activity")
async def admin_get_driver_activity(driver_id: str, limit: int = 100):
    """Get full activity timeline for a driver, newest first."""
    activities = await db_supabase.get_rows(
        "driver_activity_log",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=limit,
    )
    return activities or []


@router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(
    driver_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get rides for a driver, enriched with rider_name for the admin slideout.

    The Supabase helper doesn't expose OFFSET natively, so we over-fetch
    `offset + limit` rows and slice in-process. Cheap on the row counts
    we expect per driver; if a single driver ever exceeds 500 rides we
    should switch to a cursor-based scheme keyed by created_at.
    """
    fetch_size = offset + limit
    rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=fetch_size,
    )
    page = rides[offset : offset + limit]

    # Enrich with rider_name so the admin sees who the trip was with —
    # batch-fetched in one query rather than N lookups.
    rider_ids = list({r.get("rider_id") for r in page if r.get("rider_id")})
    _drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, [])

    enriched = []
    for r in page:
        rider = users_map.get(r.get("rider_id"))
        enriched.append({**r, "rider_name": _user_display_name(rider)})

    return {"rides": enriched, "total": len(rides), "offset": offset, "limit": limit}


@router.get("/drivers/{driver_id}/live-stats")
async def admin_get_driver_live_stats(driver_id: str):
    """Live aggregate stats for the admin slideout's QuickStat header.

    The four header cards (Rating / Rides / Earnings / Accept Rate) used to
    read denormalised columns straight off the drivers row, three of which
    were not actually being maintained in production:
      - drivers.total_rides         IS incremented on ride completion ✓
      - drivers.rating              IS updated via rolling average when a
                                    rider calls rate_driver — but the
                                    rating flow has a known P0 crash so
                                    most rows are stuck at the seed value
      - drivers.total_earnings      is never written by any code path
      - drivers.acceptance_rate     is not a column at all

    Computing on demand from the rides table here is cheap (one filter
    scan per driver, bounded by an O(few-hundred) rides per driver for
    the active fleet) and removes the staleness without requiring a
    background-loop rollup or denorm trigger.
    """
    rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id},
        limit=5000,
    )

    completed = [r for r in rides if r.get("status") == "completed"]
    total_assigned = len(rides)
    completed_count = len(completed)

    # driver_earnings is the post-platform-fee amount the driver actually
    # gets — same field the rider receipt + driver payout summary uses.
    total_earnings = float(sum(Decimal(str(r.get("driver_earnings") or 0)) for r in completed))

    rated = [r for r in completed if (r.get("rider_rating") or 0) > 0]
    avg_rating = round(sum(float(r["rider_rating"]) for r in rated) / len(rated), 2) if rated else None

    # Acceptance rate: same formula as routes/admin/analytics.py uses for
    # the rankings page (completed / total_assigned). Approximate — a true
    # rate would compare against offers sent, not assigned rides — but
    # it's the same definition operators already see elsewhere.
    acceptance_rate = round((completed_count / total_assigned) * 100, 1) if total_assigned > 0 else None

    cancelled_by_driver = sum(
        1 for r in rides if r.get("status") == "cancelled" and "driver" in (r.get("cancellation_reason") or "").lower()
    )

    # The detail slideout reads the driver's avatar from here rather than the
    # bulk drivers list, which no longer ships profile_image (see
    # admin_get_drivers). One targeted lookup on open keeps the heavy
    # base64/URL blob off the list payload while still showing the photo.
    photo_url = None
    drv = await db_supabase.get_driver_by_id(driver_id)
    if drv and drv.get("user_id"):
        user = await db_supabase.get_user_by_id(drv["user_id"])
        photo_url = (user or {}).get("profile_image")

    # Licence number is Vault-encrypted at rest, so the bulk drivers list only
    # ever carries the opaque token. Decrypt the single selected driver here and
    # ship ONLY the last 4 — enough for an admin to confirm which licence is on
    # file (and to see that one exists before editing it) without the full
    # number crossing the wire. Same masking rule the drivers CSV export uses.
    license_number_last4 = None
    if drv and drv.get("license_number"):
        token = str(drv["license_number"])
        try:
            plain = await _vault_decrypt(token, "license_admin_detail")
        except Exception:
            # _vault_decrypt already logs; a decrypt problem must not take down
            # the whole stats card.
            plain = None
        # _vault_decrypt returns the raw token unchanged when it cannot decrypt.
        license_number_last4 = _mask_license_number(plain) if plain and plain != token else None

    return {
        "total_rides": completed_count,
        "total_earnings": total_earnings,
        "avg_rating": avg_rating,
        "acceptance_rate": acceptance_rate,
        "cancelled_by_driver": cancelled_by_driver,
        "total_assigned": total_assigned,
        "photo_url": photo_url,
        "license_number_last4": license_number_last4,
        "license_number_on_file": bool(drv and drv.get("license_number")),
    }


# ── Referral admin views ─────────────────────────────────────────────
# Reuse the reward terms defined in routes.drivers so the admin views can never
# disagree with what the driver app shows. Lazy import to avoid a circular
# import at module load (routes.drivers pulls in a lot).
def _referral_terms() -> tuple[int, int]:
    try:
        from ..drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED
    except ImportError:  # module-path fallback (python -m backend.server vs top-level)
        from routes.drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED  # type: ignore
    return REFERRAL_RIDES_REQUIRED, REFERRAL_REWARD_AMOUNT


def _driver_referral_codes(driver: dict) -> list:
    """Every code a driver may have been shared under (current + legacy), so
    referees who applied an older code still count."""
    out: list = []
    for c in (driver.get("driver_code"), driver.get("referral_code"), f"DRIVER{driver['id'][:8].upper()}"):
        if c and c not in out:
            out.append(c)
    return out


def _driver_referral_code(driver: dict) -> str:
    """The primary shareable code (driver_code → referral_code → id-derived)."""
    return _driver_referral_codes(driver)[0]


async def _driver_referral_summary(driver: dict, *, include_referees: bool) -> dict:
    """Compute a referrer's referral stats (and optionally the referee list)."""
    # Per-area terms for THIS driver (the referrer), so the admin modal matches
    # the driver app and the payout loop instead of showing the global default.
    terms = await resolve_referral_terms(driver.get("service_area_id"), "driver")
    rides_required = terms["rides"]
    reward_amount = terms["referrer"]
    codes = _driver_referral_codes(driver)
    code = codes[0]

    referred_users = await db_supabase.get_rows(
        "users",
        {"referral_code_used": {"$in": codes}},
        columns="id,first_name,last_name,email,created_at",
        limit=200,
    )

    referees: list[dict] = []
    qualified = 0
    for u in referred_users:
        ref_drv = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": u["id"]}, limit=1)
        )
        completed = 0
        if ref_drv:
            completed = await db_supabase.count_documents("rides", {"driver_id": ref_drv["id"], "status": "completed"})
        is_qualified = bool(ref_drv) and completed >= rides_required
        if is_qualified:
            qualified += 1
        if include_referees:
            referees.append(
                {
                    "name": f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Driver",
                    "email": u.get("email", ""),
                    "referred_at": u.get("created_at", ""),
                    "is_driver": bool(ref_drv),
                    "completed_rides": completed,
                    "rides_required": rides_required,
                    "rides_remaining": max(0, rides_required - completed),
                    "qualified": is_qualified,
                    "status": "earned" if is_qualified else "in_progress",
                }
            )

    total = len(referred_users)
    # Earned total from snapshotted PAID payouts (won't change retroactively when
    # area terms change); estimate fallback until a payout has been paid.
    paid = await paid_referral_earnings(driver["user_id"], "driver") if driver.get("user_id") else None
    earnings = paid if paid is not None else (qualified * reward_amount)

    # Who referred THIS driver (inbound) — resolve users.referred_by (= the
    # referrer's driver id for driver referrals) to a name + code. None when this
    # driver wasn't referred (or was referred via a rider code → not a driver row).
    referred_by = None
    me = (
        (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("users", {"id": driver.get("user_id")}, columns="referred_by", limit=1)
        )
        if driver.get("user_id")
        else None
    )
    ref_drv_id = (me or {}).get("referred_by")
    if ref_drv_id:
        ref_drv = await db_supabase.get_driver_by_id(ref_drv_id)
        if ref_drv:
            ref_user = await db_supabase.get_user_by_id(ref_drv.get("user_id")) if ref_drv.get("user_id") else None
            referred_by = {
                "name": f"{(ref_user or {}).get('first_name', '')} {(ref_user or {}).get('last_name', '')}".strip()
                or ref_drv.get("name")
                or "Driver",
                "code": _driver_referral_code(ref_drv),
            }

    summary = {
        "referral_code": code,
        "total_referrals": total,
        "qualified_referrals": qualified,
        "pending_referrals": total - qualified,
        "referral_earnings": earnings,
        "reward_amount": reward_amount,
        "rides_required": rides_required,
        "referred_by": referred_by,
    }
    if include_referees:
        summary["referees"] = referees
    return summary


@router.get("/drivers/{driver_id}/referrals")
async def admin_get_driver_referrals(driver_id: str, admin: dict = Depends(get_admin_user)):
    """Referees a specific driver brought in, with per-referee reward progress."""
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return await _driver_referral_summary(driver, include_referees=True)


@router.get("/drivers/{driver_id}/training")
async def admin_get_driver_training(
    driver_id: str,
    refresh: bool = Query(False),
    admin: dict = Depends(get_admin_user),
):
    """Driver's training record from the external LMS, matched by phone number.

    Returns {matched, phone_last4, lms: <LMS payload data>} where lms carries
    registration status, completion percentage, certificates, and history
    (quiz attempts + reminder communications). matched=false when the phone
    has no LMS driver record or the driver has no usable phone on file.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    user = await db_supabase.get_user_by_id(driver["user_id"]) if driver.get("user_id") else None
    phone = (user or {}).get("phone") or driver.get("phone") or ""

    normalized = lms_service.normalize_phone(phone)
    if not normalized:
        return {"matched": False, "reason": "no_phone", "phone_last4": None, "lms": None}

    try:
        payload = await lms_service.get_training_by_phone(phone, force_refresh=refresh)
    except lms_service.LMSNotConfiguredError as e:
        raise HTTPException(status_code=503, detail="LMS integration is not configured") from e
    except lms_service.LMSUpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {
        "matched": bool(payload.get("matched")),
        "reason": None if payload.get("matched") else "not_found_in_lms",
        "phone_last4": normalized[-4:],
        "lms": payload.get("data"),
    }


@router.get("/referrals/leaderboard")
async def admin_get_referral_leaderboard(
    limit: int = Query(20, ge=1, le=100),
    admin: dict = Depends(get_admin_user),
):
    """Fleet-wide top referrers, with fleet totals.

    Driven by the referral_payouts ledger (kind='driver') keyed on
    referrer_user_id — the same source of truth as /referrals/analytics. The old
    version tallied users.referred_by → the referrer's DRIVER id and did
    `if not drv: continue`, so a referrer whose driver row was deleted/soft-deleted
    silently vanished from the board (the reported "driver referrers missing in
    admin" bug). The ledger holds the USER id, so the referrer always resolves and
    earnings are the actual snapshotted paid amounts. This counts ledger claims
    (qualified / in-pipeline referrals), not every raw signup — the signup funnel
    lives in /referrals/analytics.
    """
    rides_required, reward_amount = _referral_terms()

    def _money(x) -> Decimal:
        try:
            return Decimal(str(x))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    claims = await db_supabase.get_rows(
        "referral_payouts",
        {"kind": "driver"},
        columns="referrer_user_id,referrer_reward,status",
        limit=20000,
    )
    by_referrer: dict[str, dict] = {}
    for c in claims:
        ruid = c.get("referrer_user_id")
        if not ruid:
            continue
        agg = by_referrer.setdefault(ruid, {"total": 0, "qualified": 0, "earnings": Decimal("0")})
        agg["total"] += 1
        if c.get("status") == "paid":
            agg["qualified"] += 1
            agg["earnings"] += _money(c.get("referrer_reward"))

    fleet_total_referrals = sum(a["total"] for a in by_referrer.values())
    top = sorted(by_referrer.items(), key=lambda kv: kv[1]["total"], reverse=True)[:limit]

    leaders: list[dict] = []
    for ref_user_id, agg in top:
        ref_user = await db_supabase.get_user_by_id(ref_user_id)
        drv = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": ref_user_id}, limit=1)
        )
        name = (
            f"{(ref_user or {}).get('first_name', '')} {(ref_user or {}).get('last_name', '')}".strip()
            or (drv or {}).get("name")
            or "Driver"
        )
        leaders.append(
            {
                "driver_id": (drv or {}).get("id"),
                "driver_code": _driver_referral_code(drv) if drv else None,
                "name": name,
                "total_referrals": agg["total"],
                "qualified_referrals": agg["qualified"],
                "referral_earnings": agg["earnings"],
            }
        )

    return {
        "leaders": leaders,
        "fleet_total_referrals": fleet_total_referrals,
        "fleet_total_referrers": len(by_referrer),
        "reward_amount": reward_amount,
        "rides_required": rides_required,
    }


@router.get("/referrals/rider-leaderboard")
async def admin_get_rider_referral_leaderboard(
    limit: int = Query(20, ge=1, le=100),
    admin: dict = Depends(get_admin_user),
):
    """Fleet-wide top RIDER referrers (riders referring riders). Same response
    shape as the driver leaderboard so the admin UI component is shared."""
    try:
        from ..users import (  # type: ignore
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )
    except ImportError:
        from routes.users import (  # type: ignore
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )

    users = await db_supabase.get_rows(
        "users", {}, columns="id,referral_code_used,referred_by,first_name,last_name", limit=10000
    )
    by_referrer: dict[str, list[str]] = {}
    name_by_id: dict[str, str] = {}
    for u in users:
        name_by_id[u["id"]] = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Rider"
        code = u.get("referral_code_used")
        rid = u.get("referred_by")
        # Rider referrals use a RIDE-prefixed code and store the referrer's user id.
        if code and str(code).upper().startswith("RIDE") and rid:
            by_referrer.setdefault(rid, []).append(u["id"])

    fleet_total = sum(len(v) for v in by_referrer.values())
    top = sorted(by_referrer.items(), key=lambda kv: len(kv[1]), reverse=True)[:limit]

    leaders: list[dict] = []
    for referrer_id, referee_ids in top:
        qualified = 0
        for ride_user_id in referee_ids:
            completed = await db_supabase.count_documents("rides", {"rider_id": ride_user_id, "status": "completed"})
            if completed >= RIDER_REFERRAL_RIDES_REQUIRED:
                qualified += 1
        # Snapshotted PAID earnings when available; estimate fallback otherwise.
        paid = await paid_referral_earnings(referrer_id, "rider")
        leader_earnings = paid if paid is not None else (qualified * RIDER_REFERRER_REWARD)
        leaders.append(
            {
                # driver_id/driver_code reuse the shared UI fields — here they
                # carry the referrer's user id and RIDE code.
                "driver_id": referrer_id,
                "driver_code": f"RIDE{referrer_id[:8].upper()}",
                "name": name_by_id.get(referrer_id, "Rider"),
                "total_referrals": len(referee_ids),
                "qualified_referrals": qualified,
                "referral_earnings": leader_earnings,
            }
        )

    return {
        "leaders": leaders,
        "fleet_total_referrals": fleet_total,
        "fleet_total_referrers": len(by_referrer),
        "reward_amount": RIDER_REFERRER_REWARD,
        "rides_required": RIDER_REFERRAL_RIDES_REQUIRED,
    }


@router.get("/referrals/analytics")
async def admin_get_referral_analytics(
    source: str = Query("driver", pattern="^(driver|rider)$"),
    service_area_id: str | None = Query(None),
    start: str | None = Query(None, description="ISO date inclusive lower bound (YYYY-MM-DD)"),
    end: str | None = Query(None, description="ISO date inclusive upper bound (YYYY-MM-DD)"),
    admin: dict = Depends(get_admin_user),
):
    """Referral analytics hub: redemption funnel, amounts paid, and a daily
    redemption trend, filterable by rider/driver, service area, and date.

    Data sources:
      * Funnel + amounts + trend read the referral_payouts ledger (source of
        truth for money actually moved). It carries kind, service_area_id
        (snapshot at claim), status, paid_at and reward amounts, so area/date
        filtering is exact.
      * Top-of-funnel "total_referred" (sign-ups via a code) is tallied from
        users.referred_by. Users aren't area-tagged until they qualify, so this
        line is null when a service-area filter is active (UI shows "—").

    In-memory filtering matches the leaderboard endpoints' scale assumption;
    move to a SQL/RPC rollup if the ledger grows large.
    """

    def _d(x) -> Decimal:
        try:
            return Decimal(str(x if x is not None else 0))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _m(x: Decimal) -> str:
        # ROUND_HALF_UP to match the house money convention (fare_service._round);
        # default banker's rounding would disagree at the half-cent boundary.
        return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _in_range(iso_ts: str | None) -> bool:
        if not iso_ts:
            return start is None  # undated rows only pass when no lower bound
        day = str(iso_ts)[:10]
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True

    # ---- Ledger: redemption funnel, amounts, trend ----
    filt: dict = {"kind": source}
    if service_area_id:
        filt["service_area_id"] = service_area_id
    payouts = await db_supabase.get_rows(
        "referral_payouts",
        filt,
        columns="status,referrer_reward,referee_reward,paid_at,created_at",
        limit=10000,
    )
    in_window = [p for p in payouts if _in_range(p.get("created_at"))]

    qualified = len(in_window)
    paid_rows = [p for p in in_window if p.get("status") == "paid"]
    redeemed = len(paid_rows)
    processing = sum(1 for p in in_window if p.get("status") == "processing")
    failed = sum(1 for p in in_window if p.get("status") == "failed")

    # Break the paid total into the referrer vs referee sides so the admin can
    # show ACTUAL referee-side payouts distinctly from the per-area config reward
    # (service-areas page shows the configured $, not what's actually been paid).
    referrer_paid = Decimal("0")
    referee_paid = Decimal("0")
    for p in paid_rows:
        referrer_paid += _d(p.get("referrer_reward"))
        referee_paid += _d(p.get("referee_reward"))
    total_paid = referrer_paid + referee_paid
    avg_paid = (total_paid / redeemed) if redeemed else Decimal("0")

    # Daily redemption trend keyed by paid_at (rows that actually paid).
    trend_map: dict[str, dict] = {}
    for p in paid_rows:
        day = str(p.get("paid_at") or p.get("created_at") or "")[:10]
        if not day:
            continue
        bucket = trend_map.setdefault(day, {"date": day, "redeemed": 0, "paid": Decimal("0")})
        bucket["redeemed"] += 1
        bucket["paid"] += _d(p.get("referrer_reward")) + _d(p.get("referee_reward"))
    trend = [
        {"date": b["date"], "redeemed": b["redeemed"], "paid": _m(b["paid"])}
        for b in sorted(trend_map.values(), key=lambda b: b["date"])
    ]

    # ---- Top of funnel: sign-ups via a referral code (users.referred_by) ----
    total_referred: int | None = None
    if not service_area_id:
        users = await db_supabase.get_rows(
            "users", {}, columns="referred_by,referral_code_used,created_at", limit=10000
        )
        total_referred = 0
        for u in users:
            code = u.get("referral_code_used")
            rid = u.get("referred_by")
            if not (rid and code):
                continue
            is_rider = str(code).upper().startswith("RIDE")
            if (source == "rider") != is_rider:
                continue
            if not _in_range(u.get("created_at")):
                continue
            total_referred += 1

    # A display ratio (not a monetary amount) — float is fine here; do NOT copy
    # this pattern into any fare/payout path, which must stay Decimal.
    redemption_rate = round(redeemed / total_referred, 4) if total_referred else None

    if source == "rider":
        try:
            from ..users import RIDER_REFERRAL_RIDES_REQUIRED, RIDER_REFERRER_REWARD  # type: ignore
        except ImportError:
            from routes.users import RIDER_REFERRAL_RIDES_REQUIRED, RIDER_REFERRER_REWARD  # type: ignore
        rides_required, reward_amount = RIDER_REFERRAL_RIDES_REQUIRED, RIDER_REFERRER_REWARD
    else:
        rides_required, reward_amount = _referral_terms()

    return {
        "source": source,
        "funnel": {
            "total_referred": total_referred,
            "qualified": qualified,
            "redeemed": redeemed,
            "processing": processing,
            "failed": failed,
            "redemption_rate": redemption_rate,
            "total_paid": _m(total_paid),
            "referrer_paid": _m(referrer_paid),
            "referee_paid": _m(referee_paid),
            "avg_paid": _m(avg_paid),
        },
        "trend": trend,
        "reward_amount": reward_amount,
        "rides_required": rides_required,
    }


@router.get("/referrals/failed-claims")
async def admin_get_failed_referral_claims(
    source: str = Query("rider", pattern="^(rider|driver)$"),
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    """Failed referral_payouts claims (e.g. the pre-198 constraint-bug backlog)
    with referrer/referee names + amounts, so ops can see WHICH claims failed and
    re-queue them via POST .../requeue instead of running raw SQL."""
    rows = await db_supabase.get_rows(
        "referral_payouts",
        {"status": "failed", "kind": source},
        columns="id,referrer_user_id,referee_user_id,kind,referrer_reward,referee_reward,created_at",
        order="created_at",
        desc=True,
        limit=limit,
    )
    claims: list[dict] = []
    # Small per-row name lookups, bounded by `limit` — fine for the current fleet.
    for r in rows or []:
        referrer = await db_supabase.get_user_by_id(r["referrer_user_id"]) if r.get("referrer_user_id") else None
        referee = await db_supabase.get_user_by_id(r["referee_user_id"]) if r.get("referee_user_id") else None
        claims.append(
            {
                "id": r.get("id"),
                "referee_user_id": r.get("referee_user_id"),
                "kind": r.get("kind"),
                "referrer_name": _user_display_name(referrer),
                "referee_name": _user_display_name(referee),
                "referrer_reward": r.get("referrer_reward"),
                "referee_reward": r.get("referee_reward"),
                "created_at": r.get("created_at"),
            }
        )
    return {"claims": claims, "total": len(claims)}


@router.post("/referrals/failed-claims/{referee_user_id}/requeue")
async def admin_requeue_failed_referral(referee_user_id: str, admin: dict = Depends(get_admin_user)):
    """Re-credit a FAILED referral claim, SIDE-AWARE.

    Credits ONLY the side(s) not already paid — read from referrer_credited_at /
    referee_credited_at on the row — using the amounts snapshotted at claim time.
    This is the safe fix for the migration-198/199 constraint backlog: when the
    referrer's 'referral_reward' landed but the referee's 'referral_bonus' was
    rejected, the referrer is left untouched and only the referee is paid. The old
    behaviour deleted the row and let the loop re-pay BOTH sides, double-crediting
    an already-paid referrer. Synchronous (no 5-min wait), atomic (claims the row
    failed -> processing), one claim at a time, audit-logged.

    Do NOT use on a claim whose logs show 'ledger write AND its reversal failed' —
    that already moved money while its credited-at stayed NULL, so it looks owed and
    would double-credit. Those need manual reconciliation.
    """
    try:
        result = await recredit_failed_claim(referee_user_id)
    except ReferralClaimNotFound:
        raise HTTPException(status_code=404, detail="No failed claim for this referee") from None
    await log_admin_action(
        admin,
        "referral_claim_requeued",
        "referral_payouts",
        result["id"],
        {"referee_user_id": referee_user_id, "kind": result.get("kind"), "credited": result.get("credited")},
    )
    return {"success": True, "requeued": referee_user_id, "credited": result.get("credited", [])}


@router.get("/referrals/pairs")
async def admin_get_referral_pairs(
    source: str = Query("driver", pattern="^(rider|driver)$"),
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    """Referrer→referee pairs from the referral_payouts ledger (who referred whom)
    with names, status, and amounts — so the admin sees the relationships, not
    just aggregate leaderboard counts."""
    rows = await db_supabase.get_rows(
        "referral_payouts",
        {"kind": source},
        columns="id,referrer_user_id,referee_user_id,status,referrer_reward,referee_reward,created_at",
        order="created_at",
        desc=True,
        limit=limit,
    )
    pairs: list[dict] = []
    for r in rows or []:
        referrer = await db_supabase.get_user_by_id(r["referrer_user_id"]) if r.get("referrer_user_id") else None
        referee = await db_supabase.get_user_by_id(r["referee_user_id"]) if r.get("referee_user_id") else None
        pairs.append(
            {
                "id": r.get("id"),
                "referrer_name": _user_display_name(referrer),
                "referee_name": _user_display_name(referee),
                "status": r.get("status"),
                "referrer_reward": r.get("referrer_reward"),
                "referee_reward": r.get("referee_reward"),
                "created_at": r.get("created_at"),
            }
        )
    return {"pairs": pairs, "total": len(pairs)}


@router.get("/drivers/{driver_id}/payouts-summary")
async def admin_get_driver_payouts_summary(driver_id: str, limit: int = Query(50, ge=1, le=200)):
    """Comprehensive payout view for the driver slideout's Payouts tab.

    Returns the same operational picture an Uber/Lyft fleet-ops admin needs
    when investigating an earnings or payout question:

      - summary        — lifetime / paid-out / pending / on-hold / YTD,
                         last payout date+amount, tips
      - payment_method — bank-account preview + Stripe Connect status
      - payouts        — newest-first list (capped by `limit`) ready to
                         render in a table with a Retry action for the
                         failed rows

    Money is computed with Decimal then surfaced as float for the JSON
    layer. The drivers.total_earnings column is ignored on purpose — it's
    never maintained in production (see admin_get_driver_live_stats
    comment for why).
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # ---- Aggregate from rides ----
    rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "status": "completed"},
        limit=10000,
    )

    def _dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x or 0))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    lifetime_ride_earnings = sum((_dec(r.get("driver_earnings")) for r in rides), Decimal("0"))
    lifetime_tips = sum((_dec(r.get("tip_amount")) for r in rides), Decimal("0"))

    # ---- Aggregate from driver_bonuses (quest/referral/adjustment) ----
    bonus_rows = await db_supabase.get_rows(
        "driver_bonuses",
        {"driver_id": driver_id},
        limit=10000,
    )
    total_bonuses = sum((_dec(b.get("amount") or 0) for b in bonus_rows), Decimal("0"))
    lifetime_earnings = lifetime_ride_earnings + total_bonuses

    year_start = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc).isoformat()
    ytd_ride_earnings = sum(
        (
            _dec(r.get("driver_earnings"))
            for r in rides
            if (r.get("ride_completed_at") or r.get("completed_at") or r.get("created_at") or "") >= year_start
        ),
        Decimal("0"),
    )
    ytd_bonuses = sum(
        (_dec(b.get("amount") or 0) for b in bonus_rows if (b.get("created_at") or "") >= year_start),
        Decimal("0"),
    )
    ytd_earnings = ytd_ride_earnings + ytd_bonuses

    # Active days in last 30d — same definition the driver app's "Active
    # days" earnings metric uses (≥1 completed ride on that calendar date).
    thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent_dates = {
        (r.get("ride_completed_at") or r.get("completed_at") or r.get("created_at") or "")[:10]
        for r in rides
        if (r.get("ride_completed_at") or r.get("completed_at") or r.get("created_at") or "") >= thirty_days_ago
    }
    active_days_30d = len([d for d in recent_dates if d])

    # ---- Aggregate from payouts ----
    payouts = await db_supabase.get_rows(
        "payouts",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=max(limit, 200),
    )

    def _sum_by_status(*statuses: str) -> Decimal:
        return sum(
            (_dec(p.get("amount")) for p in payouts if p.get("status") in statuses),
            Decimal("0"),
        )

    total_paid_out = _sum_by_status("completed")
    pending_in_flight = _sum_by_status("pending", "processing")
    on_hold = _sum_by_status("failed")

    # Amount owed to the driver that hasn't been queued for payout yet.
    # Includes ride earnings + bonuses - paid out - in flight, matching
    # the driver-facing balance in routes/drivers/earnings.py.
    pending_balance = max(lifetime_earnings - total_paid_out - pending_in_flight, Decimal("0"))

    last_completed = next((p for p in payouts if p.get("status") == "completed"), None)
    last_failed = next((p for p in payouts if p.get("status") == "failed"), None)

    # ---- Payment method ----
    bank = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver_id}, limit=1)
    )
    stripe_account_id = driver.get("stripe_account_id")
    payment_method = {
        "has_bank_account": bool(bank or stripe_account_id),
        "bank_name": (bank or {}).get("bank_name"),
        "account_last4": (bank or {}).get("account_last4"),
        "account_holder_name": (bank or {}).get("account_holder_name"),
        "account_type": (bank or {}).get("account_type"),
        "is_verified": bool((bank or {}).get("is_verified")) if bank else None,
        "stripe_connected": bool(stripe_account_id),
        # Last 6 of the Stripe account id is enough for an operator to
        # confirm the right connect account is wired up without leaking
        # the full id (acct_xxx) into the admin frontend.
        "stripe_account_hint": (stripe_account_id[-6:] if stripe_account_id else None),
    }

    # Stripe Connect KYC + tax identity mirror (migration 92). These
    # columns are populated by the account.updated webhook handler in
    # services/stripe_kyc_sync.py and refreshed on demand via the
    # /refresh-stripe-kyc endpoint below.
    kyc = {
        "details_submitted": bool(driver.get("stripe_details_submitted")),
        "charges_enabled": bool(driver.get("stripe_charges_enabled")),
        "payouts_enabled": bool(driver.get("stripe_payouts_enabled")),
        "verification_status": driver.get("stripe_verification_status"),
        "business_type": driver.get("stripe_business_type"),
        "id_number_provided": bool(driver.get("stripe_id_number_provided")),
        "id_number_last4": driver.get("stripe_id_number_last4"),
        # Canonical column is gst_bn (migration 58). API field name stays
        # gst_hst_number for clarity in the admin UI; the column-level
        # gst_hst_number we briefly added was dropped from migration 92.
        "gst_hst_number": driver.get("gst_bn"),
        "gst_registered": bool(driver.get("gst_registered")),
        "requirements_due": driver.get("stripe_requirements_due") or [],
        "requirements_past_due": driver.get("stripe_requirements_past_due") or [],
        "disabled_reason": driver.get("stripe_disabled_reason"),
        "tos_accepted_at": driver.get("stripe_tos_accepted_at"),
        "last_synced_at": driver.get("stripe_last_synced_at"),
    }

    return {
        "summary": {
            "lifetime_earnings": float(lifetime_earnings),
            "lifetime_ride_earnings": float(lifetime_ride_earnings),
            "lifetime_bonuses": float(total_bonuses),
            "lifetime_tips": float(lifetime_tips),
            "ytd_earnings": float(ytd_earnings),
            "total_paid_out": float(total_paid_out),
            "pending_in_flight": float(pending_in_flight),
            "pending_balance": float(pending_balance),
            "on_hold": float(on_hold),
            "rides_count": len(rides),
            "active_days_30d": active_days_30d,
            "last_payout": (
                {
                    "id": last_completed.get("id"),
                    "amount": float(_dec(last_completed.get("amount"))),
                    "processed_at": last_completed.get("processed_at") or last_completed.get("created_at"),
                    "bank_name": last_completed.get("bank_name"),
                    "account_last4": last_completed.get("account_last4"),
                }
                if last_completed
                else None
            ),
            "last_failed_payout": (
                {
                    "id": last_failed.get("id"),
                    "amount": float(_dec(last_failed.get("amount"))),
                    "error_message": last_failed.get("error_message"),
                    "created_at": last_failed.get("created_at"),
                }
                if last_failed
                else None
            ),
        },
        "payment_method": payment_method,
        "kyc": kyc,
        "payouts": [
            {
                "id": p.get("id"),
                "amount": float(_dec(p.get("amount"))),
                "status": p.get("status"),
                "payout_type": p.get("payout_type"),
                "stripe_transfer_id": p.get("stripe_transfer_id"),
                "stripe_payout_id": p.get("stripe_payout_id"),
                "bank_name": p.get("bank_name"),
                "account_last4": p.get("account_last4"),
                "error_message": p.get("error_message"),
                "created_at": p.get("created_at"),
                "processed_at": p.get("processed_at"),
            }
            for p in payouts[:limit]
        ],
        "bonuses": [
            {
                "id": b.get("id"),
                "amount": float(_dec(b.get("amount") or 0)),
                "kind": b.get("kind"),
                "description": b.get("description"),
                "created_at": b.get("created_at"),
            }
            for b in sorted(bonus_rows, key=lambda x: x.get("created_at") or "", reverse=True)
        ],
    }


@router.post("/drivers/{driver_id}/refresh-stripe-kyc")
async def admin_refresh_driver_kyc(driver_id: str, admin: dict = Depends(get_admin_user)):
    """Pull the latest Stripe Connect KYC state for this driver into our cache.

    Used by the "Refresh from Stripe" button on the Payouts tab. Webhook
    delivery is best-effort (Stripe retries on 5xx for ~3 days) so the
    manual refresh is the operator's escape hatch when status looks stale.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        from ..services.stripe_kyc_sync import refresh_driver_kyc
    except ImportError:
        from services.stripe_kyc_sync import refresh_driver_kyc  # type: ignore

    # Opt in to retiring an account the running key cannot see: this button
    # is the operator's repair path for exactly that case.
    result = await refresh_driver_kyc(driver, retire_if_unreachable=True)
    status = result.get("status")
    await log_admin_action(
        admin,
        "stripe_kyc_refresh",
        "drivers",
        driver_id,
        {"status": status},
    )

    # Every outcome used to return a bare 200 with the raw status dict, so an
    # operator clicking "Refresh from Stripe" got an identical success response
    # whether the sync worked, the key was missing, or Stripe errored — and the
    # dashboard toasted "Synced from Stripe" regardless. A failure that reports
    # success is the error-swallowing CLAUDE.md forbids, so the genuine
    # failures now carry a status code the client cannot mistake.
    if status == "stripe_not_configured":
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured — set stripe_secret_key in Settings before refreshing.",
        )
    if status == "stripe_error":
        raise HTTPException(
            status_code=502,
            detail="Stripe could not be reached. Nothing was changed — try again shortly.",
        )

    # The remaining outcomes are all real answers, not failures — but each one
    # means something different, so say which. The dashboard shows `message`.
    messages = {
        "ok": "Verification status synced from Stripe.",
        "no_stripe_account": (
            "This driver has no Stripe account on file, so there was nothing to refresh. "
            "They need to complete 'Set up payouts' in the driver app."
        ),
        "account_not_on_key": (
            "The driver's Stripe account is not reachable on the current API key "
            "(typically a test/live key change). It has been detached and their payout "
            "details cleared — they must set up payouts again in the driver app."
        ),
    }
    return {
        **result,
        # `synced` is the flag the UI should branch on: only "ok" actually
        # pulled fresh state from Stripe.
        "synced": status == "ok",
        "message": messages.get(status, f"Unexpected sync status: {status}"),
    }


@router.post("/drivers/{driver_id}/refresh-stripe-payouts")
async def admin_refresh_driver_stripe_payouts(driver_id: str, admin: dict = Depends(get_admin_user)):
    """Sync ALL financial data from Stripe for a single driver.

    Pulls Stripe Transfers (platform -> connected account) via the payout sync
    service and materializes any missing ones as ``payouts`` rows. Also triggers
    the connect-ledger sync to pull bank payouts and balance transactions from
    the driver's connected account.

    Returns the full set of synced transfer records with timestamps so the
    admin dashboard can display every payment Stripe knows about for this
    driver, supporting earnings review and T4A generation.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    stripe_account_id = (driver.get("stripe_account_id") or "").strip()
    if not stripe_account_id:
        raise HTTPException(
            status_code=400,
            detail="Driver has no Stripe Connect account on file. They must complete payout setup first.",
        )

    try:
        from ...services import stripe_payout_sync_service as sync_svc
        from ...services.stripe_connect_ledger_service import sync_connect_ledger
        from ...settings_loader import get_app_settings
    except ImportError:
        from services import stripe_payout_sync_service as sync_svc  # type: ignore
        from services.stripe_connect_ledger_service import sync_connect_ledger  # type: ignore
        from settings_loader import get_app_settings  # type: ignore

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured — set stripe_secret_key in Settings.",
        )

    batch = f"admin-refresh-{driver_id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    # 1. Sync Stripe Transfers -> payouts table
    plan = await sync_svc.build_plan(
        stripe_secret,
        batch,
        driver_ids=[driver_id],
    )

    transfers_inserted = 0
    transfers_skipped = 0
    plan_errors = []
    if plan.errors:
        plan_errors = [{"field": e.field, "message": e.message} for e in plan.errors]
        logger.warning("[STRIPE-REFRESH] plan has errors for driver %s: %s", driver_id, plan_errors)
    else:
        try:
            commit_result = await asyncio.to_thread(sync_svc.commit_plan, plan)
            transfers_inserted = commit_result.get("inserted", 0)
            transfers_skipped = commit_result.get("skipped_existing", 0)
        except Exception:
            logger.error("[STRIPE-REFRESH] commit failed for driver %s", driver_id, exc_info=True)
            raise HTTPException(
                status_code=502,
                detail="Failed to write synced transfers to the database. Try again.",
            )

    # 2. Sync connected-account bank payouts + balance transactions
    ledger_result = await sync_connect_ledger(
        stripe_secret,
        driver_ids=[driver_id],
    )

    await log_admin_action(
        admin,
        "stripe_payout_refresh",
        "drivers",
        driver_id,
        {
            "transfers_inserted": transfers_inserted,
            "transfers_skipped": transfers_skipped,
            "payouts_upserted": ledger_result.payouts_upserted,
            "ledger_upserted": ledger_result.ledger_upserted,
        },
    )

    # 3. Read back the full payout history for this driver
    all_payouts = await db_supabase.get_rows(
        "payouts",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=500,
    )

    # 4. Read back synced bank payouts (connected-account -> bank)
    bank_payouts = await db_supabase.get_rows(
        "driver_stripe_payouts",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=500,
    )

    def _dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x or 0))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    return {
        "synced": True,
        "message": (
            f"Synced from Stripe: {transfers_inserted} new transfer(s), "
            f"{transfers_skipped} already tracked. "
            f"{ledger_result.payouts_upserted} bank payout(s), "
            f"{ledger_result.ledger_upserted} ledger entries."
        ),
        "transfers_inserted": transfers_inserted,
        "transfers_skipped": transfers_skipped,
        "bank_payouts_synced": ledger_result.payouts_upserted,
        "ledger_entries_synced": ledger_result.ledger_upserted,
        "plan_warnings": [{"field": w.field, "message": w.message} for w in plan.warnings],
        "plan_errors": plan_errors,
        "ledger_warnings": [{"field": w.field, "message": w.message} for w in ledger_result.warnings],
        "ledger_errors": [{"field": e.field, "message": e.message} for e in ledger_result.errors],
        "payouts": [
            {
                "id": p.get("id"),
                "amount": float(_dec(p.get("amount"))),
                "status": p.get("status"),
                "payout_type": p.get("payout_type"),
                "stripe_transfer_id": p.get("stripe_transfer_id"),
                "stripe_payout_id": p.get("stripe_payout_id"),
                "bank_name": p.get("bank_name"),
                "account_last4": p.get("account_last4"),
                "error_message": p.get("error_message"),
                "created_at": p.get("created_at"),
                "processed_at": p.get("processed_at"),
            }
            for p in all_payouts
        ],
        "bank_payouts": [
            {
                "id": bp.get("id"),
                "amount": float(_dec(bp.get("amount"))),
                "currency": bp.get("currency"),
                "status": bp.get("status"),
                "method": bp.get("method"),
                "arrival_date": bp.get("arrival_date"),
                "bank_last4": bp.get("bank_last4"),
                "failure_code": bp.get("failure_code"),
                "failure_message": bp.get("failure_message"),
                "created_at": bp.get("created_at"),
                "synced_at": bp.get("synced_at"),
            }
            for bp in bank_payouts
        ],
    }


class RefreshAllKycRequest(BaseModel):
    """Scope for the bulk KYC refresh. Empty body = every driver with a
    Stripe account. `retire_unreachable` is opt-in because retiring detaches
    payout destinations in bulk — the single-driver button stays the
    deliberate way to do that one account at a time."""

    model_config = {"extra": "forbid"}

    driver_ids: Optional[List[str]] = None
    retire_unreachable: bool = False


@router.post("/drivers/refresh-stripe-kyc")
async def admin_refresh_all_driver_kyc(body: RefreshAllKycRequest, admin: dict = Depends(get_admin_user)):
    """One-click KYC refresh across the fleet ("is there any single button").

    Pulls live Stripe Connect state for every driver that has an account and
    mirrors it onto their row — the same per-driver sync the slideout button
    runs, fanned out with bounded concurrency. Returns a per-status breakdown
    so the operator sees exactly what happened instead of a blind 200:

        {"total": 42, "ok": 38, "no_stripe_account": 0,
         "account_not_on_key": 3, "stripe_error": 1, "drivers": {...}}

    `account_not_on_key` counts drivers whose account the current key cannot
    see (the test→live signature). With the default retire_unreachable=false
    they are only REPORTED — nothing is detached — so this endpoint is safe to
    click first and read; re-run with retire_unreachable=true to also repair.
    """
    import asyncio as _asyncio

    if (admin or {}).get("role") != "super_admin":
        # Fleet-wide Stripe reads (and optionally fleet-wide retires) are a
        # bigger hammer than the per-driver button; keep it super_admin.
        raise HTTPException(status_code=403, detail="Bulk KYC refresh requires super_admin")

    try:
        from ..services.stripe_kyc_sync import refresh_driver_kyc
    except ImportError:
        from services.stripe_kyc_sync import refresh_driver_kyc  # type: ignore

    if body.driver_ids:
        drivers = [d for did in body.driver_ids if (d := await db_supabase.get_driver_by_id(did))]
    else:
        # $notnull, NOT {"$ne": None} — $ne compiles to SQL `<> NULL`, which
        # never matches anything (see repositories/_base.py's $notnull note).
        drivers = await db_supabase.get_rows("drivers", {"stripe_account_id": {"$notnull": True}}, limit=2000)

    counts: Dict[str, int] = {}
    per_driver: Dict[str, str] = {}
    sem = _asyncio.Semaphore(8)  # stay well under Stripe's rate limit

    async def one(driver: dict) -> None:
        async with sem:
            try:
                res = await refresh_driver_kyc(driver, retire_if_unreachable=body.retire_unreachable)
                status = res.get("status", "unknown")
            except Exception:
                logger.error("bulk kyc refresh failed for driver %s", driver.get("id"), exc_info=True)
                status = "stripe_error"
        per_driver[driver["id"]] = status

    results = await _asyncio.gather(*(one(d) for d in drivers), return_exceptions=True)
    for driver, outcome in zip(drivers, results, strict=True):
        if isinstance(outcome, BaseException):  # belt-and-braces; one() already catches
            per_driver[driver["id"]] = "stripe_error"
    for status in per_driver.values():
        counts[status] = counts.get(status, 0) + 1

    await log_admin_action(
        admin,
        "stripe_kyc_refresh_bulk",
        "drivers",
        f"count:{len(drivers)}",
        {"counts": counts, "retire_unreachable": body.retire_unreachable},
    )
    return {
        "total": len(drivers),
        **counts,
        "drivers": per_driver,
        "note": (
            "account_not_on_key drivers were only reported, not detached"
            if not body.retire_unreachable
            else "account_not_on_key drivers were retired and must re-onboard payouts"
        ),
    }


@router.post("/drivers/{driver_id}/reveal-sin")
async def admin_reveal_driver_sin(driver_id: str, admin: dict = Depends(get_admin_user)):
    """One-shot retrieval of the driver SIN from Stripe for tax filing.

    The SIN is held by Stripe Connect Express (never persisted on our
    side). This endpoint:
      1. Calls Stripe Account.retrieve with expand=["individual.id_number"]
         — Stripe surfaces the SIN to the platform owner once per call
      2. Writes an audit_log row capturing admin, driver, timestamp,
         IP/user-agent (caller supplies)
      3. Returns the plaintext SIN to the caller exactly once
      4. NEVER stores the SIN in our database

    Restricted to super_admin to keep the reveal surface narrow — every
    other admin role sees only the last 4 from the cache columns.
    Each successful reveal generates an audit_log row that ops + the
    privacy officer can review.
    """
    # Hard-gated to super_admin only. Regular admins (role="admin") see
    # only the last-4 from the cache columns — that's enough to confirm
    # SIN is on file at Stripe but doesn't expose the regulated value.
    # Every successful reveal already writes an audit_log row, but the
    # additional role check is defence-in-depth: even if an admin token
    # is somehow leaked, the reveal path stays closed.
    if (admin.get("role") or "").lower() != "super_admin":
        raise HTTPException(status_code=403, detail="reveal_sin requires super_admin role")

    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    if not driver.get("stripe_account_id"):
        raise HTTPException(status_code=400, detail="Driver has no Stripe Connect account")
    if not driver.get("stripe_id_number_provided"):
        raise HTTPException(status_code=400, detail="No SIN on file at Stripe yet")

    try:
        from ..services.stripe_kyc_sync import reveal_sin_from_stripe
    except ImportError:
        from services.stripe_kyc_sync import reveal_sin_from_stripe  # type: ignore

    # Audit log BEFORE the reveal, so a Stripe failure still leaves a
    # trail of the intent. metadata never carries the SIN itself.
    audit_id = await log_admin_action(
        admin,
        "driver_sin_reveal",
        "drivers",
        driver_id,
        {
            "stripe_account_id_last6": driver["stripe_account_id"][-6:],
            "sin_last4": driver.get("stripe_id_number_last4"),
        },
    )

    sin = await reveal_sin_from_stripe(driver)
    if not sin:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve SIN from Stripe. Try again or check Stripe Dashboard.",
        )

    # Surface ONLY in the immediate response. Frontend is responsible for
    # showing it for a short window and never sending it back to any other
    # service.
    return {
        "sin": sin,
        "sin_last4": sin[-4:],
        "audit_log_id": audit_id,
        "warning": "This value is not stored. Audit log records every reveal.",
    }


@router.get("/drivers/{driver_id}/daily-stats")
async def admin_get_driver_daily_stats(
    driver_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get aggregated daily stats for a driver. Default: last 30 days."""
    if not end_date:
        end_date = datetime.now(timezone.utc).date().isoformat()
    if not start_date:
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()

    stats = await db_supabase.get_rows(
        "driver_daily_stats",
        {
            "driver_id": driver_id,
            "stat_date": {"$gte": start_date, "$lte": end_date},
        },
        order="stat_date",
        desc=True,
        limit=400,
    )
    return stats or []


# ---------- Driver Area Assignment ----------


@router.put("/drivers/{driver_id}/area")
async def admin_assign_driver_area(driver_id: str, service_area_id: str):
    """Assign a driver to a specific service area."""
    await db_supabase.update_one(
        "drivers",
        {"id": driver_id},
        {
            "service_area_id": service_area_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"message": f"Driver assigned to area {service_area_id}"}


@router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    hours: int = Query(24),
):
    """Get driver's location history (table: driver_location_history)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    locations = await db_supabase.get_rows(
        "driver_location_history",
        {"driver_id": driver_id, "timestamp": {"$gte": cutoff}},
        order="timestamp",
        limit=5000,
    )
    return [
        {
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "timestamp": loc.get("timestamp"),
        }
        for loc in locations
    ]


@router.get("/drivers/{driver_id}/daily-activity")
async def admin_driver_daily_activity(
    driver_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD in Regina time; defaults to today"),
    admin_user: dict = Depends(get_admin_user),
):
    """Driver daily activity: per-phase km, empty (P1+P2) vs riding (P3) time, and
    a per-ride breakdown with phase km/timestamps and the empty gap before each
    ride. Day boundaries in America/Regina."""
    try:
        from ...utils.driver_activity import REGINA_TZ, build_daily_activity, regina_day_bounds_utc
    except ImportError:
        from utils.driver_activity import REGINA_TZ, build_daily_activity, regina_day_bounds_utc  # type: ignore

    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from None
    else:
        d = datetime.now(REGINA_TZ).date()

    win_start, win_end = regina_day_bounds_utc(d)
    ws_iso, we_iso = win_start.isoformat(), win_end.isoformat()

    # Periods overlapping the day: fetch from one day before the window start
    # (to catch a period that began just before midnight and spans in) through
    # the window end; build_daily_activity clips each interval to the window.
    lookback_iso = (win_start - timedelta(days=1)).isoformat()
    periods = await db_supabase.get_rows(
        "driver_insurance_periods",
        {"driver_id": driver_id, "started_at": {"$gte": lookback_iso, "$lt": we_iso}},
        order="started_at",
        limit=2000,
    )
    # Rides the driver worked that day (keyed on when they accepted it).
    rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "driver_accepted_at": {"$gte": ws_iso, "$lt": we_iso}},
        order="driver_accepted_at",
        limit=500,
    )

    report = build_daily_activity(periods, rides, win_start, win_end)
    report["date"] = d.isoformat()
    report["tz"] = "America/Regina"
    return report
