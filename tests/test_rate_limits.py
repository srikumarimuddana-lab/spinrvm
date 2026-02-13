import os
import types
from unittest.mock import AsyncMock

import pytest
try:
    from fastapi.testclient import TestClient
except Exception:
    pytest.skip("fastapi not installed; skipping rate-limit tests", allow_module_level=True)

# Ensure env vars exist for import-time DB client creation (no Mongo required)


@pytest.fixture(autouse=True)
def mock_db(monkeypatch):
    """Replace the `db` used in backend.server with simple async mocks for collections."""
    import backend.server as server

    class DummyColl:
        def __init__(self):
            self.delete_many = AsyncMock(return_value=None)
            self.insert_one = AsyncMock(return_value=None)
            self.find_one = AsyncMock(return_value=None)
            self.update_one = AsyncMock(return_value=None)
            self.find = AsyncMock(return_value=[])
            self.count_documents = AsyncMock(return_value=0)
            self.to_list = AsyncMock(return_value=[])

    dummy_db = types.SimpleNamespace()
    dummy_db.otps = DummyColl()
    dummy_db.otp_records = DummyColl()
    dummy_db.users = DummyColl()
    dummy_db.drivers = DummyColl()
    dummy_db.rides = DummyColl()
    dummy_db.vehicle_types = DummyColl()
    dummy_db.fare_configs = DummyColl()
    dummy_db.service_areas = DummyColl()
    dummy_db.settings = DummyColl()

    monkeypatch.setattr(server, 'db', dummy_db)
    return


def test_send_otp_rate_limit():
    import backend.server as server

    client = TestClient(server.app)

    # Allowed 5 per minute; send 5 successful requests
    for i in range(5):
        res = client.post('/api/auth/send-otp', json={'phone': '1234567890'})
        assert res.status_code == 200, f"Expected 200, got {res.status_code} on try {i}"

    # 6th should be rate-limited
    res = client.post('/api/auth/send-otp', json={'phone': '1234567890'})
    assert res.status_code == 429, f"Expected 429 on rate limit, got {res.status_code}"


def test_verify_otp_rate_limit():
    import backend.server as server

    client = TestClient(server.app)

    # Allowed 10 per minute; send 10 requests which may be invalid but are counted
    for i in range(10):
        res = client.post('/api/auth/verify-otp', json={'phone': '1234567890', 'code': '0000'})
        assert res.status_code in (200, 400), f"Unexpected status {res.status_code} on try {i}"

    # 11th should be rate-limited
    res = client.post('/api/auth/verify-otp', json={'phone': '1234567890', 'code': '0000'})
    assert res.status_code == 429, f"Expected 429 on rate limit, got {res.status_code}"
