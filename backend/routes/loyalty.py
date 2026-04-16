"""Rider loyalty / rewards program — earn points per ride, unlock tiers, redeem rewards.

Points earned: 1 point per $1 spent on rides. Tier thresholds:
  Bronze:   0-499 lifetime points
  Silver:   500-1499
  Gold:     1500-4999
  Platinum: 5000+

Tiers give bonus multipliers and can be redeemed for wallet credits.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_current_user
except ImportError:
    from db import db

    from dependencies import get_current_user

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
    account = await db.loyalty_accounts.find_one({"user_id": user_id})
    if account:
        return account
    account = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "points": 0,
        "lifetime_points": 0,
        "tier": "bronze",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.loyalty_accounts.insert_one(account)
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
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Failed to fetch loyalty history: {e}")
        txns = []
    return txns


@api_router.post("/earn")
async def earn_points_for_ride(ride_id: str = Query(...), current_user: dict = Depends(get_current_user)):
    """Award loyalty points for a completed ride. Called after ride completion."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Ride not completed")

    # Check if already awarded
    existing = await db.loyalty_transactions.find_one(
        {"user_id": current_user["id"], "reference_id": ride_id, "type": "ride_earned"}
    )
    if existing:
        return {"already_awarded": True, "points": 0}

    fare = float(ride.get("total_fare", 0))
    account = await _get_or_create_account(current_user["id"])
    tier = account.get("tier", "bronze")
    multiplier = TIER_MULTIPLIERS.get(tier, 1.0)
    base_points = int(fare * POINTS_PER_DOLLAR)
    bonus_points = int(base_points * (multiplier - 1.0))
    total_points = base_points + bonus_points

    if total_points <= 0:
        return {"points": 0}

    new_balance = account.get("points", 0) + total_points
    new_lifetime = account.get("lifetime_points", 0) + total_points
    new_tier = _calculate_tier(new_lifetime)

    await db.loyalty_accounts.update_one(
        {"id": account["id"]},
        {
            "$set": {
                "points": new_balance,
                "lifetime_points": new_lifetime,
                "tier": new_tier,
                "updated_at": datetime.utcnow().isoformat(),
            }
        },
    )

    # Record transaction
    await db.loyalty_transactions.insert_one(
        {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "points": total_points,
            "type": "ride_earned",
            "reference_id": ride_id,
            "description": f"Earned {base_points} pts + {bonus_points} bonus ({tier} {multiplier}x)",
            "created_at": datetime.utcnow().isoformat(),
        }
    )

    tier_upgraded = new_tier != tier

    return {
        "points_earned": total_points,
        "base_points": base_points,
        "bonus_points": bonus_points,
        "new_balance": new_balance,
        "tier": new_tier,
        "tier_upgraded": tier_upgraded,
    }


class RedeemRequest(BaseModel):
    points: int = Field(..., gt=0)


@api_router.post("/redeem")
async def redeem_points(req: RedeemRequest, current_user: dict = Depends(get_current_user)):
    """Redeem loyalty points for wallet credit."""
    if req.points < REDEMPTION_RATE:
        raise HTTPException(status_code=400, detail=f"Minimum redemption is {REDEMPTION_RATE} points")

    account = await _get_or_create_account(current_user["id"])
    if account.get("points", 0) < req.points:
        raise HTTPException(status_code=400, detail="Insufficient points")

    credit_amount = round(req.points / REDEMPTION_RATE, 2)
    new_balance = account.get("points", 0) - req.points

    await db.loyalty_accounts.update_one(
        {"id": account["id"]},
        {"$set": {"points": new_balance, "updated_at": datetime.utcnow().isoformat()}},
    )

    # Credit to wallet
    try:
        from .wallet import _d, _record_transaction, get_or_create_wallet

        wallet = await get_or_create_wallet(current_user["id"])
        old_wb = _d(wallet.get("balance", 0))
        new_wb = old_wb + _d(credit_amount)
        await db.wallets.update_one(
            {"id": wallet["id"]},
            {"$set": {"balance": float(new_wb), "updated_at": datetime.utcnow().isoformat()}},
        )
        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=current_user["id"],
            txn_type="bonus",
            amount=credit_amount,
            balance_after=float(new_wb),
            description=f"Loyalty redemption: {req.points} pts → ${credit_amount:.2f}",
        )
    except Exception as e:
        logger.error(f"Loyalty redeem wallet credit failed: {e}")

    await db.loyalty_transactions.insert_one(
        {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "points": -req.points,
            "type": "redeemed",
            "description": f"Redeemed {req.points} pts for ${credit_amount:.2f} wallet credit",
            "created_at": datetime.utcnow().isoformat(),
        }
    )

    return {
        "redeemed_points": req.points,
        "credit_amount": credit_amount,
        "remaining_points": new_balance,
    }
