"""Integration tests for the quest/bonus-challenge endpoints.

Routes under test (backend/routes/quests.py):
  GET    /api/v1/quests                              — available quests for driver
  POST   /api/v1/quests/{quest_id}/join              — opt-in to a quest
  GET    /api/v1/quests/my-quests                    — driver's active quests
  POST   /api/v1/quests/progress/{progress_id}/claim — claim completed quest reward
  POST   /api/v1/quests/admin/create                 — admin: create quest
  GET    /api/v1/quests/admin/list                   — admin: list all quests
  PATCH  /api/v1/quests/admin/{quest_id}             — admin: update quest
  GET    /api/v1/quests/admin/{quest_id}/participants — admin: quest participants
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
_PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
_NOW = datetime.now(timezone.utc).isoformat()

SAMPLE_USER = {"id": "user_123", "phone": "+1234567890", "role": "rider", "is_driver": True}
SAMPLE_ADMIN = {"id": "admin_1", "phone": "+1112223333", "role": "admin", "is_driver": False}

SAMPLE_DRIVER = {
    "id": "driver_123",
    "user_id": "user_123",
    "rating": 4.8,
    "service_area_id": "area_1",
}

SAMPLE_QUEST = {
    "id": "quest_1",
    "title": "Complete 10 Rides",
    "description": "Earn a bonus by completing 10 rides this week.",
    "type": "ride_count",
    "target_value": 10.0,
    "reward_amount": 25.0,
    "reward_type": "wallet_credit",
    "start_date": _PAST,
    "end_date": _FUTURE,
    "is_active": True,
    "max_participants": None,
    "service_area_id": None,
    "min_driver_rating": None,
    "created_at": _NOW,
    "updated_at": _NOW,
}

SAMPLE_PROGRESS = {
    "id": "progress_1",
    "quest_id": "quest_1",
    "driver_id": "driver_123",
    "current_value": 10,
    "status": "completed",
    "started_at": _NOW,
    "created_at": _NOW,
    "updated_at": _NOW,
}


def make_mock_db():
    """Build a mock that mirrors the db_supabase flat-function interface.

    Routes call ``await db.find_one("drivers", {...})``, not
    ``await db.drivers.find_one({...})``.  We expose per-table sub-mocks as
    attributes so individual tests can override them with::

        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)

    The top-level ``find_one`` / ``insert_one`` / ``update_one`` AsyncMocks
    dispatch to the per-table attribute by table name.
    """
    mock = MagicMock()

    # Per-table sub-mocks
    _tables = ("drivers", "quests", "quest_progress", "wallets", "wallet_transactions", "users", "driver_bonuses")
    for tbl in _tables:
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, tbl, col_mock)

    # Default get_rows at top level (table-agnostic)
    mock.get_rows = AsyncMock(return_value=[])

    # Dispatcher: routes call db.find_one("drivers", ...) — delegate to per-table mock
    async def _find_one(table, filters=None, **kwargs):
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.find_one(filters, **kwargs)
        return None

    async def _insert_one(table, doc, **kwargs):
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.insert_one(doc, **kwargs)
        return None

    async def _update_one(table, filters, update, **kwargs):
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.update_one(filters, update, **kwargs)
        return None

    mock.find_one = _find_one
    mock.insert_one = _insert_one
    mock.update_one = _update_one

    # Atomic wallet RPC used by claim_quest_reward
    from decimal import Decimal

    mock.wallet_increment_balance = AsyncMock(return_value=Decimal("25.00"))

    return mock


@pytest.fixture
def client():
    import dependencies  # same module the routes use (routes use relative '..dependencies')
    from backend.server import app  # ensures server.py sys.path setup runs first

    app.dependency_overrides[dependencies.get_current_user] = lambda: SAMPLE_USER
    app.dependency_overrides[dependencies.get_admin_user] = lambda: SAMPLE_ADMIN
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestGetAvailableQuests:
    """GET /api/v1/quests"""

    def test_driver_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests")

        assert resp.status_code == 404
        assert "driver not found" in resp.json()["detail"].lower()

    def test_returns_active_quests_for_driver(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.get_rows = AsyncMock(side_effect=[[SAMPLE_QUEST], []])

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests")

        assert resp.status_code == 200
        quests = resp.json()
        assert len(quests) == 1
        assert quests[0]["id"] == "quest_1"

    def test_expired_quests_excluded(self, client):
        expired_quest = {**SAMPLE_QUEST, "end_date": _PAST}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.get_rows = AsyncMock(side_effect=[[expired_quest], []])

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_unauthenticated_request_rejected(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as c:
            resp = c.get("/api/v1/quests")
        assert resp.status_code == 401


class TestJoinQuest:
    """POST /api/v1/quests/{quest_id}/join"""

    def test_join_quest_success(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.quest_progress.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 200
        data = resp.json()
        assert data["quest_id"] == "quest_1"
        assert data["status"] == "active"
        assert data["current_value"] == 0

    def test_driver_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 404

    def test_quest_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quests.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/bad_quest/join")

        assert resp.status_code == 404

    def test_already_joined_returns_400(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 400
        assert "already joined" in resp.json()["detail"].lower()

    def test_inactive_quest_returns_400(self, client):
        inactive_quest = {**SAMPLE_QUEST, "is_active": False}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quests.find_one = AsyncMock(return_value=inactive_quest)
        mock_db.quest_progress.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 400

    def test_join_foreign_area_scoped_quest_returns_403(self, client):
        """An area-scoped quest must reject a driver from a different service
        area at join time — not just hide it from GET /quests — so its reward
        can't be claimed by someone outside the intended area."""
        scoped_quest = {**SAMPLE_QUEST, "service_area_id": "area_2"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)  # area_1
        mock_db.quests.find_one = AsyncMock(return_value=scoped_quest)
        mock_db.quest_progress.find_one = AsyncMock(return_value=None)
        insert = AsyncMock(return_value=None)
        mock_db.quest_progress.insert_one = insert

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 403
        assert "service area" in resp.json()["detail"].lower()
        insert.assert_not_awaited()  # never created progress for an ineligible driver

    def test_join_matching_area_scoped_quest_succeeds(self, client):
        """A driver in the quest's own service area can still join."""
        scoped_quest = {**SAMPLE_QUEST, "service_area_id": "area_1"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)  # area_1
        mock_db.quests.find_one = AsyncMock(return_value=scoped_quest)
        mock_db.quest_progress.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/quest_1/join")

        assert resp.status_code == 200


class TestGetMyQuests:
    """GET /api/v1/quests/my-quests"""

    def test_driver_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/my-quests")

        assert resp.status_code == 404

    def test_returns_joined_quests_with_progress(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.get_rows = AsyncMock(return_value=[SAMPLE_PROGRESS])
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/my-quests")

        assert resp.status_code == 200
        quests = resp.json()
        assert len(quests) == 1
        assert quests[0]["status"] == "completed"
        assert quests[0]["progress_pct"] == 100.0

    def test_empty_quest_list(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.get_rows = AsyncMock(return_value=[])

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/my-quests")

        assert resp.status_code == 200
        assert resp.json() == []


class TestClaimQuestReward:
    """POST /api/v1/quests/progress/{progress_id}/claim"""

    def test_claim_completed_quest_reward(self, client):
        claimed_row = {**SAMPLE_PROGRESS, "status": "claimed"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)
        mock_db.quest_progress.update_one = AsyncMock(return_value=claimed_row)  # atomic claim wins
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "claimed"
        assert data["reward_amount"] == 25.0
        # Reward is a PAYABLE driver bonus (driver_bonuses ledger) — folds into
        # payable_balance + Stripe payout — NOT a rider wallet credit.
        mock_db.driver_bonuses.insert_one.assert_awaited_once()
        bonus_doc = mock_db.driver_bonuses.insert_one.await_args.args[0]
        assert bonus_doc["kind"] == "quest"
        assert bonus_doc["source_id"] == "progress_1"
        assert bonus_doc["driver_id"] == SAMPLE_DRIVER["id"]
        assert str(bonus_doc["amount"]) == "25.00"
        mock_db.wallet_increment_balance.assert_not_awaited()
        # The claim filter must carry the CAS status guard.
        claim_filters = mock_db.quest_progress.update_one.await_args_list[0].args[0]
        assert claim_filters == {"id": "progress_1", "status": "completed"}

    def test_concurrent_claim_loses_cas_returns_409(self, client):
        """Two simultaneous claims: the loser's CAS update matches zero rows
        and must 409 without crediting the wallet."""
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)
        mock_db.quest_progress.update_one = AsyncMock(return_value=None)  # zero rows — race lost
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)

        with patch("routes.quests.db", mock_db), patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 409
        assert "already claimed" in resp.json()["detail"].lower()
        mock_db.wallet_increment_balance.assert_not_awaited()

    def test_bonus_insert_failure_releases_claim_and_returns_503(self, client):
        """If recording the driver_bonuses row fails and no bonus row already
        exists, the claim must be released (status back to completed) so the
        driver can retry, and the endpoint returns 503 — never leave the claim
        'claimed' with no money recorded."""
        claimed_row = {**SAMPLE_PROGRESS, "status": "claimed"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)
        mock_db.quest_progress.update_one = AsyncMock(side_effect=[claimed_row, SAMPLE_PROGRESS])
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.driver_bonuses.insert_one = AsyncMock(side_effect=RuntimeError("insert down"))
        mock_db.driver_bonuses.find_one = AsyncMock(return_value=None)  # no existing bonus → real failure

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 503
        # Claim released back to 'completed' so the driver can retry.
        assert mock_db.quest_progress.update_one.await_count == 2
        release_filters, release_update = mock_db.quest_progress.update_one.await_args_list[1].args[:2]
        assert release_filters == {"id": "progress_1", "status": "claimed"}
        assert release_update["$set"]["status"] == "completed"

    def test_idempotent_claim_when_bonus_already_recorded(self, client):
        """If the bonus insert conflicts but a bonus row already exists (retry /
        unique-index), the claim is success (money already recorded), not a
        failure — 200, no 503, no double credit."""
        claimed_row = {**SAMPLE_PROGRESS, "status": "claimed"}
        existing_bonus = {"id": "bonus_1", "kind": "quest", "source_id": "progress_1", "amount": "25.00"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)
        mock_db.quest_progress.update_one = AsyncMock(return_value=claimed_row)
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.driver_bonuses.insert_one = AsyncMock(side_effect=RuntimeError("unique violation"))
        mock_db.driver_bonuses.find_one = AsyncMock(return_value=existing_bonus)  # already credited

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 200
        assert resp.json()["status"] == "claimed"

    def test_progress_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/bad_progress/claim")

        assert resp.status_code == 404

    def test_not_completed_returns_400(self, client):
        in_progress = {**SAMPLE_PROGRESS, "status": "active", "current_value": 5}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=in_progress)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"].lower()

    def test_unauthorized_claim_returns_403(self, client):
        other_driver_progress = {**SAMPLE_PROGRESS, "driver_id": "other_driver"}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=other_driver_progress)

        with patch("routes.quests.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 403


class TestAdminCreateQuest:
    """POST /api/v1/quests/admin/create"""

    def test_admin_creates_quest(self, client):
        mock_db = make_mock_db()

        with patch("routes.quests.db", mock_db):
            resp = client.post(
                "/api/v1/quests/admin/create",
                json={
                    "title": "Weekend Sprint",
                    "description": "Complete 5 rides this weekend.",
                    "type": "ride_count",
                    "target_value": 5,
                    "reward_amount": 10.0,
                    "start_date": _PAST,
                    "end_date": _FUTURE,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Weekend Sprint"
        assert data["is_active"] is True
        assert "id" in data


class TestAdminListQuests:
    """GET /api/v1/quests/admin/list"""

    def test_returns_quest_list_with_stats(self, client):
        progress_rows = [
            {**SAMPLE_PROGRESS, "id": "p1", "status": "active"},
            {**SAMPLE_PROGRESS, "id": "p2", "status": "claimed"},
        ]
        mock_db = make_mock_db()
        # First call: quests list; subsequent calls: quest_progress per quest
        mock_db.get_rows = AsyncMock(side_effect=[[SAMPLE_QUEST], progress_rows])

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/admin/list")

        assert resp.status_code == 200
        quests = resp.json()
        assert len(quests) == 1
        stats = quests[0]["stats"]
        assert stats["total_participants"] == 2
        assert stats["claimed"] == 1

    def test_empty_list(self, client):
        mock_db = make_mock_db()
        mock_db.get_rows = AsyncMock(return_value=[])

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/admin/list")

        assert resp.status_code == 200
        assert resp.json() == []


class TestAdminUpdateQuest:
    """PATCH /api/v1/quests/admin/{quest_id}"""

    def test_update_quest_title(self, client):
        mock_db = make_mock_db()
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)

        with patch("routes.quests.db", mock_db):
            resp = client.patch(
                "/api/v1/quests/admin/quest_1",
                json={"title": "Updated Title"},
            )

        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"

    def test_quest_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.quests.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.patch(
                "/api/v1/quests/admin/bad_quest",
                json={"is_active": False},
            )

        assert resp.status_code == 404


class TestAdminGetParticipants:
    """GET /api/v1/quests/admin/{quest_id}/participants"""

    def test_returns_participants_list(self, client):
        mock_db = make_mock_db()
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.get_rows = AsyncMock(return_value=[SAMPLE_PROGRESS])
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.users.find_one = AsyncMock(return_value={"id": "user_123", "first_name": "Test", "last_name": "Driver"})

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/admin/quest_1/participants")

        assert resp.status_code == 200
        participants = resp.json()
        assert len(participants) == 1
        assert participants[0]["driver_name"] == "Test Driver"
        assert participants[0]["progress_pct"] == 100.0

    def test_quest_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.quests.find_one = AsyncMock(return_value=None)

        with patch("routes.quests.db", mock_db):
            resp = client.get("/api/v1/quests/admin/bad_quest/participants")

        assert resp.status_code == 404


class TestQuestTrackerOnRideComplete:
    """utils.quest_tracker.update_quest_progress_on_ride_complete — the hook the
    ride-completion paths (drivers.py::complete_ride, rides.py::rider_complete_ride)
    call so joined quests actually advance instead of sitting at 0%."""

    @pytest.mark.anyio
    async def test_ride_count_increments_and_completes(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        active_progress = {
            "id": "progress_1",
            "quest_id": "quest_1",
            "driver_id": "driver_123",
            "current_value": 9,  # one ride short of the target of 10
            "status": "active",
        }
        mock_db = make_mock_db()
        mock_db.get_rows = AsyncMock(return_value=[active_progress])
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        captured = {}

        async def _update_one(table, filters, update, **kwargs):
            captured["table"] = table
            captured["update"] = update
            return {"id": "progress_1"}

        mock_db.update_one = _update_one

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        assert captured["table"] == "quest_progress"
        changes = captured["update"]["$set"]
        assert changes["current_value"] == 10
        assert changes["status"] == "completed"
        assert "completed_at" in changes

    @pytest.mark.anyio
    async def test_no_active_progress_is_noop(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        mock_db = make_mock_db()
        mock_db.get_rows = AsyncMock(return_value=[])
        called = {"update": False}

        async def _update_one(*args, **kwargs):
            called["update"] = True

        mock_db.update_one = _update_one

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        assert called["update"] is False

    @staticmethod
    def _peak_setup(completed_at_utc):
        """Wire a peak_rides quest at current_value 2 and capture the update."""
        peak_quest = {**SAMPLE_QUEST, "id": "quest_peak", "type": "peak_rides", "target_value": 5.0}
        active_progress = {
            "id": "progress_peak",
            "quest_id": "quest_peak",
            "driver_id": "driver_123",
            "current_value": 2,
            "status": "active",
        }
        mock_db = make_mock_db()
        mock_db.get_rows = AsyncMock(return_value=[active_progress])
        mock_db.quests.find_one = AsyncMock(return_value=peak_quest)
        captured = {}

        async def _update_one(table, filters, update, **kwargs):
            captured["update"] = update
            return {"id": "progress_peak"}

        mock_db.update_one = _update_one
        # No service_area_id on the ride → tracker falls back to America/Regina (UTC-6).
        ride = {"id": "ride_1", "ride_completed_at": completed_at_utc, "service_area_id": None}
        return mock_db, ride, captured

    @pytest.mark.anyio
    async def test_peak_rides_counts_in_local_evening_peak(self):
        """23:30 UTC is 17:30 in America/Regina (5:30 PM) — inside the 5-8 PM
        peak window, so the quest advances even though the UTC hour (23) is not."""
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        mock_db, ride, captured = self._peak_setup("2026-06-21T23:30:00+00:00")
        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", ride)

        assert captured["update"]["$set"]["current_value"] == 3

    @pytest.mark.anyio
    async def test_peak_rides_ignores_utc_only_peak_hour(self):
        """08:30 UTC looks like a 7-9 AM peak in UTC, but it is 02:30 local in
        America/Regina — off-peak — so the quest must NOT advance."""
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        mock_db, ride, captured = self._peak_setup("2026-06-21T08:30:00+00:00")
        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", ride)

        assert captured["update"]["$set"]["current_value"] == 2
