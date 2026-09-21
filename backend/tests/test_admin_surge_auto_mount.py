"""PUT .../service-areas/{id}/surge/auto (features.admin_reset_surge_to_auto) is
also mounted under /api/admin/ — the App-Check-exempt namespace the browser
admin dashboard can reach (/api/v1/ is App-Check-enforced in production, so the
dashboard's resetSurgeToAuto() could never succeed there). Gated on the
service_areas module like its /api/admin/service-areas siblings.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_USER = {"id": "user_123", "phone": "+1234567890", "role": "rider"}
SAMPLE_ADMIN = {"id": "admin_1", "role": "admin", "_admin_verified": True, "modules": ["service_areas"]}
AREA = {"id": "area_1", "surge_source": "manual", "surge_multiplier": 1.5}


@pytest.fixture
def client():
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: SAMPLE_USER
    app.dependency_overrides[dependencies.get_admin_user] = lambda: SAMPLE_ADMIN
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _mock_db(area):
    mock = MagicMock()
    mock.find_one = AsyncMock(return_value=area)
    mock.update_one = AsyncMock(return_value=None)
    return mock


@pytest.mark.parametrize("prefix", ["/api/admin", "/api/v1"])
def test_reset_surge_to_auto_served_at_both_prefixes(client, prefix):
    mock_db = _mock_db(AREA)

    with patch("features.db", mock_db):
        resp = client.put(f"{prefix}/service-areas/area_1/surge/auto")

    assert resp.status_code == 200
    update_set = mock_db.update_one.call_args.args[2]["$set"]
    assert update_set["surge_source"] == "auto"
    assert update_set["surge_multiplier"] == 1.0


def test_unknown_area_404s(client):
    with patch("features.db", _mock_db(None)):
        resp = client.put("/api/admin/service-areas/nope/surge/auto")

    assert resp.status_code == 404


def test_still_requires_admin(client):
    import dependencies
    from backend.server import app

    app.dependency_overrides.pop(dependencies.get_admin_user)
    resp = client.put("/api/admin/service-areas/area_1/surge/auto")

    assert resp.status_code == 403


def test_admin_path_requires_service_areas_module(client):
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_admin_user] = lambda: {**SAMPLE_ADMIN, "modules": ["drivers"]}
    with patch("features.db", _mock_db(AREA)) as mock_db:
        resp = client.put("/api/admin/service-areas/area_1/surge/auto")

    assert resp.status_code == 403
    mock_db.update_one.assert_not_called()


def test_admin_path_is_app_check_exempt():
    from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES

    assert any("/api/admin/service-areas/area_1/surge/auto".startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES)
