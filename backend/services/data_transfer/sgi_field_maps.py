"""Map a `drivers` table row onto the row-value dicts
`sgi_form_filler.fill_driver_details_form`/`fill_vehicle_details_form`
expect. Kept separate from the PDF-filling mechanics so a future SGI form
revision (new fields, renamed columns) only touches this file.

Column names verified against `driver_import_service.py`'s
`build_plan`/commit-diff logic (the authoritative source for what actually
lands in the `drivers` table): `full_name`, `license_number`,
`license_class`, `license_plate`, `vehicle_year`, `vehicle_make`,
`vehicle_model`, `vehicle_vin`, `spinr_approved`,
`background_check_expiry_date`, `vehicle_inspection_expiry_date`. The
`drivers` table has no per-document boolean "status" column — document
review state lives in `driver_documents.status`; the *_expiry_date columns
on `drivers` are a denormalized "currently has a non-expired one on file"
signal written by the import/approval flow, which is what's used below.
An earlier draft of this file referenced fabricated column names
(`license_verified`, `criminal_record_check_status`,
`vehicle_inspection_status`) that don't exist on this table and would have
silently evaluated to False forever — caught and fixed before commit.
"""

from datetime import date, datetime, timezone
from typing import Any


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _has_unexpired_date(value: Any) -> bool:
    """True when `value` is an ISO date string on/after today — used as the
    "currently on file and not expired" signal for the *_expiry_date
    columns, since the drivers table has no separate boolean flag."""
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except ValueError:
        return False


def driver_to_driver_details_row(driver: dict[str, Any], action: str = "add") -> dict[str, Any]:
    """Map a drivers-table row to a D00032 row dict."""
    return {
        "full_name": driver.get("full_name", ""),
        "licence_number": driver.get("license_number", ""),
        "licence_class": driver.get("license_class", ""),
        "effective_date": _today_iso(),
        "action": action,
        "verified_driver_history": bool(driver.get("spinr_approved")),
        # SGI's own field 8 requires the criminal record check to be dated
        # within 90 days of submission — this reflects only "an unexpired
        # background check is on file," not a fresh 90-day compliance
        # re-check performed here. The admin generating this form is
        # responsible for confirming the SGI-specific 90-day window before
        # submission; this module doesn't attempt that verification.
        "criminal_record_check_attached": _has_unexpired_date(driver.get("background_check_expiry_date")),
    }


def driver_to_vehicle_details_row(driver: dict[str, Any], action: str = "add") -> dict[str, Any]:
    """Map a drivers-table row's vehicle fields to a D00033 row dict.

    Note: `vehicle_vin` may be stored encrypted at rest (see
    `driver_import_service.py`'s `vin_plain`/"(re)encrypt at commit"
    handling) — this function passes the column value through as-is. If the
    stored value is ciphertext rather than plaintext, the generated SGI form
    will show the encrypted string instead of a real VIN; decrypting it here
    needs the same crypto helper the import path uses, which wasn't wired
    into this subtask. Flagged as a known gap, not silently worked around.
    """
    year = driver.get("vehicle_year")
    make = driver.get("vehicle_make", "")
    model = driver.get("vehicle_model", "")
    year_make_model = " ".join(str(p) for p in (year, make, model) if p)
    return {
        "licence_plate_number": driver.get("license_plate", ""),
        "vin": driver.get("vehicle_vin", "") or "",
        "year_make_model": year_make_model,
        "registered_owners_name": driver.get("full_name", ""),
        "vehicle_date": _today_iso(),
        "valid_inspection": _has_unexpired_date(driver.get("vehicle_inspection_expiry_date")),
        "action": action,
    }
