"""Read-only status summary for every legacy-migration/bulk-import/backfill
admin tool, in the verified dependency order (see docs/runbooks/
migration-tool-order.md for the full reasoning).

**This module never writes.** It exists purely to answer "what's already
run, and what's still pending" for the admin dashboard's Bulk Operations
Migration Checklist panel -- every actual import/backfill is still driven by
its own dedicated tool (own dry-run, own commit, own confirmation), same as
before this module existed.

Three tools in the ordered list have no purely-Supabase-derivable "done"
state -- their own completion check requires re-comparing against the
source CSV (the rider/driver created_at fixups) or is itself a one-time
repair with no fixed target population (the orphaned-account backfill has
no "eligible total" to divide by, only a raw defect count). Those are
reported with ``state="manual_check_required"`` rather than a fabricated
percentage -- see each function's own comment for why.

SIN note: both the SIN/DOB backfill (#4) and the Tax-ID import (#9) can
populate ``drivers.sin`` -- they're two different CSV sources for the same
column, not two independent counters. This module reports the SIN count
once, under SIN/DOB backfill; the Tax-ID import's own status only reports
``gst_bn`` (its only column the SIN/DOB backfill never touches), with a note
explaining why SIN isn't double-counted there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from ..supabase_client import supabase
    from .migration_data_quality_service import build_data_quality_scan_plan, fetch_needs_review_ride_ids
    from .migration_driver_repair_service import build_driver_repair_plan
except ImportError:
    from services.migration_data_quality_service import (  # type: ignore
        build_data_quality_scan_plan,
        fetch_needs_review_ride_ids,
    )
    from services.migration_driver_repair_service import build_driver_repair_plan  # type: ignore
    from supabase_client import supabase  # type: ignore

# Mirrors the constants in each importer's own service module -- duplicated
# here rather than imported, matching this codebase's existing per-module
# small-constant convention (see pre_launch_flag_service.py's LAUNCH_DATE
# comment for the same rationale: these modules operate on different
# tables/concerns and a cross-import for one string isn't worth the coupling).
_SASKATOON_DRIVER_SOURCE = "legacy_saskatoon_driver_import"
_MONGO_DRIVER_SOURCE = "legacy_mongo_driver_import"
_RIDER_CSV_SOURCE_KEY = "rider_csv_import"
_SAVED_ADDRESS_SOURCE = "legacy_customer_address_import"


@dataclass
class ToolStatus:
    order: int
    id: str
    name: str
    # "not_started" | "partial" | "done" | "manual_check_required"
    state: str
    detail: str
    admin_path: str
    warning: str | None = None


@dataclass
class MigrationStatusReport:
    tools: list[ToolStatus] = field(default_factory=list)


def _count(table: str, filters: dict[str, Any]) -> int:
    """SELECT id with the given filters, return len(). Uses PostgREST's
    exact count header would be more efficient than fetching rows, but this
    repo's existing db_supabase/repositories layer doesn't expose a
    count-only path through the raw client used here -- fetching `id` only
    keeps the payload small. Not used for tables that could return
    thousands of rows without a narrowing filter (checked per call site)."""
    q = supabase.table(table).select("id")
    for key, val in filters.items():
        if isinstance(val, tuple) and val[0] == "not_null":
            q = q.filter(key, "not.is", "null")
        elif isinstance(val, tuple) and val[0] == "eq":
            q = q.filter(key, "eq", val[1])
        elif isinstance(val, tuple) and val[0] == "like":
            q = q.filter(key, "like", val[1])
        else:
            q = q.eq(key, val)
    return len(q.execute().data or [])


def _eligible_driver_ids() -> list[str]:
    """Drivers created via the Saskatoon CSV (#1) or the Mongo export (#2,
    including linked/enriched rows) -- the population every driver-scoped
    backfill (#4, #5, #9's gst_bn) is gated on. Same two-marker-key logic as
    pre_launch_flag_service.py's _fetch_pre_launch_driver_candidates."""
    by_id: dict[str, None] = {}
    for key in ("source", "mongo_driver_history"):
        rows = (
            supabase.table("drivers")
            .select("id")
            .filter(f"legacy_import_metadata->>{key}", "not.is", "null")
            .execute()
            .data
            or []
        )
        for r in rows:
            if r.get("id"):
                by_id[r["id"]] = None
    return list(by_id.keys())


def _tool_1_bulk_driver_import() -> ToolStatus:
    n = _count("drivers", {"legacy_import_metadata->>source": ("eq", _SASKATOON_DRIVER_SOURCE)})
    return ToolStatus(
        1,
        "bulk_driver_import",
        "Bulk Driver Import (Saskatoon CSV)",
        "not_started" if n == 0 else "done",
        f"{n} driver(s) imported" if n else "No rows imported yet",
        "/dashboard/drivers/import",
    )


def _tool_2_legacy_driver_import(eligible_ids: list[str]) -> ToolStatus:
    mongo_new = _count("drivers", {"legacy_import_metadata->>source": ("eq", _MONGO_DRIVER_SOURCE)})
    linked = (
        len(eligible_ids)
        - mongo_new
        - _count("drivers", {"legacy_import_metadata->>source": ("eq", _SASKATOON_DRIVER_SOURCE)})
    )
    n = mongo_new + max(linked, 0)
    return ToolStatus(
        2,
        "legacy_driver_import",
        "Legacy Driver Import (Mongo drivers.csv)",
        "not_started" if n == 0 else "done",
        f"{mongo_new} new + {max(linked, 0)} linked/enriched driver(s)" if n else "No rows imported yet",
        "/dashboard/drivers/legacy-import",
    )


def _tool_3_bulk_rider_import() -> ToolStatus:
    n = len(
        supabase.table("users")
        .select("id")
        .filter(f"legacy_import_metadata->{_RIDER_CSV_SOURCE_KEY}->>source", "not.is", "null")
        .execute()
        .data
        or []
    )
    return ToolStatus(
        3,
        "bulk_rider_import",
        "Bulk Rider Import",
        "not_started" if n == 0 else "done",
        f"{n} rider(s) imported" if n else "No rows imported yet",
        "/dashboard/bulk-operations",
    )


def _tool_4_sin_dob_backfill(eligible_ids: list[str]) -> ToolStatus:
    if not eligible_ids:
        return ToolStatus(
            4,
            "sin_dob_backfill",
            "Legacy SIN/DOB Backfill",
            "not_started",
            "No eligible drivers yet (run Bulk Driver Import or Legacy Driver Import first)",
            "/dashboard/drivers/legacy-sin-dob-backfill",
        )
    rows = supabase.table("drivers").select("id,sin,date_of_birth").in_("id", eligible_ids).execute().data or []
    with_sin = sum(1 for r in rows if r.get("sin"))
    with_dob = sum(1 for r in rows if r.get("date_of_birth"))
    total = len(eligible_ids)
    state = (
        "done"
        if with_sin == total and with_dob == total
        else ("not_started" if with_sin == 0 and with_dob == 0 else "partial")
    )
    return ToolStatus(
        4,
        "sin_dob_backfill",
        "Legacy SIN/DOB Backfill",
        state,
        f"SIN {with_sin}/{total}, DOB {with_dob}/{total} of eligible drivers",
        "/dashboard/drivers/legacy-sin-dob-backfill",
    )


def _tool_5_vehicle_history_backfill(eligible_ids: list[str]) -> ToolStatus:
    if not eligible_ids:
        return ToolStatus(
            5,
            "vehicle_history_backfill",
            "Legacy Vehicle-History Backfill",
            "not_started",
            "No eligible drivers yet (run Bulk Driver Import or Legacy Driver Import first)",
            "/dashboard/drivers/legacy-vehicle-history-backfill",
        )
    rows = (
        supabase.table("driver_vehicle_history").select("driver_id").in_("driver_id", eligible_ids).execute().data or []
    )
    with_history = len({r["driver_id"] for r in rows if r.get("driver_id")})
    total = len(eligible_ids)
    state = "done" if with_history == total else ("not_started" if with_history == 0 else "partial")
    return ToolStatus(
        5,
        "vehicle_history_backfill",
        "Legacy Vehicle-History Backfill",
        state,
        f"{with_history}/{total} eligible drivers have a vehicle-history row",
        "/dashboard/drivers/legacy-vehicle-history-backfill",
    )


def _tool_6_orphaned_accounts() -> ToolStatus:
    # No fixed "eligible total" -- this is a one-time defect count (drivers
    # flagged is_driver=true by a since-fixed race in the Legacy Driver
    # Import link path, with no matching `drivers` row). Zero is the only
    # meaningful target; any positive count is actionable, not a
    # not_started/partial spectrum.
    driver_flagged = supabase.table("users").select("id").eq("is_driver", True).execute().data or []
    ids = [r["id"] for r in driver_flagged if r.get("id")]
    if not ids:
        return ToolStatus(
            6,
            "orphaned_accounts",
            "Fix Orphaned Legacy-Linked Accounts",
            "done",
            "No is_driver=true users to check",
            "/dashboard/drivers/legacy-import",
        )
    have_driver_row = {
        r["user_id"]
        for r in supabase.table("drivers").select("user_id").in_("user_id", ids).execute().data or []
        if r.get("user_id")
    }
    orphaned = len(ids) - len(have_driver_row)
    return ToolStatus(
        6,
        "orphaned_accounts",
        "Fix Orphaned Legacy-Linked Accounts",
        "done" if orphaned == 0 else "partial",
        f"{orphaned} orphaned account(s) found" if orphaned else "Clean — no orphans found",
        "/dashboard/drivers/legacy-import",
        warning="Action needed" if orphaned else None,
    )


def _tool_7_manual() -> ToolStatus:
    return ToolStatus(
        7,
        "driver_join_date_fix",
        "Fix Backfilled Driver Join Dates",
        "manual_check_required",
        "No Supabase-only signal — re-run its own Preview to check",
        "/dashboard/drivers/legacy-import",
    )


def _tool_8_stripe_mapping() -> ToolStatus:
    drivers_mapped = _count("drivers", {"stripe_account_id": ("not_null", None)})
    riders_mapped = _count("users", {"stripe_customer_id": ("not_null", None)})
    n = drivers_mapped + riders_mapped
    return ToolStatus(
        8,
        "stripe_mapping_import",
        "Stripe Mapping Import",
        "not_started" if n == 0 else "done",
        f"{drivers_mapped} driver(s), {riders_mapped} rider(s) mapped" if n else "Nothing mapped yet",
        "/dashboard/bulk-operations",
    )


def _tool_9_tax_id_import(eligible_ids: list[str]) -> ToolStatus:
    if not eligible_ids:
        return ToolStatus(
            9,
            "tax_id_import",
            "Bulk Driver Tax-ID Import",
            "not_started",
            "No eligible drivers yet",
            "/dashboard/bulk-operations",
        )
    rows = supabase.table("drivers").select("id,gst_bn").in_("id", eligible_ids).execute().data or []
    with_gst = sum(1 for r in rows if r.get("gst_bn"))
    total = len(eligible_ids)
    state = "done" if with_gst == total else ("not_started" if with_gst == 0 else "partial")
    return ToolStatus(
        9,
        "tax_id_import",
        "Bulk Driver Tax-ID Import",
        state,
        f"GST/HST BN {with_gst}/{total} of eligible drivers (SIN reported under #4 — same column, two possible sources)",
        "/dashboard/bulk-operations",
    )


def _tool_10_saved_address_backfill() -> ToolStatus:
    # Deliberately defensive, not a blanket error swallow: migration 373
    # (saved_addresses.legacy_import_metadata) exists in backend/migrations/
    # but was confirmed NOT applied to production as of 2026-08-31 (checked
    # directly against schema_migrations -- 374/2026-08-31 is applied, 373
    # and 375 are not). Until it's applied, this column doesn't exist and
    # the query below 500s; every other tool's status must still render, so
    # this one specific, already-diagnosed gap gets its own honest state
    # instead of taking the whole endpoint down.
    try:
        n = len(
            supabase.table("saved_addresses")
            .select("id")
            .filter("legacy_import_metadata->>source", "eq", _SAVED_ADDRESS_SOURCE)
            .execute()
            .data
            or []
        )
    except Exception:
        return ToolStatus(
            10,
            "saved_address_backfill",
            "Legacy Saved-Address Backfill",
            "manual_check_required",
            "Migration 373 (saved_addresses.legacy_import_metadata) has not been applied to "
            "production yet -- this tool cannot be committed until it is.",
            "/dashboard/riders/legacy-saved-address-backfill",
            warning="Migration 373 not applied",
        )
    return ToolStatus(
        10,
        "saved_address_backfill",
        "Legacy Saved-Address Backfill",
        "not_started" if n == 0 else "done",
        f"{n} saved address(es) imported" if n else "No rows imported yet",
        "/dashboard/riders/legacy-saved-address-backfill",
    )


def _tool_11_legacy_booking_import() -> int:
    return _count("rides", {"legacy_import_metadata->>old_booking_id": ("not_null", None)})


def _tool_11_status() -> ToolStatus:
    n = _tool_11_legacy_booking_import()
    return ToolStatus(
        11,
        "legacy_booking_import",
        "Legacy Booking Import",
        "not_started" if n == 0 else "done",
        f"{n} legacy ride(s) imported" if n else "No rides imported yet",
        "/dashboard/bulk-operations",
    )


def _tool_12_manual() -> ToolStatus:
    return ToolStatus(
        12,
        "rider_join_date_fix",
        "Fix Rider Join Dates",
        "manual_check_required",
        "No Supabase-only signal — re-run its own Preview against the rider CSV to check",
        "/dashboard/bulk-operations",
    )


def _tool_13_wallet_import() -> ToolStatus:
    n = len(
        supabase.table("wallet_transactions")
        .select("id")
        .filter("reference_id", "like", "legacy-wallet-%")
        .execute()
        .data
        or []
    )
    return ToolStatus(
        13,
        "wallet_import",
        "Legacy Wallet-Balance Import",
        "not_started" if n == 0 else "done",
        f"{n} legacy wallet transaction(s) applied" if n else "No transactions applied yet",
        "/dashboard/bulk-operations",
    )


def _imported_ride_ids() -> list[str]:
    rows = (
        supabase.table("rides")
        .select("id,route_snapshot_url,planned_route_polyline")
        .filter("legacy_import_metadata", "not.is", "null")
        .execute()
        .data
        or []
    )
    return rows


def _tool_14_and_15_route_tools(imported_rides: list[dict]) -> tuple[ToolStatus, ToolStatus]:
    total = len(imported_rides)
    if total == 0:
        empty = (
            "not_started",
            "No imported rides yet (run Legacy Booking Import first)",
        )
        return (
            ToolStatus(14, "route_snapshots", "Route Map Snapshots", empty[0], empty[1], "/dashboard/bulk-operations"),
            ToolStatus(15, "route_backfill", "Route Backfill", empty[0], empty[1], "/dashboard/bulk-operations"),
        )
    with_snapshot = sum(1 for r in imported_rides if r.get("route_snapshot_url"))
    with_route = sum(1 for r in imported_rides if r.get("planned_route_polyline"))
    snap_state = "done" if with_snapshot == total else ("not_started" if with_snapshot == 0 else "partial")
    route_state = "done" if with_route == total else ("not_started" if with_route == 0 else "partial")
    return (
        ToolStatus(
            14,
            "route_snapshots",
            "Route Map Snapshots",
            snap_state,
            f"{with_snapshot}/{total} imported rides have a snapshot",
            "/dashboard/bulk-operations",
        ),
        ToolStatus(
            15,
            "route_backfill",
            "Route Backfill",
            route_state,
            # planned_route_polyline being non-null doesn't itself prove it's
            # a real road route vs. a straight-line placeholder -- this is
            # an approximation for a status display, not the backfill
            # tool's own eligibility check (see routes/admin/rides.py).
            f"{with_route}/{total} imported rides have a route polyline (approximate — see tool for exact eligibility)",
            "/dashboard/bulk-operations",
        ),
    )


def _tool_16_pre_launch_flag() -> ToolStatus:
    drivers_flagged = _count("drivers", {"legacy_import_metadata->>pre_launch_test": ("eq", "true")})
    rides_flagged = _count("rides", {"legacy_import_metadata->>pre_launch_test": ("eq", "true")})
    n = drivers_flagged + rides_flagged
    return ToolStatus(
        16,
        "pre_launch_flag",
        "Pre-Launch Legacy Data Flagging",
        "not_started" if n == 0 else "done",
        f"{drivers_flagged} driver(s), {rides_flagged} ride(s) flagged" if n else "Nothing flagged yet",
        "/dashboard/bulk-operations",
    )


def _tool_17_data_quality_scan() -> ToolStatus:
    """Unlike every other tool above, there's no fixed "eligible population"
    to divide by -- only a fraction of completed rides are ever expected to
    have an issue at all, so an "X/Y" ratio against total completed rides
    would misleadingly imply most rides need review. Instead this re-runs
    the same live, read-only detection scan the admin tool's own Preview
    button runs (build_data_quality_scan_plan, in
    migration_data_quality_service.py) to get an honest "still pending"
    count, and separately counts what's already flagged via
    fetch_needs_review_ride_ids -- the same function the admin Rides page's
    "Needs Review" filter uses, so this panel and that filter always agree.
    """
    pending = build_data_quality_scan_plan().stats.get("rides_affected", 0)
    flagged = len(fetch_needs_review_ride_ids())
    if pending == 0 and flagged == 0:
        state, detail = "done", "No missing-driver/rider, placeholder-address, or $0-fare rows found"
    elif pending == 0:
        state, detail = "done", f"{flagged} row(s) flagged, 0 pending"
    elif flagged == 0:
        state, detail = "not_started", f"{pending} row(s) found needing review, not yet flagged"
    else:
        state, detail = "partial", f"{flagged} flagged, {pending} more pending"
    return ToolStatus(
        17, "data_quality_scan", "Migration Data Quality Scan", state, detail, "/dashboard/bulk-operations"
    )


def _tool_18_driver_repair() -> ToolStatus:
    """Same reasoning as #17: no fixed eligible population, so this re-runs
    the live, read-only build_driver_repair_plan (migration_driver_repair_
    service.py) to report how many missing_driver rides are re-matchable
    against the CURRENT drivers table right now, rather than a stale count.
    Driver-side only -- see that module's docstring for why there is no
    rider-side equivalent."""
    plan = build_driver_repair_plan()
    stats = plan.stats
    repairable = stats.get("repairable", 0)
    still_unmatched = stats.get("still_unmatched", 0)
    ambiguous = stats.get("ambiguous_old_driver_id_skipped", 0)
    if repairable == 0 and still_unmatched == 0 and ambiguous == 0:
        state, detail = "done", "No old_driver_id-bearing missing_driver rows found"
    elif repairable > 0:
        detail = f"{repairable} ride(s) repairable now"
        if still_unmatched:
            detail += f", {still_unmatched} still unmatched"
        if ambiguous:
            detail += f", {ambiguous} ambiguous (skipped)"
        state = "not_started"
    else:
        state, detail = "manual_check_required", f"{still_unmatched} still unmatched, {ambiguous} ambiguous"
    return ToolStatus(18, "driver_repair", "Driver-Repair Pass", state, detail, "/dashboard/bulk-operations")


def get_migration_status() -> MigrationStatusReport:
    """Read-only. Runs every count query above and returns all 18 tool
    statuses in the verified dependency order. No writes, no side effects."""
    eligible_ids = _eligible_driver_ids()
    imported_rides = _imported_ride_ids()
    route_snapshots, route_backfill = _tool_14_and_15_route_tools(imported_rides)

    return MigrationStatusReport(
        tools=[
            _tool_1_bulk_driver_import(),
            _tool_2_legacy_driver_import(eligible_ids),
            _tool_3_bulk_rider_import(),
            _tool_4_sin_dob_backfill(eligible_ids),
            _tool_5_vehicle_history_backfill(eligible_ids),
            _tool_6_orphaned_accounts(),
            _tool_7_manual(),
            _tool_8_stripe_mapping(),
            _tool_9_tax_id_import(eligible_ids),
            _tool_10_saved_address_backfill(),
            _tool_11_status(),
            _tool_12_manual(),
            _tool_13_wallet_import(),
            route_snapshots,
            route_backfill,
            _tool_16_pre_launch_flag(),
            _tool_17_data_quality_scan(),
            _tool_18_driver_repair(),
        ]
    )
