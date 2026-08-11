"""T4A summaries, earnings CSV export, PIPEDA data export.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    Any,
    APIRouter,
    BackgroundTasks,
    Decimal,
    Depends,
    HTTPException,
    Query,
    Request,
    RideStatus,
    datetime,
    db_supabase,
    dsar_export_limit,
    get_current_user,
    json,
    logger,
    tax_doc_email_limit,
    timedelta,
    timezone,
    uuid,
)
from ._shared import (  # noqa: F401
    _money_str,
    _ride_income,
)

try:
    from ...utils.legacy_rides import drop_legacy_rides
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from utils.legacy_rides import drop_legacy_rides  # type: ignore

router = APIRouter()

# Matches the Saskatchewan trip-record retention window (7 years, see
# CLAUDE.md § regulatory) — there is no reportable income older than the
# records we are required to keep.
_T4A_YEAR_LOOKBACK = 7


# MUST stay declared BEFORE `/t4a/{year}`: that route types `year` as int, so
# a request for /t4a/years would otherwise be matched there and 422 on the
# path coercion instead of reaching this handler.
@router.get("/t4a/years")
async def get_t4a_years(current_user: dict = Depends(get_current_user)):
    """Tax years this driver actually has reportable income for.

    The apps used to synthesize "the last three completed years" client-side
    and offer a T4A for each, so a new driver — or a migrated one whose only
    income was previous-app rides now excluded from Spinr's books
    (utils/legacy_rides) — was offered slips for years they earned nothing,
    and emailing one produced a $0.00 document. Years with no income are
    simply absent here so the client can render nothing instead of guessing.

    Buckets by ``created_at`` because that is the field
    ``get_rides_for_driver`` filters the slip on — using a different field
    here would let this list disagree with the slip it advertises.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    this_year = datetime.now(timezone.utc).year
    earliest = this_year - _T4A_YEAR_LOOKBACK + 1

    # Same two income sources, and the same legacy exclusion, as the slip.
    rides = drop_legacy_rides(
        await db_supabase.get_rides_for_driver(
            driver["id"],
            statuses=[RideStatus.COMPLETED],
            from_date=f"{earliest}-01-01",
            to_date=f"{this_year + 1}-01-01",
            limit=10000,
        )
    )
    synced_rows = await db_supabase.get_rows(
        "payouts",
        {
            "driver_id": driver["id"],
            "payout_type": "stripe_sync",
            "created_at": {
                "$gte": f"{earliest}-01-01T00:00:00+00:00",
                "$lt": f"{this_year + 1}-01-01T00:00:00+00:00",
            },
        },
        limit=10000,
    )

    totals: dict[int, Decimal] = {}
    trips: dict[int, int] = {}

    def _year_of(row: dict) -> int | None:
        stamp = str(row.get("created_at") or "")[:4]
        return int(stamp) if stamp.isdigit() else None

    for r in rides:
        y = _year_of(r)
        if y is None:
            continue
        totals[y] = totals.get(y, Decimal("0")) + _ride_income(r)
        trips[y] = trips.get(y, 0) + 1
    for p in synced_rows:
        y = _year_of(p)
        if y is None:
            continue
        totals[y] = totals.get(y, Decimal("0")) + Decimal(str(p.get("amount") or "0"))

    years = [
        {
            "year": y,
            "total_earnings": _money_str(total),
            "total_trips": trips.get(y, 0),
        }
        for y, total in sorted(totals.items(), reverse=True)
        # A year that nets to zero (or negative, via reversals) is not income
        # worth offering a slip for.
        if total > Decimal("0")
    ]
    return {"years": years}


@router.get("/t4a/{year}")
async def get_t4a_summary(year: int, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Previous-app rides brought in by the booking importer are NOT Spinr
    # income: that money was earned and paid out by the old app, and where it
    # was paid through Stripe it is already reported below via the
    # 'stripe_sync' rows. Counting both would report the same legacy dollars
    # to the CRA twice. The repo helper takes no extra filters, so the
    # exclusion is applied here (see utils/legacy_rides).
    rides = drop_legacy_rides(
        await db_supabase.get_rides_for_driver(
            driver["id"],
            statuses=[RideStatus.COMPLETED],
            from_date=f"{year}-01-01",
            to_date=f"{year + 1}-01-01",
            limit=10000,
        )
    )

    # Legacy-era income synced from Stripe transfer history
    # (services/stripe_payout_sync_service.py): payouts rows with
    # payout_type='stripe_sync' are the record of legacy income actually PAID
    # through Stripe — the T4A must report it, attributed to the year of the
    # transfer (CRA reports amounts paid). App-native payouts are cash-outs of
    # the ride earnings summed below, and 'legacy_import' offsets pair with
    # imported rides now excluded above — only the synced type is added, so
    # nothing double-counts.
    # Queried via this module's db binding so the established
    # _deps.db_supabase patch point covers it in tests.
    synced_rows = await db_supabase.get_rows(
        "payouts",
        {
            "driver_id": driver["id"],
            "payout_type": "stripe_sync",
            "created_at": {
                "$gte": f"{year}-01-01T00:00:00+00:00",
                "$lt": f"{year + 1}-01-01T00:00:00+00:00",
            },
        },
        limit=10000,
    )
    synced_earnings = sum((Decimal(str(p.get("amount") or "0")) for p in synced_rows), Decimal("0"))

    # T4A reports the driver's INCOME — sum driver_earnings (see _ride_income),
    # not the gross fare; that would misreport income to the CRA if they ever
    # diverge under a future fee model.
    total_earnings = _money_str(sum((_ride_income(r) for r in rides), Decimal("0")) + synced_earnings)

    driver_name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or None
    return {
        "year": year,
        "total_earnings": total_earnings,
        "total_trips": len(rides),
        "platform_fees": "0.00",
        "net_earnings": total_earnings,
        # Slice of total_earnings that came from the Stripe-synced legacy
        # history — shown so a driver (or auditor) can reconcile the slip.
        "legacy_synced_earnings": _money_str(synced_earnings),
        "gst_registered": driver.get("gst_registered", False),
        "gst_bn": driver.get("gst_bn") or "",
        # Last 4 only. The driver's slip shows it masked so they can confirm we
        # hold the right number; `drivers.sin` itself is never read here, and
        # this summary is returned over the API.
        "sin_last4": driver.get("sin_last4") or "",
        "sin_on_file": bool(driver.get("sin")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_url": f"/api/v1/drivers/t4a/{year}/pdf",
        "driver_name": driver_name,
    }


@router.get("/t4a/{year}/pdf")
async def download_t4a_pdf(year: int, current_user: dict = Depends(get_current_user)):
    from fastapi.responses import Response as _Response

    summary = await get_t4a_summary(year, current_user)
    summary["driver_name"] = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or None
    pdf_bytes = _deps.generate_t4a_pdf(summary)
    filename = f"T4A_{year}_{current_user['id'][:8]}.pdf"
    return _Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/earnings/export")
async def export_earnings(year: int = Query(None), current_user: dict = Depends(get_current_user)):
    if not year:
        year = datetime.now(timezone.utc).year

    summary_data = await get_t4a_summary(year, current_user)

    # CRA T4A-compatible CSV. GST/BN columns are required for drivers who
    # earn above the T4A reporting threshold and hold a GST/HST account.
    csv_data = (
        "Year,Total Earnings,Total Trips,Net Earnings,GST Registered,GST/HST Business Number,SIN\n"
        f"{year},"
        f"{summary_data['total_earnings']},"
        f"{summary_data['total_trips']},"
        f"{summary_data['net_earnings']},"
        f"{'Yes' if summary_data['gst_registered'] else 'No'},"
        f"{summary_data['gst_bn']},"
        # Masked, matching the PDF. This file leaves our control the moment the
        # driver forwards it to an accountant, so it carries the last 4 only.
        f"{'Ending in ' + summary_data['sin_last4'] if summary_data['sin_last4'] else 'Not on file'}"
    )
    filename = f"earnings_export_{year}.csv"

    return {"data": csv_data, "filename": filename}


def _driver_email_or_400(current_user: dict) -> str:
    """Return the driver's on-file email or raise 400.

    Tax documents are delivered by email only (no in-app download), so a real
    address is mandatory. Falling back to the phone number would hand a raw
    phone number to the email provider — it fails to send and risks logging PII.
    """
    email = (current_user.get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="No email address on file to send the document to.")
    return email


async def _email_driver_document(
    user_id: str,
    email: str,
    *,
    subject: str,
    body: str,
    filename: str,
    content: bytes,
    mime: str,
    log_id: str,
) -> None:
    """Background task: email a generated document to the driver as an attachment.

    Shared by the T4A PDF and earnings-CSV senders (and any future tax/document
    email). Runs off-request, so a send failure can't surface to the client —
    it is logged loudly (never swallowed), mirroring the DSAR export pattern.
    """
    try:
        sent = await _deps.send_email(
            to=email,
            subject=subject,
            body=body,
            attachments=[{"filename": filename, "content": content, "mime": mime}],
            email_type="transactional",
            recipient_user_id=user_id,
            log_id=log_id,
        )
        if sent:
            logger.info("%s emailed for user %s (%s)", log_id, user_id, filename)
        else:
            # Both providers rejected without raising — surface, don't log success.
            logger.error("%s email NOT sent (provider rejected) for user %s (%s)", log_id, user_id, filename)
    except Exception as exc:
        original = exc.details.get("original") if hasattr(exc, "details") and isinstance(exc.details, dict) else None
        logger.error(
            "%s email failed for user %s (%s): %s%s",
            log_id,
            user_id,
            filename,
            exc,
            f" — {original}" if original else "",
            exc_info=True,
        )


async def _email_t4a_document(user_id: str, email: str, year: int, summary: dict) -> None:
    """Background task: render the T4A PDF and email it to the driver."""
    try:
        pdf_bytes = _deps.generate_t4a_pdf(summary)
    except Exception:
        # Render failure is logged here (not in the shared sender) since it's
        # specific to PDF generation; nothing is emailed.
        logger.error("T4A PDF render failed for user %s year %s", user_id, year, exc_info=True)
        return
    filename = f"T4A_{year}_{user_id[:8]}.pdf"
    await _email_driver_document(
        user_id,
        email,
        subject=f"Your Spinr T4A summary for {year}",
        body=(
            "Hi,\n\n"
            f"As requested, your T4A earnings summary for the {year} tax year is "
            f'attached as a PDF ("{filename}").\n\n'
            "Keep this document for your Canadian tax filing. If you have any "
            "questions, contact support@spinr.ca.\n\n"
            "— The Spinr Team"
        ),
        filename=filename,
        content=pdf_bytes,
        mime="application/pdf",
        log_id="t4a",
    )


async def _email_earnings_csv(user_id: str, email: str, year: int, csv_data: str) -> None:
    """Background task: email the trip-by-trip earnings CSV to the driver."""
    filename = f"earnings_export_{year}.csv"
    await _email_driver_document(
        user_id,
        email,
        subject=f"Your Spinr earnings export for {year}",
        body=(
            "Hi,\n\n"
            f"As requested, your detailed earnings export for {year} is attached "
            f'as a CSV ("{filename}").\n\n'
            "If you have any questions, contact support@spinr.ca.\n\n"
            "— The Spinr Team"
        ),
        filename=filename,
        content=csv_data.encode("utf-8"),
        mime="text/csv",
        log_id="earnings",
    )


@router.post("/t4a/{year}/email")
@tax_doc_email_limit
async def email_t4a_summary(
    year: int,
    background_tasks: BackgroundTasks,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Email the T4A summary PDF to the driver (no in-app download).

    Returns immediately; the PDF render and email send happen in a background
    task so the driver does not wait.

    Rate-limited (@tax_doc_email_limit, 6/hour) — each call reads up to 10k
    rides and sends an email; the cap prevents inbox/SES abuse. SlowAPI needs a
    parameter named ``request`` typed as starlette Request; do not remove it.
    """
    email = _driver_email_or_400(current_user)
    summary = await get_t4a_summary(year, current_user)
    background_tasks.add_task(_email_t4a_document, current_user["id"], email, year, summary)
    return {"message": f"Your T4A summary for {year} is on its way. Check your email."}


@router.post("/earnings/export/email")
@tax_doc_email_limit
async def email_earnings_export(
    background_tasks: BackgroundTasks,
    year: int = Query(None),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Email the trip-by-trip earnings CSV to the driver (no in-app download).

    Rate-limited (@tax_doc_email_limit, 6/hour). SlowAPI needs a parameter
    named ``request`` typed as starlette Request; do not remove it.
    """
    if not year:
        year = datetime.now(timezone.utc).year
    email = _driver_email_or_400(current_user)
    export = await export_earnings(year=year, current_user=current_user)
    background_tasks.add_task(_email_earnings_csv, current_user["id"], email, year, export["data"])
    return {"message": f"Your earnings export for {year} is on its way. Check your email."}


async def _email_statement_document(user_id: str, email: str, statement: dict) -> None:
    """Background task: render the earnings-statement PDF and email it."""
    try:
        from ...utils.driver_statement_pdf import generate_driver_statement_pdf
    except ImportError:
        from utils.driver_statement_pdf import generate_driver_statement_pdf  # type: ignore
    try:
        pdf_bytes = generate_driver_statement_pdf(statement)
    except Exception:
        logger.error("statement PDF render failed for user %s", user_id, exc_info=True)
        return
    filename = f"spinr-statement-{statement['period_type']}-{statement['period_start']}.pdf"
    await _email_driver_document(
        user_id,
        email,
        subject=f"Your Spinr earnings statement — {statement['period_label']}",
        body=(
            "Hi,\n\n"
            f"As requested, your Spinr earnings statement for {statement['period_label']} "
            f'is attached as a PDF ("{filename}").\n\n'
            f"  Total earnings: ${statement['earnings']['total']}\n"
            f"  Trips completed: {statement['trips']}\n"
            f"  Paid out this period: ${statement['payouts_total']}\n\n"
            "Questions? Contact support@spinr.ca.\n\n"
            "— The Spinr Team"
        ),
        filename=filename,
        content=pdf_bytes,
        mime="application/pdf",
        log_id="stmt",
    )


@router.post("/statements/email")
@tax_doc_email_limit
async def email_driver_statement(
    background_tasks: BackgroundTasks,
    period_type: str = Query(..., description="weekly or monthly"),
    period_start: str = Query(..., description="Monday (weekly) or 1st (monthly), YYYY-MM-DD"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Email the driver their earnings statement for one anchored period —
    self-serve re-send of what the periodic job delivers (drivers can always
    request a copy, e.g. after changing their email address).

    Rate-limited (@tax_doc_email_limit, 6/hour) like the other tax-document
    senders. SlowAPI needs a parameter named ``request`` typed as starlette
    Request; do not remove it.
    """
    from datetime import date as _date

    try:
        from ...utils.driver_statement import PERIOD_TYPES, build_statement
    except ImportError:
        from utils.driver_statement import PERIOD_TYPES, build_statement  # type: ignore

    if period_type not in PERIOD_TYPES:
        raise HTTPException(status_code=422, detail="period_type must be weekly or monthly")
    try:
        start_d = _date.fromisoformat(period_start)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="period_start must be YYYY-MM-DD") from e

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    email = _driver_email_or_400(current_user)

    driver_name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or None
    try:
        statement = await build_statement(driver, period_type, start_d, driver_name=driver_name)
    except ValueError as e:
        # Misaligned anchor (weekly not a Monday / monthly not the 1st).
        raise HTTPException(status_code=422, detail=str(e)) from e

    background_tasks.add_task(_email_statement_document, current_user["id"], email, statement)
    return {"message": f"Your earnings statement for {statement['period_label']} is on its way. Check your email."}


# Account (users) fields omitted from a data export: credentials and internal
# session/auth state that are not "personal data about the subject".
#  - password_hash: credential
#  - fcm_token*: device push credentials (impersonation risk if intercepted)
#  - token_version / current_session_id / sessions_invalid_before: auth/session
#    revocation state, useless to the subject and a replay-window roadmap
#  - stripe_customer_id: operational Stripe identifier
_EXPORT_REDACT_ACCOUNT = frozenset(
    {
        "password_hash",
        "fcm_token",
        "fcm_token_rider",
        "fcm_token_driver",
        "token_version",
        "current_session_id",
        "sessions_invalid_before",
        "stripe_customer_id",
    }
)

# Per-ride fields omitted from a data export: these describe the RIDER, not the
# driver (the data subject). Raw pickup/dropoff coordinates, the route polyline,
# and rider_id are third-party PII (PIPEDA s.4.5). The human-readable
# pickup/dropoff addresses are kept — they are the driver's own trip record.
_EXPORT_REDACT_RIDE = frozenset(
    {
        "rider_id",
        "pickup_lat",
        "pickup_lng",
        "pickup_nav_lat",
        "pickup_nav_lng",
        "dropoff_lat",
        "dropoff_lng",
        "dropoff_nav_lat",
        "dropoff_nav_lng",
        "route_polyline",
        "phase_polylines",
        "polyline",
    }
)


# Driver-profile (drivers) fields omitted from a data export:
#  - password_hash / fcm_token: credentials
#  - stripe_account_id / bank_account: financial credentials (already excluded
#    from normal self-responses via _STRIP_FROM_SELF_RESPONSE)
#  - lat / lng / location_geog: transient last-known GPS, not stored profile data
_EXPORT_REDACT_DRIVER = frozenset(
    {
        "password_hash",
        "fcm_token",
        "stripe_account_id",
        "bank_account",
        "lat",
        "lng",
        "location_geog",
    }
)


def _csv_cell(value: Any) -> str:
    """Render a single CSV cell. Nested dict/list → compact JSON; None → ''."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _rows_to_csv(rows: list) -> str:
    """Tabular CSV for a list of dict records (union of keys, first-seen order)."""
    if not rows:
        return "No records.\n"
    import csv  # noqa: PLC0415
    import io  # noqa: PLC0415

    fieldnames: list = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(row.get(k)) for k in fieldnames})
    return buf.getvalue()


def _object_to_csv(obj: dict) -> str:
    """Two-column field,value CSV for a single record (account/profile)."""
    import csv  # noqa: PLC0415
    import io  # noqa: PLC0415

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["field", "value"])
    for key, value in (obj or {}).items():
        writer.writerow([key, _csv_cell(value)])
    return buf.getvalue()


def _build_export_readme(payload: dict, generated_on: str) -> str:
    """Human-readable index of what's in the export archive."""
    account = payload.get("account", {}) or {}
    raw_name = account.get("name") or account.get("full_name") or account.get("first_name") or "Driver"
    # Strip control characters so a name with newlines can't corrupt the README.
    name = " ".join(str(raw_name).split()) or "Driver"
    return (
        "Spinr — Personal Data Export\n"
        "============================\n\n"
        f"Generated: {generated_on}\n"
        f"Account: {name}\n\n"
        "This archive contains the personal data Spinr holds about you, provided\n"
        "in PIPEDA-compliant form. Files:\n\n"
        "  account.csv                  Your account record (field,value).\n"
        "  driver_profile.csv           Your driver profile (field,value).\n"
        "  rides.csv                    Your trip history (one row per ride).\n"
        "  payouts.csv                  Your payout history (one row per payout).\n"
        "  documents.csv                Records of documents you uploaded\n"
        "                               (the file contents themselves are not included).\n"
        "  notification_preferences.csv Your notification settings.\n"
        "  raw_data.json                The complete export in machine-readable JSON.\n\n"
        "Counts:\n"
        f"  Rides:     {len(payload.get('rides') or [])}\n"
        f"  Payouts:   {len(payload.get('payouts') or [])}\n"
        f"  Documents: {len(payload.get('documents') or [])}\n\n"
        "Questions or deletion requests: privacy@spinr.ca\n"
    )


def _build_export_zip(payload: dict, generated_on: str) -> bytes:
    """Bundle the export payload into a ZIP of CSV files + README + JSON."""
    import io  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    files = {
        "README.txt": _build_export_readme(payload, generated_on),
        "account.csv": _object_to_csv(payload.get("account", {})),
        "driver_profile.csv": _object_to_csv(payload.get("driver_profile", {})),
        "rides.csv": _rows_to_csv(payload.get("rides") or []),
        "payouts.csv": _rows_to_csv(payload.get("payouts") or []),
        "documents.csv": _rows_to_csv(payload.get("documents") or []),
        "notification_preferences.csv": _rows_to_csv(payload.get("notification_preferences") or []),
        "raw_data.json": json.dumps(payload, indent=2, default=str),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _build_export_email_html(filename: str) -> str:
    """Lyft-style 'your data is ready' HTML email body."""
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1a1a1a;line-height:1.5">'
        '<h2 style="margin:0 0 12px">Your data export is ready</h2>'
        "<p>As requested, your personal data held by Spinr is attached as a ZIP archive "
        f"(<strong>{filename}</strong>).</p>"
        "<p>Inside you'll find spreadsheet (CSV) files you can open in Excel, Numbers, or "
        "Google Sheets:</p>"
        "<ul>"
        "<li><strong>README.txt</strong> — what each file contains</li>"
        "<li><strong>account.csv</strong>, <strong>driver_profile.csv</strong> — your profile</li>"
        "<li><strong>rides.csv</strong> — your trip history</li>"
        "<li><strong>payouts.csv</strong> — your payout history</li>"
        "<li><strong>documents.csv</strong> — your uploaded document records</li>"
        "<li><strong>notification_preferences.csv</strong> — your notification settings</li>"
        "<li><strong>raw_data.json</strong> — the complete machine-readable export</li>"
        "</ul>"
        '<p style="color:#555;font-size:13px">Questions about your data or want it deleted? '
        'Contact <a href="mailto:privacy@spinr.ca">privacy@spinr.ca</a>.</p>'
        '<p style="color:#888;font-size:12px">— The Spinr Team</p>'
        "</div>"
    )


# Signed download link lives for 7 days — long enough for the driver to grab it
# on their own schedule, short enough that a leaked link self-expires.
_EXPORT_LINK_TTL_SECONDS = 7 * 24 * 3600


async def _upload_export_zip(user_id: str, zip_bytes: bytes, expires_in_seconds: int) -> str:
    """Upload the export ZIP to the private ``data-exports`` bucket and return a
    time-limited signed download URL. Raises on failure so the caller can fall
    back to attaching the ZIP."""
    import asyncio  # noqa: PLC0415

    try:
        from ...supabase_client import supabase  # type: ignore
    except ImportError:
        from supabase_client import supabase  # type: ignore
    try:
        from ...documents import _extract_signed_url  # type: ignore
    except ImportError:
        from documents import _extract_signed_url  # type: ignore

    bucket = "data-exports"
    storage_path = f"exports/{user_id}/{uuid.uuid4()}.zip"
    loop = asyncio.get_running_loop()

    # Best-effort provisioning: a private bucket so objects are reachable only
    # via a signed URL. Swallow "already exists" and any supabase-py signature
    # drift — a genuinely missing bucket surfaces on the upload below.
    def _ensure_bucket() -> None:
        try:
            supabase.storage.create_bucket(bucket, options={"public": False})
        except Exception as exc:
            # Already exists (the common case) or a supabase-py signature
            # difference — debug-only; a real missing bucket surfaces on upload.
            logger.debug("data-exports bucket ensure skipped: %s", exc)

    await loop.run_in_executor(None, _ensure_bucket)
    await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(bucket).upload(
            path=storage_path,
            file=zip_bytes,
            file_options={"content-type": "application/zip", "upsert": "true"},
        ),
    )
    res = await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(bucket).create_signed_url(storage_path, expires_in_seconds),
    )

    # Record the object so the purge loop can delete it after the link expires
    # (PIPEDA data minimization — see utils/data_export_purge.py). Best-effort:
    # the export itself already succeeded, so a tracking-insert failure must not
    # fail the user's request — but log it at error level so the orphan is
    # visible (the loop can only purge what it knows about).
    try:
        await db_supabase.insert_one(
            "data_export_objects",
            {
                "user_id": user_id,
                "storage_path": storage_path,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat(),
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to record data-export object for purge tracking (orphan risk) user=%s: %s",
            user_id,
            exc,
            exc_info=True,
        )

    return _extract_signed_url(res)


#: What the export ZIP contains, itemised.
#: An access request is answered properly by telling the person what they are
#: being given, not just handing them an archive — so this manifest survives
#: from the pre-branding plain-text version rather than being summarised away.
_EXPORT_MANIFEST = (
    "The download is a ZIP archive containing:\n"
    "• README.txt — what each file contains\n"
    "• account.csv, driver_profile.csv — your profile\n"
    "• rides.csv — your trip history\n"
    "• payouts.csv — your payout history\n"
    "• documents.csv — your uploaded document records\n"
    "• notification_preferences.csv — your notification settings\n"
    "• raw_data.json — the complete machine-readable export"
)


async def _build_export_link_email(download_url: str, expires_human: str):
    """'Your data is ready' email with a download button, on the shared shell.

    A PIPEDA access request answered by an unbranded email containing a link to
    "your personal data" is indistinguishable from a phishing attempt, which is
    a poor way to deliver a privacy right. The shared layout puts the real logo
    and the configured company details around it.

    Replaces the separate HTML and plain-text builders this used to have: the
    layout renders both from one source, so the two cannot drift.
    """
    try:
        from ...utils.email_layout import render_email
    except ImportError:
        from utils.email_layout import render_email  # type: ignore

    return await render_email(
        greeting="Hi,",
        heading="Your data export is ready",
        paragraphs=[
            "As requested, your personal data held by Spinr is ready to download.",
            _EXPORT_MANIFEST,
            "The CSV files open in Excel, Numbers, or Google Sheets.",
            f"This secure link expires on {expires_human}. If it expires before you download "
            "it, just request a new export from the app.",
        ],
        cta=("Download my data (ZIP)", download_url),
        footnote="Questions about your data, or want it deleted? Contact privacy@spinr.ca.",
    )


@router.post("/me/export-data")
@dsar_export_limit
async def export_driver_data(
    background_tasks: BackgroundTasks,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """GDPR/PIPEDA: export all personal data for the authenticated driver.

    Immediately returns a confirmation message. The actual data collection,
    JSON generation, and email delivery happen in a background task so the
    driver does not wait.

    Rate-limited (@dsar_export_limit, 3/hour) — each call fans out DB reads, a
    ZIP build, a Storage upload, and an email. SlowAPI needs a parameter named
    ``request`` typed as starlette Request; do not remove it.
    """
    user_id = current_user["id"]
    # Export is delivered by email only — require a real address. Falling back
    # to the phone number (the old behaviour) would pass a raw phone number to
    # the email provider, which both fails to send and risks logging the number.
    email = (current_user.get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="No email address on file to send the export to.")

    background_tasks.add_task(_build_and_email_data_export, user_id, email)
    return {"message": "Your data export is being prepared. Check your email."}


async def _build_and_email_data_export(user_id: str, email: str) -> None:
    """Background task: collect all driver data and email a JSON export."""
    import asyncio  # noqa: PLC0415

    try:
        # B-P2-6: PIPEDA right-to-access has a 30-day SLA but riders/drivers
        # judge "fast" by the email arrival, not the SLA. The previous
        # 6-sequential-await pattern accumulated ~6× round-trip latency.
        # Wave 1 (no driver_id needed) — 3 reads in parallel.
        driver_rows, user_rows, notification_prefs = await asyncio.gather(
            db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1),
            db_supabase.get_rows("users", {"id": user_id}, limit=1),
            db_supabase.get_rows("notification_preferences", {"user_id": user_id}, limit=1),
        )
        driver = (driver_rows or [{}])[0] if driver_rows else {}
        user = (user_rows or [{}])[0] if user_rows else {}
        driver_id = driver.get("id", "")
        notification_prefs = notification_prefs or []

        # Wave 2 (driver_id-dependent) — 3 reads in parallel. Skip entirely
        # if there's no driver row (rider-only account requesting export).
        rides: list = []
        payouts: list = []
        documents: list = []
        if driver_id:
            rides, payouts, documents = await asyncio.gather(
                db_supabase.get_rows(
                    "rides",
                    {"driver_id": driver_id},
                    limit=500,
                    order="created_at",
                    desc=True,
                ),
                db_supabase.get_rows(
                    "driver_payouts",
                    {"driver_id": driver_id},
                    limit=200,
                    order="created_at",
                    desc=True,
                ),
                db_supabase.get_rows("driver_documents", {"driver_id": driver_id}, limit=50),
            )
            rides = rides or []
            payouts = payouts or []
            documents = documents or []

        export_payload = {
            "export_generated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "account": {k: v for k, v in user.items() if k not in _EXPORT_REDACT_ACCOUNT},
            "driver_profile": {k: v for k, v in driver.items() if k not in _EXPORT_REDACT_DRIVER},
            "rides": [{k: v for k, v in r.items() if k not in _EXPORT_REDACT_RIDE} for r in rides],
            "payouts": payouts,
            "documents": [{k: v for k, v in doc.items() if k != "document_url"} for doc in documents],
            "notification_preferences": notification_prefs,
        }

        # Lyft-style bundle: human-readable CSV files (one per data category)
        # plus a README and the complete machine-readable JSON, zipped together.
        # Drivers get spreadsheets they can open, not a raw JSON blob.
        now = datetime.now(timezone.utc)
        generated_on = now.strftime("%Y-%m-%d")
        zip_bytes = _build_export_zip(export_payload, generated_on)
        subject = "Your Spinr data export is ready"

        # Primary delivery: a time-limited signed download link (like Lyft) —
        # keeps PII out of the email body and lets a leaked link self-expire.
        try:
            expires_human = (now + timedelta(seconds=_EXPORT_LINK_TTL_SECONDS)).strftime("%B %d, %Y")
            download_url = await _upload_export_zip(user_id, zip_bytes, _EXPORT_LINK_TTL_SECONDS)
            # The URL is interpolated into an HTML href — refuse anything that
            # isn't a plain https URL so a malformed value can't break out of
            # the attribute. (Triggers the attachment fallback below.)
            if not download_url.startswith("https://"):
                raise ValueError(f"unexpected signed URL scheme: {download_url[:30]!r}")
            _export_rendered = await _build_export_link_email(download_url, expires_human)
            await _deps.send_email(
                to=email,
                subject=subject,
                body=_export_rendered.text,
                html=_export_rendered.html,
                email_type="dsar",
                recipient_user_id=user_id,
                log_id="dsar",
            )
            logger.info("Data export link emailed for user %s (expires %s)", user_id, expires_human)
        except Exception as link_exc:
            # Storage/link generation failed — fall back to attaching the ZIP so
            # the PIPEDA access request is still fulfilled. Logged loudly so the
            # storage problem gets fixed rather than masked.
            logger.error(
                "Data export link generation failed for user %s; falling back to attachment: %s",
                user_id,
                link_exc,
                exc_info=True,
            )
            filename = f"spinr-data-export-{generated_on}.zip"
            await _deps.send_email(
                to=email,
                subject=subject,
                body=(
                    "Hi,\n\n"
                    "As requested, your personal data held by Spinr is attached as a ZIP "
                    f'archive ("{filename}").\n\n'
                    "If you have any questions about your data or would like to request "
                    "deletion, please contact privacy@spinr.ca.\n\n"
                    "— The Spinr Team"
                ),
                html=_build_export_email_html(filename),
                attachments=[{"filename": filename, "content": zip_bytes, "mime": "application/zip"}],
                email_type="dsar",
                recipient_user_id=user_id,
                log_id="dsar",
            )
            logger.info("Data export emailed as attachment (fallback) for user %s", user_id)
        logger.info(
            "dsar_export_completed",
            extra={
                "user_id": user_id,
                "domain": "privacy",
                "metric": "dsar_export_completed",
            },
        )

    except Exception as exc:
        # Surface the full traceback and, for DatabaseError, the original DB
        # error (str(exc) alone yields only "Database operation failed").
        original = exc.details.get("original") if hasattr(exc, "details") and isinstance(exc.details, dict) else None
        logger.error(
            "Data export failed for user %s: %s%s",
            user_id,
            exc,
            f" — {original}" if original else "",
            exc_info=True,
        )
