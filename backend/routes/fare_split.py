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
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

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


def _money_str(v) -> str:
    return str(_d(v))


def _split_shares(total_fare: Decimal, split_count: int) -> "tuple[Decimal, Decimal]":
    """Split ``total_fare`` into (participant_share, requester_share).

    Participant shares round DOWN to whole cents and the requester absorbs
    the remainder, so ``participant_share × (split_count - 1) +
    requester_share == total_fare`` exactly. A naive ``round(total/count)``
    for every share drifts: $10.00 / 3 → 3 × $3.33 = $9.99. ROUND_DOWN
    (not HALF_UP) keeps the requester share non-negative for any total.
    """
    total = _d(total_fare)
    participant_share = (total / split_count).quantize(_TWO, rounding=ROUND_DOWN)
    requester_share = total - participant_share * (split_count - 1)
    return participant_share, requester_share


def _requester_share_from_rows(split: dict, participants: "list[dict]") -> Decimal:
    """Requester's share = total minus what the non-declined participants owe.

    Derived from the stored participant rows so it stays exact after
    decline-driven recalculations.
    """
    active_sum = sum(
        (_d(p.get("share_amount") or 0) for p in participants if p.get("status") != "declined"),
        Decimal("0"),
    )
    return _d(split["total_fare"]) - active_sum


# ── Request Schemas ──────────────────────────────────────────────────


class CreateFareSplitRequest(BaseModel):
    ride_id: str
    participant_phones: List[str] = Field(
        ..., min_length=1, max_length=5
    )  # Product limit: max 5 participants by design

    @field_validator("participant_phones", mode="before")
    @classmethod
    def validate_phone(cls, value: List[str]) -> List[str]:
        for v in value:
            if not re.match(r"^\+1\d{10}$", v):
                raise ValueError(f"Phone must be in +1XXXXXXXXXX format: {v}")
        return value


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

    # Check no existing active split for this ride
    existing = await db.find_one("fare_splits", {"ride_id": req.ride_id, "status": {"$ne": "cancelled"}})
    if existing:
        raise HTTPException(status_code=400, detail="Fare split already exists for this ride")

    total_fare = _d(ride.get("grand_total") or ride.get("total_fare", 0))
    split_count = len(req.participant_phones) + 1  # +1 for requester
    participant_share, requester_share = _split_shares(total_fare, split_count)
    share_amount = _money_str(participant_share)

    # Create the fare split record
    split_id = str(uuid.uuid4())
    split_data = {
        "id": split_id,
        "ride_id": req.ride_id,
        "requester_id": current_user["id"],
        "total_fare": _money_str(total_fare),
        "split_count": split_count,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.insert_one("fare_splits", split_data)

    # Create participant entries
    participants = []
    for phone in req.participant_phones:
        # Look up user by phone (may not exist yet)
        user = await db.find_one("users", {"phone": phone})
        participant = {
            "id": str(uuid.uuid4()),
            "fare_split_id": split_id,
            "user_id": user["id"] if user else None,
            "phone": phone,
            "share_amount": share_amount,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.insert_one("fare_split_participants", participant)
        participants.append(participant)

    return {
        "id": split_id,
        "ride_id": req.ride_id,
        "total_fare": _money_str(total_fare),
        "split_count": split_count,
        # Requester absorbs the rounding remainder so shares sum to total_fare
        "your_share": _money_str(requester_share),
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

    # Viewer-aware share: participants owe their stored row; the requester
    # owes the exact remainder, so all shares sum to total_fare.
    own_row = next((p for p in participants if p.get("user_id") == user_id), None)
    if split["requester_id"] != user_id and own_row is not None:
        your_share = _money_str(own_row.get("share_amount") or 0)
    else:
        your_share = _money_str(_requester_share_from_rows(split, participants))

    return {
        "id": split["id"],
        "ride_id": split["ride_id"],
        "requester_id": split["requester_id"],
        "total_fare": _money_str(split["total_fare"]),
        "split_count": split["split_count"],
        "your_share": your_share,
        "status": split["status"],
        "participants": [
            {
                "id": p["id"],
                "user_id": p.get("user_id"),
                "share_amount": _money_str(p["share_amount"]),
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

    # Viewer-aware share — same remainder rule as get_fare_split.
    own_row = next((p for p in participants if p.get("user_id") == user_id), None)
    if not is_owner and own_row is not None:
        your_share = _money_str(own_row.get("share_amount") or 0)
    else:
        your_share = _money_str(_requester_share_from_rows(split, participants))

    return {
        "has_split": True,
        "split": {
            "id": split["id"],
            "total_fare": _money_str(split["total_fare"]),
            "split_count": split["split_count"],
            "your_share": your_share,
            "status": split["status"],
            "participants": [
                {
                    "id": p["id"],
                    "share_amount": _money_str(p["share_amount"]),
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
        {"$set": {"status": new_status}},
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
            new_participant_share, _requester_share = _split_shares(_d(split["total_fare"]), active_count)
            new_share = _money_str(new_participant_share)

            # Update share amounts for remaining participants
            for p in all_participants:
                if p["status"] not in ("declined",):
                    await db.update_one(
                        "fare_split_participants",
                        {"id": p["id"]},
                        {"$set": {"share_amount": new_share}},
                    )

            await db.update_one(
                "fare_splits",
                {"id": split["id"]},
                {
                    "$set": {
                        "split_count": active_count,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
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
            amount="-" + _money_str(share_amount),
            balance_after=_money_str(new_balance),
            reference_id=participant["fare_split_id"],
            description=f"Fare split payment ${share_amount:.2f}",
        )
        # Participant already marked 'paid' atomically by fare_split_pay_share RPC
    else:
        # Card path: mark participant as paid
        await db.update_one(
            "fare_split_participants",
            {"id": participant_id},
            {
                "$set": {
                    "status": "paid",
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                }
            },
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
                {
                    "$set": {
                        "status": "completed",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )

    return {"status": "paid", "share_amount": _money_str(share_amount)}


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
        {
            "$set": {
                "status": "cancelled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
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
                balance_after = _money_str(updated_wallet.get("balance", 0)) if updated_wallet else "0.00"
                await _record_transaction(
                    wallet_id=wallet["id"],
                    user_id=p["user_id"],
                    txn_type="fare_split_refund",
                    amount=_money_str(refund),
                    balance_after=balance_after,
                    reference_id=split_id,
                    description=f"Fare split cancellation refund ${refund:.2f}",
                )
            except Exception as refund_err:
                logger.error(f"[FARE_SPLIT] Refund failed for participant {p['id']} on split {split_id}: {refund_err}")

    return {"status": "cancelled"}
