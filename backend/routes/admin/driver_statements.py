"""Admin driver earnings-statement endpoints (driver → payouts section).

Backs the admin dashboard's payout view: filter a driver's statement by
dates, then either DOWNLOAD the PDF or SEND it to the driver by email —
"just in case" access to exactly the document the periodic statement job
(utils/driver_statement_job.py) emails drivers.

- ``GET  /api/admin/drivers/{driver_id}/statements`` — ledger of produced
  statements (sent / skipped / failed) for support triage.
- ``GET  /api/admin/drivers/{driver_id}/statements/pdf`` — on-demand PDF for
  an anchored period (``period_type`` + ``period_start``) or an arbitrary
  date range (``start`` + ``end``, the payout-section date filter).
- ``POST /api/admin/drivers/{driver_id}/statements/email`` — same selection,
  emailed to the driver's on-file address.

Statements are regenerated from live data on every call (never stored), so
admin download, admin re-send, and the driver's own emailed copy can never
diverge. Gated on the "drivers" module at the mount; download and send are
audited with ids + period only (no PII in audit rows, per CLAUDE.md).
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...features import send_email
    from ...utils.audit_logger import log_admin_action
    from ...utils.driver_statement import PERIOD_TYPES, build_custom_statement, build_statement
    from ...utils.driver_statement_pdf import generate_driver_statement_pdf
    from ...utils.rate_limiter import (
        admin_statement_download_limit,
        admin_statement_email_limit,
    )
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # noqa: F401
    from features import send_email  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_statement import (  # type: ignore
        PERIOD_TYPES,
        build_custom_statement,
        build_statement,
    )
    from utils.driver_statement_pdf import generate_driver_statement_pdf  # type: ignore
    from utils.rate_limiter import (  # type: ignore
        admin_statement_download_limit,
        admin_statement_email_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD") from e


async def _driver_or_404(driver_id: str) -> dict:
    rows = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Driver not found")
    return rows[0]


async def _build_for_selection(
    driver: dict,
    period_type: Optional[str],
    period_start: Optional[str],
    start: Optional[str],
    end: Optional[str],
) -> dict:
    """Resolve the admin's period selection into a built statement.

    Two mutually exclusive selections: an anchored period (period_type +
    period_start — what the job sent) or a custom date range (start + end —
    the payout-section date filter).
    """
    user = await db_supabase.get_user_by_id(driver.get("user_id")) if driver.get("user_id") else None
    name = (
        f"{(user or {}).get('first_name', '')} {(user or {}).get('last_name', '')}".strip()
        or driver.get("name")
        or "Driver"
    )
    try:
        if start or end:
            if not (start and end):
                raise HTTPException(status_code=422, detail="Provide both start and end for a date range")
            return await build_custom_statement(
                driver, _parse_date(start, "start"), _parse_date(end, "end"), driver_name=name
            )
        if period_type and period_start:
            if period_type not in PERIOD_TYPES:
                raise HTTPException(status_code=422, detail="period_type must be weekly or monthly")
            return await build_statement(
                driver, period_type, _parse_date(period_start, "period_start"), driver_name=name
            )
    except ValueError as e:
        # Misaligned anchor or invalid/oversized range from the builder.
        raise HTTPException(status_code=422, detail=str(e)) from e
    raise HTTPException(
        status_code=422,
        detail="Provide period_type+period_start or start+end dates",
    )


@router.get("/drivers/{driver_id}/statements")
async def list_driver_statements(
    driver_id: str,
    limit: int = Query(24, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_admin_user),
):
    """Ledger of statements the periodic job produced for this driver."""
    await _driver_or_404(driver_id)
    rows = await db_supabase.get_rows(
        "driver_statements",
        {"driver_id": driver_id},
        limit=limit,
        offset=offset,
        order="period_start",
        desc=True,
    )
    return {
        "statements": [
            {
                "id": r.get("id"),
                "period_type": r.get("period_type"),
                "period_start": r.get("period_start"),
                "period_end": r.get("period_end"),
                "status": r.get("status"),
                "totals": r.get("totals"),
                "email_sent_at": r.get("email_sent_at"),
                "created_at": r.get("created_at"),
            }
            for r in rows or []
        ]
    }


@router.get("/drivers/{driver_id}/statements/pdf")
@admin_statement_download_limit
async def download_driver_statement(
    driver_id: str,
    request: Request = None,
    period_type: Optional[str] = Query(None),
    period_start: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    admin: dict = Depends(get_admin_user),
):
    """Download the statement PDF for the selected period / date range.

    Rate-limited (60/hour): rebuilds the statement from live data and renders
    a PDF. SlowAPI needs a parameter named ``request`` typed as starlette
    Request; do not remove it.
    """
    driver = await _driver_or_404(driver_id)
    statement = await _build_for_selection(driver, period_type, period_start, start, end)
    pdf_bytes = generate_driver_statement_pdf(statement)

    await log_admin_action(
        admin,
        "driver_statement_download",
        "drivers",
        driver_id,
        {"period_type": statement["period_type"], "period_start": statement["period_start"], "period_end": statement["period_end"]},
    )
    filename = f"spinr-statement-{statement['period_type']}-{statement['period_start']}-{driver_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/drivers/{driver_id}/statements/email")
@admin_statement_email_limit
async def email_driver_statement_admin(
    driver_id: str,
    request: Request = None,
    period_type: Optional[str] = Query(None),
    period_start: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    admin: dict = Depends(get_admin_user),
):
    """Send the selected statement to the driver's on-file email address.

    Rate-limited (20/hour): this sends mail to the DRIVER's inbox at an
    admin's discretion, so it needs the same class of guard as the driver's
    own tax-document sends — a compromised admin session must not be able to
    inbox-bomb a driver or burn SES sender reputation. SlowAPI needs a
    parameter named ``request`` typed as starlette Request; do not remove it.
    """
    driver = await _driver_or_404(driver_id)
    user = await db_supabase.get_user_by_id(driver.get("user_id")) if driver.get("user_id") else None
    email = ((user or {}).get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Driver has no email address on file")

    statement = await _build_for_selection(driver, period_type, period_start, start, end)
    pdf_bytes = generate_driver_statement_pdf(statement)
    filename = f"spinr-statement-{statement['period_type']}-{statement['period_start']}.pdf"
    sent = await send_email(
        to=email,
        subject=f"Your Spinr earnings statement — {statement['period_label']}",
        body=(
            "Hi,\n\n"
            f"Your Spinr earnings statement for {statement['period_label']} is attached "
            f'as a PDF ("{filename}").\n\n'
            "Questions? Contact support@spinr.ca.\n\n"
            "— The Spinr Team"
        ),
        attachments=[{"filename": filename, "content": pdf_bytes, "mime": "application/pdf"}],
        email_type="transactional",
        recipient_user_id=driver.get("user_id"),
        log_id="stmt-admin",
    )
    if not sent:
        # Provider rejected without raising — surface it, never report success.
        logger.error("admin statement email NOT sent (provider rejected) driver=%s", driver_id)
        raise HTTPException(status_code=502, detail="Email could not be sent. Try again.")

    await log_admin_action(
        admin,
        "driver_statement_email",
        "drivers",
        driver_id,
        {
            "period_type": statement["period_type"],
            "period_start": statement["period_start"],
            "period_end": statement["period_end"],
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"sent": True, "period_label": statement["period_label"]}
