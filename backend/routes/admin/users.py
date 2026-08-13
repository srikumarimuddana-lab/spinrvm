import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...db_supabase import run_sync
    from ...dependencies import get_admin_user
    from ...settings_loader import get_app_settings
    from ...utils.stripe_mode import is_missing_on_key
except ImportError:
    import db_supabase
    from db_supabase import run_sync
    from dependencies import get_admin_user
    from settings_loader import get_app_settings
    from utils.stripe_mode import is_missing_on_key

logger = logging.getLogger(__name__)

router = APIRouter()

# Columns the admin Users list / search / export read that ACTUALLY EXIST on
# the users table. We project explicitly to keep users.profile_image OUT of
# these bulk reads: for accounts created before the `profile-photos` storage
# bucket, profile_image holds a full base64 data URI, so `SELECT *` shipped ~one
# image blob per row — that's the multi-MB payload (and the slow DB read on the
# 1000-row export). The Users page never displays an avatar.
#
# NB: total_rides, rating, is_verified, city and status are NOT columns on the
# users table (they're driver/derived fields); the frontend defaults them
# client-side. They must NOT be listed here — projecting a non-existent column
# makes Postgres raise 42703 ("column does not exist") and the endpoint 503s.
# The single-user detail endpoint (admin_get_user_details) still selects *.
_USER_LIST_COLUMNS = (
    "id,first_name,last_name,email,phone,role,created_at,is_rider,is_driver,status,status_reason,suspended_until"
)


class UserStatusRequest(BaseModel):
    status: Literal["active", "suspended", "banned"]
    # Moderation note — required when suspending or banning so the action is
    # auditable (and shown back to the rider). Ignored when reactivating.
    reason: Optional[str] = None
    # Optional auto-lift time for a *temporary* suspension (ISO-8601). Omit for
    # an indefinite suspension (admin must reactivate). Only used for 'suspended'.
    suspended_until: Optional[str] = None


# ---------- Users (riders) ----------


@router.get("/users")
async def admin_get_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    role: Optional[str] = None,
):
    """Get users with optional role filter, search, and pagination.

    `role` accepts: "rider", "driver", "admin", or "all" (default).
    Legacy callers that omit `role` get every non-admin user — so
    anyone the admin sees in the app (rider or driver) is represented
    here. This replaces the old hardcoded `role='rider'` filter that
    silently hid driver-registered users from the Users page.
    """
    filters: Dict[str, Any] = {}
    role_param = (role or "all").lower()
    if role_param == "rider":
        filters["is_rider"] = True
    elif role_param == "driver":
        filters["is_driver"] = True
    elif role_param == "both":
        filters["is_rider"] = True
        filters["is_driver"] = True
    elif role_param == "admin":
        filters["role"] = "admin"
    elif role_param == "all":
        # Every non-admin user — managed on the Staff page instead.
        filters["role"] = {"$ne": "admin"}
    else:
        raise HTTPException(status_code=400, detail="role must be one of rider, driver, both, admin, all")

    if search:
        # Basic contains-match on phone / email / first_name; callers typically
        # pass partial phone digits or email substrings. `id` is included so
        # pasting a rider/driver UUID (e.g. from a support ticket) still finds
        # the exact user even though it never matches name/contact fields.
        #
        # Pass the raw term: $regex is compiled to a SQL ILIKE pattern, and the
        # query layer owns escaping (_escape_like + PostgREST quoting). Applying
        # re.escape() here — as this did — leaked regex escapes into the LIKE
        # pattern, so "O'Neil-Smith" searched for a literal backslash.
        term = search.strip()
        if term:
            filters["$or"] = [
                {"phone": {"$regex": term, "$options": "i"}},
                {"email": {"$regex": term, "$options": "i"}},
                {"first_name": {"$regex": term, "$options": "i"}},
                {"last_name": {"$regex": term, "$options": "i"}},
                {"id": {"$regex": term, "$options": "i"}},
            ]
    users = await db_supabase.get_rows(
        "users",
        filters,
        columns=_USER_LIST_COLUMNS,
        order="created_at",
        desc=True,
        limit=limit,
        offset=offset,
    )
    return users


class UserSearchRequest(BaseModel):
    search: str
    role: Optional[str] = "all"
    limit: int = 5


@router.post("/users/search")
async def admin_search_users(
    body: UserSearchRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Typeahead search for users via POST body to keep search terms out of server logs."""
    return await admin_get_users(role=body.role, search=body.search, limit=body.limit)


async def _list_user_cards(user: Dict[str, Any]) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Best-effort list of the rider's saved cards (brand + last-4 + expiry) from
    Stripe. Returns (cards, error): on a Stripe failure we return (None, message)
    so the rest of the user detail still loads — the card panel surfaces the
    error rather than 503'ing the whole page. Only the masked last-4 is exposed;
    the PAN never touches the backend (PCI)."""
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        return [], None
    try:
        settings = await get_app_settings()
        stripe_secret = (settings or {}).get("stripe_secret_key", "")
        if not stripe_secret:
            return [], None
        methods = await run_sync(
            lambda: stripe.PaymentMethod.list(customer=customer_id, type="card", api_key=stripe_secret)
        )
        default_pm = user.get("default_payment_method")
        return (
            [
                {
                    "id": m.id,
                    "brand": (m.card.brand or "").capitalize(),
                    "last4": m.card.last4,
                    "exp_month": m.card.exp_month,
                    "exp_year": m.card.exp_year,
                    "is_default": m.id == default_pm,
                }
                for m in methods.data
            ],
            None,
        )
    except Exception as e:
        if is_missing_on_key(e, customer_id):
            # Not a Stripe outage: this customer does not exist on the running
            # key — the signature of a test→live key rotation over rows minted
            # under the old mode. Say so precisely, because "Could not load
            # cards" sends the operator hunting for an outage that isn't there.
            # The rider's own cards screen repairs this on next open
            # (routes/payments.py::with_customer_repair).
            logger.warning(
                "admin: rider's Stripe customer is not on the current key",
                extra={"domain": "admin", "user_id": user.get("id")},
            )
            return None, (
                "This rider's Stripe customer does not exist on the current API key "
                "(likely a test→live key change). It is replaced automatically the next "
                "time they open the payment screen; any cards saved before the change "
                "cannot be recovered and must be re-added."
            )
        logger.error(
            "admin: failed to list cards from Stripe",
            exc_info=True,
            extra={"domain": "admin", "user_id": user.get("id")},
        )
        return None, "Could not load cards from Stripe."


# Columns the admin detail view renders for a rider's recent rides. Explicitly
# EXCLUDES raw GPS (pickup_lat/lng, dropoff_lat/lng) and any route polyline —
# PIPEDA forbids raw coordinates leaving the data store; the UI only shows the
# address text, status, fare and date. `legacy_import_metadata` is included
# so the panel can flag a row as imported from the previous app (JSONB import
# provenance only — batch/source/timestamps — never PII, see
# docs/audit/2026-08-13-migrated-data-visibility-audit.md Finding 4).
_DETAIL_RIDE_COLUMNS = "id,status,pickup_address,dropoff_address,total_fare,created_at,legacy_import_metadata"


@router.get("/users/{user_id}")
async def admin_get_user_details(user_id: str, admin: dict = Depends(get_admin_user)):
    """Get detailed user information: profile, recent rides, total ride count,
    and the masked (last-4) cards on file from Stripe."""
    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Recent rides — projected to exclude raw GPS coordinates (PIPEDA).
    rides = await db_supabase.get_rows(
        "rides",
        {"rider_id": user_id},
        columns=_DETAIL_RIDE_COLUMNS,
        order="created_at",
        desc=True,
        limit=10,
    )

    cards, cards_error = await _list_user_cards(user)

    # Drop the heavy base64 profile_image blob (same reason the list endpoint
    # projects it out). Build a NEW dict — never mutate the cached user row.
    safe_user = {k: v for k, v in user.items() if k != "profile_image"}
    return {
        **safe_user,
        "total_rides": await db_supabase.count_documents("rides", {"rider_id": user_id}),
        "recent_rides": rides,
        "cards": cards,
        "cards_error": cards_error,
    }


@router.put("/users/{user_id}/status")
async def admin_update_user_status(user_id: str, status_data: UserStatusRequest, admin: dict = Depends(get_admin_user)):
    """Suspend, ban, or reactivate a rider account (Uber/Lyft-style moderation).

    - suspended / banned require a `reason` (stored + audited + shown to the rider).
    - `suspended_until` makes a suspension temporary (auto-lifts at booking time);
      omit it for an indefinite suspension that an admin must reactivate.
    - reactivating (status='active') clears the reason and the suspension timer.

    Enforcement lives at booking time (routes/rides.py): suspended/banned riders
    cannot request a ride.
    """
    new_status = status_data.status
    reason = (status_data.reason or "").strip()

    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if new_status in ("suspended", "banned") and not reason:
        verb = "ban" if new_status == "banned" else "suspend"
        raise HTTPException(status_code=422, detail=f"A reason is required to {verb} an account.")

    old_status = user.get("status") or "active"

    now_iso = datetime.now(timezone.utc).isoformat()
    update: Dict[str, Any] = {
        "status": new_status,
        "status_changed_at": now_iso,
        "status_changed_by": admin["id"],
        "updated_at": now_iso,
    }
    if new_status == "active":
        # Reactivation clears the moderation context — and also any pending
        # deletion, so an admin can honour a rider withdrawing their DSAR
        # delete request within the 30-day grace window.
        update["status_reason"] = None
        update["suspended_until"] = None
        update["deletion_requested_at"] = None
        update["deletion_scheduled_at"] = None
    else:
        update["status_reason"] = reason
        # suspended_until only applies to a temporary suspension.
        update["suspended_until"] = status_data.suspended_until if new_status == "suspended" else None

    try:
        await db_supabase.update_one("users", {"id": user_id}, update)
    except Exception as e:
        logger.error(
            "Failed to update user status",
            exc_info=True,
            extra={"domain": "admin", "user_id": user_id, "new_status": new_status},
        )
        raise HTTPException(status_code=503, detail="Could not update account status.") from e

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "user_status_change",
            "entity_type": "user",
            "entity_id": user_id,
            "details": {
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason or None,
                "suspended_until": update.get("suspended_until"),
            },
            "created_at": now_iso,
        },
    )

    # Best-effort: tell the rider their account state changed (never blocks the
    # admin action if push delivery fails).
    if new_status != old_status:
        try:
            try:
                from ...features import send_push_notification
            except ImportError:
                from features import send_push_notification  # type: ignore
            if new_status == "banned":
                title, body = "Account deactivated", reason or "Your account has been deactivated. Contact support."
            elif new_status == "suspended":
                title, body = "Account suspended", reason or "Your account has been suspended. Contact support."
            else:
                title, body = "Account reactivated", "Your account is active again. Welcome back!"
            await send_push_notification(
                user_id, title, body, data={"type": "account_status", "status": new_status}, target_app="rider"
            )
        except Exception:
            logger.warning("account status push failed", exc_info=True, extra={"domain": "admin", "user_id": user_id})

    return {
        "message": f"User status updated to {new_status}",
        "status": new_status,
        "status_reason": update.get("status_reason"),
        "suspended_until": update.get("suspended_until"),
    }


class UserFlagsRequest(BaseModel):
    is_rider: Optional[bool] = None
    is_driver: Optional[bool] = None


@router.patch("/users/{user_id}/role")
async def admin_update_user_flags(
    user_id: str,
    body: UserFlagsRequest,
    admin: dict = Depends(get_admin_user),
):
    """Set is_rider / is_driver flags independently (dual-role support).

    Each flag is optional — send only the ones you want to change.
    A user can have both flags true (rides and drives), either alone,
    or neither (deactivated from both roles).

    Setting is_driver=False does NOT delete the drivers row — trip history
    is preserved. Setting is_driver=True does NOT create a drivers row —
    the user must complete onboarding first.
    """
    if body.is_rider is None and body.is_driver is None:
        raise HTTPException(status_code=400, detail="Provide at least one of is_rider or is_driver")

    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    old_flags = {"is_rider": user.get("is_rider"), "is_driver": user.get("is_driver")}
    new_flags: dict = {}

    if body.is_rider is not None:
        update["is_rider"] = body.is_rider
        new_flags["is_rider"] = body.is_rider
    if body.is_driver is not None:
        update["is_driver"] = body.is_driver
        new_flags["is_driver"] = body.is_driver
        # Keep role column in sync for backward-compat (admin queries, JWT)
        if body.is_driver and user.get("role") not in ("driver", "admin"):
            update["role"] = "driver"
        elif not body.is_driver and not body.is_rider and user.get("role") == "driver":
            update["role"] = "rider"

    await db_supabase.update_one("users", {"id": user_id}, update)

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "flags_change",
            "entity_type": "user",
            "entity_id": user_id,
            "details": {"old_flags": old_flags, "new_flags": new_flags},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    logger.info(f"Admin {admin['id']} updated user {user_id} flags: {old_flags} → {new_flags}")
    return {"message": "User flags updated", "old": old_flags, "new": new_flags}


# ---------- Export & PII Audit ----------

_EXPORT_MAX_ROWS = 5000


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[0] if local else ''}***@{domain}"


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return f"***-{digits[-4:]}" if len(digits) >= 4 else "***"


@router.get("/export/users")
async def admin_export_users(
    limit: int = Query(1000, ge=1, le=_EXPORT_MAX_ROWS),
    admin: dict = Depends(get_admin_user),
):
    """Export users with masked PII. Writes an audit log entry."""
    is_super = admin.get("role") == "super_admin"
    users = await db_supabase.get_rows(
        "users",
        {"role": {"$ne": "admin"}},
        columns=_USER_LIST_COLUMNS,
        order="created_at",
        desc=True,
        limit=limit,
    )
    out = []
    for u in users:
        row = {
            "id": u.get("id"),
            "name": f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
            or u.get("email")
            or u.get("phone"),
            "email": u.get("email") if is_super else _mask_email(u.get("email")),
            "phone": u.get("phone") if is_super else _mask_phone(u.get("phone")),
            "city": u.get("city"),
            "role": u.get("role"),
            "total_rides": u.get("total_rides", 0),
            "rating": u.get("rating"),
            "is_verified": u.get("is_verified"),
            "status": u.get("status"),
            "created_at": u.get("created_at"),
        }
        out.append(row)

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "export_users",
            "entity_type": "users",
            "entity_id": "export",
            "details": {"row_count": len(out), "pii_unmasked": is_super},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"users": out, "count": len(out)}


# ---------- DSAR (Data Subject Access Requests) ----------


@router.get("/dsars")
async def admin_list_dsars(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin: dict = Depends(get_admin_user),
):
    """DV-17: List PIPEDA data-export requests with SLA deadline tracking.

    PIPEDA s.9 requires a response within 30 days. Requests approaching or
    past their `response_due_at` should be prioritised.
    """
    filters: Dict[str, Any] = {}
    if status in ("pending", "in_progress", "completed", "rejected"):
        filters["status"] = status

    requests = await db_supabase.get_rows(
        "data_export_requests",
        filters,
        order="response_due_at",
        desc=False,
        limit=limit,
        offset=offset,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    for req in requests:
        due = req.get("response_due_at")
        if due and req.get("status") == "pending":
            req["overdue"] = due < now_iso

    return {"dsars": requests, "total": len(requests)}


@router.patch("/dsars/{request_id}/status")
async def admin_update_dsar_status(
    request_id: str,
    status: str,
    admin: dict = Depends(get_admin_user),
):
    """Update DSAR status (in_progress, completed, rejected)."""
    if status not in ("in_progress", "completed", "rejected"):
        raise HTTPException(
            status_code=400,
            detail="status must be one of in_progress, completed, rejected",
        )

    req = await db_supabase.get_one("data_export_requests", {"id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="DSAR request not found")

    update: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if status == "completed":
        update["completed_at"] = datetime.now(timezone.utc).isoformat()

    await db_supabase.update_one("data_export_requests", {"id": request_id}, update)

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": f"dsar_{status}",
            "entity_type": "data_export_request",
            "entity_id": request_id,
            "details": {"user_id": req.get("user_id"), "new_status": status},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"message": f"DSAR {request_id} marked {status}"}
