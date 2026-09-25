"""
disputes.py -- the retired in-app dispute endpoints.

In-app disputes are disabled (owner decision 2026-09-25): a user who
disagrees with a charge emails support@spinr.ca (a Zoho Desk ticket) or
contacts their card issuer (a bank chargeback, handled by the Stripe
charge.dispute.* webhooks + stripe_disputes, which live elsewhere and are
untouched). What remains here are the 410 stubs, kept so an old app build or
an old admin client gets a clear answer instead of a 404/405. The original
handlers were deleted 2026-09-25; the `disputes` table, its migrations and
RLS stay (7-year retention), and existing rows are readable through the
admin GET list/stats/detail routes in routes/admin/support.py. See
docs/change-log/2026-09-25-disable-in-app-disputes.md and
docs/change-log/2026-09-25-remove-disabled-dispute-code.md.
"""

from fastapi import APIRouter, HTTPException

api_router = APIRouter(prefix="/disputes", tags=["Disputes"])

IN_APP_DISPUTES_DISABLED_DETAIL = (
    "In-app disputes are not available. For questions about a charge, "
    "email support@spinr.ca or contact your card issuer."
)
ADMIN_DISPUTE_WRITES_DISABLED_DETAIL = (
    "In-app disputes are disabled; existing dispute records are read-only. "
    "Issue any refund in the Stripe Dashboard. Bank chargebacks are on the Chargebacks tab."
)


@api_router.post("")
async def create_dispute_disabled():
    """In-app disputes are not offered: always 410, no body parsing, no side effects.

    Structured detail (same {code, message} shape as OUTSIDE_SERVICE_AREA,
    which shared/api/client.ts's extractError reads): a plain-string 4xx
    detail goes through utils.pii.redact_error_detail, whose email pattern
    would turn support@spinr.ca into "[redacted]". A dict detail is passed
    through as a deliberately built payload.
    """
    raise HTTPException(
        status_code=410,
        detail={"code": "IN_APP_DISPUTES_DISABLED", "message": IN_APP_DISPUTES_DISABLED_DETAIL},
    )


# ============ Admin Dispute Endpoints ============

# GET /admin/disputes (list), /stats and /{id} are served by
# routes/admin/support.py, which is mounted first. A second, shadowed GET
# list handler that used to live here was deleted 2026-09-25; the
# test_disputes_disabled.py route-table test pins which handler serves it.
admin_router = APIRouter(prefix="/disputes", tags=["Admin Disputes"])


@admin_router.put("/{dispute_id}/resolve")
async def admin_resolve_dispute_disabled(dispute_id: str):
    """In-app disputes are disabled: always 410, no DB read, no Stripe refund, no push."""
    raise HTTPException(status_code=410, detail=ADMIN_DISPUTE_WRITES_DISABLED_DETAIL)
