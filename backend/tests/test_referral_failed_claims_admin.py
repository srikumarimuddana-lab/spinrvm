"""Admin failed-referral-claim re-queue endpoint (bug #5 → side-aware re-credit).

The endpoint now delegates to referral_payout.recredit_failed_claim, which credits
ONLY the side(s) not already paid (no double-credit on an already-paid referrer).
Pin the endpoint contract: 404 when there's no failed claim, and a successful
re-credit is audit-logged with the claim id + which sides were credited.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

try:
    from backend.routes.admin import drivers as admin_drivers
    from backend.utils.referral_payout import ReferralClaimNotFound
except ImportError:  # pragma: no cover - bare-path test runs
    from routes.admin import drivers as admin_drivers
    from utils.referral_payout import ReferralClaimNotFound


def test_requeue_404_when_no_failed_claim(monkeypatch):
    monkeypatch.setattr(admin_drivers, "recredit_failed_claim", AsyncMock(side_effect=ReferralClaimNotFound("rf1")))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(admin_drivers.admin_requeue_failed_referral("rf1", admin={"id": "a1"}))
    assert ei.value.status_code == 404


def test_requeue_recredits_and_audits(monkeypatch):
    recredit = AsyncMock(
        return_value={
            "id": "claim1",
            "referee_user_id": "rf1",
            "kind": "rider",
            "credited": ["referee"],
            "status": "paid",
        }
    )
    audit = AsyncMock()
    monkeypatch.setattr(admin_drivers, "recredit_failed_claim", recredit)
    monkeypatch.setattr(admin_drivers, "log_admin_action", audit)

    out = asyncio.run(admin_drivers.admin_requeue_failed_referral("rf1", admin={"id": "a1"}))
    assert out["success"] is True
    assert out["credited"] == ["referee"]
    recredit.assert_awaited_once_with("rf1")
    # Audited against the claim id, recording which sides were credited.
    audit.assert_awaited_once()
    args = audit.await_args.args
    assert args[2] == "referral_payouts"
    assert args[3] == "claim1"
    assert args[4]["credited"] == ["referee"]
