"""Quest / Bonus Challenges — gamified driver incentive system.

Admins create time-limited quests with targets (ride count, earnings, hours).
Drivers opt-in, progress is tracked automatically, and rewards are paid to
wallet on claim.
"""

import logging
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_admin_user, get_current_user
except ImportError:
    from db import db
    from dependencies import get_admin_user, get_current_user

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/quests", tags=["Quests"])

_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


# ── Request Schemas ──────────────────────────────────────────────────


class CreateQuestRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    type: str = Field(..., pattern="^(ride_count|earnings_target|online_hours|peak_rides|consecutive_days|rating_maintained)$")
    target_value: float = Field(..., gt=0)
    reward_amount: float = Field(..., gt=0, le=500)
    reward_type: str = Field(default="wallet_credit", pattern="^(cash|wallet_credit)$")
    start_date: str  # ISO datetime
    end_date: str    # ISO datetime
    max_participants: Optional[int] = None
    service_area_id: Optional[str] = None
    min_driver_rating: Optional[float] = None


class UpdateQuestRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    reward_amount: Optional[float] = None
    max_participants: Optional[int] = None


# ── Driver-Facing Endpoints ──────────────────────────────────────────


@api_router.get("")
async def get_available_quests(current_user: dict = Depends(get_current_user)):
    """Get quests available to the current driver."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.utcnow().isoformat()

    try:
        # Get all active quests
        quests = await db.get_rows(
            "quests",
            {"is_active": True},
            limit=50,
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Error fetching quests: {e}")
        quests = []

    # Filter by date range and eligibility
    available = []
    for q in quests:
        start = q.get("start_date", "")
        end = q.get("end_date", "")
        if start > now or end < now:
            continue

        # Check rating requirement
        min_rating = q.get("min_driver_rating")
        if min_rating and driver.get("rating", 5.0) < min_rating:
            continue

        # Check service area requirement
        area_id = q.get("service_area_id")
        if area_id and driver.get("service_area_id") != area_id:
            continue

        available.append(q)

    # Get driver's progress for these quests
    quest_ids = [q["id"] for q in available]
    progress_map = {}
    if quest_ids:
        try:
            progress_rows = await db.get_rows(
                "quest_progress",
                {"driver_id": driver["id"]},
                limit=100,
            )
            progress_map = {p["quest_id"]: p for p in progress_rows if p["quest_id"] in quest_ids}
        except Exception as e:
            logger.error(f"Error fetching progress: {e}")

    result = []
    for q in available:
        progress = progress_map.get(q["id"])
        current_value = progress["current_value"] if progress else 0
        target = q["target_value"]
        pct = min(100, round((current_value / target) * 100, 1)) if target > 0 else 0

        result.append({
            "id": q["id"],
            "title": q["title"],
            "description": q["description"],
            "type": q["type"],
            "target_value": q["target_value"],
            "reward_amount": q["reward_amount"],
            "reward_type": q.get("reward_type", "wallet_credit"),
            "start_date": q.get("start_date"),
            "end_date": q.get("end_date"),
            "current_value": current_value,
            "progress_pct": pct,
            "status": progress["status"] if progress else "available",
            "progress_id": progress["id"] if progress else None,
        })

    return result


@api_router.post("/{quest_id}/join")
async def join_quest(quest_id: str, current_user: dict = Depends(get_current_user)):
    """Opt-in to a quest."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    quest = await db.quests.find_one({"id": quest_id})
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    if not quest.get("is_active", True):
        raise HTTPException(status_code=400, detail="Quest is no longer active")

    now = datetime.utcnow().isoformat()
    if quest.get("end_date", "") < now:
        raise HTTPException(status_code=400, detail="Quest has ended")

    # Check if already joined
    existing = await db.quest_progress.find_one({"quest_id": quest_id, "driver_id": driver["id"]})
    if existing:
        raise HTTPException(status_code=400, detail="Already joined this quest")

    # Check max participants
    if quest.get("max_participants"):
        try:
            current_count_rows = await db.get_rows(
                "quest_progress",
                {"quest_id": quest_id},
                limit=quest["max_participants"] + 1,
            )
            if len(current_count_rows) >= quest["max_participants"]:
                raise HTTPException(status_code=400, detail="Quest is full")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error checking quest capacity: {e}")

    progress_data = {
        "id": str(uuid.uuid4()),
        "quest_id": quest_id,
        "driver_id": driver["id"],
        "current_value": 0,
        "status": "active",
        "started_at": datetime.utcnow().isoformat(),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.quest_progress.insert_one(progress_data)

    return {
        "progress_id": progress_data["id"],
        "quest_id": quest_id,
        "status": "active",
        "current_value": 0,
        "target_value": quest["target_value"],
    }


@api_router.get("/my-quests")
async def get_my_quests(current_user: dict = Depends(get_current_user)):
    """Get all quests the driver has joined with current progress."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        progress_rows = await db.get_rows(
            "quest_progress",
            {"driver_id": driver["id"]},
            limit=50,
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Error fetching quest progress: {e}")
        progress_rows = []

    result = []
    for p in progress_rows:
        quest = await db.quests.find_one({"id": p["quest_id"]})
        if not quest:
            continue

        target = quest["target_value"]
        current = p["current_value"]
        pct = min(100, round((current / target) * 100, 1)) if target > 0 else 0

        result.append({
            "progress_id": p["id"],
            "quest": {
                "id": quest["id"],
                "title": quest["title"],
                "description": quest["description"],
                "type": quest["type"],
                "target_value": target,
                "reward_amount": quest["reward_amount"],
                "reward_type": quest.get("reward_type", "wallet_credit"),
                "start_date": quest.get("start_date"),
                "end_date": quest.get("end_date"),
            },
            "current_value": current,
            "progress_pct": pct,
            "status": p["status"],
            "started_at": p.get("started_at"),
            "completed_at": p.get("completed_at"),
            "claimed_at": p.get("claimed_at"),
        })

    return result


@api_router.post("/progress/{progress_id}/claim")
async def claim_quest_reward(progress_id: str, current_user: dict = Depends(get_current_user)):
    """Claim the reward for a completed quest."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    progress = await db.quest_progress.find_one({"id": progress_id})
    if not progress:
        raise HTTPException(status_code=404, detail="Quest progress not found")

    if progress["driver_id"] != driver["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if progress["status"] != "completed":
        raise HTTPException(status_code=400, detail="Quest is not completed yet")

    quest = await db.quests.find_one({"id": progress["quest_id"]})
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    reward_amount = float(quest["reward_amount"])

    # Pay reward to wallet
    if quest.get("reward_type", "wallet_credit") == "wallet_credit":
        from .wallet import get_or_create_wallet, _record_transaction

        wallet = await get_or_create_wallet(current_user["id"])
        old_balance = _d(wallet.get("balance", 0))
        new_balance = old_balance + _d(reward_amount)

        await db.wallets.update_one(
            {"id": wallet["id"]},
            {"$set": {"balance": float(new_balance), "updated_at": datetime.utcnow().isoformat()}},
        )
        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=current_user["id"],
            txn_type="quest_reward",
            amount=reward_amount,
            balance_after=float(new_balance),
            reference_id=quest["id"],
            description=f"Quest reward: {quest['title']}",
        )

    # Mark as claimed
    await db.quest_progress.update_one(
        {"id": progress_id},
        {"$set": {
            "status": "claimed",
            "claimed_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }},
    )

    return {
        "status": "claimed",
        "reward_amount": reward_amount,
        "reward_type": quest.get("reward_type", "wallet_credit"),
    }


# ── Admin Endpoints ──────────────────────────────────────────────────


@api_router.post("/admin/create")
async def admin_create_quest(req: CreateQuestRequest, admin: dict = Depends(get_admin_user)):
    """Create a new quest (admin only)."""
    quest_data = {
        "id": str(uuid.uuid4()),
        "title": req.title,
        "description": req.description,
        "type": req.type,
        "target_value": req.target_value,
        "reward_amount": req.reward_amount,
        "reward_type": req.reward_type,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "is_active": True,
        "max_participants": req.max_participants,
        "service_area_id": req.service_area_id,
        "min_driver_rating": req.min_driver_rating,
        "metadata": {},
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.quests.insert_one(quest_data)

    return quest_data


@api_router.get("/admin/list")
async def admin_list_quests(
    is_active: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_admin_user),
):
    """List all quests with participation stats (admin only)."""
    filters = {}
    if is_active is not None:
        filters["is_active"] = is_active

    try:
        quests = await db.get_rows(
            "quests",
            filters,
            limit=limit,
            skip=offset,
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Error fetching quests: {e}")
        quests = []

    result = []
    for q in quests:
        # Get participation stats
        try:
            progress_rows = await db.get_rows(
                "quest_progress",
                {"quest_id": q["id"]},
                limit=1000,
            )
            total_participants = len(progress_rows)
            completed = sum(1 for p in progress_rows if p["status"] in ("completed", "claimed"))
            claimed = sum(1 for p in progress_rows if p["status"] == "claimed")
        except Exception:
            total_participants = completed = claimed = 0

        result.append({
            **q,
            "stats": {
                "total_participants": total_participants,
                "completed": completed,
                "claimed": claimed,
            },
        })

    return result


@api_router.patch("/admin/{quest_id}")
async def admin_update_quest(quest_id: str, req: UpdateQuestRequest, admin: dict = Depends(get_admin_user)):
    """Update a quest (admin only)."""
    quest = await db.quests.find_one({"id": quest_id})
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    update_data = {"updated_at": datetime.utcnow().isoformat()}
    if req.title is not None:
        update_data["title"] = req.title
    if req.description is not None:
        update_data["description"] = req.description
    if req.is_active is not None:
        update_data["is_active"] = req.is_active
    if req.reward_amount is not None:
        update_data["reward_amount"] = req.reward_amount
    if req.max_participants is not None:
        update_data["max_participants"] = req.max_participants

    await db.quests.update_one({"id": quest_id}, {"$set": update_data})

    return {**quest, **update_data}


@api_router.get("/admin/{quest_id}/participants")
async def admin_get_quest_participants(
    quest_id: str,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_admin_user),
):
    """Get participants of a quest with progress details (admin only)."""
    quest = await db.quests.find_one({"id": quest_id})
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    filters = {"quest_id": quest_id}
    if status:
        filters["status"] = status

    try:
        progress_rows = await db.get_rows(
            "quest_progress",
            filters,
            limit=limit,
            skip=offset,
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Error fetching participants: {e}")
        progress_rows = []

    result = []
    for p in progress_rows:
        driver = await db.drivers.find_one({"id": p["driver_id"]})
        driver_name = "Unknown"
        if driver:
            user = await db.users.find_one({"id": driver.get("user_id")})
            if user:
                driver_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()

        target = quest["target_value"]
        current = p["current_value"]
        pct = min(100, round((current / target) * 100, 1)) if target > 0 else 0

        result.append({
            "progress_id": p["id"],
            "driver_id": p["driver_id"],
            "driver_name": driver_name,
            "current_value": current,
            "target_value": target,
            "progress_pct": pct,
            "status": p["status"],
            "started_at": p.get("started_at"),
            "completed_at": p.get("completed_at"),
            "claimed_at": p.get("claimed_at"),
        })

    return result
