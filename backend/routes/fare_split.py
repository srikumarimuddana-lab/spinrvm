"""Fare splitting — riders can split a ride's fare with friends.

Flow:
1. Rider creates a fare split for a ride, specifying participant phones
2. Participants receive notification and can accept/decline
3. Accepted participants pay their share (via wallet or card)
4. Requester pays reduced share once all accepts are in
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator

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
    participant_phones: List[str] = Field(
        ..., min_length=1, max_length=5
    )  # Product limit: max 5 participants by design

    @validator("participant_phones", each_item=True)
    def validate_phone(cls, v: str) -> str:
        if not re.match(r"^\+1\d{10}$", v):
            raise ValueError(f"Phone must be in +1XXXXXXXXXX format: {v}")
        return v

    @validator("participant_phones")
    def no_duplicate_phones(cls, v: List[str]) -> List[str]:
        if len(v) != len(set(v)):
            raise ValueError("Duplicate phone numbers are not allowed")
        return v


class RespondToSplitRequest(BaseModel):
    action: str = Field(..., pattern="^(accept|decline)$")


class PaySplitRequest(BaseModel):
    payment_method: str = Field(default="wallet", pattern="^(wallet|card)$")


# ── Endpoints ────────────────────────────────────────────────────────


@api_router.post("")
async def create_fare_split(req: CreateFareSplitRequest, current_user: dict = Depends(get_current_user)):
    """Create a fare split request for a ride."""
    ride = await db.find_one("rides", {"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the ride requester can split the fare")

    # Initiating rider cannot add themselves as a participant
    requester_phone = current_user.get("phone")
    if requester_phone and requester_phone in req.participant_phones:
        raise HTTPException(status_code=400, detail="Initiating rider cannot add themselves as a participant")

    # Check no existing active split for this ride
    existing = await db.find_one("fare_splits", {"ride_id": req.ride_id, "status": {"$ne": "cancelled"}})
    if existing:
        raise HTTPException(status_code=400, detail="Fare split already exists for this ride")

    total_fare = _d(ride.get("grand_total") or ride.get("total_fare") or 0)
    split_count = len(req.participant_phones) + 1  # +1 for requester
    share_amount = _d(total_fare / split_count)

    # Create the fare split record
    split_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    split_data = {
        "id": split_id,
        "ride_id": req.ride_id,
        "requester_id": current_user["id"],
        "total_fare": str(total_fare),
        "split_count": split_count,
        "status": "pending",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    await db.insert_one("fare_splits", split_data)

    # Batch-resolve participant phones → user IDs in a single query instead
    # of one DB round-trip per phone (N+1 avoided).
    phone_list = list(req.participant_phones)
    existing_users = await db.get_rows(
        "users",
        {"phone": {"$in": phone_list}},
        limit=len(phone_list) + 1,
    )
    phone_to_user_id = {u["phone"]: u["id"] for u in existing_users if u.get("phone")}

    # Create participant entries
    participants = []
    for phone in phone_list:
        participant = {
            "id": str(uuid.uuid4()),
            "fare_split_id": split_id,
            "user_id": phone_to_user_id.get(phone),
            "phone": phone,
            "share_amount": str(share_amount),
            "status": "pending",
            "created_at": now_iso,
        }
        await db.insert_one("fare_split_participants", participant)
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
                "share_amount": p["share_amount"],
                "status": p["status"],
            }
            for p in participants
        ],
    }


@api_router.get("/{split_id}")
async def get_fare_split(split_id: str, current_user: dict = Depends(get_current_user)):
    """Get fare split details."""
    split = await db.find_one("fare_splits", {"id": split_id})
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

    share_amount = _d(Decimal(str(split["total_fare"])) / split["split_count"])

    return {
        "id": split["id"],
        "ride_id": split["ride_id"],
        "requester_id": split["requester_id"],
        "total_fare": split["total_fare"],
        "split_count": split["split_count"],
        "your_share": str(share_amount),
        "status": split["status"],
        "participants": [
            {
                "id": p["id"],
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
    split = await db.find_one("fare_splits", {"ride_id": ride_id, "status": {"$ne": "cancelled"}})
    if not split:
        return {"has_split": False}

    participants = await db.get_rows(
        "fare_split_participants",
        {"fare_split_id": split["id"]},
        limit=10,
    )

    user_id = current_user["id"]
    is_owner = split.get("requester_id") == user_id
    is_participant = any(p.get("user_id") == user_id for p in participants)
    if not (is_owner or is_participant):
        raise HTTPException(status_code=403, detail="Access denied")

    share_amount = str(_d(Decimal(str(split["total_fare"])) / split["split_count"]))

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
    participant = await db.find_one("fare_split_participants", {"id": participant_id})
    if not participant:
        raise HTTPException(status_code=404, detail="Split invitation not found")

    if participant.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if participant["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Already {participant['status']}")

    new_status = "accepted" if req.action == "accept" else "declined"
    await db.update_one(
        "fare_split_participants",
        {"id": participant_id},
        {"status": new_status},
    )

    # If declined, update split status and recalculate shares
    if new_status == "declined":
        split = await db.find_one("fare_splits", {"id": participant["fare_split_id"]})
        if split:
            all_participants = await db.get_rows(
                "fare_split_participants",
                {"fare_split_id": split["id"]},
                limit=10,
            )
            active_count = sum(1 for p in all_participants if p["status"] not in ("declined",)) + 1  # +1 requester
            new_share = _d(Decimal(str(split["total_fare"])) / active_count)

            # Update share amounts for remaining participants
            for p in all_participants:
                if p["status"] not in ("declined",):
                    await db.update_one(
                        "fare_split_participants",
                        {"id": p["id"]},
                        {"share_amount": str(new_share)},
                    )

            await db.update_one(
                "fare_splits",
                {"id": split["id"]},
                {"$set": {"split_count": active_count, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )

    return {"status": new_status}


@api_router.post("/participant/{participant_id}/pay")
async def pay_split_share(
    participant_id: str,
    req: PaySplitRequest,
    current_user: dict = Depends(get_current_user),
):
    """Pay your share of a fare split."""
    participant = await db.find_one("fare_split_participants", {"id": participant_id})
    if not participant:
        raise HTTPException(status_code=404, detail="Split invitation not found")

    if participant.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if participant["status"] == "paid":
        raise HTTPException(status_code=400, detail="Already paid")

    if participant["status"] != "accepted":
        raise HTTPException(status_code=400, detail="Must accept the split first")

    share_amount = _d(participant["share_amount"])

    if req.payment_method == "wallet":
        from .wallet import _record_transaction, get_or_create_wallet

        wallet = await get_or_create_wallet(current_user["id"])
        try:
            new_balance = await db.fare_split_pay_share(
                wallet_id=wallet["id"],
                participant_id=participant_id,
                amount=share_amount,
            )
        except ValueError as exc:
            if "insufficient_funds" in str(exc):
                raise HTTPException(status_code=400, detail="Insufficient wallet balance") from exc
            raise

        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=current_user["id"],
            txn_type="fare_split_sent",
            amount=-float(share_amount),
            balance_after=float(new_balance),
            reference_id=participant["fare_split_id"],
            description=f"Fare split payment ${share_amount:.2f}",
        )
        # Participant already marked 'paid' atomically by fare_split_pay_share RPC
    else:
        # Card path: mark participant as paid
        await db.update_one(
            "fare_split_participants",
            {"id": participant_id},
            {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}},
        )

    # Check if all participants have paid → mark split as completed
    split = await db.find_one("fare_splits", {"id": participant["fare_split_id"]})
    if split:
        all_participants = await db.get_rows(
            "fare_split_participants",
            {"fare_split_id": split["id"]},
            limit=10,
        )
        all_resolved = all(p["status"] in ("paid", "declined") for p in all_participants)
        if all_resolved:
            await db.update_one(
                "fare_splits",
                {"id": split["id"]},
                {"$set": {"status": "completed", "updated_at": datetime.now(timezone.utc).isoformat()}},
            )

    return {"status": "paid", "share_amount": float(share_amount)}


@api_router.post("/{split_id}/cancel")
async def cancel_fare_split(split_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a fare split (only the requester can do this)."""
    split = await db.find_one("fare_splits", {"id": split_id})
    if not split:
        raise HTTPException(status_code=404, detail="Fare split not found")

    if split["requester_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the requester can cancel")

    if split["status"] == "completed":
        raise HTTPException(status_code=400, detail="Cannot cancel a completed split")

    await db.update_one(
        "fare_splits",
        {"id": split_id},
        {"$set": {"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Refund any participants who had already paid
    participants = await db.get_rows(
        "fare_split_participants",
        {"fare_split_id": split_id},
        limit=10,
    )
    paid_participants = [p for p in participants if p.get("status") == "paid"]
    if paid_participants:
        from .wallet import _record_transaction, get_or_create_wallet

        for p in paid_participants:
            if not p.get("user_id"):
                continue
            try:
                wallet = await get_or_create_wallet(p["user_id"])
                refund = _d(p["share_amount"])
                await db.wallet_increment_balance(wallet["id"], refund)
                updated_wallet = await db.find_one("wallets", {"id": wallet["id"]})
                balance_after = float(updated_wallet.get("balance", 0)) if updated_wallet else 0.0
                await _record_transaction(
                    wallet_id=wallet["id"],
                    user_id=p["user_id"],
                    txn_type="fare_split_refund",
                    amount=float(refund),
                    balance_after=balance_after,
                    reference_id=split_id,
                    description=f"Fare split cancellation refund ${refund:.2f}",
                )
            except Exception as refund_err:
                logger.error(f"[FARE_SPLIT] Refund failed for participant {p['id']} on split {split_id}: {refund_err}")

    return {"status": "cancelled"}
