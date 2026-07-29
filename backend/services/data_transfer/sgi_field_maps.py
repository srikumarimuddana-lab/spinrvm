"""Map a `drivers` table row onto the row-value dicts
`sgi_form_filler.fill_driver_details_form`/`fill_vehicle_details_form`
expect. Kept separate from the PDF-filling mechanics so a future SGI form
revision (new fields, renamed columns) only touches this file.

Column names verified directly against the real `drivers` table schema
(prior versions of this docstring cited `full_name` and `spinr_approved` as
verified column names — neither exists on `drivers`; the real columns are
`name` and `is_verified`. That bug meant `driver.get("full_name")` and
`driver.get("spinr_approved")` always fell through to their defaults,
silently blanking the driver's name on the generated PDF and always
reporting "not verified" — caught by re-checking
`information_schema.columns` against production, not by re-reading this
file's own claims). Real columns used below: `name`, `license_number`,
`license_class`, `license_plate`, `vehicle_year`, `vehicle_make`,
`vehicle_model`, `vehicle_vin`. The `drivers` table has no per-document
boolean "status" column — document review state lives in
`driver_documents.status`.
"""

from datetime import datetime, timezone
from typing import Any


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def driver_to_driver_details_row(driver: dict[str, Any], action: str = "add") -> dict[str, Any]:
    """Map a drivers-table row to a D00032 row dict.

    `verified_driver_history` and `criminal_record_check_attached` default
    to Yes on every generated row, per explicit product decision: an admin
    only reaches this form after selecting drivers through the Data
    Transfer search/select flow, which already scopes to onboarded Spinr
    drivers, so these two SGI attestation checkboxes are pre-checked rather
    than silently defaulting to "No" off an unrelated derived signal. The
    admin remains responsible for un-checking per-row before submission if a
    specific driver doesn't actually meet SGI's criteria (this module has no
    per-row override UI yet — see the Data Transfer module's ACTION_ITEMS).
    """
    return {
        "full_name": driver.get("name", ""),
        "licence_number": driver.get("license_number", ""),
        "licence_class": driver.get("license_class", ""),
        "effective_date": _today_iso(),
        "action": action,
        "verified_driver_history": True,
        "criminal_record_check_attached": True,
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
        "registered_owners_name": driver.get("name", ""),
        "vehicle_date": _today_iso(),
        # Defaults to Yes for the same reason as driver_details' two
        # attestation fields above — see that docstring.
        "valid_inspection": True,
        "action": action,
    }
