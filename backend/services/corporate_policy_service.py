"""Policy evaluation for corporate ride bookings (v1 stub).

Pure function — no I/O. Safe to call from tests without any DB fixtures.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List

try:
    import pytz as _pytz
except ImportError:
    _pytz = None  # type: ignore

logger = logging.getLogger(__name__)

# Day-of-week abbreviations → Python weekday index (Mon=0 … Sun=6)
_DOW_MAP: Dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def evaluate_policy(policy: dict, ride_context: dict) -> dict:
    """Evaluate v1 corporate policy rules against a ride context.

    Returns ``{"pass": bool, "failed_rules": list[str], "bypassed_rules": list[str]}``.

    Rules evaluated:
    - max_fare_per_ride  — estimated_fare (or final_fare) vs policy cap
    - time_window        — pickup time within allowed day+time windows (company tz)
    - allowed_payment_source — allowance_only blocks rides with empty allowance
    - geofence           — STUB: always passes; PostGIS not available in unit tests

    If ``ride_context["policy_override"]`` is True all rules are short-circuited
    but would-be failures are captured in ``bypassed_rules`` for audit.
    """
    if not policy:
        return {"pass": True, "failed_rules": [], "bypassed_rules": []}

    failed: List[str] = []

    # ── Rule 1: max_fare_per_ride ─────────────────────────────────────────────
    max_fare = policy.get("max_fare_per_ride")
    if max_fare is not None:
        fare = ride_context.get("estimated_fare") or ride_context.get("final_fare")
        if fare is not None and float(fare) > float(max_fare):
            failed.append("max_fare_per_ride")

    # ── Rule 2: time_window ───────────────────────────────────────────────────
    windows = policy.get("allowed_time_windows")
    if windows:
        pickup_raw = ride_context.get("pickup_time")
        if pickup_raw:
            try:
                if isinstance(pickup_raw, str):
                    pickup_dt = datetime.fromisoformat(pickup_raw)
                else:
                    pickup_dt = pickup_raw

                tz_name = policy.get("timezone") or "America/Toronto"
                if _pytz is not None:
                    tz = _pytz.timezone(tz_name)
                    if pickup_dt.tzinfo is None:
                        # Naive datetime — caller provides local company time already
                        pickup_dt = tz.localize(pickup_dt)
                    else:
                        pickup_dt = pickup_dt.astimezone(tz)

                day_idx = pickup_dt.weekday()
                hhmm = pickup_dt.strftime("%H:%M")

                in_window = any(
                    _DOW_MAP.get(w.get("day", "").lower()) == day_idx
                    and w.get("start", "00:00") <= hhmm <= w.get("end", "23:59")
                    for w in windows
                )
                if not in_window:
                    failed.append("time_window")
            except Exception as exc:
                # Treat parse failures as a pass — never block a ride on a
                # clock-parsing bug; the audit log will surface the issue.
                logger.warning("[policy] time_window parse error (treating as pass): %s", exc)

    # ── Rule 3: allowed_payment_source ───────────────────────────────────────
    allowed_source = policy.get("allowed_payment_source", "both")
    if allowed_source == "allowance_only":
        allowance = ride_context.get("allowance") or {}
        if allowance.get("type") != "unlimited":
            amt = float(allowance.get("amount") or 0)
            used = float(allowance.get("used") or 0)
            remaining = amt - max(used, 0.0)
            if remaining <= 0:
                failed.append("allowed_payment_source")

    # ── Rule 4: geofence ─────────────────────────────────────────────────────
    # TODO: implement PostGIS ST_Contains check against policy["allowed_geofence"]
    # once spatial queries are available in the test environment.  Both pickup
    # AND dropoff must lie inside at least one polygon in the GeoJSON
    # FeatureCollection.  For now we always pass and warn so operators know
    # the check is not active.
    if policy.get("allowed_geofence"):
        logger.warning(
            "[policy] geofence rule skipped — PostGIS stub (not yet implemented)"
        )

    # ── Override: bypass all rules but still surface would-be failures ────────
    if ride_context.get("policy_override"):
        return {"pass": True, "failed_rules": [], "bypassed_rules": failed}

    return {"pass": len(failed) == 0, "failed_rules": failed, "bypassed_rules": []}
