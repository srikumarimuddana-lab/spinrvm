"""Rider loyalty / rewards program — earn points per ride, unlock tiers.

Points earned: 1 point per $1 spent on rides. Tier thresholds:
  Bronze:   0-499 lifetime points
  Silver:   500-1499
  Gold:     1500-4999
  Platinum: 5000+

Tiers give bonus multipliers. Point redemption for wallet credit is currently
disabled (see redeem_points) — the old flow could double-credit the wallet under
concurrent requests. Earning and tier display are unaffected.
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ..db import db
    from ..dependencies import get_current_user
    from ..features import send_push_notification
    from ..utils.error_handling import DuplicateRecordError
except ImportError:
    from db import db
    from dependencies import get_current_user
    from features import send_push_notification
    from utils.error_handling import DuplicateRecordError

# db is the db_supabase module (re-exported by backend/db.py shim); .rpc is the
# Supabase RPC caller used for atomic wallet credits during point redemption.
logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/loyalty", tags=["Loyalty"])

TIER_THRESHOLDS = [
    (5000, "platinum"),
    (1500, "gold"),
    (500, "silver"),
    (0, "bronze"),
]

TIER_MULTIPLIERS = {
    "bronze": 1.0,
    "silver": 1.25,
    "gold": 1.5,
    "platinum": 2.0,
}

POINTS_PER_DOLLAR = 1
REDEMPTION_RATE = 100  # 100 points = $1 wallet credit


def _calculate_tier(lifetime_points: int) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if lifetime_points >= threshold:
            return tier
    return "bronze"


async def _get_or_create_account(user_id: str) -> dict:
    account = await db.find_one("loyalty_accounts", {"user_id": user_id})
    if account:
        return account
    account = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "points": 0,
        "lifetime_points": 0,
        "tier": "bronze",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.insert_one("loyalty_accounts", account)
    return account


@api_router.get("")
async def get_loyalty_status(current_user: dict = Depends(get_current_user)):
    """Get rider's loyalty account status, tier, and points."""
    account = await _get_or_create_account(current_user["id"])
    tier = account.get("tier", "bronze")
    lifetime = account.get("lifetime_points", 0)
    next_tier = None
    for threshold, t in TIER_THRESHOLDS:
        if threshold > lifetime:
            next_tier = {"tier": t, "points_needed": threshold - lifetime}

    return {
        "points": account.get("points", 0),
        "lifetime_points": lifetime,
        "tier": tier,
        "multiplier": TIER_MULTIPLIERS.get(tier, 1.0),
        "next_tier": next_tier,
        "redemption_rate": REDEMPTION_RATE,
    }


@api_router.get("/history")
async def get_loyalty_history(
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Get loyalty points transaction history."""
    try:
        txns = await db.get_rows(
            "loyalty_transactions",
            {"user_id": current_user["id"]},
            limit=limit,
            order="created_at",
        )
    except Exception as e:
        # Returning [] on a DB error tells the rider they have no history —
        # surface 503 so the client retries instead.
        logger.error(f"Failed to fetch loyalty history: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Loyalty history temporarily unavailable") from e
    return txns


@api_router.post("/earn")
async def earn_points_for_ride(ride_id: str = Query(...), current_user: dict = Depends(get_current_user)):
    """Award loyalty points for a completed ride. Called after ride completion."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Ride not completed")

    fare = Decimal(str(ride.get("total_fare") or 0))
    account = await _get_or_create_account(current_user["id"])
    tier = account.get("tier", "bronze")
    multiplier = TIER_MULTIPLIERS.get(tier, 1.0)
    base_points = int(fare * POINTS_PER_DOLLAR)
    bonus_points = int(base_points * (multiplier - 1.0))
    total_points = base_points + bonus_points

    if total_points <= 0:
        return {"points": 0}

    # INSERT the transaction first. The unique index (user_id, reference_id) WHERE
    # type='ride_earned' makes this idempotent — a concurrent request that already
    # inserted will cause a DuplicateRecordError here, and we return a no-op instead
    # of double-awarding.
    try:
        await db.insert_one(
            "loyalty_transactions",
            {
                "id": str(uuid.uuid4()),
                "user_id": current_user["id"],
                "points": total_points,
                "type": "ride_earned",
                "reference_id": ride_id,
                "description": f"Earned {base_points} pts + {bonus_points} bonus ({tier} {multiplier}x)",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except DuplicateRecordError:
        return {"already_awarded": True, "points": 0}

    # INSERT succeeded — safe to update the account balance
    new_balance = account.get("points", 0) + total_points
    new_lifetime = account.get("lifetime_points", 0) + total_points
    new_tier = _calculate_tier(new_lifetime)

    await db.update_one(
        "loyalty_accounts",
        {"id": account["id"]},
        {
            "$set": {
                "points": new_balance,
                "lifetime_points": new_lifetime,
                "tier": new_tier,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    tier_upgraded = new_tier != tier

    if tier_upgraded:
        # N15/R34 (ACTION_ITEMS.md): tier changes previously had zero
        # notification call — a rider only found out by opening the loyalty
        # screen. Best-effort/informational (priority="normal", respects the
        # push opt-out); the account row above already committed, so a push
        # failure must never surface as a failed points-earn call — the
        # response the client is waiting on already reflects the new tier.
        try:
            await send_push_notification(
                current_user["id"],
                "You've reached a new tier!",
                f"Congratulations — you're now {new_tier.capitalize()} tier "
                f"with a {TIER_MULTIPLIERS.get(new_tier, 1.0)}x points multiplier.",
                data={"type": "loyalty_tier_upgraded", "tier": new_tier, "previous_tier": tier},
                target_app="rider",
            )
        except Exception as e:
            logger.warning(
                f"earn_points_for_ride: tier-upgrade push failed user_id={current_user['id']} tier={new_tier}: {e}",
                exc_info=True,
            )

    return {
        "points_earned": total_points,
        "base_points": base_points,
        "bonus_points": bonus_points,
        "new_balance": new_balance,
        "tier": new_tier,
        "tier_upgraded": tier_upgraded,
    }


@api_router.post("/redeem", deprecated=True)
async def redeem_points(current_user: dict = Depends(get_current_user)):
    """Loyalty point redemption is withdrawn.

    The previous implementation credited the wallet, then debited points with a
    non-atomic read-then-write (no conditional on the points balance). Two
    concurrent redemptions both passed the balance check and both credited the
    wallet — a real dollar loss plus a corrupt points ledger. Redemption stays
    disabled until it is reimplemented as a single atomic points debit (e.g. a
    ``UPDATE ... SET points = points - :n WHERE points >= :n RETURNING`` RPC that
    only credits the wallet when a row is claimed). Points earning and the
    balance/tier display are unaffected.
    """
    raise HTTPException(status_code=410, detail="Loyalty point redemption is currently unavailable.")
