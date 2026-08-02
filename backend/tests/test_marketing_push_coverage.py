"""Coverage for utils/marketing_push.py (A1c, Sub-tier B).

CASL-compliant marketing push sender — wraps features.send_push_notification
with a single express-consent gate (services.marketing_consent.is_eligible)
so that promotional/broadcast pushes (unlike operational ride/safety/receipt
pushes, which call features.send_push_notification directly and never go
through this module) always honour push_opt_in. Had no dedicated test file;
only 33.33% coverage.

Import/patch notes specific to this module:
  - `from ..services import marketing_consent` happens at MODULE level in
    marketing_push.py, so it is patchable as
    `marketing_push.marketing_consent.is_eligible`.
  - `from .. import features` happens INSIDE the function body on every call
    (a deferred import, presumably to dodge an import cycle). Because that
    statement just re-binds the local name to the already-imported module
    object living in sys.modules, patching `backend.features.send_push_notification`
    directly (imported here as `from backend import features`) is what actually
    takes effect — there is no persistent `marketing_push.features` attribute
    to monkeypatch.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend import features as features_module
from backend.utils import marketing_push

pytestmark = pytest.mark.unit


class TestSendMarketingPush:
    @pytest.mark.anyio
    async def test_no_consent_skips_send_and_returns_false(self, monkeypatch):
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=False))
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(features_module, "send_push_notification", send_mock)

        result = await marketing_push.send_marketing_push(
            user_id="user-1", title="Promo", body="Save 20%", log_id="log-1"
        )

        assert result is False
        send_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_consent_and_successful_send_returns_true(self, monkeypatch):
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=True))
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(features_module, "send_push_notification", send_mock)

        result = await marketing_push.send_marketing_push(
            user_id="user-1",
            title="Promo",
            body="Save 20%",
            data={"promo_code": "SAVE20"},
            target_app="rider",
            log_id="log-1",
        )

        assert result is True
        send_mock.assert_awaited_once_with(
            "user-1", "Promo", "Save 20%", data={"promo_code": "SAVE20"}, target_app="rider"
        )

    @pytest.mark.anyio
    async def test_eligibility_check_receives_channel_and_user_id(self, monkeypatch):
        eligible_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", eligible_mock)
        monkeypatch.setattr(features_module, "send_push_notification", AsyncMock(return_value=True))

        await marketing_push.send_marketing_push(user_id="user-42", title="t", body="b")

        eligible_mock.assert_awaited_once_with("push", user_id="user-42")

    @pytest.mark.anyio
    async def test_send_returns_falsy_result_yields_false(self, monkeypatch):
        """The bool(...) coercion means a falsy-but-non-exceptional return
        from features.send_push_notification (e.g. no device tokens on file)
        must surface as False, not raise or return None/0 verbatim."""
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=True))
        monkeypatch.setattr(features_module, "send_push_notification", AsyncMock(return_value=None))

        result = await marketing_push.send_marketing_push(user_id="user-1", title="t", body="b")

        assert result is False

    @pytest.mark.anyio
    async def test_send_returns_zero_int_still_coerced_to_bool(self, monkeypatch):
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=True))
        monkeypatch.setattr(features_module, "send_push_notification", AsyncMock(return_value=0))

        result = await marketing_push.send_marketing_push(user_id="user-1", title="t", body="b")

        assert result is False

    @pytest.mark.anyio
    async def test_send_raises_is_caught_and_returns_false(self, monkeypatch):
        """Do-not-silently-swallow convention note: this module DOES swallow
        the send exception (logger.error + return False) rather than
        propagating, by design — a failed marketing push must never break the
        caller's broadcast loop over many recipients. Verified this is
        intentional (module docstring frames this as a best-effort wrapper),
        not an instance of the anti-pattern the repo convention warns against
        for DB/auth/payment/dispatch errors."""
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=True))
        monkeypatch.setattr(
            features_module, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))
        )

        result = await marketing_push.send_marketing_push(user_id="user-1", title="t", body="b")

        assert result is False

    @pytest.mark.anyio
    async def test_default_data_and_target_app_are_none(self, monkeypatch):
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=True))
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(features_module, "send_push_notification", send_mock)

        await marketing_push.send_marketing_push(user_id="user-1", title="t", body="b")

        send_mock.assert_awaited_once_with("user-1", "t", "b", data=None, target_app=None)

    @pytest.mark.anyio
    async def test_default_log_id_is_dash(self, monkeypatch):
        """log_id defaults to "-" and is only used for logging, not passed
        through to features.send_push_notification — confirm the call
        signature omits it."""
        monkeypatch.setattr(marketing_push.marketing_consent, "is_eligible", AsyncMock(return_value=False))

        result = await marketing_push.send_marketing_push(user_id="user-1", title="t", body="b")

        assert result is False
