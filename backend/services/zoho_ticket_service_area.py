"""Service-area resolution + persistence for Zoho Desk tickets.

Zoho has no concept of Spinr service areas, so the link lives only in our local
mirror (``zoho_desk_tickets.service_area_id`` — migration 200). Two ways a
ticket gets a service area:

* **auto** — the ticket's contact email resolves to a known rider/driver, and
  that user's most recent ride has a ``service_area_id``. We treat "the user is
  already in our database with a service area" as the remedial signal and assign
  it for the admin.
* **manual** — no auto match (phone-only rider, unknown contact, rider with no
  rides yet): the admin picks an area in the Help Desk. The UI highlights these
  tickets, but assignment is never mandatory.

The sync upsert (utils/zoho_desk_sync.py) only writes the columns from
``_map_ticket()`` — which excludes these — so an assignment made here survives
every subsequent sync.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - direct module import in tests
    import db_supabase

logger = logging.getLogger(__name__)

_TABLE = "zoho_desk_tickets"


def _ticket_contact_email(ticket: Dict[str, Any]) -> Optional[str]:
    """Best-effort contact email from either a live Zoho payload or a mirror row.

    Live Zoho tickets nest it under ``contact.email`` (with ``email`` as a
    fallback); mirror rows store the resolved value in ``contact_email``.
    """
    contact = ticket.get("contact") or {}
    email = contact.get("email") or ticket.get("email") or ticket.get("contact_email")
    email = (email or "").strip().lower()
    return email or None


async def resolve_suggested_area(ticket: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive a service area for a ticket from its contact's footprint.

    Resolves the contact email to a known user, then takes the first service
    area from either (a) their most recent ride (rider history) or, failing
    that, (b) their driver profile's home ``service_area_id``. Returns
    ``{"service_area_id", "service_area_name", "matched_user_id"}`` or ``None``
    (unknown contact / phone-only rider / no area on file -> admin assigns
    manually).

    Best-effort by design: a DB failure yields ``None`` rather than breaking the
    ticket view, but is logged at ``error`` (not ``warning``) so a real outage
    still surfaces — per CLAUDE.md's DB-error rule.
    """
    email = _ticket_contact_email(ticket)
    if not email:
        return None
    try:
        user = await db_supabase.find_one("users", {"email": email})
        if not user or not user.get("id"):
            return None

        rides = await db_supabase.get_rides_for_user(user["id"], limit=20)
        area_id = next(
            (r.get("service_area_id") for r in (rides or []) if r.get("service_area_id")),
            None,
        )
        # Driver contacts rarely have rider rides; fall back to their driver
        # profile's home service area (drivers carry service_area_id directly).
        if not area_id:
            driver = await db_supabase.get_driver_by_user_id_cached(user["id"])
            area_id = (driver or {}).get("service_area_id")
        if not area_id:
            return None

        area = await db_supabase.find_one("service_areas", {"id": area_id})
        return {
            "service_area_id": area_id,
            "service_area_name": (area or {}).get("name"),
            "matched_user_id": user["id"],
        }
    except Exception as e:
        detail = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(
            "service-area resolution failed (best-effort, no suggestion): %s",
            detail or e,
            exc_info=True,
        )
        return None


async def get_ticket_area(zoho_id: str) -> Dict[str, Any]:
    """Read the persisted service-area assignment for a mirror row.

    Returns the assignment fields (id/source/assigned_at/assigned_by) plus the
    resolved area name. Empty dict when the ticket isn't mirrored yet.
    """
    row = await db_supabase.find_one(_TABLE, {"zoho_id": str(zoho_id)})
    if not row:
        return {}
    area_id = row.get("service_area_id")
    name = None
    if area_id:
        area = await db_supabase.find_one("service_areas", {"id": area_id})
        name = (area or {}).get("name")
    return {
        "service_area_id": area_id,
        "service_area_name": name,
        "service_area_source": row.get("service_area_source"),
        "service_area_assigned_at": row.get("service_area_assigned_at"),
        "service_area_assigned_by": row.get("service_area_assigned_by"),
    }


async def set_ticket_area(
    zoho_id: str, service_area_id: Optional[str], *, source: str, assigned_by: str, upsert: bool = False
) -> Dict[str, Any]:
    """Persist (or clear, when ``service_area_id`` is None) a ticket's service
    area on the mirror row. ``source`` is 'auto' or 'manual'.

    ``upsert`` creates a stub mirror row (keyed by ``zoho_id``) when the ticket
    isn't mirrored yet — used for an explicit admin assignment so it isn't lost.
    The auto path leaves it False so merely viewing a ticket never writes a stub.
    """
    updates = {
        "service_area_id": service_area_id,
        "service_area_source": source if service_area_id else None,
        "service_area_assigned_at": datetime.now(timezone.utc).isoformat() if service_area_id else None,
        "service_area_assigned_by": assigned_by if service_area_id else None,
    }
    await db_supabase.update_one(_TABLE, {"zoho_id": str(zoho_id)}, updates, upsert=upsert)
    return await get_ticket_area(zoho_id)


async def assignment_view(zoho_id: str, ticket: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Help Desk service-area panel state for a ticket.

    If the ticket already has an area, returns it. If not, computes a suggestion
    from the contact's ride history and **auto-assigns it** (source 'auto') so
    the admin sees it pre-filled. When there's no suggestion, returns
    ``needs_assignment=True`` so the UI highlights the ticket for a manual pick.
    Auto-assignment is best-effort and never blocks the response.
    """
    current = await get_ticket_area(zoho_id)
    if current.get("service_area_id"):
        return {**current, "needs_assignment": False, "suggested": None}

    suggested = await resolve_suggested_area(ticket)
    if suggested:
        try:
            saved = await set_ticket_area(
                zoho_id, suggested["service_area_id"], source="auto", assigned_by="system"
            )
            if saved.get("service_area_id"):
                return {**saved, "needs_assignment": False, "suggested": suggested}
        except Exception:
            # Persisting is best-effort; fall through to surfacing the suggestion.
            logger.error("auto-assign of service area failed for ticket %s", zoho_id, exc_info=True)
        # Ticket not mirrored yet (no-op update) — surface the suggestion so the
        # admin can apply it manually instead of silently dropping it.
        return {**current, "needs_assignment": True, "suggested": suggested}

    return {**current, "needs_assignment": True, "suggested": None}
