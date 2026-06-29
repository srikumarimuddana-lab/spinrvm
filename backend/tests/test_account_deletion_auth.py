"""Account-deletion (PIPEDA) auth enforcement + reactivation token.

Core security guarantees of the "block + self-serve reactivate" flow:
  - a pending_deletion / deleted / deleted_at account is refused by the auth guard
    (so a deletion-requested account cannot be used on any device, even with a
    previously-issued token — the bug was that login just signed the user out but
    let them straight back in).
  - the single-purpose reactivation token round-trips, cannot be used as a normal
    access token, and expires.
"""

from datetime import datetime, timedelta, timezone

import jwt as _jwt
import pytest
from fastapi import HTTPException

try:
    from backend import dependencies as deps
except ImportError:  # pragma: no cover - bare-path test runs
    import dependencies as deps


def test_account_inaccessible_predicate():
    assert deps._account_inaccessible({"status": "pending_deletion"})
    assert deps._account_inaccessible({"status": "deleted"})
    assert deps._account_inaccessible({"status": "active", "deleted_at": "2026-01-01T00:00:00Z"})
    assert not deps._account_inaccessible({"status": "active"})
    assert not deps._account_inaccessible({"status": "active", "deleted_at": None})
    assert not deps._account_inaccessible({})  # missing status → treated as usable


def test_enforce_account_active_blocks_deleted_accounts():
    for status in ("pending_deletion", "deleted"):
        with pytest.raises(HTTPException) as ei:
            deps._enforce_account_active({"status": status})
        assert ei.value.status_code == 403
        assert ei.value.detail == "ERR_ACCOUNT_DELETED"
    # An active account passes through unharmed.
    deps._enforce_account_active({"status": "active"})


def test_reactivation_token_roundtrips():
    tok = deps.create_reactivation_token("u1", "+15551234567")
    assert deps.verify_reactivation_token(tok) == "u1"


def test_normal_access_token_is_not_accepted_as_reactivation_token():
    access = deps.create_jwt_token("u1", "+15551234567")
    with pytest.raises(HTTPException) as ei:
        deps.verify_reactivation_token(access)
    assert ei.value.status_code == 401


def test_reactivation_token_carries_purpose_claim():
    tok = deps.create_reactivation_token("u1", "+15551234567")
    payload = _jwt.decode(tok, deps.settings.JWT_SECRET, algorithms=[deps.JWT_ALGORITHM], options={"verify_aud": False})
    assert payload["purpose"] == deps._REACTIVATION_PURPOSE


def test_expired_reactivation_token_rejected():
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": "u1",
        "phone": "+1",
        "aud": deps.JWT_AUD_MOBILE,
        "purpose": deps._REACTIVATION_PURPOSE,
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    expired = _jwt.encode(payload, deps.settings.JWT_SECRET, algorithm=deps.JWT_ALGORITHM)
    with pytest.raises(HTTPException) as ei:
        deps.verify_reactivation_token(expired)
    assert ei.value.status_code == 401
    assert ei.value.detail == "ERR_REACTIVATION_EXPIRED"
