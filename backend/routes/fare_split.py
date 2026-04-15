"""Fare splitting — riders can split a ride's fare with friends.

Flow:
1. Rider creates a fare split for a ride, specifying participant phones
2. Participants receive notification and can accept/decline
3. Accepted participants pay their share (via wallet or card)
4. Requester pays reduced share once all accepts are in
"""

import logging
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_current_user
except ImportError:
    from db import db
    from dependencies import get_current_user

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/fare-split", tags=["Fare Split"])

_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


# ── Request Schemas ──────────────────────────────────────────────────


class CreateFareSplitRequest(BaseModel):
    ride_id: str
    participant_phones: List[str] = Field(..., min_length=1, max_length=5)


class RespondToSplitRequest(BaseModel):
    action: str = Field(..., pattern="^(accept|decline)$")


class PaySplitRequest(BaseModel):
    payment_method: str = Field(default="wallet", pattern="^(wallet|card)$")


# ── Endpoints ────────────────────────────────────────────────────────


@api_router.post("")
async def create_fare_split(req: CreateFareSplitRequest, current_user: dict = Depends(get_current_user)):
    """Create a fare split request for a ride."""
    ride = await db.rides.find_one({"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the ride requester can split the fare")

    # Check no existing active split for this ride
    existing = await db.fare_splits.find_one({"ride_id": req.ride_id, "status": {"$ne": "cancelled"}})
    if existing:
        raise HTTPException(status_code=400, detail="Fare split already exists for this ride")

    total_fare = float(ride.get("grand_total") or ride.get("total_fare", 0))
    split_count = len(req.participant_phones) + 1  # +1 for requester
    share_amount = float(_d(total_fare / split_count))

    # Create the fare split record
    split_id = str(uuid.uuid4())
    split_data = {
        "id": split_id,
        "ride_id": req.ride_id,
        "requester_id": current_user["id"],
        "total_fare": total_fare,
        "split_count": split_count,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.fare_splits.insert_one(split_data)

    # Create participant entries
    participants = []
    for phone in req.participant_phones:
        # Look up user by phone (may not exist yet)
        user = await db.users.find_one({"phone": phone})
        participant = {
            "id": str(uuid.uuid4()),
            "fare_split_id": split_id,
            "user_id": user["id"] if user else None,
            "phone": phone,
            "share_amount": share_amount,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
        await db.fare_split_participants.insert_one(participant)
        participants.append(participant)

    return {
        "id": split_id,
        "ride_id": req.ride_id,
        "total_fare": total_fare,
        "split_count": split_count,
        "your_share": share_amount,
        "participants": [
            {
                "id": p["id"],
                "phone": p["phone"],
                "share_amount": p["share_amount"],
                "status": p["status"],
            }
            for p in participants
        ],
    }


@api_router.get("/{split_id}")
async def get_fare_split(split_id: str, current_user: dict = Depends(get_current_user)):
    """Get fare split details."""
    split = await db.fare_splits.find_one({"id": split_id})
    if not split:
        raise HTTPException(status_code=404, detail="Fare split not found")

    participants = await db.get_rows(
        "fare_split_participants",
        {"fare_split_id": split_id},
        limit=10,
    )

    # Check access: must be requester or a participant
    user_id = current_user["id"]
    is_participant = any(p.get("user_id") == user_id for p in participants)
    if split["requester_id"] != user_id and not is_participant:
        raise HTTPException(status_code=403, detail="Not authorized to view this fare split")

    share_amount = float(_d(split["total_fare"] / split["split_count"]))

    return {
        "id": split["id"],
        "ride_id": split["ride_id"],
        "requester_id": split["requester_id"],
        "total_fare": split["total_fare"],
        "split_count": split["split_count"],
        "your_share": share_amount,
        "status": split["status"],
        "participants": [
            {
                "id": p["id"],
                "phone": p.get("phone"),
                "user_id": p.get("user_id"),
                "share_amount": p["share_amount"],
                "status": p["status"],
                "paid_at": p.get("paid_at"),
            }
            for p in participants
        ],
        "created_at": split.get("created_at"),
    }


@api_router.get("/ride/{ride_id}")
async def get_fare_split_for_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get fare split for a specific ride (if any)."""
    split = await db.fare_splits.find_one({"ride_id": ride_id, "status": {"$ne": "cancelled"}})
    if not split:
        return {"has_split": False}

    participants = await db.get_rows(
        "fare_split_participants",
        {"fare_split_id": split["id"]},
        limit=10,
    )

    share_amount = float(_d(split["total_fare"] / split["split_count"]))

    return {
        "has_split": True,
        "split": {
            "id": split["id"],
            "total_fare": split["total_fare"],
            "split_count": split["split_count"],
            "your_share": share_amount,
            "status": split["status"],
            "participants": [
                {
                    "id": p["id"],
                    "phone": p.get("phone"),
                    "share_amount": p["share_amount"],
                    "status": p["status"],
                }
                for p in participants
            ],
        },
    }


@api_router.post("/participant/{participant_id}/respond")
async def respond_to_split(
    participant_id: str,
    req: RespondToSplitRequest,
    current_user: dict = Depends(get_current_user),
):
    """Accept or decline a fare split invitation."""
    participant = await db.fare_split_participants.find_one({"id": participant_id})
    if not participant:
        raise HTTPException(status_code=404, detail="Split invitation not found")

    if participant.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if participant["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already {participant['status']}")

    new_status = "accepted" if req.action == "accept" else "declined"
    await db.fare_split_participants.update_one(
        {"id": participant_id},
        {"$set": {"status": new_status}},
    )

    # If declined, update split status and recalculate shares
    if new_status == "declined":
        split = await db.fare_splits.find_one({"id": participant["fare_split_id"]})
        if split:
            all_participants = await db.get_rows(
                "fare_split_participants",
                {"fare_split_id": split["id"]},
                limit=10,
            )
            active_count = sum(1 for p in all_participants if p["status"] not in ("declined",)) + 1  # +1 requester
            new_share = float(_d(split["total_fare"] / active_count))

            # Update share amounts for remaining participants
            for p in all_participants:
                if p["status"] not in ("declined",):
                    await db.fare_split_participants.update_one(
                        {"id": p["id"]},
                        {"$set": {"share_amount": new_share}},
                    )

            await db.fare_splits.update_one(
                {"id": split["id"]},
                {"$set": {"split_count": active_count, "updated_at": datetime.utcnow().isoformat()}},
            )

    return {"status": new_status}


@api_router.post("/participant/{participant_id}/pay")
async def pay_split_share(
    participant_id: str,
    req: PaySplitRequest,
    current_user: dict = Depends(get_current_user),
):
    """Pay your share of a fare split."""
    participant = await db.fare_split_participants.find_one({"id": participant_id})
    if not participant:
        raise HTTPException(status_code=404, detail="Split invitation not found")

    if participant.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if participant["status"] == "paid":
        raise HTTPException(status_code=400, detail="Already paid")

    if participant["status"] != "accepted":
        raise HTTPException(status_code=400, detail="Must accept the split first")

    share_amount = float(participant["share_amount"])

    if req.payment_method == "wallet":
        # Import wallet helper
        from .wallet import _record_transaction, get_or_create_wallet

        wallet = await get_or_create_wallet(current_user["id"])
        balance = _d(wallet.get("balance", 0))
        debit = _d(share_amount)

        if balance < debit:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance")

        new_balance = balance - debit
        await db.wallets.update_one(
            {"id": wallet["id"]},
            {"$set": {"balance": float(new_balance), "updated_at": datetime.utcnow().isoformat()}},
        )
        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=current_user["id"],
            txn_type="fare_split_sent",
            amount=-share_amount,
            balance_after=float(new_balance),
            reference_id=participant["fare_split_id"],
            description=f"Fare split payment ${share_amount:.2f}",
        )

    # Mark participant as paid
    await db.fare_split_participants.update_one(
        {"id": participant_id},
        {"$set": {"status": "paid", "paid_at": datetime.utcnow().isoformat()}},
    )

    # Check if all participants have paid → mark split as completed
    split = await db.fare_splits.find_one({"id": participant["fare_split_id"]})
    if split:
        all_participants = await db.get_rows(
            "fare_split_participants",
            {"fare_split_id": split["id"]},
            limit=10,
        )
        all_resolved = all(p["status"] in ("paid", "declined") for p in all_participants)
        if all_resolved:
            await db.fare_splits.update_one(
                {"id": split["id"]},
                {"$set": {"status": "completed", "updated_at": datetime.utcnow().isoformat()}},
            )

    return {"status": "paid", "share_amount": share_amount}


@api_router.post("/{split_id}/cancel")
async def cancel_fare_split(split_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a fare split (only the requester can do this)."""
    split = await db.fare_splits.find_one({"id": split_id})
    if not split:
        raise HTTPException(status_code=404, detail="Fare split not found")

    if split["requester_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the requester can cancel")

    if split["status"] == "completed":
        raise HTTPException(status_code=400, detail="Cannot cancel a completed split")

    await db.fare_splits.update_one(
        {"id": split_id},
        {"$set": {"status": "cancelled", "updated_at": datetime.utcnow().isoformat()}},
    )

    return {"status": "cancelled"}
