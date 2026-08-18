"""C23 (ACTION_ITEMS.md) Action item 4: zip download of chargeback evidence
for a ride -- invoice PDF, route-map PNG, ride-timeline + account-history
PDF, a GPS-trail CSV, and a draft cover letter.

Kept as its own router (not folded into routes/admin/rides.py's
rides_router) so it can be gated by require_module("support") -- the module
that actually governs the chargeback-handling surface (the Chargebacks tab
lives on support.py's support_router, C23 item 3) -- independent of
require_module("rides"), which is a much broader ops/dispatch grant that
has no business reading PIPEDA-sensitive dispute evidence. Security-auditor
finding on the first version of this endpoint: it was mounted under
rides_router and inherited "rides", which both over-grants (any rides-module
admin could download it) and under-grants (a support-only admin, the actual
intended user, couldn't).

Read-only: this is a *download* for a human to review and edit, never
auto-submitted -- that's item 5 (routes/admin/dispute_evidence_submission.py),
behind its own explicit-confirm, require_super_admin endpoint.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Response

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
    from ...utils.dispute_evidence_pack import (
        build_account_history_summary,
        build_cover_letter_text,
        build_gps_trail_rows,
        build_ride_timeline,
    )
    from ...utils.dispute_evidence_pdf import render_invoice_summary_pdf, render_timeline_and_history_pdf
    from .rides import _fetch_route_map_png_bytes
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.audit_logger import log_admin_action
    from utils.dispute_evidence_pack import (
        build_account_history_summary,
        build_cover_letter_text,
        build_gps_trail_rows,
        build_ride_timeline,
    )
    from utils.dispute_evidence_pdf import render_invoice_summary_pdf, render_timeline_and_history_pdf

    from .rides import _fetch_route_map_png_bytes  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/rides/{ride_id}/dispute-pack")
async def admin_get_dispute_evidence_pack(
    ride_id: str,
    admin_user: dict = Depends(get_admin_user),
):
    """Zip of everything a support agent needs to respond to a card-network
    chargeback on this ride.

    Looks up the most recent stripe_disputes row for this ride so the pack
    can reference the actual dispute id/reason/amount; still generates a
    ride-only pack (with a placeholder dispute reference) if no dispute row
    exists yet, since a support agent may want to review the ride before a
    dispute officially lands.
    """
    ride = await db_supabase.get_ride_details_enriched(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    try:
        dispute_rows = await db_supabase.get_rows(
            "stripe_disputes",
            {"ride_id": ride_id},
            order="created_at",
            desc=True,
            limit=1,
        )
    except Exception as exc:
        logger.error("dispute-pack: stripe_disputes lookup failed for ride %s: %s", ride_id, exc, exc_info=True)
        raise HTTPException(status_code=503, detail="ERR_DATABASE") from exc
    dispute = dispute_rows[0] if dispute_rows else {"stripe_dispute_id": "(no dispute on file)", "reason": "n/a"}

    account_summary = await build_account_history_summary(ride)
    timeline = build_ride_timeline(ride)
    gps_rows = build_gps_trail_rows(ride)
    cover_letter = build_cover_letter_text(ride, dispute, account_summary)

    invoice_pdf = render_invoice_summary_pdf(ride, dispute)
    timeline_pdf = render_timeline_and_history_pdf(ride, timeline, account_summary)

    # Route map is best-effort -- a missing Google Maps key or upstream
    # failure shouldn't block the rest of the pack from downloading.
    try:
        route_map_png = await _fetch_route_map_png_bytes(ride)
    except HTTPException as exc:
        logger.warning("dispute-pack: route map unavailable for ride %s: %s", ride_id, exc.detail)
        route_map_png = None

    ride_code = ride.get("ride_code") or ride_id

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("invoice.pdf", invoice_pdf)
        zf.writestr("timeline_and_account_history.pdf", timeline_pdf)
        if route_map_png:
            zf.writestr("route_map.png", route_map_png)
        else:
            zf.writestr("route_map_UNAVAILABLE.txt", "Route map could not be generated -- see admin logs.")

        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=["timestamp", "lat", "lng", "phase"])
        writer.writeheader()
        writer.writerows(gps_rows)
        zf.writestr("gps_trail.csv", csv_buf.getvalue())

        zf.writestr("cover_letter_DRAFT.txt", cover_letter)

    await log_admin_action(
        admin_user,
        "dispute_evidence_pack_download",
        "ride",
        ride_id,
        {"stripe_dispute_id": dispute.get("stripe_dispute_id"), "has_route_map": route_map_png is not None},
    )

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="dispute_evidence_{ride_code}.zip"'},
    )
