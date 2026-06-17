"""Test for log-correlation fix: _extract_user_id must read `user_id` (telemetry).

Spinr JWTs carry the user id in `user_id`, not `sub`. Reading `sub` left
request.state.user_id (and every log line's user correlation) permanently None.
"""

from types import SimpleNamespace

import jwt
import pytest

pytestmark = pytest.mark.unit


def _req(token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(headers=headers)


def test_extract_user_id_reads_user_id_claim():
    from backend.core.middleware import _extract_user_id

    token = jwt.encode({"user_id": "u-123"}, "secret", algorithm="HS256")
    assert _extract_user_id(_req(token)) == "u-123"


def test_extract_user_id_falls_back_to_sub():
    from backend.core.middleware import _extract_user_id

    token = jwt.encode({"sub": "legacy-1"}, "secret", algorithm="HS256")
    assert _extract_user_id(_req(token)) == "legacy-1"


def test_extract_user_id_none_when_absent_or_unauth():
    from backend.core.middleware import _extract_user_id

    no_claims = jwt.encode({"role": "rider"}, "secret", algorithm="HS256")
    assert _extract_user_id(_req(no_claims)) is None
    assert _extract_user_id(_req(None)) is None  # no Authorization header
    assert _extract_user_id(SimpleNamespace(headers={"Authorization": "Basic xyz"})) is None
