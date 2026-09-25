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
- At most one Home and one Work saved address per user (migration 485's
  partial unique index on ``(user_id, icon) WHERE icon IN ('home','work')``).
  A legacy row that would be a second Home/Work for the same user -- either
  because one already exists in ``saved_addresses``, or an earlier row in
  this same batch already claimed it -- is imported as a plain 'location'
  place instead of a second Home/Work. No address is dropped. A residual
  race at commit time (a write lands between validate and commit) is
  handled the same way and reported in ``ImportCommitResult.race_conflicts``
  rather than aborting the run -- see ``commit_saved_address_import_plan``.

The CSV's own column shapes don't map 1:1 onto ``SavedAddress`` --
``name`` in the source is actually the full formatted address string, and
``type`` (home/work/blank) is the short label. Mapped accordingly:
``address`` <- CSV ``name``, ``name``/``icon`` <- CSV ``type``.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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
# Icons migration 485's unique index singles out -- at most one row per
# (user_id, icon) for these two. Everything else (including _DEFAULT_ICON)
# can repeat freely, same as routes/addresses.py's own singleton rule.
_SINGLETON_ICONS = frozenset(_TYPE_ICONS.values())


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
    # A row that would have been a second Home/Work for its user was
    # imported as a plain 'location' place instead (see module docstring).
    # Not a "skipped_*" counter -- the row IS in rows_to_insert, just
    # retyped, so it isn't dropped like the skipped_* counters above.
    downgraded_duplicate_home_work: int = 0
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
    existing_home_work_by_user: dict[str, set[str]] = {}
    for row in _select_in("saved_addresses", "user_id,address,icon", "user_id", user_ids):
        existing_by_user.setdefault(row["user_id"], set()).add(row["address"])
        icon = row.get("icon")
        if icon in _SINGLETON_ICONS:
            existing_home_work_by_user.setdefault(row["user_id"], set()).add(icon)

    # Tracks a home/work icon a *prior row in this same batch* already
    # claimed for a user, so two legacy 'home' rows for the same rider don't
    # both try to become the DB's one Home -- the DB-existing check above
    # only sees rows already committed, not siblings still being planned.
    claimed_this_batch: dict[str, set[str]] = {}

    # Earliest-created legacy row wins the DB's one Home/one Work slot, not
    # just whichever the CSV happens to list first -- same "oldest row is
    # kept" rule routes/addresses.py uses for this table's own live
    # replace-on-save logic, rather than depending on export row order.
    for r in sorted(resolved, key=lambda c: c["created_at"]):
        if r["address"] in existing_by_user.get(r["user_id"], set()):
            plan.skipped_already_imported += 1
            continue

        icon = r["icon"]
        if icon in _SINGLETON_ICONS:
            claimed = claimed_this_batch.setdefault(r["user_id"], set())
            if icon in existing_home_work_by_user.get(r["user_id"], set()) or icon in claimed:
                icon = _DEFAULT_ICON
                plan.downgraded_duplicate_home_work += 1
            else:
                claimed.add(icon)

        plan.rows_to_insert.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": r["user_id"],
                "name": r["label"],
                "address": r["address"],
                "lat": r["lat"],
                "lng": r["lng"],
                "icon": icon,
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


@dataclass
class ImportCommitResult:
    inserted: int = 0
    # A row that lost a race for (user_id, icon) against a write that landed
    # between validate and commit (another import run, or the rider's own
    # app) -- not inserted this run. Re-running validate+commit picks it up
    # again and, per build_saved_address_import_plan, imports it as a plain
    # 'location' place instead. Never silently dropped: logged at error
    # level in commit_saved_address_import_plan and returned here so the
    # admin route can report it.
    race_conflicts: list[ImportReportItem] = field(default_factory=list)


def _is_unique_violation(exc: Exception) -> bool:
    """Postgres 23505 via PostgREST/supabase-py -- same detection this
    repo's other raw-supabase-py legacy-import commits use (e.g.
    services/stripe_payout_sync_service.py's ``_is_unique_violation``)."""
    return getattr(exc, "code", None) == "23505" or "duplicate key value" in str(exc)


def commit_saved_address_import_plan(plan: SavedAddressImportPlan) -> ImportCommitResult:
    """Insert the planned rows. Sync (call via ``asyncio.to_thread`` from routes).

    Inserts in chunks of 200 for throughput. If a chunk hits migration 485's
    one-Home/one-Work unique index -- a race the plan-build dedup didn't see
    because it landed after validate -- that chunk falls back to row-by-row
    inserts instead of losing every row in it. Only the specific
    conflicting row(s) are excluded; everything else in the chunk still
    commits. A conflict on any icon other than home/work is not this race
    (e.g. an id collision) and is raised, never swallowed.
    """
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")

    result = ImportCommitResult()
    for i in range(0, len(plan.rows_to_insert), 200):
        chunk = plan.rows_to_insert[i : i + 200]
        try:
            supabase.table("saved_addresses").insert(chunk).execute()
            result.inserted += len(chunk)
            continue
        except Exception as e:
            if not _is_unique_violation(e):
                raise

        for row in chunk:
            try:
                supabase.table("saved_addresses").insert(row).execute()
                result.inserted += 1
            except Exception as row_e:
                if not _is_unique_violation(row_e) or row["icon"] not in _SINGLETON_ICONS:
                    # Not the home/work race this fallback exists for --
                    # surface it loudly rather than guess.
                    raise
                logger.error(
                    "saved_addresses home/work unique violation on legacy import "
                    "(user_id=%s icon=%s old_address_id=%s) -- a concurrent write "
                    "claimed this Home/Work first; row not inserted this run",
                    row["user_id"],
                    row["icon"],
                    row["legacy_import_metadata"]["old_address_id"],
                )
                result.race_conflicts.append(
                    ImportReportItem(
                        0,
                        "icon",
                        f"home/work race on user_id={row['user_id']} icon={row['icon']}; not inserted",
                    )
                )
    return result
