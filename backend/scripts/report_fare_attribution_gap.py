"""READ-ONLY: quantify the historical minimum-fare attribution gap.

Before the min-fare uplift fix, ``calculate_fare`` booked ``driver_earnings``
from the un-clamped subtotal, so on a minimum-fare ride the ``minimum − subtotal``
uplift the rider paid was attributed to nobody: ``total_fare`` did not equal
``driver_earnings + admin_earnings``. This script scans completed rides and sums
the unallocated dollars — the input to the back-pay / T4A-correction decision.

It NEVER writes: only ``get_rows`` reads. There is no --apply mode by design;
a backfill is a separate, deliberate step.

  python scripts/report_fare_attribution_gap.py           # console summary
  python scripts/report_fare_attribution_gap.py --json    # + machine-readable JSON

PIPEDA: only internal ids (ride_id, driver_id) and dollar sums are emitted —
no names, emails, phone numbers, or coordinates.
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List

import db_supabase as db

_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.01")
_PAGE = 1000


def _q2(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def compute_gap_report(rides: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the fare-attribution gap over a list of ride rows.

    A ride is counted when ``total_fare − ((driver_earnings − tip_amount) +
    admin_earnings) > 0.01`` (tips are added to driver_earnings post-settlement,
    so they're subtracted back out). Legacy rows missing both earnings columns
    are skipped. Returns totals plus per-calendar-year and per-driver breakdowns.
    """
    total_gap = Decimal("0")
    affected = 0
    by_year: Dict[str, Dict[str, Any]] = {}
    by_driver: Dict[str, Dict[str, Any]] = {}

    for ride in rides:
        driver = ride.get("driver_earnings")
        admin = ride.get("admin_earnings")
        if driver is None and admin is None:
            continue
        total = _q2(ride.get("total_fare"))
        attributed = _q2(driver) - _q2(ride.get("tip_amount")) + _q2(admin)
        gap = total - attributed
        if gap <= _TOLERANCE:
            continue

        affected += 1
        total_gap += gap

        year = str(ride.get("ride_completed_at") or "")[:4] or "unknown"
        yb = by_year.setdefault(year, {"rides": 0, "unallocated": Decimal("0")})
        yb["rides"] += 1
        yb["unallocated"] += gap

        driver_id = str(ride.get("driver_id") or "unknown")
        dbk = by_driver.setdefault(driver_id, {"rides": 0, "unallocated": Decimal("0")})
        dbk["rides"] += 1
        dbk["unallocated"] += gap

    def _finalize(groups: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        return {
            k: {"rides": v["rides"], "unallocated": float(_q2(v["unallocated"]))} for k, v in sorted(groups.items())
        }

    return {
        "affected_rides": affected,
        "total_unallocated": float(_q2(total_gap)),
        "by_year": _finalize(by_year),
        "by_driver": _finalize(by_driver),
    }


async def _scan_rides() -> List[Dict[str, Any]]:
    """READ-ONLY paginated scan of completed rides with the money columns."""
    columns = "id,driver_id,total_fare,driver_earnings,admin_earnings,tip_amount,ride_completed_at"
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            await db.get_rows(
                "rides",
                {"status": "completed"},
                columns=columns,
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


def _print_console(report: Dict[str, Any], scanned: int) -> None:
    print("=" * 60)
    print("Fare-attribution gap report (READ-ONLY)")
    print("=" * 60)
    print(f"Rides scanned (completed) : {scanned}")
    print(f"Affected rides            : {report['affected_rides']}")
    print(f"Total unallocated ($)     : {report['total_unallocated']:.2f}")
    print("-" * 60)
    print("By year:")
    for year, v in report["by_year"].items():
        print(f"  {year}: {v['rides']:>6} rides   ${v['unallocated']:.2f}")
    print("-" * 60)
    print(f"By driver: {len(report['by_driver'])} driver(s) affected")
    for driver_id, v in report["by_driver"].items():
        print(f"  {driver_id}: {v['rides']:>4} rides   ${v['unallocated']:.2f}")
    print("=" * 60)


async def main(as_json: bool) -> None:
    rides = await _scan_rides()
    report = compute_gap_report(rides)
    _print_console(report, scanned=len(rides))
    if as_json:
        print("\n--- JSON ---")
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main(as_json="--json" in sys.argv))
