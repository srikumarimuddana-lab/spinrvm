"""
App-event -> Zoho Desk ticket bridge.

Helpers that raise a Zoho Desk ticket when something happens in the Spinr app
(Lost & Found today; support / disputes next). All helpers are:

  * **best-effort** — never raise into the caller's request flow (a Zoho outage
    must not break reporting a found item),
  * **idempotent** — they no-op when the source record already links a ticket,
  * **opt-in** — they no-op when the integration is disabled.

Call them via ``asyncio.create_task`` so the inline request stays fast.
"""

import logging
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
    from . import zoho_desk_service as zoho
    from .zoho_desk_service import ZohoDeskError
except ImportError:  # pragma: no cover
    import db_supabase
    from services import zoho_desk_service as zoho
    from services.zoho_desk_service import ZohoDeskError

logger = logging.getLogger(__name__)


async def _enabled() -> bool:
    cfg = await db_supabase.find_one("zoho_desk_config", {"id": "default"})
    return bool(cfg and cfg.get("enabled"))


def _split_name(name: str) -> tuple[str, Optional[str]]:
    parts = (name or "").strip().split()
    if not parts:
        return "", None
    return parts[0], (" ".join(parts[1:]) or None)


async def _link_ticket(
    *,
    table: str,
    record: Dict[str, Any],
    subject: str,
    description: str,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    contact_user_id: Optional[str] = None,
) -> None:
    """Create a Zoho ticket for a source record and link it back via
    ``<table>.zoho_ticket_id``. Best-effort + idempotent + opt-in. Used by the
    per-entity helpers below."""
    try:
        if record.get("zoho_ticket_id") or not await _enabled():
            return
        user: Dict[str, Any] = {}
        if contact_user_id:
            user = await db_supabase.find_one("users", {"id": contact_user_id}) or {}
        name = (user.get("name") or "").strip() or (
            f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        )
        first, last = _split_name(name or "Customer")
        result = await zoho.create_ticket(
            subject=subject,
            description=description,
            email=user.get("email"),
            first_name=first,
            last_name=last,
            phone=user.get("phone"),
            priority=priority,
            channel="Web",
            category=category,
        )
        zoho_id = str(result.get("id") or "")
        if zoho_id:
            await db_supabase.update_one(table, {"id": record["id"]}, {"zoho_ticket_id": zoho_id})
            logger.info("Created Zoho ticket %s for %s %s", zoho_id, table, record.get("id"))
    except ZohoDeskError as e:
        logger.warning("Zoho ticket skipped for %s (%s): %s", table, e.status, e.message)
    except Exception:
        logger.error("Failed to create Zoho ticket for %s %s", table, record.get("id"), exc_info=True)


async def create_ticket_for_complaint(complaint: Dict[str, Any], ride: Optional[Dict[str, Any]] = None) -> None:
    """Zoho ticket for an admin complaint against a participant."""
    against = complaint.get("against_type")
    contact = complaint.get("against_id") if against == "rider" else None
    ride_code = (ride or {}).get("ride_code") or complaint.get("ride_id") or ""
    await _link_ticket(
        table="complaints",
        record=complaint,
        subject=f"Complaint — {complaint.get('category') or 'ride'}",
        description=(
            "Complaint logged from the Spinr admin panel.\n\n"
            f"Against: {against}\n"
            f"Category: {complaint.get('category')}\n"
            f"Details: {complaint.get('description') or '—'}\n"
            f"Ride: {ride_code}\n"
            f"Complaint ID: {complaint.get('id')}"
        ),
        category="Complaint",
        contact_user_id=contact,
    )


async def create_ticket_for_flag(flag: Dict[str, Any], ride: Optional[Dict[str, Any]] = None) -> None:
    """Zoho ticket for a participant flag."""
    target = flag.get("target_type")
    contact = flag.get("target_id") if target == "rider" else None
    ride_code = (ride or {}).get("ride_code") or flag.get("ride_id") or ""
    await _link_ticket(
        table="flags",
        record=flag,
        subject=f"Flag — {flag.get('reason') or target or 'participant'}",
        description=(
            "Participant flagged from the Spinr admin panel.\n\n"
            f"Target: {target}\n"
            f"Reason: {flag.get('reason')}\n"
            f"Details: {flag.get('description') or '—'}\n"
            f"Ride: {ride_code}\n"
            f"Flag ID: {flag.get('id')}"
        ),
        category="Flag",
        contact_user_id=contact,
    )


async def create_ticket_for_safety(incident: Dict[str, Any]) -> None:
    """Zoho ticket for a safety incident (high priority)."""
    await _link_ticket(
        table="safety_incidents",
        record=incident,
        subject=f"Safety — {incident.get('category') or 'incident'}",
        description=(
            "Safety incident reported from the Spinr app.\n\n"
            f"Category: {incident.get('category')}\n"
            f"Reported by: {incident.get('role')}\n"
            f"Details: {incident.get('description') or '—'}\n"
            f"Ride: {incident.get('ride_id') or '—'}\n"
            f"Incident ID: {incident.get('id')}"
        ),
        category="Safety",
        priority="Urgent",
        contact_user_id=incident.get("reported_by_user_id"),
    )


# Tables whose records auto-close when their linked Zoho ticket is closed.
# `is_active` entries use the boolean flag; otherwise the status column is set.
_LINKED_TABLES = [
    {"table": "lost_and_found", "status_col": "status", "closed": "resolved"},
    {"table": "disputes", "status_col": "status", "closed": "resolved"},
    {"table": "complaints", "status_col": "status", "closed": "resolved"},
    {"table": "safety_incidents", "status_col": "status", "closed": "resolved"},
    {"table": "flags", "is_active": True},
]


async def close_linked_records(closed_zoho_ids) -> None:
    """When Zoho tickets close, close the linked Spinr records (reverse sync).

    Called from the mirror sync for incrementally-changed tickets only, so the
    id set stays small. Idempotent: skips records already closed.
    """
    ids = [str(i) for i in (closed_zoho_ids or []) if i]
    if not ids:
        return
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for spec in _LINKED_TABLES:
        table = spec["table"]
        try:
            rows = await db_supabase.get_rows(table, {"zoho_ticket_id": {"$in": ids}})
            for r in rows or []:
                if spec.get("is_active"):
                    if r.get("is_active") is False:
                        continue
                    await db_supabase.update_one(table, {"id": r["id"]}, {"is_active": False})
                else:
                    col = spec["status_col"]
                    if r.get(col) == spec["closed"]:
                        continue
                    await db_supabase.update_one(
                        table, {"id": r["id"]}, {col: spec["closed"], "updated_at": now}
                    )
                logger.info("Closed %s %s from Zoho ticket %s", table, r.get("id"), r.get("zoho_ticket_id"))
        except Exception:
            logger.error("Reverse-close failed for table %s", table, exc_info=True)


async def create_ticket_for_lost_and_found(case: Dict[str, Any], ride: Optional[Dict[str, Any]] = None) -> None:
    """Open a Zoho Desk ticket for a Lost & Found case and link it back via
    ``lost_and_found.zoho_ticket_id``. Safe to call fire-and-forget."""
    try:
        if case.get("zoho_ticket_id") or not await _enabled():
            return

        rider: Dict[str, Any] = {}
        rid = case.get("rider_user_id")
        if rid:
            rider = await db_supabase.find_one("users", {"id": rid}) or {}

        name = (rider.get("name") or "").strip() or (
            f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip()
        )
        first, last = _split_name(name or "Rider")
        item = case.get("item_description") or "item"
        ride_code = (ride or {}).get("ride_code") or case.get("ride_id") or ""
        description = (
            "Lost & Found case opened from the Spinr app.\n\n"
            f"Item: {item}\n"
            f"Category: {case.get('item_category')}\n"
            f"Ride: {ride_code}\n"
            f"Case ID: {case.get('id')}\n"
            f"Reported by: {case.get('reporter_type')}"
        )

        result = await zoho.create_ticket(
            subject=f"Lost & Found — {item[:60]}",
            description=description,
            email=rider.get("email"),
            first_name=first,
            last_name=last,
            phone=rider.get("phone"),
            channel="Web",
            category="Lost & Found",
        )
        zoho_id = str(result.get("id") or "")
        if zoho_id:
            await db_supabase.update_one("lost_and_found", {"id": case["id"]}, {"zoho_ticket_id": zoho_id})
            logger.info("Created Zoho ticket %s for L&F case %s", zoho_id, case.get("id"))
    except ZohoDeskError as e:
        # Misconfigured / missing department / scope — log, don't break the flow.
        logger.warning("Zoho L&F ticket skipped (%s): %s", e.status, e.message)
    except Exception:
        logger.error("Failed to create Zoho ticket for L&F case %s", case.get("id"), exc_info=True)


async def create_ticket_for_dispute(dispute: Dict[str, Any], ride: Optional[Dict[str, Any]] = None) -> None:
    """Open a Zoho Desk ticket for a payment dispute / refund request and link
    it back via ``disputes.zoho_ticket_id``. Fire-and-forget; idempotent."""
    try:
        if dispute.get("zoho_ticket_id") or not await _enabled():
            return

        user: Dict[str, Any] = {}
        uid = dispute.get("user_id")
        if uid:
            user = await db_supabase.find_one("users", {"id": uid}) or {}

        name = (user.get("name") or "").strip() or (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip())
        first, last = _split_name(name or "Rider")
        reason = dispute.get("reason") or "Dispute"
        ride_code = (ride or {}).get("ride_code") or dispute.get("ride_id") or ""
        description = (
            "Dispute / refund request opened from the Spinr app.\n\n"
            f"Reason: {reason}\n"
            f"Details: {dispute.get('description') or '—'}\n"
            f"Requested amount: {dispute.get('requested_amount')}\n"
            f"Original fare: {dispute.get('original_fare')}\n"
            f"Ride: {ride_code}\n"
            f"Dispute ID: {dispute.get('id')}"
        )

        result = await zoho.create_ticket(
            subject=f"Dispute & Refund — {str(reason)[:50]}",
            description=description,
            email=user.get("email"),
            first_name=first,
            last_name=last,
            phone=user.get("phone"),
            priority="High",
            channel="Web",
            category="Payment",
        )
        zoho_id = str(result.get("id") or "")
        if zoho_id:
            await db_supabase.update_one("disputes", {"id": dispute["id"]}, {"zoho_ticket_id": zoho_id})
            logger.info("Created Zoho ticket %s for dispute %s", zoho_id, dispute.get("id"))
    except ZohoDeskError as e:
        logger.warning("Zoho dispute ticket skipped (%s): %s", e.status, e.message)
    except Exception:
        logger.error("Failed to create Zoho ticket for dispute %s", dispute.get("id"), exc_info=True)


async def create_support_ticket(
    *, user: Dict[str, Any], message: str, transcript: Optional[str] = None
) -> Dict[str, Any]:
    """Open a Zoho Desk ticket from a support-chat escalation. Unlike the
    fire-and-forget helpers above, this is user-triggered, so it RAISES
    ZohoDeskError on failure (disabled / not configured / missing department)
    for the route to surface a friendly fallback."""
    if not await _enabled():
        raise ZohoDeskError("Zoho Desk integration is disabled.", status=503)

    user = user or {}
    email = user.get("email")
    if not email and user.get("id"):
        fetched = await db_supabase.find_one("users", {"id": user["id"]}) or {}
        user = {**fetched, **{k: v for k, v in user.items() if v is not None}}
        email = user.get("email")

    name = (user.get("name") or "").strip() or (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip())
    first, last = _split_name(name or "Customer")
    msg = (message or "").strip()
    subject = (msg.splitlines()[0][:70] if msg else "") or "Support request"
    # "(no message)" is only accurate when there's truly nothing to show —
    # a blank rider message with an attached transcript should lead with
    # the transcript, not a misleading placeholder line.
    description = msg or ("" if transcript else "(no message)")
    if transcript:
        description += f"\n\n--- Chat transcript ---\n{transcript}"

    return await zoho.create_ticket(
        subject=f"Support — {subject}",
        description=description,
        email=email,
        first_name=first,
        last_name=last,
        phone=user.get("phone"),
        channel="Chat",
    )
