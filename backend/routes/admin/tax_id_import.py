"""Admin bulk import of driver tax identifiers (SIN + GST/HST BN).

One-time migration path for drivers who predate in-app SIN/GST collection:
the operator already holds their numbers (collected on the old platform) and
loads them here so every driver passes the SIN-before-Stripe onboarding gate
and the GST payout gate without being re-asked in the app.

Mirrors the mapping-import contract (``stripe_import.py``):

- ``POST /api/admin/tax-ids/import/validate`` — parse + validate the CSV and
  return a dry-run report. No writes.
- ``POST /api/admin/tax-ids/import/commit`` — re-parse + re-validate and,
  only if there are no errors, fill the columns. SIN is Vault-encrypted by
  the same fail-closed helper the driver path uses, then pushed to Stripe in
  the background for drivers who already have a Connect account (skipping
  any account where Stripe reports id_number_provided).

A second pair of endpoints, ``.../import/prepare-validate`` and
``.../import/prepare-commit``, takes the raw MongoDB-export ``banks.csv`` +
``drivers.csv`` directly instead of a pre-built ``phone,sin,gst_bn`` CSV —
mirroring ``legacy_sin_dob_backfill.py``'s two-file upload/validate/commit
shape. The join is the exact, already-tested
``scripts/build_legacy_tax_id_csv.py:build_rows`` the CLI script uses; the
resulting rows feed straight into the same ``_build_plan``/``_apply_plan``
this file's single-CSV path uses, so validation and writes behave
identically regardless of which upload path produced the rows. This exists
so an operator never has to build the ready CSV by hand (or paste real SIN/
GST values into a chat session to get one built) — both raw export files go
straight from their machine to this endpoint, and neither is written to
disk here.

CSV format: header ``phone,sin,gst_bn``; phone is the match key, sin/gst_bn
each optional per row but at least one required.

Fill policy is NULL-only, both columns. For SIN that is the immutability
rule (changes go through /admin/drivers/{id}/update-sin, audited, with a
reason). For GST it keeps a re-run of the same CSV from silently reverting
a number a driver has since corrected in the app.

Reports carry only ``row_ref`` / ``field`` / ``message`` — never a SIN, BN,
or full phone number (row_ref shows the last 4 digits of the phone only) —
per the PIPEDA rules in CLAUDE.md. validate_sin error messages are built to
never echo their input, which is what makes them safe to forward here.

Access: strictly super_admin — same posture as reveal-sin/update-sin. The
router mount adds ``require_super_admin``; the per-handler check below is
defence in depth, matching stripe_import.py.
"""

import csv
import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...routes.drivers._shared import _encrypt_driver_pii
    from ...routes.drivers.payouts import _GST_BN_RE, prefill_sin_to_stripe
    from ...scripts.build_legacy_tax_id_csv import build_rows as _build_legacy_tax_id_rows
    from ...services.driver_import_service import read_mongo_export_csv_text
    from ...services.stripe_mapping_import_service import _phone_lookup_keys
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
    from ...utils.background import spawn
    from ...utils.driver_import_token import (
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from ...utils.rate_limiter import tax_id_import_commit_limit, tax_id_import_validate_limit
    from ...utils.sin import sin_last4, validate_sin
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # type: ignore # noqa: F401
    from routes.drivers._shared import _encrypt_driver_pii  # type: ignore
    from routes.drivers.payouts import _GST_BN_RE, prefill_sin_to_stripe  # type: ignore
    from scripts.build_legacy_tax_id_csv import build_rows as _build_legacy_tax_id_rows  # type: ignore
    from services.driver_import_service import read_mongo_export_csv_text  # type: ignore
    from services.stripe_mapping_import_service import _phone_lookup_keys  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore # noqa: F401
    from utils.background import spawn  # type: ignore
    from utils.driver_import_token import (  # type: ignore
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.rate_limiter import tax_id_import_commit_limit, tax_id_import_validate_limit  # type: ignore
    from utils.sin import sin_last4, validate_sin  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CSV_BYTES = 1_000_000  # 1 MB
MAX_ROWS = 500

_HEADER = ["phone", "sin", "gst_bn"]


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Tax ID import requires super_admin")


def _row_ref(index: int, phone: str) -> str:
    """PII-safe row handle: CSV line number + phone last-4."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"row {index} (…{digits[-4:]})" if len(digits) >= 4 else f"row {index}"


def _parse_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if header != _HEADER:
        raise ValueError(f"CSV header must be exactly: {','.join(_HEADER)}")
    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({k: (v or "").strip() for k, v in raw.items() if k})
    if not rows:
        raise ValueError("CSV has no data rows")
    return rows


async def _read_rows(tax_csv: UploadFile) -> list[dict[str, str]]:
    raw = await tax_csv.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = _parse_rows(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import")
    return rows


async def _prefetch_drivers(rows: list[dict[str, str]]) -> dict[str, dict]:
    """Drivers keyed by every phone spelling a CSV row might use."""
    keys: list[str] = []
    for r in rows:
        for k in _phone_lookup_keys(r.get("phone") or ""):
            if k not in keys:
                keys.append(k)
    if not keys:
        return {}
    drivers = await db_supabase.get_rows("drivers", {"phone": {"$in": keys}}, limit=len(keys))
    return {d["phone"]: d for d in drivers if d.get("phone")}


async def _build_plan(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Validate every row; returns errors/warnings/updates. Read-only.

    An update entry carries the driver id plus the PLAINTEXT sin/gst values —
    the plan lives only inside one request (validate discards it; commit
    encrypts before any write) and is never logged or returned.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    updates: list[dict[str, Any]] = []

    by_phone = await _prefetch_drivers(rows)
    seen_phones: set[str] = set()

    for i, row in enumerate(rows, start=2):  # 2 = first data line in the file
        phone = row.get("phone") or ""
        ref = _row_ref(i, phone)

        if not phone:
            errors.append({"row_ref": f"row {i}", "field": "phone", "message": "phone is required"})
            continue

        lookup = _phone_lookup_keys(phone)
        norm = lookup[0]
        if norm in seen_phones:
            errors.append({"row_ref": ref, "field": "phone", "message": "duplicate phone in CSV"})
            continue
        seen_phones.add(norm)

        driver = next((by_phone[k] for k in lookup if k in by_phone), None)
        if not driver:
            errors.append({"row_ref": ref, "field": "phone", "message": "no driver with this phone"})
            continue

        sin_raw = row.get("sin") or ""
        gst_raw = (row.get("gst_bn") or "").replace(" ", "").upper()
        if not sin_raw and not gst_raw:
            errors.append({"row_ref": ref, "field": "row", "message": "row has neither sin nor gst_bn"})
            continue

        update: dict[str, Any] = {"driver_id": driver["id"], "row_ref": ref}

        if sin_raw:
            if driver.get("sin"):
                # NULL-only fill = the immutability rule. Corrections go
                # through /admin/drivers/{id}/update-sin with a reason.
                warnings.append(
                    {
                        "row_ref": ref,
                        "field": "sin",
                        "message": "SIN already on file — skipped (use update-sin to correct)",
                    }
                )
            else:
                try:
                    update["sin"] = validate_sin(sin_raw)
                except ValueError as exc:
                    # validate_sin messages never echo the digits.
                    errors.append({"row_ref": ref, "field": "sin", "message": str(exc)})
                    continue

        if gst_raw:
            if driver.get("gst_bn"):
                warnings.append({"row_ref": ref, "field": "gst_bn", "message": "GST BN already on file — skipped"})
            elif not _GST_BN_RE.match(gst_raw):
                errors.append(
                    {"row_ref": ref, "field": "gst_bn", "message": "not a valid BN (9 digits, optional RTxxxx)"}
                )
                continue
            else:
                update["gst_bn"] = gst_raw

        if "sin" in update or "gst_bn" in update:
            update["stripe_account_id"] = driver.get("stripe_account_id")
            updates.append(update)
        else:
            warnings.append(
                {"row_ref": ref, "field": "row", "message": "nothing to write — both values already on file"}
            )

    return {"errors": errors, "warnings": warnings, "updates": updates}


def _report(plan: dict[str, Any], batch: str, total_rows: int) -> dict[str, Any]:
    return {
        "batch": batch,
        "can_commit": len(plan["errors"]) == 0,
        "counts": {
            "rows": total_rows,
            "to_write": len(plan["updates"]),
            "sin_to_write": sum(1 for u in plan["updates"] if "sin" in u),
            "gst_to_write": sum(1 for u in plan["updates"] if "gst_bn" in u),
            "skipped": len(plan["warnings"]),
        },
        "warnings": plan["warnings"],
        "errors": plan["errors"],
    }


# Guardrails for the raw-export upload path, matching legacy_sin_dob_backfill.py's
# reasoning: banks.csv/drivers.csv are small (hundreds of rows) with many more
# columns than the built phone,sin,gst_bn CSV, so these limits are per-file and
# looser than MAX_CSV_BYTES/MAX_ROWS above.
MAX_LEGACY_EXPORT_CSV_BYTES = 2_000_000  # 2 MB, per file
MAX_LEGACY_EXPORT_ROWS = 2_000  # per file


async def _read_one_legacy_csv(upload: UploadFile) -> tuple[list[dict[str, str]], bytes]:
    """Read + size-guard one raw Mongo-export CSV. Returns (rows, raw bytes)."""
    raw = await upload.read()
    if len(raw) > MAX_LEGACY_EXPORT_CSV_BYTES:
        raise HTTPException(
            status_code=413, detail=f"CSV exceeds the {MAX_LEGACY_EXPORT_CSV_BYTES // 1_000_000} MB limit"
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = read_mongo_export_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(rows) > MAX_LEGACY_EXPORT_ROWS:
        raise HTTPException(
            status_code=422, detail=f"CSV has {len(rows)} rows; the limit is {MAX_LEGACY_EXPORT_ROWS} per import"
        )
    return rows, raw


async def _read_legacy_export_pair(
    banks_csv: UploadFile, drivers_csv: UploadFile
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """Read both raw export CSVs and bind a single hash to their combined bytes.

    Mirrors legacy_sin_dob_backfill.py's ``_read_csv_pair``: the commit token
    is bound to ``sha256(banks_bytes + b"|" + drivers_bytes)``, so swapping
    either file between validate and commit invalidates the token.
    """
    bank_rows, banks_raw = await _read_one_legacy_csv(banks_csv)
    driver_rows, drivers_raw = await _read_one_legacy_csv(drivers_csv)
    combined_sha256 = hashlib.sha256(banks_raw + b"|" + drivers_raw).hexdigest()
    return bank_rows, driver_rows, combined_sha256


async def _apply_plan(plan: dict[str, Any], batch: str, admin: dict) -> dict[str, Any]:
    """Write every update in an already-clean plan (``plan["errors"]`` empty)
    and audit-log the result. Shared by both commit endpoints below — the
    single-CSV path and the raw-export path build the same plan shape via
    ``_build_plan``, so the write logic doesn't need to know which produced it.
    """
    now = datetime.now(timezone.utc).isoformat()
    written_sin = 0
    written_gst = 0
    stripe_pushes: list[dict[str, str]] = []
    warnings = list(plan["warnings"])
    for u in plan["updates"]:
        # One write per column, each a compare-and-set on that column being
        # NULL. The plan's already-on-file checks read a snapshot taken at the
        # top of the request; a driver self-entering their SIN in the app
        # while this loop runs would otherwise be silently clobbered by the
        # CSV value — bypassing the immutability rule with no reason and no
        # per-driver audit. 0 rows matched → changed since validation →
        # skipped with a warning, same convention as the ride-acceptance
        # {'status': 'searching'} guard.
        if "sin" in u:
            # Fail-closed Vault encryption, same helper as the driver path:
            # Vault down → 503 out of the whole commit, never plaintext in a
            # column. Rows already written stay written; a re-run converges
            # (they skip as already-on-file warnings).
            encrypted = await _encrypt_driver_pii(
                {"sin": u["sin"], "sin_last4": sin_last4(u["sin"]), "sin_collected_at": now, "updated_at": now}
            )
            result = await db_supabase.update_one("drivers", {"id": u["driver_id"], "sin": None}, encrypted)
            if result is None:
                warnings.append(
                    {"row_ref": u["row_ref"], "field": "sin", "message": "SIN set since validation — skipped"}
                )
            else:
                written_sin += 1
                if u.get("stripe_account_id"):
                    stripe_pushes.append(
                        {
                            "driver_id": u["driver_id"],
                            "sin_token": encrypted["sin"],
                            "account_id": u["stripe_account_id"],
                        }
                    )
        if "gst_bn" in u:
            result = await db_supabase.update_one(
                "drivers",
                {"id": u["driver_id"], "gst_bn": None},
                {"gst_bn": u["gst_bn"], "gst_registered": True, "updated_at": now},
            )
            if result is None:
                warnings.append(
                    {"row_ref": u["row_ref"], "field": "gst_bn", "message": "GST BN set since validation — skipped"}
                )
            else:
                written_gst += 1

    stripe_push = "not_applicable"
    if stripe_pushes:
        # Never inline: up to MAX_ROWS Stripe round-trips. spawn(), not a bare
        # create_task — the loop holds only a weak reference to tasks, and an
        # unreferenced push could be garbage-collected mid-flight, silently
        # dropping SIN pushes with no log evidence (see utils/background.py).
        spawn(_push_sins_to_stripe(stripe_pushes, batch))
        stripe_push = "started"

    # Audit carries counts + batch only — never CSV contents.
    await log_admin_action(
        admin,
        "driver_tax_id_import",
        "drivers",
        batch,
        {"written_sin": written_sin, "written_gst": written_gst, "stripe_pushes": len(stripe_pushes)},
    )
    return {
        "batch": batch,
        "committed": True,
        "written_sin": written_sin,
        "written_gst": written_gst,
        "stripe_push": stripe_push,
        "warnings": warnings,
    }


async def _push_sins_to_stripe(pushes: list[dict[str, str]], batch: str) -> None:
    """Background: hand Stripe the freshly-imported SINs, one driver at a time.

    Uses the same best-effort prefill as onboarding (skips accounts where
    Stripe already reports id_number_provided; never raises). Sequential on
    purpose — ≤ MAX_ROWS calls, and Stripe rate limits are shared with the
    live product.
    """
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.warning("[TAX-ID-IMPORT] Stripe not configured; imported SINs not pushed", extra={"batch": batch})
        return
    outcomes: dict[str, int] = {}
    for p in pushes:
        out = await prefill_sin_to_stripe({"id": p["driver_id"], "sin": p["sin_token"]}, p["account_id"], stripe_secret)
        outcomes[out] = outcomes.get(out, 0) + 1
    logger.info("[TAX-ID-IMPORT] Stripe SIN push finished", extra={"batch": batch, "outcomes": outcomes})


@router.post("/tax-ids/import/validate")
@tax_id_import_validate_limit
async def validate_tax_id_import(
    request: Request,
    tax_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse, match, and validate the CSV. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_rows(tax_csv)
    plan = await _build_plan(rows)
    return _report(plan, batch, len(rows))


@router.post("/tax-ids/import/commit")
@tax_id_import_commit_limit
async def commit_tax_id_import(
    request: Request,
    tax_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate and, only if clean, fill the NULL sin/gst_bn columns."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_rows(tax_csv)
    plan = await _build_plan(rows)

    if plan["errors"]:
        # Same contract as the other importers: refuse with the full report
        # rather than half-applying a CSV the operator hasn't seen fail.
        return {**_report(plan, batch, len(rows)), "committed": False}

    return await _apply_plan(plan, batch, admin)


@router.post("/tax-ids/import/prepare-validate")
@tax_id_import_validate_limit
async def validate_tax_id_import_from_legacy_export(
    request: Request,
    banks_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run over the raw Mongo-export ``banks.csv`` + ``drivers.csv``,
    instead of a pre-built ``phone,sin,gst_bn`` CSV. No writes.

    Joins server-side via the same, already production-verified
    ``build_rows()`` the CLI script (``scripts/build_legacy_tax_id_csv.py``)
    uses, then runs the result through the exact same ``_build_plan()`` the
    single-CSV path uses below — validation behaves identically regardless of
    which upload path produced the rows.

    Like ``legacy_sin_dob_backfill.py``, the response carries a
    validation_token bound to (batch, sha256(banks_bytes + b"|" +
    drivers_bytes), admin.id); ``/prepare-commit`` requires it.
    """
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bank_rows, driver_rows, csv_sha256 = await _read_legacy_export_pair(banks_csv, drivers_csv)
    rows, build_stats = _build_legacy_tax_id_rows(bank_rows, driver_rows)
    if not rows:
        raise HTTPException(status_code=422, detail="No rows with a SIN or GST/HST BN matched a driver by phone")
    plan = await _build_plan(rows)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    report = _report(plan, batch, len(rows))
    report["validation_token"] = validation_token
    # Join-level counts (unmatched/duplicate/missing-both) so the operator can
    # see why the row count is lower than banks.csv's own row count, without
    # any of it ever naming a phone/SIN/GST value.
    report["join_stats"] = {
        "banks_rows": build_stats["banks_rows"],
        "unmatched_no_phone": build_stats["unmatched_no_phone"],
        "skipped_no_sin_or_gst": build_stats["skipped_no_sin_or_gst"],
        "duplicate_phone_groups": build_stats["duplicate_phone_groups"],
    }
    return report


@router.post("/tax-ids/import/prepare-commit")
@tax_id_import_commit_limit
async def commit_tax_id_import_from_legacy_export(
    request: Request,
    banks_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the same two raw export files and, only if clean, apply
    the fill — identical write path to ``commit_tax_id_import`` above (both
    call ``_apply_plan``), just fed rows built from banks.csv+drivers.csv
    instead of a pre-built CSV.

    Requires validation_token from a prior /prepare-validate call for this
    exact (batch, combined CSV bytes, admin), same gap-#45-shaped guarantee
    as the single-CSV commit's sibling tools.
    """
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bank_rows, driver_rows, csv_sha256 = await _read_legacy_export_pair(banks_csv, drivers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validate these CSVs before committing (or re-validate — a file or batch changed): {e}",
        ) from e
    rows, _build_stats = _build_legacy_tax_id_rows(bank_rows, driver_rows)
    plan = await _build_plan(rows)

    if plan["errors"]:
        # Data (or the matched drivers' already-on-file state) changed since
        # the operator validated. Refuse but return 200 with committed=false
        # and the full report, same contract as commit_tax_id_import above.
        return {**_report(plan, batch, len(rows)), "committed": False}

    return await _apply_plan(plan, batch, admin)
