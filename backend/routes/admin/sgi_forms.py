"""Generate the real SGI compliance PDFs (D00032 Driver Details, D00033
Vehicle Details) for selected drivers, for submission to SGI per the
Saskatchewan Transportation Act reporting obligations (CLAUDE.md's
Saskatchewan Regulatory section).

Row-value mapping (driver/user row -> filler dict) lives in
``services/data_transfer/sgi_field_maps.py``, kept separate from the
row-slot mechanics in ``sgi_form_filler.py`` so a future SGI form revision
only touches the mapping, not the PDF-filling code.
"""

import io
import logging
import re as _re
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...routes.drivers._shared import _decrypt_driver_pii
    from ...services.data_transfer import (
        bundle_zip_builder,
        entity_export_service,
        observability,
        sgi_field_maps,
        sgi_form_filler,
    )
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import data_transfer_export_limit
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from routes.drivers._shared import _decrypt_driver_pii
    from services.data_transfer import (
        bundle_zip_builder,
        entity_export_service,
        observability,
        sgi_field_maps,
        sgi_form_filler,
    )
    from utils.audit_logger import log_admin_action
    from utils.rate_limiter import data_transfer_export_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# SGI D00032/D00033 are Saskatchewan-only regulator forms (CLAUDE.md's
# regulatory-sk context; docs/reporting/sgi-form-field-mapping.md). As Spinr
# expands into Alberta, a driver whose `regulatory_authority` is anything
# else must never end up on an SGI submission — hard block, not a warning,
# per the explicit decision on this: a form silently sent to the wrong
# regulator is a compliance incident, not a UX inconvenience to smooth over.
_SGI_AUTHORITY = "SGI"


def _out_of_scope_drivers(driver_rows: list[dict]) -> list[dict]:
    """Drivers whose `regulatory_authority` is not exactly `_SGI_AUTHORITY`
    — this now includes NULL/missing. ACTION_ITEMS.md B13 (round 2, 2026-08-22):
    the backfill is confirmed complete (production: 0 of 212 drivers NULL,
    all SGI/SK) and every driver write path (`routes/drivers/profile.py`'s
    two auto-create branches, `routes/drivers/location.py`'s admin
    `POST /drivers`) now goes through `_shared._resolve_regulatory_defaults()`,
    so a NULL row can no longer be produced by normal driver creation. The
    former NULL-passes grandfather allowance is retired — a NULL row is
    now treated the same as any other non-SGI authority (out of scope,
    blocked), since a NULL row post-backfill would mean the write-path fix
    regressed, not that it's a legitimate legacy row to wave through."""
    return [d for d in driver_rows if d.get("regulatory_authority") != _SGI_AUTHORITY]


class SgiFormRequest(BaseModel):
    form_type: str = Field(..., pattern="^(driver_details|vehicle_details)$")
    # `users.id` — the canonical entity_id used across the whole Data
    # Transfer module (search, export, SGI forms). NOT `drivers.id`; see the
    # driver lookup below, which resolves via `drivers.user_id`.
    driver_ids: list[str]
    action: str = Field("add", pattern="^(add|remove|change)$")


# The supporting evidence SGI expects alongside a D00032 submission. Narrower
# than "every document type" on purpose: an SGI package needs proof of
# eligibility, not the driver's whole file (PIPEDA data minimization, and the
# same reasoning as the Export tab's per-type file selection).
SGI_SUPPORTING_DOC_TYPES = [
    "drivers_license",
    "drivers_abstract",
    "background_check",
    "vehicle_inspection",
    "insurance",
]

# A document bundle is far heavier than a filled form (raw scans for every
# driver, vs. one PDF), and this endpoint returns it inline rather than
# backgrounding it the way /data-transfer/export does. Cap it at a submission
# batch's worth: D00032 holds 10 driver rows, D00033 holds 16, so 25 covers
# the largest single filing with headroom without letting someone pull 100
# drivers' scans through one request/response.
MAX_DOCUMENT_BUNDLE_DRIVERS = 25


class SgiDocumentBundleRequest(BaseModel):
    # `users.id`, same as SgiFormRequest — see its comment.
    driver_ids: list[str]
    # Required for the same reason /data-transfer/export requires it (PIA
    # recommendation R-C): this moves real drivers' identity documents, so
    # there must be a contemporaneous record of why.
    reason: str = Field(..., min_length=10, max_length=200)
    doc_types: Optional[list[str]] = None


@router.post("/data-transfer/sgi-forms/generate")
async def generate_sgi_form(
    body: SgiFormRequest,
    admin: dict = Depends(get_admin_user),
):
    """Fill the requested SGI form for the given drivers and return the PDF
    directly (small file, single request/response — no Storage upload needed
    unlike the ZIP/CSV export, which can be much larger)."""
    max_rows = sgi_form_filler.FORM_MAX_ROWS[body.form_type]
    if not body.driver_ids:
        raise HTTPException(status_code=400, detail="No drivers selected")
    if len(body.driver_ids) > max_rows:
        raise HTTPException(
            status_code=422,
            detail=f"{len(body.driver_ids)} drivers requested; the {body.form_type} form has {max_rows} rows",
        )

    # Regression: this previously looked up `drivers.id`, but every other
    # part of the Data Transfer module (search's driver-scoped variant vs.
    # its default/mixed variant, and entity_export_service.gather_entity_bundle,
    # which is explicitly documented as taking `users.id`) disagreed on
    # which ID `driver_ids` actually meant -- selecting a driver from one
    # search mode made export work and SGI forms silently return nothing;
    # selecting from the other mode did the reverse. Standardized on
    # `users.id` everywhere; resolve the driver row via the FK instead of PK.
    driver_rows = await db_supabase.get_rows("drivers", {"user_id": {"$in": body.driver_ids}})
    if not driver_rows:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    out_of_scope = _out_of_scope_drivers(driver_rows)
    if out_of_scope:
        authorities = sorted({d.get("regulatory_authority") or "unspecified" for d in out_of_scope})
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(out_of_scope)} of the selected drivers are regulated by "
                f"{', '.join(authorities)}, not SGI — SGI forms cannot be generated for them. "
                "Remove these drivers from the selection and generate their own province's "
                "form separately."
            ),
        )

    # license_number is vault-encrypted at rest (a vault.secrets UUID, not the
    # real value — see routes/drivers/_shared.py's _VAULT_PII_FIELDS). D00032
    # needs the real licence number on the form, not the encrypted token.
    driver_rows = [await _decrypt_driver_pii(d) for d in driver_rows]

    # `get_rows(..., {"$in": ...})` does not preserve `body.driver_ids`'
    # order, and that order isn't otherwise meaningful (it's whatever
    # sequence the admin happened to click rows in Search & Select) — sort
    # by name so the generated regulator form reads predictably rather than
    # in arbitrary DB row order.
    driver_rows.sort(key=lambda d: (d.get("name") or "").lower())

    try:
        if body.form_type == "driver_details":
            row_dicts = [sgi_field_maps.driver_to_driver_details_row(d, action=body.action) for d in driver_rows]
            pdf_bytes = sgi_form_filler.fill_driver_details_form(row_dicts)
            filename = "SGI_D00032_Driver_Details.pdf"
        else:
            row_dicts = [sgi_field_maps.driver_to_vehicle_details_row(d, action=body.action) for d in driver_rows]
            pdf_bytes = sgi_form_filler.fill_vehicle_details_form(row_dicts)
            filename = "SGI_D00033_Vehicle_Details.pdf"
    except Exception as e:
        logger.error("data-transfer sgi-forms: fill failed for form_type=%s", body.form_type, exc_info=True)
        observability.record_sgi_form_result(body.form_type, "failed")
        observability.capture_failure(
            "SGI form generation failed",
            "data_transfer_sgi_form_failed",
            {
                "admin_id": admin.get("id"),
                "form_type": body.form_type,
                "driver_count": len(driver_rows),
                "error": str(e),
            },
        )
        raise HTTPException(status_code=502, detail="Could not generate the SGI form") from e

    observability.record_sgi_form_result(body.form_type, "completed")

    # Record that the removal was actually filed. Without this there was no way
    # to tell an already-submitted removal from one nobody had done, so the only
    # options were to re-file (duplicate submission to SGI) or hope. Stamped per
    # form because a driver needs BOTH D00032 and D00033 — marking the job done
    # off whichever was generated first would leave the vehicle on SGI's books.
    #
    # Best-effort: the PDF is already built and returned below, and failing the
    # request here would make the admin re-generate (and re-file) the very form
    # that just succeeded. Logged at error so a persistent failure is actionable
    # rather than silently leaving the queue full.
    if body.action == "remove":
        stamp_column = (
            "regulator_removal_driver_form_at"
            if body.form_type == "driver_details"
            else "regulator_removal_vehicle_form_at"
        )
        stamped_at = datetime.now(timezone.utc).isoformat()
        for d in driver_rows:
            try:
                await db_supabase.update_one(
                    "drivers",
                    {"id": d["id"]},
                    {stamp_column: stamped_at, "regulator_removal_reported_by": admin.get("id")},
                )
            except Exception:
                logger.error(
                    "data-transfer sgi-forms: failed to stamp %s for driver %s — removal queue will still show it as outstanding",
                    stamp_column,
                    d.get("id"),
                    exc_info=True,
                )

    await log_admin_action(
        admin,
        "sgi_form_generated",
        "drivers",
        ",".join(body.driver_ids),
        {"form_type": body.form_type, "driver_count": len(driver_rows), "action": body.action},
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/data-transfer/sgi-forms/removal-queue")
async def sgi_removal_queue(
    include_filed: bool = Query(False, description="Also return removals already filed on both forms"),
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Drivers who left but are still filed with the regulator.

    Spinr stops dispatching the moment a driver deletes their account, but SGI
    goes on listing them as an active passenger-for-hire driver until the
    D00032 removal row is filed — and their vehicle until D00033. Nothing used
    to surface that backlog, so it depended on an admin remembering.

    Returns one entry per driver with a per-form outstanding flag, since both
    forms are required and they are filed separately. `entity_id` is `users.id`
    to match what the generate endpoint and the rest of the Data Transfer
    module take as `driver_ids`.
    """
    rows = await db_supabase.get_rows(
        "drivers",
        {"regulator_removal_required": True},
        columns=(
            "id,user_id,name,license_plate,regulatory_authority,regulator_removal_effective_date,"
            "regulator_removal_driver_form_at,regulator_removal_vehicle_form_at,deleted_at"
        ),
        order="regulator_removal_effective_date",
        limit=500,
    )

    def _entry(d: dict) -> Optional[dict]:
        driver_form_done = bool(d.get("regulator_removal_driver_form_at"))
        vehicle_form_done = bool(d.get("regulator_removal_vehicle_form_at"))
        if not include_filed and driver_form_done and vehicle_form_done:
            return None
        return {
            # users.id — the id every other Data Transfer endpoint takes.
            "entity_id": d.get("user_id"),
            "driver_id": d.get("id"),
            "name": d.get("name") or "",
            "license_plate": d.get("license_plate") or "",
            "regulatory_authority": d.get("regulatory_authority"),
            "effective_date": d.get("regulator_removal_effective_date")
            or (str(d.get("deleted_at") or "")[:10] or None),
            "driver_form_filed_at": d.get("regulator_removal_driver_form_at"),
            "vehicle_form_filed_at": d.get("regulator_removal_vehicle_form_at"),
            "driver_form_outstanding": not driver_form_done,
            "vehicle_form_outstanding": not vehicle_form_done,
        }

    entries = [e for e in (_entry(d) for d in rows or []) if e]
    # A driver with no linked users row cannot be selected in the Data Transfer
    # flow (which keys on users.id), so it would silently never clear. Surface
    # the count rather than dropping it on the floor.
    unresolvable = sum(1 for e in entries if not e["entity_id"])
    if unresolvable:
        logger.error(
            "sgi removal queue: %s driver(s) owe a regulator removal but have no linked user row — "
            "they cannot be selected for form generation",
            unresolvable,
        )
    return {"drivers": entries, "count": len(entries), "unresolvable": unresolvable}


@router.post("/data-transfer/sgi-forms/documents")
@data_transfer_export_limit
async def download_sgi_supporting_documents(
    body: SgiDocumentBundleRequest,
    request: Request = None,
    admin: dict = Depends(get_admin_user),
):
    """ZIP of the selected drivers' supporting documents, for filing alongside
    a D00032/D00033 submission.

    The forms endpoint above returns only the filled PDF — it never touches
    driver_documents — so there was no way to get a driver's actual scans out
    of the SGI Compliance Forms tab at all. An SGI package needs both.

    Reuses the Data Transfer export pipeline (entity_export_service +
    bundle_zip_builder) rather than re-implementing document fetching: that
    path already resolves storage keys across every stored URL shape, records
    a per-document file_export_status so a missing scan is explained rather
    than silently absent, and is the code that carries the module's test
    coverage. Returned inline (not backgrounded like /data-transfer/export)
    because MAX_DOCUMENT_BUNDLE_DRIVERS keeps it to a submission-sized batch.

    Rate-limited with the export limiter — same data, same threat model.
    SlowAPI requires a parameter named ``request`` typed as starlette
    Request; do not remove it.
    """
    if not body.driver_ids:
        raise HTTPException(status_code=400, detail="No drivers selected")
    if len(body.driver_ids) > MAX_DOCUMENT_BUNDLE_DRIVERS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(body.driver_ids)} drivers requested; the document bundle is limited to "
                f"{MAX_DOCUMENT_BUNDLE_DRIVERS} per download. Split the selection into smaller batches."
            ),
        )

    driver_rows = await db_supabase.get_rows("drivers", {"user_id": {"$in": body.driver_ids}})
    if not driver_rows:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    # Same hard block as form generation: a non-SGI driver's documents must
    # not be assembled into an SGI submission package either.
    out_of_scope = _out_of_scope_drivers(driver_rows)
    if out_of_scope:
        authorities = sorted({d.get("regulatory_authority") or "unspecified" for d in out_of_scope})
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(out_of_scope)} of the selected drivers are regulated by "
                f"{', '.join(authorities)}, not SGI — their documents cannot be bundled into an SGI "
                "submission. Remove these drivers from the selection."
            ),
        )

    doc_types = body.doc_types or SGI_SUPPORTING_DOC_TYPES
    try:
        bundles = await entity_export_service.gather_entity_bundles(
            [("driver", uid) for uid in body.driver_ids],
            doc_types=doc_types,
            # An SGI submission is about driver eligibility, not trip
            # history — exact pickup/dropoff coordinates have no business
            # in this package.
            include_ride_gps=False,
            # Files, not just metadata rows: getting the scans out is the
            # entire point of this endpoint.
            doc_file_types=doc_types,
        )
    except Exception as e:
        logger.error("sgi supporting documents: gather failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Could not read driver documents") from e

    if not bundles:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    zip_bytes = bundle_zip_builder.build_export_zip(bundles)

    # Count what actually made it in, so the caller can be told plainly when a
    # scan is missing instead of discovering an absent file later. Mirrors the
    # ZIP's own README/file_export_status reporting.
    included = sum(1 for b in bundles for d in b.get("documents", []) if d.get("_content_status") == "included")
    listed = sum(len(b.get("documents", [])) for b in bundles)
    if included < listed:
        logger.error(
            "sgi supporting documents: %s of %s document(s) had no retrievable file (admin=%s)",
            listed - included,
            listed,
            admin.get("id"),
        )

    await log_admin_action(
        admin,
        "sgi_supporting_documents_download",
        "driver_documents",
        None,
        {
            "driver_count": len(bundles),
            "requested": len(body.driver_ids),
            "doc_types": doc_types,
            "documents_listed": listed,
            "documents_with_files": included,
            "reason": body.reason,
        },
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="SGI_Supporting_Documents_{stamp}.zip"',
            # Lets the tab report "12 of 14 documents included" without
            # unzipping; the ZIP's documents.csv remains the detailed record.
            "X-Documents-Listed": str(listed),
            "X-Documents-Included": str(included),
        },
    )


# The one document SGI wants alongside a driver-eligibility filing. Narrower
# than SGI_SUPPORTING_DOC_TYPES on purpose: the submission package is the
# forms plus proof of criminal-record clearance, not the driver's whole file.
SGI_PACKAGE_DOC_TYPE = "background_check"


class SgiPackageRequest(BaseModel):
    # `users.id`, same as SgiFormRequest.
    driver_ids: list[str]
    action: str = Field("add", pattern="^(add|remove|change)$")
    reason: str = Field(..., min_length=10, max_length=200)


def _safe_filename_part(value: str) -> str:
    """Filename-safe slug for a driver name."""
    return _re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")[:60] or "driver"


def _fill_both_forms(driver_rows: list[dict], action: str) -> dict[str, bytes]:
    """Both SGI forms for these drivers, keyed by filename.

    Chunked to each form's real row count (D00032 holds 10, D00033 holds 16),
    mirroring what the Forms tab already does client-side — a selection larger
    than one form's rows becomes consecutive documents rather than being
    refused, since nothing stops an admin filing several per batch.
    """
    out: dict[str, bytes] = {}
    specs = (
        (
            "driver_details",
            "SGI_D00032_Driver_Details",
            sgi_field_maps.driver_to_driver_details_row,
            sgi_form_filler.fill_driver_details_form,
        ),
        (
            "vehicle_details",
            "SGI_D00033_Vehicle_Details",
            sgi_field_maps.driver_to_vehicle_details_row,
            sgi_form_filler.fill_vehicle_details_form,
        ),
    )
    for form_type, base_name, row_mapper, filler in specs:
        max_rows = sgi_form_filler.FORM_MAX_ROWS[form_type]
        batches = [driver_rows[i : i + max_rows] for i in range(0, len(driver_rows), max_rows)]
        for idx, batch in enumerate(batches, start=1):
            rows = [row_mapper(d, action=action) for d in batch]
            suffix = f"_{idx}" if len(batches) > 1 else ""
            out[f"{base_name}{suffix}.pdf"] = filler(rows)
    return out


@router.post("/data-transfer/sgi-forms/package")
@data_transfer_export_limit
async def download_sgi_submission_package(
    body: SgiPackageRequest,
    request: Request = None,
    admin: dict = Depends(get_admin_user),
):
    """The complete SGI submission as one ZIP: both filled forms plus each
    driver's criminal record check in whatever format they uploaded.

    This is what actually gets emailed to SGI. The forms endpoint returns PDFs
    with no evidence attached; the supporting-documents endpoint returns a
    full data-transfer bundle (per-driver folders, CSVs, README) that is far
    more than a filing needs and buries the two files that matter. Neither was
    the thing an operator assembles by hand today.

    Flat, predictably-named, nothing else in it:

        SGI_D00032_Driver_Details.pdf
        SGI_D00033_Vehicle_Details.pdf
        criminal_record_checks/JaneDoe_background_check.pdf

    JPEG and PDF checks are both included as-is — the file is not re-encoded,
    because a regulator-facing document should be exactly what the driver
    submitted.
    """
    if not body.driver_ids:
        raise HTTPException(status_code=400, detail="No drivers selected")
    if len(body.driver_ids) > MAX_DOCUMENT_BUNDLE_DRIVERS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(body.driver_ids)} drivers requested; the submission package is limited to "
                f"{MAX_DOCUMENT_BUNDLE_DRIVERS} per download. Split the selection into smaller batches."
            ),
        )

    driver_rows = await db_supabase.get_rows("drivers", {"user_id": {"$in": body.driver_ids}})
    if not driver_rows:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    out_of_scope = _out_of_scope_drivers(driver_rows)
    if out_of_scope:
        authorities = sorted({d.get("regulatory_authority") or "unspecified" for d in out_of_scope})
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(out_of_scope)} of the selected drivers are regulated by "
                f"{', '.join(authorities)}, not SGI — they cannot be included in an SGI submission. "
                "Remove these drivers from the selection."
            ),
        )

    # Same vault decrypt as form generation: D00032 needs the real licence
    # number, not the encrypted token.
    driver_rows = [await _decrypt_driver_pii(d) for d in driver_rows]
    driver_rows.sort(key=lambda d: (d.get("name") or "").lower())

    try:
        forms = _fill_both_forms(driver_rows, body.action)
    except Exception as e:
        logger.error("sgi package: form fill failed", exc_info=True)
        observability.record_sgi_form_result("package", "failed")
        raise HTTPException(status_code=502, detail="Could not generate the SGI forms") from e

    bundles = await entity_export_service.gather_entity_bundles(
        [("driver", uid) for uid in body.driver_ids],
        doc_types=[SGI_PACKAGE_DOC_TYPE],
        include_ride_gps=False,
        doc_file_types=[SGI_PACKAGE_DOC_TYPE],
    )

    # users.id -> display name, for naming each check after its driver.
    name_by_user_id = {d.get("user_id"): (d.get("name") or "") for d in driver_rows}

    included: list[str] = []
    missing: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, pdf_bytes in forms.items():
            zf.writestr(filename, pdf_bytes)

        for bundle in bundles:
            driver_label = _safe_filename_part(name_by_user_id.get(bundle["entity_id"], "") or bundle["entity_id"])
            checks = [d for d in bundle.get("documents", []) if d.get("_content")]
            if not checks:
                missing.append(driver_label)
                continue
            for n, doc in enumerate(checks, start=1):
                key = doc.get("_storage_key") or ""
                ext = key.rsplit(".", 1)[-1].lower() if "." in key else "bin"
                suffix = f"_{n}" if len(checks) > 1 else ""
                zf.writestr(
                    f"criminal_record_checks/{driver_label}_background_check{suffix}.{ext}",
                    doc["_content"],
                )
                included.append(driver_label)

        if missing:
            # Named inside the ZIP as well as in the response headers: the
            # operator is about to email this to a regulator, and a driver
            # with no clearance on file must not be discovered by SGI.
            zf.writestr(
                "MISSING_CRIMINAL_RECORD_CHECKS.txt",
                "These drivers are on the attached SGI forms but have NO criminal record\n"
                "check file in this package. Obtain their clearance before filing:\n\n"
                + "\n".join(f"  - {name}" for name in sorted(missing))
                + "\n",
            )

    if missing:
        logger.error(
            "sgi package: %s of %s driver(s) have no criminal record check file (admin=%s)",
            len(missing),
            len(bundles),
            admin.get("id"),
        )

    await log_admin_action(
        admin,
        "sgi_submission_package_download",
        "driver_documents",
        None,
        {
            "driver_count": len(driver_rows),
            "requested": len(body.driver_ids),
            "action": body.action,
            "forms": sorted(forms.keys()),
            "checks_included": len(included),
            "checks_missing": len(missing),
            "reason": body.reason,
        },
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="SGI_Submission_{stamp}.zip"',
            "X-Checks-Included": str(len(included)),
            "X-Checks-Missing": str(len(missing)),
        },
    )
