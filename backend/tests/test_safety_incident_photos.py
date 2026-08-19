"""Coverage for POST /safety/report/{incident_id}/photo.

Why this file exists: driver-app/app/report-safety.tsx has POSTed evidence
photos to this route since it shipped, but the route was never implemented —
routes/safety.py exposed only POST /report. The client wrapped each upload in
`catch {}` ("Photo upload failure is non-fatal"), so every safety evidence
photo a driver attached was silently discarded and nothing surfaced it.

These go through TestClient rather than calling the handler directly, so the
real multipart parse runs — the exact seam that let the missing route hide.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.unit

REPORTER = {"id": "user_1", "role": "driver", "is_driver": True, "phone": "+13061234567"}
OTHER_USER = {"id": "user_2", "role": "rider", "phone": "+13069998888"}

# A real 1x1 PNG so _resolve_upload_type() sniffs a genuine signature rather
# than trusting the (client-supplied, routinely wrong) declared content-type.
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

_INCIDENT = {"id": "inc_1", "reported_by_user_id": "user_1", "status": "open"}


def _client(user):
    from fastapi.testclient import TestClient

    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def reporter_client():
    from backend.server import app

    with _client(REPORTER) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def other_client():
    from backend.server import app

    with _client(OTHER_USER) as c:
        yield c
    app.dependency_overrides.clear()


def _mock_db(**overrides):
    m = MagicMock()
    m.get_rows = AsyncMock(return_value=[])
    m.insert_one = AsyncMock(return_value={"id": "photo_1"})

    async def _run_sync(fn):
        return fn()

    m.run_sync = AsyncMock(side_effect=_run_sync)
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def _mock_storage(upload_ok=True):
    bucket = MagicMock()
    if upload_ok:
        bucket.upload.return_value = {"path": "k"}
    else:
        bucket.upload.side_effect = RuntimeError("bucket unreachable")
    client = MagicMock()
    client.storage.from_.return_value = bucket
    return client, bucket


def _files():
    return {"file": ("evidence_0.jpg", _PNG_1X1, "image/jpeg")}


class TestSafetyIncidentPhotoUpload:
    def test_route_exists(self, reporter_client):
        """The regression guard for the original bug: this route 404'd at the
        ROUTER level, which the client could not distinguish from a missing
        incident. Anything other than 404-not-found proves it is mounted."""
        db = _mock_db(get_rows=AsyncMock(return_value=[_INCIDENT]))
        storage, _ = _mock_storage()
        with patch("routes.safety.db_supabase", db), patch("routes.safety.supabase", storage):
            r = reporter_client.post("/api/v1/safety/report/inc_1/photo", files=_files())
        assert r.status_code == 200

    def test_successful_upload_stores_object_and_row(self, reporter_client):
        db = _mock_db(get_rows=AsyncMock(side_effect=[[_INCIDENT], []]))
        storage, bucket = _mock_storage()
        with patch("routes.safety.db_supabase", db), patch("routes.safety.supabase", storage):
            r = reporter_client.post("/api/v1/safety/report/inc_1/photo", files=_files())

        assert r.status_code == 200
        assert r.json()["success"] is True

        bucket.upload.assert_called_once()
        saved = db.insert_one.await_args_list[0].args[1]
        assert saved["incident_id"] == "inc_1"
        assert saved["storage_key"].startswith("inc_1/")
        # Sniffed from the bytes, not the "image/jpeg" the client declared.
        assert saved["content_type"] == "image/png"
        assert saved["uploaded_by_user_id"] == "user_1"

    def test_unknown_incident_returns_404(self, reporter_client):
        db = _mock_db(get_rows=AsyncMock(return_value=[]))
        with patch("routes.safety.db_supabase", db):
            r = reporter_client.post("/api/v1/safety/report/nope/photo", files=_files())
        assert r.status_code == 404

    def test_non_reporter_cannot_attach(self, other_client):
        """Evidence can depict a third party — a user who guesses an incident
        id must not be able to attach to someone else's report."""
        db = _mock_db(get_rows=AsyncMock(return_value=[_INCIDENT]))
        with patch("routes.safety.db_supabase", db):
            r = other_client.post("/api/v1/safety/report/inc_1/photo", files=_files())
        assert r.status_code == 403
        db.insert_one.assert_not_awaited()

    def test_photo_cap_enforced(self, reporter_client):
        from routes.safety import MAX_INCIDENT_PHOTOS

        existing = [{"id": f"p{i}"} for i in range(MAX_INCIDENT_PHOTOS)]
        db = _mock_db(get_rows=AsyncMock(side_effect=[[_INCIDENT], existing]))
        with patch("routes.safety.db_supabase", db):
            r = reporter_client.post("/api/v1/safety/report/inc_1/photo", files=_files())
        assert r.status_code == 400
        db.insert_one.assert_not_awaited()

    def test_empty_file_rejected(self, reporter_client):
        db = _mock_db(get_rows=AsyncMock(side_effect=[[_INCIDENT], []]))
        with patch("routes.safety.db_supabase", db):
            r = reporter_client.post(
                "/api/v1/safety/report/inc_1/photo",
                files={"file": ("evidence_0.jpg", b"", "image/jpeg")},
            )
        assert r.status_code == 400
        db.insert_one.assert_not_awaited()

    def test_non_image_rejected(self, reporter_client):
        db = _mock_db(get_rows=AsyncMock(side_effect=[[_INCIDENT], []]))
        with patch("routes.safety.db_supabase", db):
            r = reporter_client.post(
                "/api/v1/safety/report/inc_1/photo",
                files={"file": ("notes.pdf", b"%PDF-1.4 not an image", "application/pdf")},
            )
        assert r.status_code == 400
        db.insert_one.assert_not_awaited()

    def test_storage_failure_surfaces_and_writes_no_row(self, reporter_client):
        """The whole point of this fix: a failed evidence upload must NOT be
        reported as success. Silently losing it is the original bug."""
        db = _mock_db(get_rows=AsyncMock(side_effect=[[_INCIDENT], []]))
        storage, _ = _mock_storage(upload_ok=False)
        with patch("routes.safety.db_supabase", db), patch("routes.safety.supabase", storage):
            r = reporter_client.post("/api/v1/safety/report/inc_1/photo", files=_files())
        assert r.status_code == 502
        db.insert_one.assert_not_awaited()
