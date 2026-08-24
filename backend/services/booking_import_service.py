"""Legacy booking import — parse, validate, and commit previous-app bookings.

Imports rides from the previous (MongoDB-backed) app into ``rides`` so riders
and drivers see their trip history in Spinr. Customers and drivers are matched
to existing accounts by phone number; unmatched parties import with a NULL
link so the row can be re-linked later.

Two independent code paths, selected by the legacy ``booking_status``:

- ``completed`` — full fare/earnings import (see the money-safety paragraph
  below). This is the ORIGINAL 2026-07-29 import path; its behavior is
  unchanged by the cancelled/failed path added 2026-08-20.
- ``cancelled`` / ``failed`` — added 2026-08-20
  (docs/change-log/2026-08-20-legacy-cancelled-failed-booking-import.md,
  ACTION_ITEMS.md A41). Every cancelled/failed legacy row still carries real
  pickup/dropoff GPS and ``created_at``, which PIPEDA/SK Transportation Act
  retention rules require Spinr to keep — the previous claim here that these
  rows "carry no fare, no earnings, and no history value" was wrong; they
  simply carry no MONEY value. This path writes status/cancellation fields
  only — no fare, no earnings, no payout, no driver recount ("skip
  payout-offset logic, keep GPS+timestamps" — P2-migration-completeness.md
  item #2, reconfirmed 2026-08-19). A row that has BOTH ``start_ride_at`` and
  ``complete_delivery_at`` populated regardless of its legacy status is
  structurally indistinguishable from a genuinely completed trip (mislabeled
  in the old app) and is redirected to a third branch instead: imported as
  ``rides.status='completed'`` (required by the ride state machine's "never
  cancelled after trip start" invariant) but with $0 fare/earnings/payout —
  see ``docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md``
  for why (0/225 legacy `failed` bookings, including every one of these, have
  any record in the old app's own ``payments.csv`` export — the trip
  happened but was never paid for) and
  ``docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md``
  for the implementation. Counted separately in the report
  (``cancelled_failed_zero_fare_completed``), never silently merged into
  either the normal cancelled/failed count or the real-earnings completed
  count.

Driver payouts for COMPLETED rides were ALREADY settled in the previous app.
The new app derives ``payable_balance`` live from completed rides
(``routes/drivers/earnings.py``), so importing real earnings without an offset
would let a driver withdraw money they were already paid. Every matched driver
therefore gets one offsetting ``payouts`` row (``payout_type='legacy_import'``,
``status='completed'``) whose amount equals the sum of their imported
earnings. Net payable delta per driver is exactly $0. Cancelled/failed rows
have no earnings, so they never touch this offset mechanism.

Every imported ``status='completed'`` row (both the real-earnings path and
the anomalous $0-fare path) that carries a matched driver plus
``arrived_at``/``started_at``/``completed_at`` also gets reconstructed
``driver_insurance_periods`` rows (Period 2: arrived→started; Period 3:
started→completed), each marked ``is_reconstructed=true`` — mirrors
migration 332's one-time historical backfill for the original 186 rides,
applied per-row here so every future delta import carries its own
regulatory audit trail instead of needing a manual SQL pass afterward. Same
disclosed limitation as 332: Period 2 starts from ``arrived_at``, not the
true (never-captured) driver-assignment moment. A row missing any of the
three timestamps or the driver match simply gets no period row for the leg
it can't support — never a fabricated boundary. Cancelled/failed rows never
get period rows (no driver-liability reconstruction is attempted for a
ride that was never marked completed).

Follows the same validate-then-commit contract as rider_import_service and
driver_import_service. Reports carry only row numbers + legacy booking codes —
never names, phones, or addresses.

Re-running is safe: rows already written by this importer (matched on
``rides.legacy_import_metadata->>'old_booking_id'``) are skipped, offset payout
IDs are deterministic per (batch, driver), and ``drivers.total_rides`` is
recomputed as a COUNT rather than incremented. Pass the same ``--batch`` when
resuming so payout IDs line up.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase

try:
    from ..utils.earnings_snapshot import build_earnings_snapshot
    from ..utils.money import to_decimal
except ImportError:
    from utils.earnings_snapshot import build_earnings_snapshot  # type: ignore
    from utils.money import to_decimal  # type: ignore

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "legacy_mongo_booking_import"
PAYOUT_TYPE = "legacy_import"
PAYOUT_LABEL = "Settled in previous app (legacy import)"

# The full fare/earnings import path — unchanged since 2026-07-29.
TARGET_BOOKING_STATUS = "completed"

# Added 2026-08-20 (A41): legacy bookings that never completed a trip. Both
# statuses land on rides.status='cancelled' -- Spinr's own state machine has
# no separate "failed" status, and every row in both legacy buckets is
# pre-trip (see module docstring). A booking whose legacy status is neither
# this, TARGET_BOOKING_STATUS, nor one of these (e.g. the export's 2 blank
# `""` rows) is unrecognized and is skipped entirely -- never guessed at.
LEGACY_CANCELLED_STATUSES = frozenset({"cancelled", "failed"})

# Synthetic cancellation_reason for a cancelled/failed row whose legacy
# cancelled_reason was blank (the overwhelming majority of the `failed`
# bucket -- "no driver was ever found", not a payment/system failure).
# admin_cancellation_breakdown buckets on this text, so it must read as a
# real reason, not silently render NULL into that function's 'unspecified'
# bucket.
NO_DRIVER_FOUND_REASON = "No driver found (legacy import)"

# Canadian accounts only. The legacy export contains the previous vendor's
# test accounts (country code 91 / yopmail addresses) whose rides are not real.
CANADA_COUNTRY_CODE = "1"

ZERO = Decimal("0")

# Legacy per-ride charges that map onto area_fees_breakdown. Everything the
# rider was charged beyond the ride fare, tax, and tip lives here so the
# receipt lines still sum to the amount actually charged.
FEE_COLUMNS: list[tuple[str, str]] = [
    ("city_fees", "City fee"),
    ("infrastructure_fees", "Infrastructure fee"),
    ("insurance_fees", "Insurance fee"),
    ("airport_pickup_charges", "Airport pickup fee"),
    ("airport_drop_charges", "Airport dropoff fee"),
    ("surcharge_amount", "Surcharge"),
    ("toll_price", "Tolls"),
    ("stop_charges", "Stop charges"),
    ("extra_kms_amount", "Extra distance"),
    ("extra_hours_amount", "Extra time"),
    ("night_stay_charges", "Night stay"),
    ("return_trip_charges", "Return trip"),
]

# Average city speed used only to estimate duration for the rare legacy row
# with no start-of-ride timestamp.
FALLBACK_SPEED_KMH = Decimal("30")


@dataclass
class ImportReportItem:
    row_num: int
    booking_code: str
    field: str
    message: str


@dataclass
class BookingImportPlan:
    rides_to_insert: list[dict[str, Any]] = field(default_factory=list)
    payouts_to_insert: list[dict[str, Any]] = field(default_factory=list)
    # Reconstructed driver_insurance_periods rows for newly-imported completed
    # rides -- see build_plan()'s insurance-period comment below for the
    # reconstruction rule (mirrors migration 332's one-time historical
    # backfill, applied here per-row so every future delta import gets an
    # audit trail instead of needing a manual SQL backfill each time).
    insurance_periods_to_insert: list[dict[str, Any]] = field(default_factory=list)
    driver_ids_to_recount: set[str] = field(default_factory=set)
    warnings: list[ImportReportItem] = field(default_factory=list)
    errors: list[ImportReportItem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def read_csv(path: Path) -> list[dict[str, str]]:
    """Parse a legacy export CSV into name-keyed rows.

    The previous app's booking export emits rows with one more field than the
    header: a trailing, unnamed column (a Mongo field added after the header
    was written). ``csv.DictReader`` collects those surplus values under the
    ``None`` key. They are dropped here — surplus values are positionally
    *after* every named column, so the named columns stay correctly aligned.

    Alignment is not assumed: the driver-earnings export independently repeats
    each booking's total as ``booking_amount``, and it matches the ``bookings``
    row's ``total_amount``, which would be impossible under a column shift.
    A short row (fewer fields than the header) yields ``None`` values, which
    become empty strings and are then caught by the per-field validation in
    ``build_plan`` rather than being silently treated as zero.
    """
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        try:
            return _rows_from_reader(csv.DictReader(f))
        except ValueError as e:
            raise ValueError(f"{e}: {path}") from e


def read_csv_text(text: str) -> list[dict[str, str]]:
    """Parse legacy CSV *content* into name-keyed rows.

    Sibling of :func:`read_csv` for callers that hold the content rather than a
    path — the admin upload endpoint receives bytes, not a filesystem path.

    Deliberately NOT a copy of ``rider_import_service.read_csv_text``: that one
    applies ``normalize_header`` and has no ``None``-key guard. Legacy booking
    exports need the opposite on both counts (see :func:`read_csv`), so both
    entry points share ``_rows_from_reader`` rather than reimplementing the
    parse and drifting apart.

    A leading BOM is stripped here as well as at decode time. ``read_csv``
    gets this free from ``encoding="utf-8-sig"``, but this function receives
    an already-decoded string: a caller that decoded as plain ``utf-8`` would
    otherwise leave the BOM glued to the first header, turning ``_id`` into
    ``﻿_id`` and failing every row's ID lookup. Windows-exported CSVs
    routinely carry one.
    """
    return _rows_from_reader(csv.DictReader(io.StringIO(text.lstrip("﻿"), newline="")))


def _rows_from_reader(reader: csv.DictReader) -> list[dict[str, str]]:
    """Shared row normalization for both CSV entry points."""
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    return [{k.strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def normalize_phone(phone: str) -> str:
    """Legacy 10-digit NANP number -> E.164, matching users.phone storage."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return (phone or "").strip()


def parse_money(value: str) -> Decimal:
    """Parse a legacy money field to a 2-dp Decimal (blank -> 0).

    Legacy amounts arrive as JS floats with binary artifacts (e.g.
    ``0.20660000000000012``); ``to_decimal`` quantizes them to cents.
    """
    raw = (value or "").strip()
    if not raw:
        return ZERO
    return to_decimal(raw)


def parse_epoch_ms(value: str) -> str | None:
    """Legacy epoch-milliseconds timestamp -> ISO-8601 UTC string."""
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


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def get_service_area(service_area_id: str | None, service_area_name: str) -> dict[str, Any]:
    if service_area_id:
        rows = supabase.table("service_areas").select("id,name").eq("id", service_area_id).limit(1).execute().data or []
    else:
        rows = (
            supabase.table("service_areas")
            .select("id,name")
            .ilike("name", f"%{service_area_name}%")
            .limit(5)
            .execute()
            .data
            or []
        )
    if not rows:
        raise RuntimeError(f"Service area '{service_area_id or service_area_name}' not found; pass --service-area-id")
    if len(rows) > 1 and not service_area_id:
        names = ", ".join(f"{r.get('name')} ({r.get('id')})" for r in rows)
        raise RuntimeError(f"Multiple service areas matched; pass --service-area-id. Matches: {names}")
    return rows[0]


def get_vehicle_type(vehicle_type_id: str | None, vehicle_type_name: str) -> dict[str, Any]:
    """Resolve the vehicle type stamped on every imported ride.

    ``vehicle_type_id`` is non-optional in the rider app's ride-history model,
    so historical rides need one even though the legacy export's vehicle IDs
    mean nothing in this database.
    """
    if vehicle_type_id:
        rows = supabase.table("vehicle_types").select("id,name").eq("id", vehicle_type_id).limit(1).execute().data or []
    else:
        rows = (
            supabase.table("vehicle_types")
            .select("id,name")
            .ilike("name", f"%{vehicle_type_name}%")
            .limit(5)
            .execute()
            .data
            or []
        )
    if not rows:
        raise RuntimeError(f"Vehicle type '{vehicle_type_id or vehicle_type_name}' not found; pass --vehicle-type-id")
    if len(rows) > 1 and not vehicle_type_id:
        names = ", ".join(f"{r.get('name')} ({r.get('id')})" for r in rows)
        raise RuntimeError(f"Multiple vehicle types matched; pass --vehicle-type-id. Matches: {names}")
    return rows[0]


def _index_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["_id"]: r for r in rows if r.get("_id")}


def _earnings_by_booking(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Group legacy driver-earning rows by the booking they belong to.

    Referral bonus rows carry an empty ``booking_id`` and are ignored: they are
    not ride earnings and were settled separately in the previous app.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        bid = (r.get("booking_id") or "").strip()
        if bid:
            out.setdefault(bid, []).append(r)
    return out


def _fetch_already_imported() -> set[str]:
    """Legacy booking IDs this importer has already written (resume support)."""
    rows = (
        supabase.table("rides")
        .select("legacy_import_metadata")
        .filter("legacy_import_metadata->>source", "eq", IMPORT_SOURCE)
        .execute()
        .data
        or []
    )
    out: set[str] = set()
    for r in rows:
        old_id = (r.get("legacy_import_metadata") or {}).get("old_booking_id")
        if old_id:
            out.add(old_id)
    return out


def payout_id_for(batch: str, driver_id: str) -> str:
    return f"legacy-import-{batch}-{driver_id}"


def _plan_insurance_periods(
    plan: "BookingImportPlan",
    ride: dict[str, Any],
    driver_id: str | None,
    arrived_at: str | None,
    started_at: str | None,
    completed_at: str | None,
) -> None:
    """Reconstruct Period 2 / Period 3 driver_insurance_periods rows for a
    newly-imported completed legacy ride.

    Mirrors migration 332's one-time historical backfill exactly (same
    columns, same `is_reconstructed = true` marker, same disclosed
    limitation: Period 2 starts from `arrived_at`, not the true unrecorded
    driver-assignment moment) -- applied per-row at import time instead of a
    manual SQL pass, so every future delta import gets an audit trail
    automatically. Only writes a period when every timestamp/driver_id it
    needs is present; a ride missing one (e.g. no `arrived_at`) simply gets
    no row for that period, same as migration 332 excluded rides it could
    not support -- never a fabricated boundary.

    `ride["id"]` is the client-generated UUID already set on the ride dict
    (see the `"id": str(uuid.uuid4())` line above) -- available before the
    ride itself is inserted, so both are queued together and committed in
    the same `commit_plan()` call (rides first, so the FK exists).
    """
    if not driver_id:
        return
    if arrived_at and started_at:
        plan.insurance_periods_to_insert.append(
            {
                "driver_id": driver_id,
                "period": 2,
                "ride_id": ride["id"],
                "started_at": arrived_at,
                "ended_at": started_at,
                "is_reconstructed": True,
            }
        )
    if started_at and completed_at:
        plan.insurance_periods_to_insert.append(
            {
                "driver_id": driver_id,
                "period": 3,
                "ride_id": ride["id"],
                "started_at": started_at,
                "ended_at": completed_at,
                "is_reconstructed": True,
            }
        )


def _match_rider_driver(
    cust: dict[str, str],
    drv: dict[str, str],
    users_by_phone: dict[str, dict[str, Any]],
    drivers_by_phone: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Resolve a legacy customer/driver row pair to Spinr user/driver ids by phone.

    Shared, read-only, byte-for-byte identical to the matching the completed
    path always used inline — extracted so the cancelled/failed path (added
    2026-08-20) can reuse it without duplicating the phone-normalization
    lookup. Pure function: takes no dependency on ``build_plan``'s loop state,
    so it carries no risk of changing the completed path's behavior.
    """
    rider_row = users_by_phone.get(normalize_phone(cust.get("phone", "")))
    driver_row = drivers_by_phone.get(normalize_phone(drv.get("phone", "")))
    rider_id = rider_row["id"] if rider_row else None
    driver_id = driver_row["id"] if driver_row else None
    return rider_id, driver_id


def build_plan(
    bookings: list[dict[str, str]],
    customers: list[dict[str, str]],
    drivers: list[dict[str, str]],
    earnings: list[dict[str, str]],
    *,
    service_area: dict[str, Any],
    vehicle_type: dict[str, Any],
    batch: str,
) -> BookingImportPlan:
    plan = BookingImportPlan()
    now_iso = datetime.now(timezone.utc).isoformat()

    customers_by_id = _index_by_id(customers)
    drivers_by_id = _index_by_id(drivers)
    earnings_by_booking = _earnings_by_booking(earnings)

    # Select the target rows first so prefetch only asks about phones we need.
    # `targets` (completed) is selected and filtered EXACTLY as before this
    # branch was added — see the `if status == TARGET_BOOKING_STATUS:` arm
    # below, which is a straight copy of the pre-2026-08-20 loop body.
    targets: list[tuple[int, dict[str, str]]] = []
    cancelled_failed_targets: list[tuple[int, dict[str, str]]] = []
    skipped_not_completed = 0
    skipped_test_account = 0
    cancelled_target_rows = 0
    failed_target_rows = 0
    for idx, b in enumerate(bookings, start=1):
        status = (b.get("booking_status") or "").strip()

        if status == TARGET_BOOKING_STATUS:
            cust = customers_by_id.get((b.get("customer_id") or "").strip())
            drv = drivers_by_id.get((b.get("driver_id") or "").strip())
            cust_cc = (cust or {}).get("country_code")
            drv_cc = (drv or {}).get("country_code")
            if cust_cc != CANADA_COUNTRY_CODE or drv_cc != CANADA_COUNTRY_CODE:
                skipped_test_account += 1
                continue
            targets.append((idx, b))

        elif status in LEGACY_CANCELLED_STATUSES:
            # Same test-account intent as the completed branch, adapted for a
            # party that may legitimately be entirely absent: a cancelled/
            # failed booking's driver_id is blank far more often than not
            # (only 269/712 cancelled + 14/225 failed rows in the real export
            # ever had a driver assigned) -- unlike a completed ride, which
            # always has one. Requiring the driver's country code to equal
            # Canada even when NO driver was ever assigned would wrongly
            # reject the overwhelming majority of this bucket as "test
            # accounts". The customer is always required (every booking has
            # one); the driver check applies only when a driver row exists.
            cust = customers_by_id.get((b.get("customer_id") or "").strip())
            drv = drivers_by_id.get((b.get("driver_id") or "").strip())
            cust_cc = (cust or {}).get("country_code")
            if cust_cc != CANADA_COUNTRY_CODE:
                skipped_test_account += 1
                continue
            if drv and drv.get("country_code") != CANADA_COUNTRY_CODE:
                skipped_test_account += 1
                continue
            cancelled_failed_targets.append((idx, b))
            if status == "cancelled":
                cancelled_target_rows += 1
            else:
                failed_target_rows += 1

        else:
            # Includes the export's 2 blank (`""`) booking_status rows: not
            # completed, cancelled, or failed, so genuinely unknown -- unsafe
            # to guess, always skipped here.
            skipped_not_completed += 1

    legacy_phones = sorted(
        {
            normalize_phone(p)
            for _, b in targets + cancelled_failed_targets
            for p in (
                (customers_by_id.get((b.get("customer_id") or "").strip()) or {}).get("phone", ""),
                (drivers_by_id.get((b.get("driver_id") or "").strip()) or {}).get("phone", ""),
            )
            if p
        }
    )

    users_by_phone: dict[str, dict[str, Any]] = {}
    drivers_by_phone: dict[str, dict[str, Any]] = {}
    if legacy_phones:
        for u in _select_in("users", "id,phone", "phone", legacy_phones):
            if u.get("phone") and u["phone"] not in users_by_phone:
                users_by_phone[u["phone"]] = u
        for d in _select_in("drivers", "id,phone", "phone", legacy_phones):
            if d.get("phone") and d["phone"] not in drivers_by_phone:
                drivers_by_phone[d["phone"]] = d

    already_imported = _fetch_already_imported()

    seen_old_ids: set[str] = set()
    skipped_already_imported = 0
    skipped_unmatched_both = 0
    unmatched_riders = 0
    unmatched_drivers = 0
    earnings_fallback_rows = 0
    missing_start_rows = 0
    sum_rider_paid = ZERO
    sum_driver_total = ZERO
    sum_tips = ZERO
    sum_tax = ZERO
    payout_totals: dict[str, Decimal] = {}

    for idx, b in targets:
        old_id = (b.get("_id") or "").strip()
        code = (b.get("booking_id") or "").strip() or f"row{idx}"

        if not old_id:
            plan.errors.append(ImportReportItem(idx, code, "_id", "booking is missing its legacy _id"))
            continue
        if old_id in seen_old_ids:
            plan.errors.append(ImportReportItem(idx, code, "_id", "duplicate legacy booking _id in CSV"))
            continue
        seen_old_ids.add(old_id)

        if old_id in already_imported:
            skipped_already_imported += 1
            continue

        cust = customers_by_id.get((b.get("customer_id") or "").strip()) or {}
        drv = drivers_by_id.get((b.get("driver_id") or "").strip()) or {}

        rider_id, driver_id = _match_rider_driver(cust, drv, users_by_phone, drivers_by_phone)

        if rider_id is None:
            unmatched_riders += 1
        if driver_id is None:
            unmatched_drivers += 1
        if rider_id is None and driver_id is None:
            # Nobody in this app can ever see it; importing would only add an
            # orphan row to admin listings.
            skipped_unmatched_both += 1
            continue

        # --- coordinates / addresses (NOT NULL columns) ---
        try:
            pickup_lat = float(b["pickup_lat"])
            pickup_lng = float(b["pickup_long"])
            dropoff_lat = float(b["drop_lat"])
            dropoff_lng = float(b["drop_long"])
        except (KeyError, TypeError, ValueError):
            plan.errors.append(ImportReportItem(idx, code, "coordinates", "missing or unparseable pickup/drop lat/lng"))
            continue

        pickup_address = (b.get("pickup_address") or "").strip()
        dropoff_address = (b.get("drop_address") or "").strip()
        if not pickup_address:
            pickup_address = "Address unavailable (imported ride)"
            plan.warnings.append(ImportReportItem(idx, code, "pickup_address", "legacy pickup address was blank"))
        if not dropoff_address:
            dropoff_address = "Address unavailable (imported ride)"
            plan.warnings.append(ImportReportItem(idx, code, "drop_address", "legacy drop address was blank"))

        # --- timestamps ---
        created_at = parse_epoch_ms(b.get("created_at", ""))
        completed_at = parse_epoch_ms(b.get("complete_delivery_at", ""))
        started_at = parse_epoch_ms(b.get("start_ride_at", ""))
        arrived_at = parse_epoch_ms(b.get("arrived_pickup_loc_at", ""))
        if not created_at:
            plan.errors.append(ImportReportItem(idx, code, "created_at", "missing or unparseable created_at"))
            continue
        if not completed_at:
            plan.errors.append(
                ImportReportItem(idx, code, "complete_delivery_at", "completed booking has no completion timestamp")
            )
            continue

        distance_km = parse_money(b.get("distance_in_km", ""))

        if started_at:
            elapsed_min = (
                datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
            ).total_seconds() / 60
            duration_minutes = max(1, round(elapsed_min))
        else:
            missing_start_rows += 1
            plan.warnings.append(
                ImportReportItem(idx, code, "start_ride_at", "no start timestamp; duration estimated from distance")
            )
            duration_minutes = max(1, int(distance_km / FALLBACK_SPEED_KMH * 60) + 5)

        # --- money ---
        total_amount = parse_money(b.get("total_amount", ""))
        gst = parse_money(b.get("gst", ""))
        # bookings.csv's "gst" column is exactly "commission_gst_amount" --
        # GST on Spinr's own small platform commission fee, NOT GST on the
        # rider-facing fare (verified 2026-08-15: gst == commission_gst_amount
        # in every sampled row; the fare-scaling GST lives in a completely
        # separate "payout_gst_amount" column this importer has never read).
        # tax_amount below is therefore a real number but the WRONG BASE for
        # "tax the rider paid on this ride" -- do not assume it is. The
        # correct historical rider-facing GST figure for already-imported
        # rows is not recoverable from this export (no such column exists)
        # and needs a business/legal decision, not a code change, on how to
        # treat it -- see docs/change-log/2026-08-15-legacy-payout-correction-plan.md
        # and this session's tax-audit findings. Preserved raw below so that
        # decision isn't blocked on re-deriving the number from scratch.
        payout_gst_amount = parse_money(b.get("payout_gst_amount", ""))
        discount = parse_money(b.get("coupon_discount", ""))
        tip = parse_money(b.get("tip_driver", ""))
        # Parsed separately (not read back out of `fees` below) so
        # rides.airport_fee -- the dedicated column admin/reporting surfaces
        # read directly (e.g. routes/admin/rides.py's ride-detail endpoint)
        # -- reflects the real historical charge instead of the previous
        # hardcoded 0.0. The rider-facing total is unaffected either way:
        # both charges are still folded into area_fees_breakdown/fees_total
        # below via FEE_COLUMNS, same as before.
        airport_fee = parse_money(b.get("airport_pickup_charges", "")) + parse_money(b.get("airport_drop_charges", ""))

        fees: list[dict[str, Any]] = []
        fees_total = ZERO
        for col, label in FEE_COLUMNS:
            amount = parse_money(b.get(col, ""))
            if amount > ZERO:
                fees.append({"name": label, "calculated_value": float(amount)})
                fees_total += amount

        earning_rows = earnings_by_booking.get(old_id, [])
        if earning_rows:
            driver_total = sum((parse_money(e.get("amount", "")) for e in earning_rows), ZERO)
            admin_earnings = sum((parse_money(e.get("admin_comission_amount", "")) for e in earning_rows), ZERO)
        else:
            # 4 legacy bookings have no earnings row; the booking's own
            # you_earn field carries the same number on every row where both
            # exist, so it is a safe fallback.
            earnings_fallback_rows += 1
            plan.warnings.append(
                ImportReportItem(idx, code, "earnings", "no legacy earnings row; using booking you_earn")
            )
            driver_total = parse_money(b.get("you_earn", ""))
            admin_earnings = ZERO

        driver_fare = driver_total - tip
        if driver_fare < ZERO:
            plan.errors.append(
                ImportReportItem(idx, code, "you_earn", "driver earning is smaller than the tip it should include")
            )
            continue

        # The rider-facing ride fare. The legacy export has no distance/time
        # split, so the ride line is the residual after tax, fees, and tip.
        # This is a PERMANENT limitation, not a bug to fix later: the old app
        # never recorded a per-ride distance/time breakdown at all, so
        # `distance_fare`/`time_fare` are hardcoded to 0.0 below and the whole
        # ride fare lives in `base_fare` instead. Any report that sums
        # distance_fare/time_fare across rides will silently show $0 for every
        # legacy-imported row -- that's expected, not missing data, but a
        # caller doing per-component analytics needs to know to exclude or
        # separately handle legacy_import_metadata != '{}' rows.
        residual = total_amount - gst - fees_total + discount - tip
        if residual < ZERO:
            plan.errors.append(ImportReportItem(idx, code, "total_amount", "fees + tax + tip exceed the total charged"))
            continue

        # Receipt lines, in the order _build_fare_breakdown emits them so the
        # locked snapshot and the recomputed fallback render identically. The
        # tip line must be explicit: the history endpoint appends a synthetic
        # one when a snapshot has no tip line, which would double-count it.
        lines: list[dict[str, Any]] = []
        if residual > ZERO:
            lines.append(
                {
                    "label": f"Ride fare ({round(float(distance_km), 1)} km)",
                    "amount": float(residual),
                    "type": "ride",
                }
            )
        lines.extend({"label": f["name"], "amount": f["calculated_value"], "type": "fee"} for f in fees)
        if gst > ZERO:
            lines.append({"label": "GST", "amount": float(gst), "type": "tax"})
        if discount > ZERO:
            lines.append({"label": "Promo discount", "amount": float(-discount), "type": "discount"})
        if tip > ZERO:
            lines.append({"label": "Tip", "amount": float(tip), "type": "tip"})

        ride: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "rider_id": rider_id,
            "driver_id": driver_id,
            "vehicle_type_id": vehicle_type["id"],
            "service_area_id": service_area["id"],
            "pickup_address": pickup_address,
            "pickup_lat": pickup_lat,
            "pickup_lng": pickup_lng,
            "dropoff_address": dropoff_address,
            "dropoff_lat": dropoff_lat,
            "dropoff_lng": dropoff_lng,
            "distance_km": float(distance_km),
            "duration_minutes": duration_minutes,
            # Σ(base + distance + time + tip) is the driver's payable balance.
            # Carrying the whole old-app earning in base_fare keeps that sum
            # exactly equal to what they actually earned; the rider-facing fare
            # is reconstructed from total_fare via the uplift path.
            "base_fare": float(driver_fare),
            "distance_fare": 0.0,
            "time_fare": 0.0,
            "booking_fee": 0.0,  # explicit: the column defaults to 2.0
            # Real historical airport pickup/dropoff charge (see the
            # `airport_fee` parsing comment above) -- still also folded into
            # area_fees_breakdown/fees_total below so the receipt total is
            # unaffected; this just stops admin/reporting surfaces that read
            # rides.airport_fee directly from showing $0 on a real airport trip.
            "airport_fee": float(airport_fee),
            # Verified 2026-08-15, not assumed: the old app's surge-schedule
            # config (surchargedates.csv) had every weekday's time_slots
            # empty and surchargehistories.csv was completely empty -- surge
            # was configured but never once actually applied. 1.0 is the
            # correct historical value for every imported row, not a gap.
            "surge_multiplier": 1.0,
            "total_fare": float(residual),
            "tip_amount": float(tip),
            # str() only at the serialization boundary (rides.grand_total is
            # NUMERIC(10,2) -- str(Decimal) round-trips exact, unlike
            # float()). Verified against information_schema 2026-08-18; see
            # ACTION_ITEMS.md B29 / docs/change-log/2026-08-18-b29-*.md.
            "grand_total": str(total_amount - tip),
            # tax_amount/tax_breakdown are commission-GST, not fare-GST -- see
            # the "gst"-parsing comment above. `rate: 5.0` reflects Canada's
            # actual GST rate (correct regardless of the base it's applied
            # to); it is NOT a claim that this amount is 5% of the fare.
            # rides.tax_amount is NUMERIC(8,2) -- str(Decimal), not float().
            "tax_amount": str(gst),
            "tax_breakdown": {"GST": {"rate": 5.0, "amount": float(gst)}} if gst > ZERO else {},
            "area_fees_breakdown": fees,
            # rides.area_fees_total is NUMERIC(8,2) -- str(Decimal), not float().
            "area_fees_total": str(fees_total),
            # rides.discount_amount is NUMERIC(10,2) -- str(Decimal), not float().
            "discount_amount": str(discount),
            "payment_method": "card",
            "payment_status": "paid",
            "paid_at": completed_at,
            "status": "completed",
            "created_at": created_at,
            "ride_requested_at": created_at,
            "ride_completed_at": completed_at,
            "updated_at": now_iso,
            "driver_earnings": float(driver_total),
            "admin_earnings": float(admin_earnings),
            "driver_earnings_snapshot": build_earnings_snapshot(fare=driver_fare, tip=tip),
            "fare_breakdown_snapshot": {
                "lines": lines,
                "grand_total": float(total_amount),
                "imported": True,
            },
            # Pre-claim the row so the nightly distance-reconciliation sweep
            # skips it instead of flooding on the whole imported batch.
            "distance_reconciled_at": now_iso,
            "legacy_import_metadata": {
                "batch": batch,
                "source": IMPORT_SOURCE,
                "old_booking_id": old_id,
                "old_booking_code": code,
                "old_customer_id": (b.get("customer_id") or "").strip(),
                "old_driver_id": (b.get("driver_id") or "").strip(),
                "imported_at": now_iso,
                # Preserved raw, not merged into tax_amount: the fare-scaling
                # GST component this importer doesn't (yet) know how to
                # correctly apply. Keeping the raw source number means the
                # eventual business/legal decision on historical GST
                # treatment doesn't have to re-derive it from the CSV again.
                "old_payout_gst_amount": float(payout_gst_amount),
                # True when this row has no start_ride_at and duration_minutes
                # was estimated from distance/FALLBACK_SPEED_KMH instead of
                # measured -- otherwise indistinguishable from a real duration
                # once committed (docs/audit/2026-08-19-legacy-migration-data-
                # quality-audit.md, "Legacy rides' estimated duration_minutes
                # carries no per-row marker"). Any consumer of duration_minutes
                # (e.g. the driver Activity screen's "Total Duration" stat)
                # can check this key to exclude/flag estimated rows.
                "duration_estimated": not bool(started_at),
            },
        }
        if started_at:
            # Never write `started_at`: it is a generated column off this one.
            ride["ride_started_at"] = started_at
        if arrived_at:
            ride["driver_arrived_at"] = arrived_at

        plan.rides_to_insert.append(ride)
        _plan_insurance_periods(plan, ride, driver_id, arrived_at, started_at, completed_at)

        sum_rider_paid += total_amount
        sum_driver_total += driver_total
        sum_tips += tip
        sum_tax += gst
        if driver_id:
            payout_totals[driver_id] = payout_totals.get(driver_id, ZERO) + driver_total
            plan.driver_ids_to_recount.add(driver_id)

    # Boundary marker so `rides_planned` below can keep meaning exactly what
    # it always meant (completed rides only) even though cancelled/failed
    # rows land in the SAME plan.rides_to_insert list (commit_plan inserts
    # both in one pass; there is nothing status-specific for it to skip).
    completed_rides_count = len(plan.rides_to_insert)

    # --- cancelled/failed rows: status + cancellation fields only. -----
    # Added 2026-08-20 (A41). No fare, no earnings, no payout, no driver
    # recount — see the module docstring's "skip payout-offset logic, keep
    # GPS+timestamps" design guidance. Every row here already passed the
    # test-account filter above.
    cancelled_failed_skipped_already_imported = 0
    cancelled_failed_zero_fare_completed = 0
    cancelled_failed_skipped_unmatched_both = 0
    cancelled_failed_skipped_missing_coordinates = 0
    cancelled_failed_unmatched_riders = 0
    cancelled_failed_unmatched_drivers = 0

    for idx, b in cancelled_failed_targets:
        legacy_status = (b.get("booking_status") or "").strip()
        old_id = (b.get("_id") or "").strip()
        code = (b.get("booking_id") or "").strip() or f"row{idx}"

        if not old_id:
            plan.errors.append(ImportReportItem(idx, code, "_id", "booking is missing its legacy _id"))
            continue
        if old_id in seen_old_ids:
            plan.errors.append(ImportReportItem(idx, code, "_id", "duplicate legacy booking _id in CSV"))
            continue
        seen_old_ids.add(old_id)

        if old_id in already_imported:
            cancelled_failed_skipped_already_imported += 1
            continue

        # Anomalous-row flag: a row with BOTH start_ride_at and
        # complete_delivery_at populated is structurally indistinguishable
        # from a genuinely completed trip -- mislabeled in the old app, not a
        # real cancellation/failure. Branches to the zero-fare-completed path
        # below instead of the normal cancelled/failed path.
        looks_completed = bool((b.get("start_ride_at") or "").strip()) and bool(
            (b.get("complete_delivery_at") or "").strip()
        )

        cust = customers_by_id.get((b.get("customer_id") or "").strip()) or {}
        drv = drivers_by_id.get((b.get("driver_id") or "").strip()) or {}
        rider_id, driver_id = _match_rider_driver(cust, drv, users_by_phone, drivers_by_phone)

        if rider_id is None:
            cancelled_failed_unmatched_riders += 1
        if driver_id is None:
            cancelled_failed_unmatched_drivers += 1
        if rider_id is None and driver_id is None:
            cancelled_failed_skipped_unmatched_both += 1
            continue

        # --- coordinates / addresses (NOT NULL columns) -- same rule as the
        # completed path, including the address fallback text and warning.
        try:
            pickup_lat = float(b["pickup_lat"])
            pickup_lng = float(b["pickup_long"])
            dropoff_lat = float(b["drop_lat"])
            dropoff_lng = float(b["drop_long"])
        except (KeyError, TypeError, ValueError):
            cancelled_failed_skipped_missing_coordinates += 1
            plan.errors.append(ImportReportItem(idx, code, "coordinates", "missing or unparseable pickup/drop lat/lng"))
            continue

        pickup_address = (b.get("pickup_address") or "").strip()
        dropoff_address = (b.get("drop_address") or "").strip()
        if not pickup_address:
            pickup_address = "Address unavailable (imported ride)"
            plan.warnings.append(ImportReportItem(idx, code, "pickup_address", "legacy pickup address was blank"))
        if not dropoff_address:
            dropoff_address = "Address unavailable (imported ride)"
            plan.warnings.append(ImportReportItem(idx, code, "drop_address", "legacy drop address was blank"))

        created_at = parse_epoch_ms(b.get("created_at", ""))
        if not created_at:
            plan.errors.append(ImportReportItem(idx, code, "created_at", "missing or unparseable created_at"))
            continue

        if looks_completed:
            # Disposition decided 2026-08-20 (AskUserQuestion, "Completed, $0
            # fare") after cross-referencing the old app's own payments.csv
            # export (not one of this importer's CSV inputs; checked offline
            # against the cached MongoDB export) against every cancelled/
            # failed booking: 0/225 `failed` bookings -- including all of
            # these anomalous ones -- have any payment record, vs. 257/271
            # (94.8%) of `completed` bookings. The trip almost certainly
            # happened (real driver assignment, real GPS/timestamps) but was
            # never paid for, so the completed path's offsetting-payout
            # mechanism ("already settled in the previous app") does not
            # apply here. Imported as a real completed ride -- required by
            # the ride state machine's "never cancelled after trip start"
            # invariant, since these rows have real start/end timestamps --
            # with NO fare, NO driver earnings, and NO payout. See
            # docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md
            # for the full investigation and
            # docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md
            # for this implementation.
            completed_at = parse_epoch_ms(b.get("complete_delivery_at", ""))
            started_at = parse_epoch_ms(b.get("start_ride_at", ""))
            arrived_at = parse_epoch_ms(b.get("arrived_pickup_loc_at", ""))
            if not completed_at:
                plan.errors.append(
                    ImportReportItem(idx, code, "complete_delivery_at", "unparseable completion timestamp")
                )
                continue

            distance_km = parse_money(b.get("distance_in_km", ""))
            if started_at:
                elapsed_min = (
                    datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
                ).total_seconds() / 60
                duration_minutes = max(1, round(elapsed_min))
            else:
                duration_minutes = max(1, int(distance_km / FALLBACK_SPEED_KMH * 60) + 5)

            ride = {
                "id": str(uuid.uuid4()),
                "rider_id": rider_id,
                "driver_id": driver_id,
                "vehicle_type_id": vehicle_type["id"],
                "service_area_id": service_area["id"],
                "pickup_address": pickup_address,
                "pickup_lat": pickup_lat,
                "pickup_lng": pickup_lng,
                "dropoff_address": dropoff_address,
                "dropoff_lat": dropoff_lat,
                "dropoff_lng": dropoff_lng,
                "distance_km": float(distance_km),
                "duration_minutes": duration_minutes,
                "base_fare": 0.0,
                "distance_fare": 0.0,
                "time_fare": 0.0,
                "booking_fee": 0.0,
                "airport_fee": 0.0,
                "surge_multiplier": 1.0,
                "total_fare": 0.0,
                "tip_amount": 0.0,
                "grand_total": "0",
                "tax_amount": "0",
                "tax_breakdown": {},
                "area_fees_breakdown": [],
                "area_fees_total": "0",
                "discount_amount": "0",
                # "pending", deliberately never "failed"/"processing"/
                # "requires_action": payment_retry.py's retry_failed_payments()
                # scans exactly that set for any status != 'cancelled' row and
                # would try to actually collect payment on a ~9-year-old $0
                # legacy ride. "pending" is outside its target set. auth_status
                # is deliberately left unset (never "authorized"/"fare_only")
                # so preauth_capture.py's completed+pending sweep can't claim
                # this row either. With total_fare=0, "pending" is not
                # misleading -- nothing is actually owed.
                "payment_status": "pending",
                "status": "completed",
                "created_at": created_at,
                "ride_requested_at": created_at,
                "ride_completed_at": completed_at,
                "updated_at": now_iso,
                "driver_earnings": 0.0,
                "admin_earnings": 0.0,
                "driver_earnings_snapshot": build_earnings_snapshot(fare=ZERO, tip=ZERO),
                "fare_breakdown_snapshot": {
                    # An explicit $0 line, not an empty list -- an empty
                    # `lines` array would render as a blank receipt breakdown
                    # instead of a disclosed $0 charge if the matched rider
                    # ever opens this ride's receipt (spinr-money-auditor
                    # finding, 2026-08-20).
                    "lines": [{"label": "Ride fare (legacy import, no charge)", "amount": 0.0, "type": "ride"}],
                    "grand_total": 0.0,
                    "imported": True,
                },
                "distance_reconciled_at": now_iso,
                "legacy_import_metadata": {
                    "batch": batch,
                    "source": IMPORT_SOURCE,
                    "old_booking_id": old_id,
                    "old_booking_code": code,
                    "old_customer_id": (b.get("customer_id") or "").strip(),
                    "old_driver_id": (b.get("driver_id") or "").strip(),
                    "imported_at": now_iso,
                    "duration_estimated": not bool(started_at),
                    "original_booking_status": legacy_status,
                    # True marks this row as one of the anomalous
                    # looks-completed-but-legacy-status-cancelled/failed rows,
                    # imported with $0 fare per the 2026-08-20 disposition
                    # decision -- distinguishes it from every other legacy
                    # completed row (which does carry real fare/earnings).
                    "anomalous_looks_completed_zero_fare": True,
                },
            }
            if started_at:
                ride["ride_started_at"] = started_at
            if arrived_at:
                ride["driver_arrived_at"] = arrived_at

            plan.rides_to_insert.append(ride)
            _plan_insurance_periods(plan, ride, driver_id, arrived_at, started_at, completed_at)
            cancelled_failed_zero_fare_completed += 1
            if driver_id:
                # Real completed ride -- total_rides (a plain COUNT of
                # status='completed' rows) should include it, unlike the
                # normal cancelled/failed branch below. No payout/earnings
                # delta follows from this (0.0 above).
                plan.driver_ids_to_recount.add(driver_id)
            continue

        # No reliable cancellation timestamp exists in the export (updated_at
        # is 0% populated on both legacy buckets) -- created_at is the only
        # timestamp available, so it doubles as the cancellation time.
        # Flagged as estimated so it is never mistaken for a real measured
        # cancellation moment once committed (mirrors duration_estimated).
        cancelled_at = created_at

        legacy_cancelled_by = (b.get("cancelled_by") or "").strip().lower()
        if legacy_cancelled_by == "customer":
            cancelled_by_value, cancellation_type = "rider", "rider_cancel"
        elif legacy_cancelled_by == "driver":
            cancelled_by_value, cancellation_type = "driver", "driver_cancel"
        else:
            cancelled_by_value, cancellation_type = "system", "no_drivers_found"

        legacy_reason = (b.get("cancelled_reason") or "").strip()
        cancellation_reason = legacy_reason or NO_DRIVER_FOUND_REASON

        ride = {
            "id": str(uuid.uuid4()),
            "rider_id": rider_id,
            "driver_id": driver_id,
            "vehicle_type_id": vehicle_type["id"],
            "service_area_id": service_area["id"],
            "pickup_address": pickup_address,
            "pickup_lat": pickup_lat,
            "pickup_lng": pickup_lng,
            "dropoff_address": dropoff_address,
            "dropoff_lat": dropoff_lat,
            "dropoff_lng": dropoff_lng,
            "booking_fee": 0.0,  # explicit: the column defaults to 2.0
            "status": "cancelled",
            "cancelled_at": cancelled_at,
            "cancellation_reason": cancellation_reason,
            "cancelled_by": cancelled_by_value,
            "cancellation_type": cancellation_type,
            "created_at": created_at,
            "ride_requested_at": created_at,
            "updated_at": now_iso,
            "legacy_import_metadata": {
                "batch": batch,
                "source": IMPORT_SOURCE,
                "old_booking_id": old_id,
                "old_booking_code": code,
                "old_customer_id": (b.get("customer_id") or "").strip(),
                "old_driver_id": (b.get("driver_id") or "").strip(),
                "imported_at": now_iso,
                "cancelled_at_estimated": True,
                # The literal legacy status ("cancelled" or "failed") so a
                # future consumer can tell these apart from the original
                # completed-path import without guessing from status alone
                # (both land on rides.status='cancelled' here).
                "original_booking_status": legacy_status,
            },
        }
        plan.rides_to_insert.append(ride)
        # Deliberately NOT added to plan.driver_ids_to_recount: total_rides is
        # a COUNT of status='completed' rides, and this row is never one.

    # --- offsetting payouts: neutralize the payable balance we just created ---
    existing_payout_ids: set[str] = set()
    if payout_totals:
        wanted = [payout_id_for(batch, d) for d in sorted(payout_totals)]
        existing_payout_ids = {r["id"] for r in _select_in("payouts", "id", "id", wanted)}

    for driver_id in sorted(payout_totals):
        amount = payout_totals[driver_id]
        if amount <= ZERO:
            continue
        pid = payout_id_for(batch, driver_id)
        if pid in existing_payout_ids:
            continue
        plan.payouts_to_insert.append(
            {
                "id": pid,
                "driver_id": driver_id,
                # str() only at the serialization boundary (payouts.amount is
                # NUMERIC as of migration 331 -- str(Decimal) round-trips
                # exact, unlike float()). All arithmetic above stays Decimal.
                "amount": str(amount),
                # 'completed' deducts from payable_balance and is inert to the
                # payout retry loop, the Stripe reconciler, and the migration
                # 250 reservation guard (all of which key off other statuses).
                "status": "completed",
                "payout_type": PAYOUT_TYPE,
                "bank_name": PAYOUT_LABEL,
                "created_at": now_iso,
                "processed_at": now_iso,
                "updated_at": now_iso,
            }
        )

    plan.stats = {
        "bookings_read": len(bookings),
        "skipped_not_completed": skipped_not_completed,
        "skipped_test_account": skipped_test_account,
        "target_rows": len(targets),
        "skipped_already_imported": skipped_already_imported,
        "skipped_unmatched_both": skipped_unmatched_both,
        # Completed rides only -- unchanged meaning from before the
        # cancelled/failed path existed, even though both paths now share
        # plan.rides_to_insert. See `completed_rides_count` above.
        "rides_planned": completed_rides_count,
        "unmatched_riders": unmatched_riders,
        "unmatched_drivers": unmatched_drivers,
        "earnings_fallback_rows": earnings_fallback_rows,
        "missing_start_rows": missing_start_rows,
        "payouts_planned": len(plan.payouts_to_insert),
        "payouts_skipped_existing": len(existing_payout_ids),
        "drivers_to_recount": len(plan.driver_ids_to_recount),
        # --- cancelled/failed path (added 2026-08-20, A41) ---
        "cancelled_target_rows": cancelled_target_rows,
        "failed_target_rows": failed_target_rows,
        # status='cancelled' rows only -- excludes both the real-earnings
        # completed rides (completed_rides_count) AND the zero-fare-completed
        # anomalous rows below, which also land in plan.rides_to_insert but
        # are status='completed'.
        "cancelled_failed_rides_planned": (
            len(plan.rides_to_insert) - completed_rides_count - cancelled_failed_zero_fare_completed
        ),
        "cancelled_failed_skipped_already_imported": cancelled_failed_skipped_already_imported,
        "cancelled_failed_skipped_unmatched_both": cancelled_failed_skipped_unmatched_both,
        # Renamed from cancelled_failed_skipped_looks_completed (2026-08-19
        # audit) -- these rows are no longer skipped as of the 2026-08-20
        # disposition decision; see the loop's "if looks_completed:" branch.
        "cancelled_failed_zero_fare_completed": cancelled_failed_zero_fare_completed,
        "cancelled_failed_skipped_missing_coordinates": cancelled_failed_skipped_missing_coordinates,
        "cancelled_failed_unmatched_riders": cancelled_failed_unmatched_riders,
        "cancelled_failed_unmatched_drivers": cancelled_failed_unmatched_drivers,
        "total_rides_planned": len(plan.rides_to_insert),
        "insurance_periods_planned": len(plan.insurance_periods_to_insert),
        "sum_rider_paid": float(sum_rider_paid),
        "sum_driver_total": float(sum_driver_total),
        "sum_offset_payouts": float(sum((to_decimal(p["amount"]) for p in plan.payouts_to_insert), ZERO)),
        "sum_tips": float(sum_tips),
        "sum_tax": float(sum_tax),
    }
    return plan


def commit_plan(plan: BookingImportPlan) -> None:
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")

    for i in range(0, len(plan.rides_to_insert), 200):
        supabase.table("rides").insert(plan.rides_to_insert[i : i + 200]).execute()

    # After rides: driver_insurance_periods.ride_id has a rides(id) FK, and
    # this table is append-only (migration 64's immutability trigger) with
    # no upsert path -- same partial-commit exposure the payouts insert
    # below already has (a crash between this and the rides insert leaves a
    # gap that build_plan()'s already-imported check would then skip on
    # re-run, same as it would for a missed payout row). Not a new failure
    # mode; see commit_plan's module-level re-run note.
    for i in range(0, len(plan.insurance_periods_to_insert), 200):
        supabase.table("driver_insurance_periods").insert(plan.insurance_periods_to_insert[i : i + 200]).execute()

    for i in range(0, len(plan.payouts_to_insert), 200):
        supabase.table("payouts").insert(plan.payouts_to_insert[i : i + 200]).execute()

    recount_drivers(sorted(plan.driver_ids_to_recount))


def recount_drivers(driver_ids: list[str]) -> None:
    """Reset drivers.total_rides to its invariant: COUNT of completed rides.

    Recomputing rather than incrementing keeps a re-run idempotent and matches
    how migration 74 defines the column.

    Prefers the set-based ``recount_driver_total_rides`` RPC (migration 271):
    one statement that either fully applies or fully rolls back. The per-driver
    fallback below costs two round-trips per driver, which is why the RPC
    exists — a 64-driver import is 128 sequential calls, long enough for an
    HTTP caller to time out part-way and leave counters half-updated.

    The fallback is kept so the CLI still works against a database where
    migration 271 has not been applied yet.
    """
    if not driver_ids:
        return
    try:
        supabase.rpc("recount_driver_total_rides", {"p_driver_ids": driver_ids}).execute()
        return
    except Exception as e:
        # Not swallowed: log loudly and fall back. A missing function (the
        # migration has not run) is recoverable; anything else still surfaces
        # here rather than silently skipping the recount entirely.
        logger.warning(
            "recount_driver_total_rides RPC unavailable, falling back to per-driver recount for %d driver(s): %s",
            len(driver_ids),
            e,
        )

    for driver_id in driver_ids:
        res = (
            supabase.table("rides")
            .select("id", count="exact")
            .eq("driver_id", driver_id)
            .eq("status", "completed")
            .execute()
        )
        total = res.count if res.count is not None else len(res.data or [])
        supabase.table("drivers").update({"total_rides": total}).eq("id", driver_id).execute()


def print_report(plan: BookingImportPlan, *, dry_run: bool) -> None:
    """Print counts and legacy booking codes only — never rider/driver PII."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    s = plan.stats
    print(f"\n=== Legacy booking import ({mode}) ===")
    print(f"  bookings read              : {s.get('bookings_read', 0)}")
    print(f"  skipped (not completed)    : {s.get('skipped_not_completed', 0)}")
    print(f"  skipped (test account)     : {s.get('skipped_test_account', 0)}")
    print(f"  target rows                : {s.get('target_rows', 0)}")
    print(f"  skipped (already imported) : {s.get('skipped_already_imported', 0)}")
    print(f"  skipped (no party matched) : {s.get('skipped_unmatched_both', 0)}")
    print(f"  rides to insert            : {s.get('rides_planned', 0)}")
    print(f"    with unmatched rider     : {s.get('unmatched_riders', 0)}")
    print(f"    with unmatched driver    : {s.get('unmatched_drivers', 0)}")
    print(f"    earnings fallback used   : {s.get('earnings_fallback_rows', 0)}")
    print(f"    duration estimated       : {s.get('missing_start_rows', 0)}")
    print("\n  --- money ---")
    print(f"  rider paid (legacy total)  : ${s.get('sum_rider_paid', 0):,.2f}")
    print(f"  driver earnings imported   : ${s.get('sum_driver_total', 0):,.2f}")
    print(f"    of which tips            : ${s.get('sum_tips', 0):,.2f}")
    print(f"  tax (GST) imported         : ${s.get('sum_tax', 0):,.2f}")
    print(
        f"  offsetting payouts         : ${s.get('sum_offset_payouts', 0):,.2f} "
        f"across {s.get('payouts_planned', 0)} driver(s)"
    )
    print(f"    already present, skipped : {s.get('payouts_skipped_existing', 0)}")
    print(f"  drivers to recount         : {s.get('drivers_to_recount', 0)}")

    print("\n  --- cancelled/failed (status + cancellation fields only, no money) ---")
    print(f"  cancelled target rows       : {s.get('cancelled_target_rows', 0)}")
    print(f"  failed target rows          : {s.get('failed_target_rows', 0)}")
    print(f"  rides to insert             : {s.get('cancelled_failed_rides_planned', 0)}")
    print(f"    with unmatched rider      : {s.get('cancelled_failed_unmatched_riders', 0)}")
    print(f"    with unmatched driver     : {s.get('cancelled_failed_unmatched_drivers', 0)}")
    print(f"  skipped (already imported)  : {s.get('cancelled_failed_skipped_already_imported', 0)}")
    print(f"  skipped (no party matched)  : {s.get('cancelled_failed_skipped_unmatched_both', 0)}")
    print(f"  imported completed, $0 fare : {s.get('cancelled_failed_zero_fare_completed', 0)} (looked completed)")
    print(f"  skipped (bad coordinates)   : {s.get('cancelled_failed_skipped_missing_coordinates', 0)}")
    print(f"  total rides planned (all)   : {s.get('total_rides_planned', 0)}")
    print(
        f"  insurance periods planned   : {s.get('insurance_periods_planned', 0)} (reconstructed, is_reconstructed=true)"
    )

    if plan.warnings:
        print(f"\n  --- warnings ({len(plan.warnings)}) ---")
        for w in plan.warnings[:50]:
            print(f"    row {w.row_num} [{w.booking_code}] {w.field}: {w.message}")
        if len(plan.warnings) > 50:
            print(f"    … and {len(plan.warnings) - 50} more")

    if plan.errors:
        print(f"\n  --- ERRORS ({len(plan.errors)}) ---")
        for e in plan.errors[:50]:
            print(f"    row {e.row_num} [{e.booking_code}] {e.field}: {e.message}")
        if len(plan.errors) > 50:
            print(f"    … and {len(plan.errors) - 50} more")
        print("\n  Refusing to commit until every error above is resolved.")
    print()


# ─────────────────────────────────────────────────────────────────────────
# Historical `duration_estimated` marker backfill (already-imported rides)
#
# docs/change-log/2026-08-19-legacy-migration-transparency-backend.md (§3)
# fixed build_plan() above so every *future* import stamps
# legacy_import_metadata.duration_estimated on the row it writes. That fix
# is import-code-path-only by design and explicitly does not touch the rides
# already committed by the original 2026-07-29 import — this section is the
# deferred follow-up: a dry-run-by-default backfill that stamps the SAME
# marker onto those already-imported rows, without re-estimating or
# otherwise touching duration_minutes itself.
#
# Detection mirrors build_plan()'s own condition exactly, not a
# reimplementation of it: build_plan() only ever writes ride["ride_started_at"]
# when its local `started_at` was truthy (see the "if started_at:" guard
# above), and migration 137 makes `started_at` a read-only generated column
# always equal to `ride_started_at`. So "this committed row has no
# ride_started_at" is precisely "build_plan() took the estimation branch for
# this row" — no CSV re-read or re-estimation needed.
#
# Safety rules, mirroring driver_import_service's legacy SIN/DOB backfill:
#   - only scans rows already carrying this module's own IMPORT_SOURCE in
#     legacy_import_metadata.
#   - never clobbers a row that already has a `duration_estimated` key (or
#     this backfill's own marker key) — whatever is already stamped (by the
#     importer itself, or an earlier run of this backfill) wins.
#   - the write-time guard below re-checks each row immediately before
#     writing, not just at plan time, so a concurrent run of this same
#     script can't double-stamp or race a row.
#   - reports carry only ride ids (internal UUIDs) and counts — never
#     addresses, names, or any other ride PII.
#
# Concurrent-writer hardening (docs/change-log/2026-08-19-legacy-backfill-
# concurrent-writer-fix.md): legacy_gst_backfill_service.py is a SEPARATE
# manual backfill that also read-merge-writes rides.legacy_import_metadata
# (adds `old_payout_gst_amount`) — currently plan-only, with no commit path
# of its own yet (see that module's docstring), but the moment one is added
# it becomes a second writer to this exact column. The `.filter(...,
# "duration_estimated", "is", "null")` guard below only protects THIS
# backfill's own key from being clobbered; on its own it does nothing to
# stop a stale local `meta` snapshot (read here, then written back as the
# WHOLE column) from silently dropping some OTHER key a concurrent writer
# added in between our read and our write. apply_duration_estimated_backfill
# below closes that by adding a second, whole-column optimistic-concurrency
# guard (`.filter("legacy_import_metadata", "eq", <json of the exact row we
# read>)`) so the write only succeeds if nothing else touched the column in
# between — any concurrent writer (this script racing itself, or a future
# legacy_gst_backfill_service.py apply path) is reported as a conflict
# instead of silently losing data, and is safe to retry on the next run.
# Whoever eventually gives legacy_gst_backfill_service.py a commit path MUST
# use the same whole-column snapshot-equality guard shown here — not just a
# check on its own key — for the same reason.

DURATION_ESTIMATED_BACKFILL_MARKER = "legacy_duration_estimated_backfill"


@dataclass
class DurationEstimatedBackfillItem:
    id: str
    old_booking_id: str | None
    duration_estimated: bool


@dataclass
class DurationEstimatedBackfillPlan:
    updates: list[DurationEstimatedBackfillItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_already_marked: int = 0
    total_legacy_rides_scanned: int = 0


def _fetch_legacy_rides_for_duration_backfill() -> list[dict[str, Any]]:
    """Every ride this importer has ever written, minimal columns only.

    Selects id/ride_started_at/legacy_import_metadata rather than '*' — this
    backfill has no reason to pull fare/address/PII columns into memory for a
    marker-only operation.

    Single unpaginated query, matching this file's own `_fetch_already_imported`
    (same filter, same table) rather than `legacy_gst_backfill_service.py`'s
    `.range()`-paginated sibling query — as of the 2026-08-18 insurance-period
    backfill audit (migration 332) there are 186 legacy-imported rides total,
    well under any PostgREST default row cap. If the legacy-imported row count
    ever grows past that (e.g. a much larger future migration), add the same
    `.range()` pagination `legacy_gst_backfill_service._fetch_rows_missing_field`
    already uses before relying on this function returning every row.
    """
    rows = (
        supabase.table("rides")
        .select("id,ride_started_at,legacy_import_metadata")
        .filter("legacy_import_metadata->>source", "eq", IMPORT_SOURCE)
        .execute()
        .data
        or []
    )
    return rows


def plan_duration_estimated_backfill() -> DurationEstimatedBackfillPlan:
    """Plan the historical duration_estimated marker backfill. Read-only —
    issues no writes. Idempotent across repeated dry runs: a row already
    carrying `duration_estimated` or DURATION_ESTIMATED_BACKFILL_MARKER is
    skipped, not re-planned.
    """
    plan = DurationEstimatedBackfillPlan()
    rows = _fetch_legacy_rides_for_duration_backfill()
    plan.total_legacy_rides_scanned = len(rows)

    for row in rows:
        row_id = row.get("id")
        if not row_id:
            plan.errors.append("legacy ride row is missing its id — cannot plan an update for it")
            continue

        meta = row.get("legacy_import_metadata") or {}
        if "duration_estimated" in meta or DURATION_ESTIMATED_BACKFILL_MARKER in meta:
            plan.skipped_already_marked += 1
            continue

        plan.updates.append(
            DurationEstimatedBackfillItem(
                id=row_id,
                old_booking_id=meta.get("old_booking_id"),
                # Same condition build_plan() uses at import time (see the
                # section docstring above): no ride_started_at means the
                # importer took the estimation branch for this row.
                duration_estimated=not bool(row.get("ride_started_at")),
            )
        )
    return plan


def apply_duration_estimated_backfill(plan: DurationEstimatedBackfillPlan, *, batch: str) -> list[str]:
    """Write plan.updates' duration_estimated marker onto `rides`.

    Write-time guard, not just the plan-time snapshot: immediately before
    writing each row, this re-reads its current legacy_import_metadata and
    refuses to touch a row that already carries `duration_estimated` (or this
    backfill's own marker) by the time apply() reaches it — e.g. a concurrent
    run of this same script. This mirrors
    driver_import_service.apply_legacy_sin_dob_import's guard exactly (see
    that function's docstring for why a plan-time-only check is a race): the
    plan-time skip above only proves the key was absent *when planned*. The
    PostgREST-level `.filter(..., "is", "null")` clause on the update itself
    is the atomic half of the guard — the same pattern
    driver_import_service.py's `.is_(col, "null")` calls use for a real
    column, applied here to a JSONB key.

    Second, whole-column guard (concurrent-writer hardening — see the
    section banner comment above this backfill for the full reasoning): the
    `duration_estimated IS NULL` guard above only protects THIS function's
    own key. It writes the ENTIRE `legacy_import_metadata` column back from a
    local `meta` dict built off a single read, so if some other writer (e.g.
    a future legacy_gst_backfill_service.py apply path) adds a different key
    to the same row between our read and our write, that key would
    otherwise be silently overwritten by our stale snapshot — the guard
    above would not catch it, because `duration_estimated` genuinely was
    still null at write time. `.filter("legacy_import_metadata", "eq",
    <json of the row exactly as we read it>)` closes that: it is an
    optimistic-concurrency check on the whole column, so the write only
    succeeds if nobody touched ANY key in between. A mismatch (0 rows
    updated) is reported as a conflict exactly like the existing guard
    already does — never retried onto a stale value, never silently
    dropped — and is picked up cleanly on the next run since the plan step
    always re-reads current state.

    Never touches duration_minutes — only legacy_import_metadata.

    Returns the `id` of every row whose guard didn't match (already marked,
    or any other concurrent write to the column, in the plan/apply window) —
    reported back as a conflict, never silently dropped. Safe to re-run: a
    re-plan after a partial apply only ever contains rows still missing the
    marker.
    """
    if plan.errors:
        raise RuntimeError("refusing to apply with validation errors")
    if not plan.updates:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    conflicts: list[str] = []
    for item in plan.updates:
        existing = supabase.table("rides").select("legacy_import_metadata").eq("id", item.id).execute().data
        read_meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        if "duration_estimated" in read_meta or DURATION_ESTIMATED_BACKFILL_MARKER in read_meta:
            conflicts.append(item.id)
            continue

        meta = dict(read_meta)
        meta["duration_estimated"] = item.duration_estimated
        meta[DURATION_ESTIMATED_BACKFILL_MARKER] = {"batch": batch, "backfilled_at": now_iso}

        res = (
            supabase.table("rides")
            .update({"legacy_import_metadata": meta, "updated_at": now_iso})
            .eq("id", item.id)
            .filter("legacy_import_metadata->>duration_estimated", "is", "null")
            .filter("legacy_import_metadata", "eq", json.dumps(read_meta, sort_keys=True, default=str))
            .execute()
        )
        if not res.data:
            conflicts.append(item.id)

    return conflicts


def print_duration_estimated_backfill_report(plan: DurationEstimatedBackfillPlan, *, dry_run: bool) -> None:
    """Print counts and ride ids only — never addresses, names, or any other PII."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    n_estimated = sum(1 for u in plan.updates if u.duration_estimated)
    n_measured = len(plan.updates) - n_estimated
    print(f"\n=== Legacy ride duration_estimated backfill ({mode}) ===")
    print(f"  legacy rides scanned         : {plan.total_legacy_rides_scanned}")
    print(f"  already marked, skipped      : {plan.skipped_already_marked}")
    print(f"  rows to stamp                : {len(plan.updates)}")
    print(f"    duration_estimated=true    : {n_estimated}")
    print(f"    duration_estimated=false   : {n_measured}")
    if plan.errors:
        print(f"\n  --- ERRORS ({len(plan.errors)}) ---")
        for e in plan.errors[:50]:
            print(f"    {e}")
        if len(plan.errors) > 50:
            print(f"    … and {len(plan.errors) - 50} more")
        print("\n  Refusing to apply until every error above is resolved.")
    print()
