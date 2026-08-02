"""Policy evaluation for corporate ride bookings (v1 stub).

Public API:
  evaluate_policy(policy, ride_context) -> dict
    Pure sync function — no I/O. Safe to call from tests without any DB fixtures.

  evaluate_policy_for_ride(corporate_account_id, rider_id, estimated_fare, ...) -> PolicyResult
    Async wrapper: fetches policy + allowance from DB, builds context, delegates to
    evaluate_policy. Use this from route handlers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

try:
    import pytz as _pytz
except ImportError:
    _pytz = None  # type: ignore

logger = logging.getLogger(__name__)


class PolicyResult:
    """Structured result from evaluate_policy / evaluate_policy_for_ride.

    Attributes:
        passed         True when all rules pass (or policy_override active).
        failed_rules   Rules that would have failed (empty on full pass).
        bypassed_rules Rules skipped due to policy_override (for audit log).
        policy         The raw policy dict that was evaluated.
        allowance      The member's allowance row fetched during evaluation.
                       Empty dict when called via the sync evaluate_policy path.
    """

    __slots__ = ("passed", "failed_rules", "bypassed_rules", "skipped_rules", "policy", "allowance")

    def __init__(
        self,
        passed: bool,
        failed_rules: List[str],
        bypassed_rules: List[str],
        skipped_rules: Optional[List[str]] = None,
        policy: Optional[dict] = None,
        allowance: Optional[dict] = None,
    ) -> None:
        self.passed = passed
        self.failed_rules = failed_rules
        self.bypassed_rules = bypassed_rules
        self.skipped_rules = skipped_rules or []
        self.policy = policy or {}
        self.allowance = allowance or {}

    @classmethod
    def _from_dict(
        cls,
        result: dict,
        policy: Optional[dict] = None,
        allowance: Optional[dict] = None,
    ) -> "PolicyResult":
        return cls(
            passed=result.get("pass", True),
            failed_rules=result.get("failed_rules", []),
            bypassed_rules=result.get("bypassed_rules", []),
            skipped_rules=result.get("skipped_rules", []),
            policy=policy,
            allowance=allowance,
        )

    def to_dict(self) -> dict:
        return {
            "pass": self.passed,
            "failed_rules": self.failed_rules,
            "bypassed_rules": self.bypassed_rules,
            "skipped_rules": self.skipped_rules,
        }


async def require_company_bookable(company_id: str, settings: Optional[dict] = None) -> None:
    """Raise a 403 if ``company_id`` can't be booked against right now.

    Single source of truth for "is this company active enough to book,"
    shared by the employee self-book path (``routes/rides/booking.py``) and
    the company-portal guest-booking path
    (``routes/corporate_company_bookings.py``) — scheduled-rides gap review,
    Finding #20. Previously these were two separately-implemented checks
    with two different definitions of "inactive": self-book only blocked
    ``suspended``/``closed`` and respected the
    ``corporate_inactive_company_blocks_booking`` kill switch; guest-booking
    blocked anything not ``"active"`` (including ``pending_verification``)
    and ignored that switch entirely. This version uses the broader status
    definition — not ``"active"`` means not bookable, so a
    not-yet-verified company can't be self-booked against either, matching
    the guest-booking path's existing (and more defensible) stance — for
    both call sites, while keeping the kill switch as a shared,
    admin-controlled escape hatch either path can be paused with.

    ``settings`` may be pre-fetched by the caller to avoid a second
    ``app_settings`` round-trip within the same request; fetched fresh if
    omitted.
    """
    from fastapi import HTTPException

    if settings is None:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings() or {}

    if not settings.get("corporate_inactive_company_blocks_booking", True):
        return

    try:
        from .. import db_supabase
    except ImportError:
        import db_supabase  # type: ignore

    company = await db_supabase.get_corporate_account_by_id(company_id) or {}
    if (company.get("status") or "").lower() != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "company_not_active",
                "message": (
                    "Your company account isn't active right now. Contact your company admin, "
                    "or use a personal payment method."
                ),
                "failed_rules": ["company_inactive"],
            },
        )


async def evaluate_policy_for_ride(
    corporate_account_id: str,
    rider_id: str,
    estimated_fare: Decimal,
    ride_type: str,  # "standard" | "xl" | "premium"
    pickup_time: datetime,  # UTC
    *,
    policy_override: bool = False,
) -> PolicyResult:
    """Fetch policy + member allowance from the DB and evaluate all rules.

    Returns a PolicyResult. Callers should inspect `.passed` and `.failed_rules`.
    Never raises — a DB failure returns a permissive PolicyResult with a warning
    so a transient outage cannot silently block every work ride.
    """
    try:
        from ..db_supabase import (  # type: ignore
            get_corporate_policy,
            get_member_allowance,
            list_active_memberships_for_user,
        )
    except ImportError:
        from db_supabase import (  # type: ignore
            get_corporate_policy,
            get_member_allowance,
            list_active_memberships_for_user,
        )

    policy: dict = {}
    allowance: dict = {}

    try:
        policy = await get_corporate_policy(corporate_account_id) or {}
    except Exception as exc:
        logger.error(
            "[policy] could not fetch policy for company=%s: %s — treating as no policy (fail-open)",
            corporate_account_id,
            exc,
            exc_info=True,
        )

    try:
        memberships = await list_active_memberships_for_user(rider_id)
        member = next((m for m in memberships if m.get("company_id") == corporate_account_id), None)
        if member:
            allowance = await get_member_allowance(member["id"]) or {}
            if policy_override is False:
                policy_override = bool(member.get("policy_override"))
    except Exception as exc:
        logger.error(
            "[policy] could not fetch allowance for rider=%s company=%s: %s — policy enforcement degraded",
            rider_id,
            corporate_account_id,
            exc,
            exc_info=True,
        )

    ride_context: dict = {
        "estimated_fare": Decimal(str(estimated_fare)),
        "ride_type": ride_type,
        "pickup_time": pickup_time,
        "allowance": allowance,
        "policy_override": policy_override,
    }

    raw = evaluate_policy(policy, ride_context)
    return PolicyResult._from_dict(raw, policy=policy, allowance=allowance)


# Day-of-week abbreviations → Python weekday index (Mon=0 … Sun=6)
_DOW_MAP: Dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def evaluate_policy(policy: dict, ride_context: dict) -> dict:
    """Evaluate v1 corporate policy rules against a ride context.

    Returns ``{"pass": bool, "failed_rules": list[str], "bypassed_rules": list[str],
               "skipped_rules": list[str]}``.

    Rules evaluated:
    - max_fare_per_ride  — estimated_fare (or final_fare) vs policy cap
    - time_window        — pickup time within allowed day+time windows (company tz)
    - allowed_payment_source — allowance_only blocks rides with empty allowance
    - geofence           — DEFERRED: always passes; awaiting PostGIS infrastructure

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
        if fare is not None and Decimal(str(fare)) > Decimal(str(max_fare)):
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
            amt = Decimal(str(allowance.get("amount") or 0))
            used = Decimal(str(allowance.get("used") or 0))
            remaining = amt - max(used, Decimal("0"))
            if remaining <= 0:
                failed.append("allowed_payment_source")

    # ── Rule 4: geofence ─────────────────────────────────────────────────────
    # DEFERRED: PostGIS ST_Contains check against policy["allowed_geofence"].
    # Both pickup AND dropoff must lie inside at least one polygon in the GeoJSON
    # FeatureCollection.  Unblocked when supabase_extensions.postgis is available
    # in the deployment environment.  Until then we warn + pass so a configured
    # geofence is visible in logs and in the PolicyResult.skipped_rules field.
    skipped: List[str] = []
    if policy.get("allowed_geofence"):
        skipped.append("geofence")
        logger.warning(
            "[policy] geofence check deferred (PostGIS unavailable) — ride_id=%s company=%s",
            ride_context.get("ride_id", "unknown"),
            ride_context.get("corporate_account_id", "unknown"),
        )

    # ── Override: bypass all rules but still surface would-be failures ────────
    if ride_context.get("policy_override"):
        return {"pass": True, "failed_rules": [], "bypassed_rules": failed, "skipped_rules": skipped}

    return {"pass": len(failed) == 0, "failed_rules": failed, "bypassed_rules": [], "skipped_rules": skipped}
