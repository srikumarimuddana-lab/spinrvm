"""On-demand trip-record export for an SGI/regulator subpoena request.

Fulfils the obligation in ``.claude/context/regulatory-sk.md`` ("On-demand
trip record production within 14 days of subpoena or regulator request").
See ``docs/compliance/sgi-quarterly.md`` for the full gap write-up this
script closes (§1 row 3, §4, §6's proposed shape) and for what is
deliberately still NOT covered.

Scope, deliberately narrow
--------------------------
This covers ONLY the on-demand, already-well-specified obligation: produce
trip records (distance + insurance-period linkage) for a date range and/or a
specific driver/ride, on request. It does NOT attempt the periodic quarterly
ride-volume/incident report or the annual driver-roster export — those two
have open questions in ``docs/compliance/sgi-quarterly.md`` §5 (submission
format/channel, exact aggregation grain) that are product/legal decisions,
not engineering ones, and are explicitly out of scope here.

PII boundary (deliberately strict, no override flag)
-----------------------------------------------------
Output never includes: rider identity of any kind, raw pickup/dropoff
addresses or coordinates, driver name/phone/email — only ``driver_id`` and
``ride_id`` (internal identifiers) plus distance/time/fare/insurance-period
data, mirroring the SQL sketch in ``docs/compliance/sgi-quarterly.md`` §6.
If a specific subpoena genuinely requires more (e.g. rider identity, exact
address), that is a manual, legally-reviewed extraction — deliberately not a
CLI flag on this script.

Read-only and replay-safe: only ``get_rows`` reads ``driver_insurance_periods``
+ embedded ``rides``; the only write is one ``compliance_export_events`` audit
row per invocation (that a row is written per run, not deduped, is
intentional — each run is itself the audit-worthy event: "who exported what
range, when").

Usage:
    python scripts/compliance_export.py --start 2026-01-01 --end 2026-04-01 \\
        --requested-by <admin_user_id> --reference "SGI-2026-0042" \\
        --out /tmp/trip_export.csv

    python scripts/compliance_export.py --start 2026-01-01 --end 2026-04-01 \\
        --driver-id <driver_id> --requested-by <admin_user_id> --format json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from backend import db_supabase as db
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    backend_dir = str(ROOT_DIR / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import db_supabase as db  # type: ignore

_PAGE = 1000
_CENT = Decimal("0.01")

# Embedded-select (PostgREST foreign-table syntax) joining the one FK from
# driver_insurance_periods.ride_id -> rides.id. Only ride-linked periods (2/3)
# are ever requested (see _scan), so the embed is never empty for a matched row.
_COLUMNS = (
    "id,driver_id,period,started_at,ended_at,ride_id,"
    "rides(id,status,created_at,planned_distance_km,actual_distance_km,"
    "phase_distances,total_fare)"
)

FIELDNAMES = [
    "ride_id",
    "driver_id",
    "insurance_period",
    "period_started_at",
    "period_ended_at",
    "ride_status",
    "ride_created_at",
    "planned_distance_km",
    "actual_distance_km",
    "phase_distances",
    "total_fare_cad",
]


def _money(v: Any) -> str:
    return str(Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP))


def redact_row(period_row: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one driver_insurance_periods(+embedded rides) row into the
    redacted export record. Pure function, no DB access — see the module
    docstring for the PII boundary this enforces.
    """
    ride = period_row.get("rides") or {}
    if isinstance(ride, list):  # some postgrest-py versions embed as a list
        ride = ride[0] if ride else {}
    return {
        "ride_id": period_row.get("ride_id"),
        "driver_id": period_row.get("driver_id"),
        "insurance_period": period_row.get("period"),
        "period_started_at": period_row.get("started_at"),
        "period_ended_at": period_row.get("ended_at"),
        "ride_status": ride.get("status"),
        "ride_created_at": ride.get("created_at"),
        "planned_distance_km": ride.get("planned_distance_km"),
        "actual_distance_km": ride.get("actual_distance_km"),
        "phase_distances": json.dumps(ride.get("phase_distances") or {}, sort_keys=True),
        "total_fare_cad": _money(ride.get("total_fare")),
    }


async def _scan(
    start: str,
    end: str,
    driver_id: Optional[str],
    ride_id: Optional[str],
) -> List[Dict[str, Any]]:
    """READ-ONLY paginated scan of driver_insurance_periods for periods 2/3
    (the only periods that carry a ride_id — see CLAUDE.md's insurance-period
    table), in [start, end), joined to rides via embedded select."""
    filters: Dict[str, Any] = {
        "$and": [
            {"started_at": {"$gte": start}},
            {"started_at": {"$lt": end}},
        ],
        "period": {"$in": [2, 3]},
    }
    if driver_id:
        filters["driver_id"] = driver_id
    if ride_id:
        filters["ride_id"] = ride_id

    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            await db.get_rows(
                "driver_insurance_periods",
                filters,
                columns=_COLUMNS,
                order="started_at",
                limit=_PAGE,
                offset=offset,
            )
            or []
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


def _render(records: List[Dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(records, indent=2, sort_keys=True) + "\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    writer.writeheader()
    for r in records:
        writer.writerow(r)
    return buf.getvalue()


async def run_export(
    start: str,
    end: str,
    requested_by: str,
    driver_id: Optional[str] = None,
    ride_id: Optional[str] = None,
    reference: Optional[str] = None,
    fmt: str = "csv",
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the export end to end: scan, redact, write output, write the
    compliance_export_events audit row. Returns a summary dict."""
    t0 = datetime.now(timezone.utc)
    period_rows = await _scan(start, end, driver_id, ride_id)
    records = [redact_row(r) for r in period_rows]
    rendered = _render(records, fmt)

    if out_path:
        with open(out_path, "w", newline="") as f:
            f.write(rendered)
    else:
        sys.stdout.write(rendered)

    elapsed_s = (datetime.now(timezone.utc) - t0).total_seconds()

    await db.insert_one(
        "compliance_export_events",
        {
            "admin_user_id": requested_by,
            "report_type": "trip_record_subpoena_export",
            "params": {
                "start": start,
                "end": end,
                "driver_id": driver_id,
                "ride_id": ride_id,
                "reference": reference,
            },
            "row_count": len(records),
        },
    )

    return {
        "row_count": len(records),
        "elapsed_seconds": elapsed_s,
        "output": out_path or "stdout",
    }


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="On-demand trip-record export for an SGI/regulator subpoena request.",
    )
    p.add_argument("--start", required=True, help="ISO date/datetime, inclusive (e.g. 2026-01-01)")
    p.add_argument("--end", required=True, help="ISO date/datetime, exclusive (e.g. 2026-04-01)")
    p.add_argument(
        "--requested-by",
        required=True,
        help="Admin user id approving/running this export — written to compliance_export_events for the audit trail",
    )
    p.add_argument("--driver-id", default=None, help="Scope to a single driver")
    p.add_argument("--ride-id", default=None, help="Scope to a single ride")
    p.add_argument(
        "--reference",
        default=None,
        help="Free-text subpoena/case reference number, stored in the audit params (not in output rows)",
    )
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--out", default=None, help="Output file path; defaults to stdout")
    return p.parse_args(argv)


async def _main(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    summary = await run_export(
        start=args.start,
        end=args.end,
        requested_by=args.requested_by,
        driver_id=args.driver_id,
        ride_id=args.ride_id,
        reference=args.reference,
        fmt=args.format,
        out_path=args.out,
    )
    print(
        f"\n--- {summary['row_count']} row(s) in {summary['elapsed_seconds']:.1f}s -> {summary['output']} ---",
        file=sys.stderr,
    )


if __name__ == "__main__":
    asyncio.run(_main())
