"""Integration tests for the fare-split endpoints.

Routes under test (backend/routes/fare_split.py):
  POST /api/v1/fare-split                              — create fare split
  GET  /api/v1/fare-split/{split_id}                  — get split details
  GET  /api/v1/fare-split/ride/{ride_id}               — get split for a ride
  POST /api/v1/fare-split/participant/{id}/respond     — accept/decline invitation
  POST /api/v1/fare-split/participant/{id}/pay         — pay share
  POST /api/v1/fare-split/{split_id}/cancel            — cancel split
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Phone numbers in +1XXXXXXXXXX format required by the route validator
PHONE_PARTICIPANT_1 = "+19876543210"
PHONE_PARTICIPANT_2 = "+11112223333"
PHONE_PARTICIPANT_3 = "+12223334444"

SAMPLE_USER = {"id": "user_123", "phone": "+12345678901", "role": "rider", "is_driver": False}

SAMPLE_RIDE = {
    "id": "ride_123",
    "rider_id": "user_123",
    "status": "completed",
    "total_fare": 30.0,
    "grand_total": 30.0,
}

SAMPLE_SPLIT = {
    "id": "split_1",
    "ride_id": "ride_123",
    "requester_id": "user_123",
    "total_fare": 30.0,
    "split_count": 2,
    "status": "pending",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

SAMPLE_PARTICIPANT = {
    "id": "part_1",
    "fare_split_id": "split_1",
    "user_id": "user_456",
    "phone": PHONE_PARTICIPANT_1,
    "share_amount": 15.0,
    "status": "accepted",
    "created_at": "2026-01-01T00:00:00",
}


def make_mock_db():
    """Build a mock that mirrors the db_supabase flat-function interface.

    Routes call ``await db.find_one("rides", {...})``, not
    ``await db.rides.find_one({...})``.  We expose per-table sub-mocks as
    attributes so individual tests can override them with::

        mock_db.rides.find_one = AsyncMock(return_value=SAMPLE_RIDE)

    The top-level ``find_one`` / ``get_rows`` / ``insert_one`` / ``update_one``
    AsyncMocks dispatch to the per-table attribute by table name.
    """
    mock = MagicMock()

    # Per-table sub-mocks (attributes used for per-test overrides)
    _tables = (
        "rides",
        "fare_splits",
        "fare_split_participants",
        "users",
        "wallets",
        "wallet_transactions",
    )
    for tbl in _tables:
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, tbl, col_mock)

    # Default get_rows at top level (table-agnostic)
    mock.get_rows = AsyncMock(return_value=[])

    # Dispatcher: routes call db.find_one("rides", ...) — delegate to per-table mock
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

    # RPC stubs used by pay_split_share
    mock.fare_split_pay_share = AsyncMock(return_value=35.0)
    mock.wallet_increment_balance = AsyncMock(return_value=None)

    return mock


@pytest.fixture
def client():
    import dependencies  # same module the routes use (routes use relative '..dependencies')
    from backend.server import app  # ensures server.py sys.path setup runs first

    app.dependency_overrides[dependencies.get_current_user] = lambda: SAMPLE_USER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestCreateFareSplit:
    """POST /api/v1/fare-split"""

    def test_create_split_success(self, client):
        participant_user = {"id": "user_456", "phone": PHONE_PARTICIPANT_1}
        mock_db = make_mock_db()
        mock_db.rides.find_one = AsyncMock(return_value=SAMPLE_RIDE)
        mock_db.fare_splits.find_one = AsyncMock(return_value=None)
        mock_db.users.find_one = AsyncMock(return_value=participant_user)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split",
                json={"ride_id": "ride_123", "participant_phones": [PHONE_PARTICIPANT_1]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ride_id"] == "ride_123"
        assert data["total_fare"] == "30.00"
        assert data["split_count"] == 2  # 1 participant + requester
        assert data["your_share"] == "15.00"
        assert len(data["participants"]) == 1

    def test_ride_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.rides.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split",
                json={"ride_id": "bad_ride", "participant_phones": [PHONE_PARTICIPANT_1]},
            )

        assert resp.status_code == 404

    def test_not_ride_requester_returns_403(self, client):
        other_ride = {**SAMPLE_RIDE, "rider_id": "other_user"}
        mock_db = make_mock_db()
        mock_db.rides.find_one = AsyncMock(return_value=other_ride)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split",
                json={"ride_id": "ride_123", "participant_phones": [PHONE_PARTICIPANT_1]},
            )

        assert resp.status_code == 403
        assert "requester" in resp.json()["detail"].lower()

    def test_split_already_exists_returns_400(self, client):
        mock_db = make_mock_db()
        mock_db.rides.find_one = AsyncMock(return_value=SAMPLE_RIDE)
        mock_db.fare_splits.find_one = AsyncMock(return_value=SAMPLE_SPLIT)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split",
                json={"ride_id": "ride_123", "participant_phones": [PHONE_PARTICIPANT_1]},
            )

        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"].lower()

    def test_three_way_split_calculates_shares(self, client):
        """$30 split 3 ways = $10 each."""
        mock_db = make_mock_db()
        mock_db.rides.find_one = AsyncMock(return_value=SAMPLE_RIDE)
        mock_db.fare_splits.find_one = AsyncMock(return_value=None)
        mock_db.users.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split",
                json={"ride_id": "ride_123", "participant_phones": [PHONE_PARTICIPANT_2, PHONE_PARTICIPANT_3]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["split_count"] == 3
        assert data["your_share"] == "10.00"


class TestGetFareSplit:
    """GET /api/v1/fare-split/{split_id}"""

    def test_requester_can_view_split(self, client):
        participants = [{**SAMPLE_PARTICIPANT, "user_id": "user_456"}]
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=SAMPLE_SPLIT)
        mock_db.get_rows = AsyncMock(return_value=participants)

        with patch("routes.fare_split.db", mock_db):
            resp = client.get("/api/v1/fare-split/split_1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "split_1"
        assert data["total_fare"] == "30.00"
        assert len(data["participants"]) == 1

    def test_split_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.get("/api/v1/fare-split/bad_split")

        assert resp.status_code == 404

    def test_unauthorized_user_returns_403(self, client):
        """A user who is neither requester nor participant cannot view."""
        other_split = {**SAMPLE_SPLIT, "requester_id": "other_user"}
        participants = [{"id": "p1", "user_id": "third_party", "share_amount": 15.0, "status": "pending"}]
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=other_split)
        mock_db.get_rows = AsyncMock(return_value=participants)

        with patch("routes.fare_split.db", mock_db):
            resp = client.get("/api/v1/fare-split/split_1")

        assert resp.status_code == 403


class TestGetFareSplitForRide:
    """GET /api/v1/fare-split/ride/{ride_id}"""

    def test_no_split_returns_has_split_false(self, client):
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.get("/api/v1/fare-split/ride/ride_123")

        assert resp.status_code == 200
        assert resp.json()["has_split"] is False

    def test_existing_split_returned(self, client):
        participants = [{"id": "p1", "phone": PHONE_PARTICIPANT_1, "share_amount": 15.0, "status": "pending"}]
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=SAMPLE_SPLIT)
        mock_db.get_rows = AsyncMock(return_value=participants)

        with patch("routes.fare_split.db", mock_db):
            resp = client.get("/api/v1/fare-split/ride/ride_123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_split"] is True
        assert data["split"]["id"] == "split_1"
        assert data["split"]["your_share"] == "15.00"


class TestRespondToSplit:
    """POST /api/v1/fare-split/participant/{id}/respond"""

    def test_accept_invitation(self, client):
        pending_participant = {**SAMPLE_PARTICIPANT, "user_id": "user_123", "status": "pending"}
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=pending_participant)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/respond",
                json={"action": "accept"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_decline_invitation_recalculates_shares(self, client):
        pending_participant = {**SAMPLE_PARTICIPANT, "user_id": "user_123", "status": "pending"}
        remaining = [{"id": "p2", "user_id": "user_789", "status": "pending", "share_amount": 15.0}]
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=pending_participant)
        mock_db.fare_splits.find_one = AsyncMock(return_value=SAMPLE_SPLIT)
        mock_db.get_rows = AsyncMock(return_value=[pending_participant, *remaining])

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/respond",
                json={"action": "decline"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "declined"

    def test_participant_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/bad_part/respond",
                json={"action": "accept"},
            )

        assert resp.status_code == 404

    def test_unauthorized_respond_returns_403(self, client):
        other_participant = {**SAMPLE_PARTICIPANT, "user_id": "other_user"}
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=other_participant)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/respond",
                json={"action": "accept"},
            )

        assert resp.status_code == 403

    def test_already_responded_returns_400(self, client):
        already_accepted = {**SAMPLE_PARTICIPANT, "user_id": "user_123", "status": "accepted"}
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=already_accepted)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/respond",
                json={"action": "accept"},
            )

        assert resp.status_code == 400


class TestPaySplitShare:
    """POST /api/v1/fare-split/participant/{id}/pay"""

    def test_pay_via_wallet(self, client):
        wallet = {"id": "wallet_1", "user_id": "user_123", "balance": 50.0, "is_active": True}
        participant = {**SAMPLE_PARTICIPANT, "user_id": "user_123"}
        split = SAMPLE_SPLIT
        remaining = [participant]
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=participant)
        mock_db.wallets.find_one = AsyncMock(return_value=wallet)
        mock_db.fare_splits.find_one = AsyncMock(return_value=split)
        mock_db.get_rows = AsyncMock(return_value=remaining)

        # pay_split_share imports get_or_create_wallet from routes.wallet,
        # so both modules' db bindings need to be mocked.
        with patch("routes.fare_split.db", mock_db), patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/pay",
                json={"payment_method": "wallet"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paid"
        assert data["share_amount"] == "15.00"

    def test_insufficient_wallet_balance_returns_400(self, client):
        empty_wallet = {"id": "wallet_1", "user_id": "user_123", "balance": 1.0, "is_active": True}
        participant = {**SAMPLE_PARTICIPANT, "user_id": "user_123"}
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=participant)
        mock_db.wallets.find_one = AsyncMock(return_value=empty_wallet)
        mock_db.fare_split_pay_share = AsyncMock(side_effect=ValueError("insufficient_funds"))

        with patch("routes.fare_split.db", mock_db), patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/pay",
                json={"payment_method": "wallet"},
            )

        assert resp.status_code == 400
        assert "insufficient" in resp.json()["detail"].lower()

    def test_must_accept_before_paying(self, client):
        pending_participant = {**SAMPLE_PARTICIPANT, "user_id": "user_123", "status": "pending"}
        mock_db = make_mock_db()
        mock_db.fare_split_participants.find_one = AsyncMock(return_value=pending_participant)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post(
                "/api/v1/fare-split/participant/part_1/pay",
                json={"payment_method": "wallet"},
            )

        assert resp.status_code == 400
        assert "accept" in resp.json()["detail"].lower()


class TestCancelFareSplit:
    """POST /api/v1/fare-split/{split_id}/cancel"""

    def test_requester_can_cancel(self, client):
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=SAMPLE_SPLIT)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post("/api/v1/fare-split/split_1/cancel")

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_split_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=None)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post("/api/v1/fare-split/bad_split/cancel")

        assert resp.status_code == 404

    def test_non_requester_cannot_cancel(self, client):
        other_split = {**SAMPLE_SPLIT, "requester_id": "other_user"}
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=other_split)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post("/api/v1/fare-split/split_1/cancel")

        assert resp.status_code == 403

    def test_completed_split_cannot_be_cancelled(self, client):
        completed_split = {**SAMPLE_SPLIT, "status": "completed"}
        mock_db = make_mock_db()
        mock_db.fare_splits.find_one = AsyncMock(return_value=completed_split)

        with patch("routes.fare_split.db", mock_db):
            resp = client.post("/api/v1/fare-split/split_1/cancel")

        assert resp.status_code == 400
        assert "completed" in resp.json()["detail"].lower()
