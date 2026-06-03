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


async def create_ticket_for_lost_and_found(
    case: Dict[str, Any], ride: Optional[Dict[str, Any]] = None
) -> None:
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
            await db_supabase.update_one(
                "lost_and_found", {"id": case["id"]}, {"zoho_ticket_id": zoho_id}
            )
            logger.info("Created Zoho ticket %s for L&F case %s", zoho_id, case.get("id"))
    except ZohoDeskError as e:
        # Misconfigured / missing department / scope — log, don't break the flow.
        logger.warning("Zoho L&F ticket skipped (%s): %s", e.status, e.message)
    except Exception:
        logger.error(
            "Failed to create Zoho ticket for L&F case %s", case.get("id"), exc_info=True
        )
