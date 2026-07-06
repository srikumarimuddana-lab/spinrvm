"""Driver self-service read tools for the AI assistant.

Answers the highest-volume driver questions from the driver's OWN record:
application/approval status, document review + CRC status, and payout/earnings.
All read-only and scoped to the authenticated driver (resolved server-side from
user_id — never a tool argument). Whitelisted fields only: no file URLs, no
addresses, no PII beyond the driver's own document review notes.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    from .tools import ToolSpec, register
except ImportError:
    from ai.tools import ToolSpec, register

try:
    from .. import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

_BOTH = frozenset({"driver"})

# Human-readable meaning + next step per driver.status. The driver app and
# onboarding use these values (see routes/drivers.py).
_STATUS_SUMMARY = {
    "pending": "Your application is submitted and waiting for our team to review your documents.",
    "needs_review": "Your application needs another look — check your documents below for anything rejected or expired.",
    "incomplete": "Your application is not finished — some required documents are still missing.",
    "active": "Your account is approved. You can go online and start accepting rides.",
    "rejected": "Your application was not approved. Contact support for the reason and next steps.",
    "suspended": "Your account is currently suspended. Contact support to resolve it.",
    "banned": "Your account has been deactivated. Contact support for details.",
}

# drivers-row expiry columns that gate going online, with a friendly label.
# background_check == Criminal Record Check (CRC).
_EXPIRY_FIELDS = {
    "background_check_expiry_date": "Criminal Record Check",
    "license_expiry_date": "Driver's licence",
    "insurance_expiry_date": "Insurance",
    "vehicle_inspection_expiry_date": "Vehicle inspection",
    "work_eligibility_expiry_date": "Work eligibility",
}


async def _driver(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return await db_supabase.get_driver_by_user_id_cached(user["id"])


def _date_only(value: Any) -> Optional[str]:
    return str(value)[:10] if value else None


def _expiry_flags(driver: Dict[str, Any]) -> List[Dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    out: List[Dict[str, Any]] = []
    for field, label in _EXPIRY_FIELDS.items():
        raw = driver.get(field)
        if not raw:
            continue
        try:
            exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        days = (exp - today).days
        if days < 0:
            out.append({"document": label, "state": "expired", "expiry_date": exp.isoformat()})
        elif days <= 30:
            out.append({"document": label, "state": "expiring_soon", "expiry_date": exp.isoformat()})
    return out


async def get_driver_application_status(user: Dict[str, Any]) -> Dict[str, Any]:
    driver = await _driver(user)
    if not driver:
        return {
            "status": "not_started",
            "summary": "We don't have a driver application on file for this account yet. "
            "Finish signing up in the driver app to get started.",
        }
    status = driver.get("status") or "pending"
    return {
        "status": status,
        "is_verified": bool(driver.get("is_verified")),
        "can_go_online": status == "active",
        "summary": _STATUS_SUMMARY.get(status, "Your application is being processed."),
        "expiring_or_expired_documents": _expiry_flags(driver),
    }


async def get_document_status(user: Dict[str, Any]) -> Dict[str, Any]:
    driver = await _driver(user)
    if not driver:
        return {"documents": [], "note": "No driver application on file yet — finish signing up in the driver app."}
    rows = await db_supabase.get_rows("documents", {"driver_id": driver["id"]}, order="uploaded_at", desc=True, limit=50)
    docs = []
    for r in rows or []:
        entry = {
            "document_type": r.get("document_type"),
            "status": r.get("status"),  # pending_review | approved | rejected
            "uploaded_on": _date_only(r.get("uploaded_at")),
            "expires_on": _date_only(r.get("expires_at")),
        }
        # Surface the reviewer's reason only when the driver must act on it.
        if r.get("status") in ("rejected", "needs_review") and r.get("reviewer_notes"):
            entry["action_needed"] = r["reviewer_notes"]
        docs.append(entry)
    return {
        "documents": docs,
        "expiring_or_expired": _expiry_flags(driver),
        "note": (
            "If a document you uploaded isn't listed, it may still be syncing — check back shortly. "
            "Expired documents (including the Criminal Record Check) must be renewed before going online."
        ),
    }


async def get_driver_earnings_summary(user: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    driver = await _driver(user)
    if not driver:
        return {"note": "No driver account on file yet."}
    rows = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver["id"], "status": "completed"},
        order="ride_completed_at",
        desc=True,
        limit=limit,
    )
    total = Decimal("0")
    trips = []
    for r in rows or []:
        earned = Decimal(str(r.get("driver_earnings") or "0"))
        total += earned
        trips.append(
            {
                "completed_on": _date_only(r.get("ride_completed_at")),
                "earnings": str(earned),
                "payment_status": r.get("payment_status"),
            }
        )
    return {
        "recent_trip_count": len(trips),
        "recent_earnings_total": str(total),
        "currency": "CAD",
        "trips": trips,
        "note": (
            "Drivers keep 100% of the fare. This lists your most recent completed trips and what you "
            "earned on each. For payout timing and bank-deposit questions, use the FAQ or contact support."
        ),
    }


register(
    ToolSpec(
        name="get_driver_application_status",
        description=(
            "Call this when the driver asks about the status of their application, whether "
            "they're approved/verified, when they can start driving, or asks you to activate "
            "their account. Returns their current approval status and what it means."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_driver_application_status,
        audiences=_BOTH,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="get_document_status",
        description=(
            "Call this when the driver asks whether a document was received or approved, what "
            "documents are missing or rejected, or about their Criminal Record Check (CRC), "
            "licence, insurance or inspection status/expiry. Returns their uploaded documents "
            "with review status and any expiring/expired items."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_document_status,
        audiences=_BOTH,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="get_driver_earnings_summary",
        description=(
            "Call this when the driver asks about their earnings or when/whether they were "
            "paid for recent trips. Returns their most recent completed trips with per-trip "
            "earnings and payment status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "How many recent trips (default 10)."}
            },
            "required": [],
        },
        handler=get_driver_earnings_summary,
        audiences=_BOTH,
        mcp_exposed=False,
    )
)
