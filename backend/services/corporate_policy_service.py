"""Corporate policy evaluation.

evaluate_policy() checks company policy rules against a ride context.
Runs at booking (phase='booking') and optionally at completion
(phase='completion'). Per spec §6: same function, same rules, both phases.
"""
from __future__ import annotations

import zoneinfo
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Literal, Optional

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore


def _d(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_time_minutes(t: str) -> int:
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1]) if len(parts) >= 2 else 0


@dataclass
class PolicyResult:
    allowed: bool
    payment_source: Literal["company_allowance", "rider_card", "rider_wallet"]
    reason: str
    failed_rules: List[str] = field(default_factory=list)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)


async def evaluate_policy(
    *,
    corporate_account_id: str,
    rider_id: str,
    estimated_fare: Decimal,
    ride_type: str,
    pickup_time: datetime,
    member_id: Optional[str] = None,
) -> PolicyResult:
    """Evaluate corporate policy for a ride booking or completion.

    Returns PolicyResult with allowed=True if the ride is permitted.
    payment_source indicates how the fare should be billed.
    """
    # 1. Fetch active policy
    policies = await db_supabase.get_rows(
        "corporate_policies",
        {"company_id": corporate_account_id, "active": True},
        limit=1,
    )
    if not policies:
        return PolicyResult(
            allowed=True,
            payment_source="company_allowance",
            reason="No active policy; allowance billing applies.",
        )
    policy = policies[0]

    # 2. Resolve member row
    if not member_id:
        members = await db_supabase.get_rows(
            "corporate_members",
            {"company_id": corporate_account_id, "user_id": rider_id, "status": "active"},
            limit=1,
        )
        if not members:
            return PolicyResult(
                allowed=False,
                payment_source="rider_card",
                reason="Rider is not an active member of this company.",
                failed_rules=["membership"],
                policy_snapshot=policy,
            )
        member = members[0]
        member_id = member["id"]
    else:
        rows = await db_supabase.get_rows("corporate_members", {"id": member_id}, limit=1)
        member = rows[0] if rows else {}

    # 3. Evaluate rules (policy_override short-circuits all checks per spec §6)
    failed_rules: List[str] = []
    bypassed_rules: List[str] = []
    policy_override = member.get("policy_override", False)

    if not policy_override:
        # Rule 1: max fare per ride
        max_fare = policy.get("max_fare_per_ride")
        if max_fare is not None and estimated_fare > _d(max_fare):
            failed_rules.append("max_fare_per_ride")

        # Rule 2: time window checked in company timezone
        time_windows = policy.get("allowed_time_windows")
        if time_windows:
            tz_name = policy.get("timezone") or "America/Toronto"
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except (zoneinfo.ZoneInfoNotFoundError, KeyError):
                tz = zoneinfo.ZoneInfo("America/Toronto")

            local_dt = pickup_time.astimezone(tz)
            day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            pickup_day = day_map[local_dt.weekday()]
            pickup_min = local_dt.hour * 60 + local_dt.minute
            in_window = any(
                w.get("day") == pickup_day
                and _parse_time_minutes(w.get("start", "00:00")) <= pickup_min
                <= _parse_time_minutes(w.get("end", "23:59"))
                for w in time_windows
            )
            if not in_window:
                failed_rules.append("time_window")
    else:
        # Record what would have failed so the audit log is complete
        max_fare = policy.get("max_fare_per_ride")
        if max_fare is not None and estimated_fare > _d(max_fare):
            bypassed_rules.append("max_fare_per_ride")

    if failed_rules:
        return PolicyResult(
            allowed=False,
            payment_source="rider_card",
            reason=f"Policy rejected: {', '.join(failed_rules)}",
            failed_rules=failed_rules,
            policy_snapshot=policy,
        )

    # 4. Check allowance balance
    allowances = await db_supabase.get_rows(
        "corporate_member_allowances",
        {"member_id": member_id, "status": "active"},
        limit=1,
    )
    allowed_source = policy.get("allowed_payment_source", "both")

    if not allowances:
        if allowed_source == "allowance_only":
            return PolicyResult(
                allowed=False,
                payment_source="rider_card",
                reason="No allowance assigned and policy requires allowance_only.",
                failed_rules=["allowance_empty"],
                policy_snapshot=policy,
            )
        return PolicyResult(
            allowed=True,
            payment_source="rider_card",
            reason="No allowance; billing to rider card.",
            policy_snapshot=policy,
        )

    allowance = allowances[0]
    if allowance.get("type") == "unlimited":
        remaining = Decimal("999999.99")
    else:
        remaining = _d(allowance.get("amount") or 0) - _d(allowance.get("used") or 0)

    if remaining >= estimated_fare:
        return PolicyResult(
            allowed=True,
            payment_source="company_allowance",
            reason="Allowance covers fare.",
            policy_snapshot=policy,
        )

    if allowed_source in ("both", "master_only"):
        return PolicyResult(
            allowed=True,
            payment_source="rider_card",
            reason="Allowance exhausted; billing to rider card per policy.",
            policy_snapshot=policy,
        )

    return PolicyResult(
        allowed=False,
        payment_source="rider_card",
        reason="Allowance exhausted and policy requires allowance_only.",
        failed_rules=["allowance_insufficient"],
        policy_snapshot=policy,
    )
