"""Legacy rider saved-address backfill (Phase 4 of the 2026-08-27 migration
plan, docs/migration/2026-08-27-legacy-data-full-migration-approach.md §4).

Imports the legacy Mongo ``customer_addresses.csv`` export into Spinr's
existing, live ``saved_addresses`` table (``routes/addresses.py``) -- the
same destination a rider's own self-serve "save an address" action already
writes to. Not a new feature or a new table; backfilling old data into a
table that already exists and is already exactly the right shape
(id/user_id/name/address/lat/lng/icon/place_id/created_at).

Two-file crosswalk, same pattern as the SIN/DOB and vehicle-history
backfills: ``customer_addresses.csv``'s own ``customer_id`` column is
actually the legacy Mongo customer's ``_id`` (**not** the Stripe
customer_id despite the shared column name) -- confirmed by
cross-referencing the real export directly, not assumed from the name.
``customers.csv`` is needed purely to resolve that ObjectId to a phone
number, same role ``drivers.csv`` plays for the driver-side backfills.

Filtering (confirmed against the real 07-26 export before writing this
file, not assumed):
- Saskatchewan bounding box only (lat 49-60 N, lng -110 to -101 W). 20 of
  301 raw rows fall outside it -- 19 explicitly tagged ``country=India``
  plus 1 blank/no-address row, the same class of test/junk data already
  found and excluded from the rider CSV import earlier in this migration
  effort. The other 281 rows (207 with a blank ``country``/``state`` but
  real in-province coordinates, plus 74 explicitly ``country=Canada``) are
  legitimate.
- Only rows that resolve to a real, already-migrated Spinr rider by phone.
- Idempotent: skips a row if an identical (user_id, address text) pair
  already exists in ``saved_addresses`` -- safe to re-run.

The CSV's own column shapes don't map 1:1 onto ``SavedAddress`` --
``name`` in the source is actually the full formatted address string, and
``type`` (home/work/blank) is the short label. Mapped accordingly:
``address`` <- CSV ``name``, ``name``/``icon`` <- CSV ``type``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from ..supabase_client import supabase
    from .driver_import_service import normalize_phone
except ImportError:
    from services.driver_import_service import normalize_phone
    from supabase_client import supabase

# Saskatchewan bounding box -- generous enough to cover the whole province
# plus a margin, tight enough to exclude the clearly-foreign junk rows
# confirmed present in the real export (see module docstring).
_SK_LAT_RANGE = (49.0, 60.0)
_SK_LNG_RANGE = (-110.0, -101.0)

IMPORT_SOURCE = "legacy_customer_address_import"

_TYPE_LABELS = {"home": "Home", "work": "Work"}
_TYPE_ICONS = {"home": "home", "work": "work"}
_DEFAULT_LABEL = "Saved Address"
_DEFAULT_ICON = "location"


@dataclass
class ImportReportItem:
    row_num: int
    field: str
    message: str


@dataclass
class SavedAddressImportPlan:
    rows_to_insert: list[dict[str, Any]] = field(default_factory=list)
    skipped_out_of_province: int = 0
    skipped_unmatched_customer: int = 0
    skipped_no_rider: int = 0
    skipped_already_imported: int = 0
    warnings: list[ImportReportItem] = field(default_factory=list)
    errors: list[ImportReportItem] = field(default_factory=list)


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def _parse_legacy_epoch_ms(value: str) -> str | None:
    """Legacy epoch-milliseconds timestamp -> ISO-8601 UTC string.

    Duplicated (not imported) from ``driver_import_service._parse_legacy_epoch_ms``
    -- matches this repo's existing per-module duplication convention.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        ms = int(float(raw))
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _in_saskatchewan(lat_raw: str, lng_raw: str) -> bool:
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return False
    return _SK_LAT_RANGE[0] <= lat <= _SK_LAT_RANGE[1] and _SK_LNG_RANGE[0] <= lng <= _SK_LNG_RANGE[1]


_REQUIRED_ADDRESS_COLUMNS = {"customer_id", "lat", "long", "name"}


def validate_required_columns(address_rows: list[dict[str, str]], plan: SavedAddressImportPlan) -> None:
    if not address_rows:
        plan.errors.append(ImportReportItem(0, "customer_addresses_csv", "customer_addresses.csv is empty"))
        return
    missing = _REQUIRED_ADDRESS_COLUMNS - set(address_rows[0].keys())
    for col in sorted(missing):
        plan.errors.append(ImportReportItem(0, col, "customer_addresses.csv is missing required column"))


def build_saved_address_import_plan(
    address_rows: list[dict[str, str]], customer_rows: list[dict[str, str]], *, batch: str
) -> SavedAddressImportPlan:
    plan = SavedAddressImportPlan()
    validate_required_columns(address_rows, plan)
    if plan.errors:
        return plan

    now_iso = datetime.now(timezone.utc).isoformat()

    phone_by_mongo_id: dict[str, str] = {}
    for row in customer_rows:
        mongo_id = (row.get("_id") or "").strip()
        phone = normalize_phone(row.get("phone", ""))
        if mongo_id and phone:
            phone_by_mongo_id.setdefault(mongo_id, phone)

    candidates: list[dict[str, Any]] = []
    for idx, row in enumerate(address_rows, start=1):
        old_id = (row.get("_id") or "").strip() or f"row-{idx}"

        if not _in_saskatchewan(row.get("lat", ""), row.get("long", "")):
            plan.skipped_out_of_province += 1
            continue

        mongo_customer_id = (row.get("customer_id") or "").strip()
        phone = phone_by_mongo_id.get(mongo_customer_id) if mongo_customer_id else None
        if not phone:
            plan.warnings.append(ImportReportItem(idx, "customer_id", "no matching customer row in customers.csv"))
            plan.skipped_unmatched_customer += 1
            continue

        address_text = (row.get("name") or "").strip()
        if not (5 <= len(address_text) <= 300):
            # SavedAddress.address is min_length=5/max_length=300 -- a row
            # this short/long is malformed source data, not a real address.
            plan.warnings.append(ImportReportItem(idx, "name", "address text is missing or an implausible length"))
            continue

        addr_type = (row.get("type") or "").strip().lower()
        candidates.append(
            {
                "old_id": old_id,
                "phone": phone,
                "address": address_text,
                "lat": float(row["lat"]),
                "lng": float(row["long"]),
                "label": _TYPE_LABELS.get(addr_type, _DEFAULT_LABEL),
                "icon": _TYPE_ICONS.get(addr_type, _DEFAULT_ICON),
                "created_at": _parse_legacy_epoch_ms(row.get("created_at", "")) or now_iso,
            }
        )

    if not candidates:
        return plan

    phones = sorted({c["phone"] for c in candidates})
    users_by_phone: dict[str, dict[str, Any]] = {}
    for u in _select_in("users", "id,phone,is_rider", "phone", phones):
        key = u.get("phone")
        if key and key not in users_by_phone:
            users_by_phone[key] = u

    resolved: list[dict[str, Any]] = []
    for c in candidates:
        user = users_by_phone.get(c["phone"])
        if not user or not user.get("is_rider"):
            plan.warnings.append(ImportReportItem(0, "phone", "no matching Spinr rider account"))
            plan.skipped_no_rider += 1
            continue
        resolved.append({**c, "user_id": user["id"]})

    if not resolved:
        return plan

    user_ids = sorted({r["user_id"] for r in resolved})
    existing_by_user: dict[str, set[str]] = {}
    for row in _select_in("saved_addresses", "user_id,address", "user_id", user_ids):
        existing_by_user.setdefault(row["user_id"], set()).add(row["address"])

    for r in resolved:
        if r["address"] in existing_by_user.get(r["user_id"], set()):
            plan.skipped_already_imported += 1
            continue
        plan.rows_to_insert.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": r["user_id"],
                "name": r["label"],
                "address": r["address"],
                "lat": r["lat"],
                "lng": r["lng"],
                "icon": r["icon"],
                "place_id": None,
                "created_at": r["created_at"],
                "legacy_import_metadata": {
                    "source": IMPORT_SOURCE,
                    "old_address_id": r["old_id"],
                    "batch": batch,
                    "imported_at": now_iso,
                },
            }
        )

    return plan


def commit_saved_address_import_plan(plan: SavedAddressImportPlan) -> None:
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")
    if plan.rows_to_insert:
        for i in range(0, len(plan.rows_to_insert), 200):
            batch = plan.rows_to_insert[i : i + 200]
            supabase.table("saved_addresses").insert(batch).execute()
