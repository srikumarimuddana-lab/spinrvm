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
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FUTURE = (datetime.utcnow() + timedelta(days=30)).isoformat()
_PAST = (datetime.utcnow() - timedelta(days=1)).isoformat()
_NOW = datetime.utcnow().isoformat()

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
    mock = MagicMock()
    mock.get_rows = AsyncMock(return_value=[])
    for col in ("drivers", "quests", "quest_progress", "wallets", "wallet_transactions", "users"):
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, col, col_mock)
    return mock


@pytest.fixture
def client():
    from backend.server import app  # ensures server.py sys.path setup runs first
    import dependencies  # same module the routes use (routes use relative '..dependencies')

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
        wallet = {"id": "wallet_1", "user_id": "user_123", "balance": 0.0, "is_active": True}
        mock_db = make_mock_db()
        mock_db.drivers.find_one = AsyncMock(return_value=SAMPLE_DRIVER)
        mock_db.quest_progress.find_one = AsyncMock(return_value=SAMPLE_PROGRESS)
        mock_db.quests.find_one = AsyncMock(return_value=SAMPLE_QUEST)
        mock_db.wallets.find_one = AsyncMock(return_value=wallet)

        # claim_quest_reward imports get_or_create_wallet from routes.wallet,
        # so both modules' db bindings need to be mocked.
        with patch("routes.quests.db", mock_db), patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/quests/progress/progress_1/claim")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "claimed"
        assert data["reward_amount"] == 25.0

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
        mock_db.users.find_one = AsyncMock(
            return_value={"id": "user_123", "first_name": "Test", "last_name": "Driver"}
        )

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
