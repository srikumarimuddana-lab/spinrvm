"""C23 Action items 4-5: shared evidence-assembly for a chargeback response.

Both the read-only zip-download endpoint (item 4,
routes/admin/rides.py's admin_get_dispute_evidence_pack) and the
Stripe-submission endpoint (item 5, routes/admin/dispute_evidence_submission.py)
need the SAME assembled evidence: a timeline of the ride, an account-history
summary showing the rider/driver aren't a fraud pattern, and a draft cover
letter. This module builds that once so the two endpoints don't duplicate
(and drift on) the PIPEDA filtering rules below.

PIPEDA data minimization (matches routes/admin/rides.py's existing
route-map.png and invoice endpoints):
- Driver identified by `driver_code` only -- never phone, plate, or address.
- GPS points limited to `navigating_to_pickup` + `trip_in_progress` phases
  (the ride-relevant window), never the driver's full location history.
- Rider identified by name/email as already shown on their own invoice --
  this is being sent back to the SAME payment processor handling their own
  charge, not to a third party.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    from ..repositories._base import count_documents, get_rows
except ImportError:  # pragma: no cover - direct module imports in tests
    from repositories._base import count_documents, get_rows  # type: ignore

logger = logging.getLogger(__name__)

_RIDE_PHASES = ("navigating_to_pickup", "trip_in_progress")


def _iso(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_gps_trail_rows(ride: Dict[str, Any]) -> List[Dict[str, Any]]:
    """GPS points at pickup/dropoff time only -- same filter as route-map.png.
    Returns dicts ready for a CSV writer (fieldnames: timestamp, lat, lng,
    phase)."""
    trail = ride.get("location_trail") or []
    rows = []
    for p in trail:
        if p.get("tracking_phase") not in _RIDE_PHASES:
            continue
        if p.get("lat") is None or p.get("lng") is None:
            continue
        rows.append(
            {
                "timestamp": _iso(p.get("recorded_at") or p.get("timestamp")) or "",
                "lat": p.get("lat"),
                "lng": p.get("lng"),
                "phase": p.get("tracking_phase"),
            }
        )
    return rows


def build_ride_timeline(ride: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordered list of {event, at} covering the ride lifecycle -- request,
    each offer, driver assignment, arrival, trip start/end -- so an admin
    (or Stripe's reviewer) can see the ride actually happened as billed."""
    events: List[Dict[str, Any]] = []

    def _add(event: str, at: Any) -> None:
        ts = _iso(at)
        if ts:
            events.append({"event": event, "at": ts})

    _add("ride_requested", ride.get("created_at"))
    # Offer-funnel rows (repositories.ride_repo.get_ride_details_enriched
    # attaches driver_name per offer): "driver_id,status,eta_seconds,
    # offered_at,responded_at" -- no created_at/accepted_at on this table.
    for offer in ride.get("offers") or []:
        driver_label = offer.get("driver_name") if isinstance(offer, dict) else None
        suffix = f" (driver {driver_label})" if driver_label else ""
        _add(f"offer_sent{suffix}", offer.get("offered_at"))
        status = offer.get("status")
        if offer.get("responded_at") and status:
            _add(f"offer_{status}{suffix}", offer.get("responded_at"))
    _add("driver_accepted", ride.get("driver_accepted_at"))
    _add("driver_arrived", ride.get("driver_arrived_at"))
    _add("trip_started", ride.get("ride_started_at"))
    _add("trip_completed", ride.get("ride_completed_at"))

    events.sort(key=lambda e: e["at"])
    return events


async def build_account_history_summary(ride: Dict[str, Any]) -> Dict[str, Any]:
    """Rider + driver account-standing snapshot -- shows the account isn't a
    fraud pattern (repeat chargebacks, brand-new account, etc). No PII beyond
    what's already on the rider's own invoice / the driver_code."""
    rider_id = ride.get("rider_id")

    summary: Dict[str, Any] = {
        "rider_account_created_at": None,
        "rider_completed_ride_count": None,
        "rider_prior_dispute_count": None,
        "driver_code": ride.get("driver_code") or "",
        # get_ride_details_enriched flattens driver fields directly onto
        # `ride` (ride["driver_completed_rides"]) rather than nesting them
        # under a "driver" key.
        "driver_completed_ride_count": ride.get("driver_completed_rides"),
    }

    if rider_id:
        # Best-effort enrichment only -- the primary evidence (invoice,
        # route map, timeline) doesn't depend on these, so a failure here
        # logs loudly (per CLAUDE.md's do-not-silently-swallow rule) but
        # doesn't block the rest of the evidence pack from being built.
        try:
            rider_rows = await get_rows("users", {"id": rider_id}, limit=1)
            if rider_rows:
                summary["rider_account_created_at"] = _iso(rider_rows[0].get("created_at"))
        except Exception:
            logger.error("dispute_evidence_pack: rider lookup failed for %s", rider_id, exc_info=True)
        try:
            summary["rider_completed_ride_count"] = await count_documents(
                "rides", {"rider_id": rider_id, "status": "completed"}
            )
        except Exception:
            logger.error("dispute_evidence_pack: completed-ride count failed for %s", rider_id, exc_info=True)
        try:
            # stripe_disputes has no user_id column -- only ride_id -- so
            # resolve the rider's ride ids first per CLAUDE.md's
            # cross-table-lookup convention, then filter disputes by those.
            rider_rides = await get_rows("rides", {"rider_id": rider_id}, limit=500, columns="id")
            ride_ids = [r["id"] for r in (rider_rides or []) if r.get("id")]
            if ride_ids:
                prior_disputes = await get_rows("stripe_disputes", {"ride_id": {"$in": ride_ids}}, limit=50)
                summary["rider_prior_dispute_count"] = len(prior_disputes) if prior_disputes is not None else None
            else:
                summary["rider_prior_dispute_count"] = 0
        except Exception:
            logger.error("dispute_evidence_pack: prior-dispute count failed for %s", rider_id, exc_info=True)

    return summary


def build_cover_letter_text(
    ride: Dict[str, Any],
    dispute: Dict[str, Any],
    account_summary: Dict[str, Any],
) -> str:
    """Plain-text draft cover letter -- a starting point for a support agent
    to edit, not a final auto-submitted document."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ride_code = ride.get("ride_code") or ride.get("id") or "unknown"
    amount = Decimal(str(dispute.get("amount_cents") or 0)) / Decimal(100)
    reason = dispute.get("reason") or "unspecified"

    lines = [
        f"Re: Dispute {dispute.get('stripe_dispute_id') or dispute.get('id')} — Ride {ride_code}",
        f"Date: {now}",
        "",
        f"This letter responds to a chargeback (reason: {reason}, amount: ${amount:.2f}) "
        f"filed against ride {ride_code}, completed via the Spinr platform.",
        "",
        "Evidence attached:",
        "- Ride invoice (fare breakdown, GST/PST line items)",
        "- Route map showing the recorded GPS path for pickup through drop-off",
        "- Ride timeline (request, driver assignment, trip start/end)",
        "- GPS trail (pickup-to-dropoff window only)",
        "- Account history summary",
        "",
    ]
    if account_summary.get("rider_completed_ride_count") is not None:
        lines.append(
            f"The account has completed {account_summary['rider_completed_ride_count']} prior ride(s) on the platform."
        )
    if account_summary.get("rider_prior_dispute_count") not in (None, 0):
        lines.append(
            f"Note: this account has {account_summary['rider_prior_dispute_count']} prior "
            "dispute record(s) — review before submission."
        )
    lines.append("")
    lines.append(
        "[Support agent: edit this draft before submitting as the Stripe evidence `uncategorized_text` field.]"
    )
    return "\n".join(lines)
