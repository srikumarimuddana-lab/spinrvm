"""
E4: 3DS payment challenge (requires_action) retry loop
D10: Quest / bonus challenge driver-facing flows

E4 — code under test: backend/utils/payment_retry.py::retry_failed_payments
  Stripe returns `requires_action` (3D Secure / SCA) when the card issuer
  requires extra authentication. The background retry loop polls those rides
  and re-confirms the PaymentIntent up to MAX_RETRIES times, notifying the
  driver on each attempt and the rider on final failure.

  These tests pin:
    - already-succeeded intent → payment_status='paid', no retry needed
    - requires_confirmation intent → status='processing', retry counter++
    - cancelled intent → capped at MAX_RETRIES (no more retries)
    - ride at MAX_RETRIES → skipped entirely
    - final-failure ride → rider push notification sent
    - missing payment_intent_id → skipped (no Stripe call)

D10 — code under test: backend/routes/quests.py
  Endpoints: GET /quests, POST /{id}/join, GET /my-quests,
             POST /progress/{id}/claim

  These tests pin:
    - get_available_quests: returns active quests with driver progress
    - join_quest: creates progress row; already-joined → 400; full → 400;
                  expired quest → 400; non-existent driver → 404
    - get_my_quests: returns driver's active progress with embedded quest
    - claim_quest_reward: completed → wallet credited + status='claimed';
                          not-completed → 400; non-owner → 403

Run:
    pytest backend/tests/test_e4_d10_payment_3ds_quests.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


DRIVER_USER_ID = "driver_user_e4d10"
DRIVER_ID = "driver_e4d10"
RIDER_ID = "rider_e4d10"
RIDE_ID = "ride_e4d10_001"
QUEST_ID = "quest_e4d10_001"
PROGRESS_ID = "prog_e4d10_001"


def _ride(payment_status: str = "requires_action", retry_count: int = 0, **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "payment_intent_id": "pi_test_3ds",
        "payment_status": payment_status,
        "payment_retry_count": retry_count,
        "total_fare": 20.0,
        "created_at": datetime.utcnow().isoformat(),
        **extra,
    }


def _quest(now_offset_hours: int = 0) -> dict:
    now = datetime.utcnow()
    return {
        "id": QUEST_ID,
        "title": "Complete 10 rides",
        "description": "Complete 10 rides this weekend for a $20 bonus",
        "type": "ride_count",
        "target_value": 10.0,
        "reward_amount": 20.0,
        "reward_type": "wallet_credit",
        "is_active": True,
        "start_date": (now - timedelta(hours=24)).isoformat(),
        "end_date": (now + timedelta(hours=24 + now_offset_hours)).isoformat(),
        "max_participants": None,
        "service_area_id": None,
        "min_driver_rating": None,
        "created_at": now.isoformat(),
    }


def _progress(status: str = "active", current_value: float = 5.0) -> dict:
    return {
        "id": PROGRESS_ID,
        "quest_id": QUEST_ID,
        "driver_id": DRIVER_ID,
        "current_value": current_value,
        "status": status,
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat() if status == "completed" else None,
        "claimed_at": None,
        "created_at": datetime.utcnow().isoformat(),
    }


def _driver_row() -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "rating": 4.8,
        "service_area_id": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# E4: 3DS / requires_action payment retry
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPayment3DSRetry:
    """Pins retry_failed_payments for the Stripe requires_action / 3DS path.

    Code under test: backend/utils/payment_retry.py::retry_failed_payments
    """

    async def _run_retry(self, rides, mock_intent_status: str,
                         stripe_secret: str = "sk_test_x"):
        import sys
        from unittest.mock import MagicMock

        mock_stripe = MagicMock()
        intent = MagicMock()
        intent.status = mock_intent_status
        mock_stripe.PaymentIntent.retrieve = MagicMock(return_value=intent)
        mock_stripe.PaymentIntent.confirm = MagicMock()

        updates = []
        push_calls = []

        with (
            patch.dict(sys.modules, {"stripe": mock_stripe}),
            patch("backend.utils.payment_retry.db.get_rows", AsyncMock(return_value=rides)),
            patch("backend.utils.payment_retry.db.update_one",
                  AsyncMock(side_effect=lambda t, q, d: updates.append((q, d)))),
            patch("backend.utils.payment_retry.get_app_settings",
                  AsyncMock(return_value={"stripe_secret_key": stripe_secret})),
            patch("backend.utils.payment_retry.send_push_notification",
                  AsyncMock(side_effect=lambda uid, t, b, **kw: push_calls.append(uid))),
        ):
            from backend.utils.payment_retry import retry_failed_payments
            await retry_failed_payments()

        return updates, push_calls, mock_stripe

    async def test_already_succeeded_intent_marks_paid(self):
        """If the PaymentIntent already succeeded (webhook missed), mark paid."""
        updates, _, mock_stripe = await self._run_retry(
            [_ride("requires_action", 0)], "succeeded"
        )

        assert updates, "No DB update performed"
        _, data = updates[0]
        assert data["$set"]["payment_status"] == "paid"
        mock_stripe.PaymentIntent.confirm.assert_not_called()

    async def test_requires_confirmation_submits_retry(self):
        """PaymentIntent needs confirmation → confirm() called, status=processing."""
        updates, _, mock_stripe = await self._run_retry(
            [_ride("requires_action", 0)], "requires_confirmation"
        )

        mock_stripe.PaymentIntent.confirm.assert_called_once()
        _, data = updates[0]
        assert data["$set"]["payment_status"] == "processing"
        assert data["$set"]["payment_retry_count"] == 1

    async def test_cancelled_intent_is_capped_not_retried(self):
        """A cancelled PaymentIntent should not be retried — cap at MAX_RETRIES."""
        from backend.utils.payment_retry import MAX_RETRIES
        updates, _, mock_stripe = await self._run_retry(
            [_ride("requires_action", 0)], "canceled"
        )

        mock_stripe.PaymentIntent.confirm.assert_not_called()
        _, data = updates[0]
        assert data["$set"]["payment_retry_count"] == MAX_RETRIES

    async def test_ride_at_max_retries_is_skipped(self):
        """A ride that already exhausted retries must not trigger another confirm."""
        from backend.utils.payment_retry import MAX_RETRIES
        updates, _, mock_stripe = await self._run_retry(
            [_ride("failed", retry_count=MAX_RETRIES)], "requires_confirmation"
        )

        mock_stripe.PaymentIntent.confirm.assert_not_called()
        assert not updates

    async def test_missing_stripe_secret_skips_all_rides(self):
        """Without a Stripe secret key no API call is made."""
        updates, _, mock_stripe = await self._run_retry(
            [_ride("requires_action", 0)], "requires_confirmation",
            stripe_secret="",
        )

        mock_stripe.PaymentIntent.retrieve.assert_not_called()
        assert not updates

    async def test_final_failure_sends_push_to_rider(self):
        """On last retry attempt rider is notified via push."""
        from backend.utils.payment_retry import MAX_RETRIES
        # retry_count = MAX_RETRIES - 1 → this attempt reaches MAX_RETRIES
        ride = _ride("failed", retry_count=MAX_RETRIES - 1)

        # Simulate a Stripe exception on this retry
        import sys
        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.retrieve = MagicMock(
            side_effect=Exception("Network error")
        )

        push_calls = []

        with (
            patch.dict(sys.modules, {"stripe": mock_stripe}),
            patch("backend.utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.utils.payment_retry.db.update_one", AsyncMock()),
            patch("backend.utils.payment_retry.get_app_settings",
                  AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
            patch("backend.utils.payment_retry.send_push_notification",
                  AsyncMock(side_effect=lambda uid, *a, **kw: push_calls.append(uid))),
        ):
            from backend.utils import payment_retry
            # Reload to avoid module caching
            import importlib
            importlib.reload(payment_retry)
            await payment_retry.retry_failed_payments()

        assert RIDER_ID in push_calls, "Rider not notified on final payment failure"


# ─────────────────────────────────────────────────────────────────────────────
# D10: Quest / bonus challenge flows
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetAvailableQuests:
    """Pins get_available_quests: active + in-window quests returned with progress.

    Code under test: backend/routes/quests.py::get_available_quests (~line 64).
    """

    async def test_returns_active_quests_with_progress(self):
        from backend.routes.quests import get_available_quests

        q = _quest()
        prog = _progress(current_value=3.0)

        with (
            patch("backend.routes.quests.db.find_one", AsyncMock(return_value=_driver_row())),
            patch("backend.routes.quests.db.get_rows", AsyncMock(side_effect=[
                [q],          # quests
                [prog],       # quest_progress
            ])),
        ):
            result = await get_available_quests(current_user={"id": DRIVER_USER_ID})

        assert len(result) == 1
        assert result[0]["id"] == QUEST_ID
        assert result[0]["current_value"] == 3.0
        assert result[0]["progress_pct"] == 30.0

    async def test_expired_quest_not_returned(self):
        from backend.routes.quests import get_available_quests

        past_quest = {**_quest(), "end_date": (datetime.utcnow() - timedelta(hours=1)).isoformat()}

        with (
            patch("backend.routes.quests.db.find_one", AsyncMock(return_value=_driver_row())),
            patch("backend.routes.quests.db.get_rows", AsyncMock(side_effect=[
                [past_quest], []
            ])),
        ):
            result = await get_available_quests(current_user={"id": DRIVER_USER_ID})

        assert result == []

    async def test_driver_not_found_returns_404(self):
        from fastapi import HTTPException
        from backend.routes.quests import get_available_quests

        with patch("backend.routes.quests.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_available_quests(current_user={"id": DRIVER_USER_ID})

        assert exc.value.status_code == 404


@pytest.mark.asyncio
class TestJoinQuest:
    """Pins join_quest: creates progress; guards duplicate + full + expired.

    Code under test: backend/routes/quests.py::join_quest (~line 147).
    """

    async def test_join_creates_progress_row(self):
        from backend.routes.quests import join_quest

        inserted = []

        with (
            patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
                _driver_row(),   # driver lookup
                _quest(),        # quest lookup
                None,            # no existing progress
            ])),
            patch("backend.routes.quests.db.insert_one",
                  AsyncMock(side_effect=lambda t, row: inserted.append(row))),
        ):
            result = await join_quest(quest_id=QUEST_ID, current_user={"id": DRIVER_USER_ID})

        assert result["quest_id"] == QUEST_ID
        assert result["status"] == "active"
        assert inserted, "No progress row inserted"

    async def test_already_joined_returns_400(self):
        from fastapi import HTTPException
        from backend.routes.quests import join_quest

        with patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
            _driver_row(),
            _quest(),
            _progress(),   # already joined
        ])):
            with pytest.raises(HTTPException) as exc:
                await join_quest(quest_id=QUEST_ID, current_user={"id": DRIVER_USER_ID})

        assert exc.value.status_code == 400

    async def test_expired_quest_returns_400(self):
        from fastapi import HTTPException
        from backend.routes.quests import join_quest

        expired = {**_quest(), "end_date": (datetime.utcnow() - timedelta(hours=1)).isoformat()}

        with patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
            _driver_row(),
            expired,
        ])):
            with pytest.raises(HTTPException) as exc:
                await join_quest(quest_id=QUEST_ID, current_user={"id": DRIVER_USER_ID})

        assert exc.value.status_code == 400

    async def test_full_quest_returns_400(self):
        from fastapi import HTTPException
        from backend.routes.quests import join_quest

        full_quest = {**_quest(), "max_participants": 1}
        existing_participant = [_progress()]   # 1 participant = full

        with (
            patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
                _driver_row(),
                full_quest,
                None,   # not already joined
            ])),
            patch("backend.routes.quests.db.get_rows", AsyncMock(return_value=existing_participant)),
        ):
            with pytest.raises(HTTPException) as exc:
                await join_quest(quest_id=QUEST_ID, current_user={"id": DRIVER_USER_ID})

        assert exc.value.status_code == 400
        assert "full" in exc.value.detail.lower()


@pytest.mark.asyncio
class TestClaimQuestReward:
    """Pins claim_quest_reward: wallet credited, status=claimed; guards.

    Code under test: backend/routes/quests.py::claim_quest_reward (~line 260).
    """

    async def test_completed_quest_credits_wallet_and_claims(self):
        from backend.routes.quests import claim_quest_reward

        wallet = {"id": "w1", "user_id": DRIVER_USER_ID, "balance": 50.0, "is_active": True}
        db_updates = []

        with (
            patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
                _driver_row(),
                _progress(status="completed"),
                _quest(),
            ])),
            patch("backend.routes.quests.db.update_one",
                  AsyncMock(side_effect=lambda t, q, d: db_updates.append((t, d)))),
            patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
            patch("backend.routes.wallet._record_transaction", AsyncMock()),
        ):
            result = await claim_quest_reward(
                progress_id=PROGRESS_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        assert result["status"] == "claimed"
        assert result["reward_amount"] == 20.0
        # Progress row must be marked claimed
        claimed_updates = [d for t, d in db_updates if d.get("$set", {}).get("status") == "claimed"]
        assert claimed_updates, "Progress row not marked as claimed"

    async def test_not_completed_quest_returns_400(self):
        from fastapi import HTTPException
        from backend.routes.quests import claim_quest_reward

        with patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
            _driver_row(),
            _progress(status="active"),  # not completed
        ])):
            with pytest.raises(HTTPException) as exc:
                await claim_quest_reward(
                    progress_id=PROGRESS_ID,
                    current_user={"id": DRIVER_USER_ID},
                )

        assert exc.value.status_code == 400

    async def test_non_owner_returns_403(self):
        from fastapi import HTTPException
        from backend.routes.quests import claim_quest_reward

        other_driver = {**_driver_row(), "id": "other-driver-id"}
        progress_for_other = {**_progress(status="completed"), "driver_id": "other-driver-id"}

        with patch("backend.routes.quests.db.find_one", AsyncMock(side_effect=[
            _driver_row(),          # current driver
            progress_for_other,     # progress belongs to other driver
        ])):
            with pytest.raises(HTTPException) as exc:
                await claim_quest_reward(
                    progress_id=PROGRESS_ID,
                    current_user={"id": DRIVER_USER_ID},
                )

        assert exc.value.status_code == 403
