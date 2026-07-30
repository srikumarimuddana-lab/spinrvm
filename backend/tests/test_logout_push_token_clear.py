"""Push-token detachment on /auth/logout.

Server-driven pushes (onboarding reminders, promos) target
`users.fcm_token*`, which logout historically left in place — so a
signed-out device kept receiving them. Clearing it is gated behind the
`logout_clears_push_token` app setting, DEFAULT OFF, because installed
binaries only register their FCM token once per app process.
"""

from unittest.mock import AsyncMock

import pytest

from routes.auth import _clear_push_token_on_logout, _push_token_columns_to_clear

DRIVER_ONLY = {"id": "u1", "is_driver": True, "is_rider": False, "fcm_token_driver": "tok-d", "fcm_token": "tok-d"}
DUAL_ROLE = {
    "id": "u2",
    "is_driver": True,
    "is_rider": True,
    "fcm_token_driver": "tok-d",
    "fcm_token_rider": "tok-r",
    "fcm_token": "tok-r",
}


class TestColumnSelection:
    def test_explicit_client_type_clears_only_that_surface(self):
        assert _push_token_columns_to_clear(DUAL_ROLE, "driver") == {"fcm_token_driver": None}

    def test_dual_role_driver_logout_preserves_rider_generic_token(self):
        """The rider app still owns the legacy `fcm_token` here (it holds
        tok-r), so a driver logout must not null it — that would silently kill
        the rider app's pushes for the same account."""
        updates = _push_token_columns_to_clear(DUAL_ROLE, "driver")
        assert "fcm_token" not in updates
        assert "fcm_token_rider" not in updates

    def test_generic_token_cleared_when_it_mirrors_the_cleared_surface(self):
        assert _push_token_columns_to_clear(DRIVER_ONLY, "driver") == {
            "fcm_token_driver": None,
            "fcm_token": None,
        }

    def test_missing_client_type_infers_from_role_flags(self):
        """Older builds don't send client_type — mirror register-token's
        inference rather than guessing."""
        assert _push_token_columns_to_clear(DRIVER_ONLY, None) == {
            "fcm_token_driver": None,
            "fcm_token": None,
        }

    def test_missing_client_type_on_dual_role_clears_both_surfaces(self):
        updates = _push_token_columns_to_clear(DUAL_ROLE, None)
        assert updates["fcm_token_driver"] is None
        assert updates["fcm_token_rider"] is None
        assert updates["fcm_token"] is None

    def test_unknown_client_type_falls_back_to_inference(self):
        assert _push_token_columns_to_clear(DRIVER_ONLY, "smartwatch") == {
            "fcm_token_driver": None,
            "fcm_token": None,
        }

    def test_no_token_on_file_is_a_no_op(self):
        assert _push_token_columns_to_clear({"id": "u3", "is_driver": True, "is_rider": False}, "driver") == {}


class TestFlagGating:
    @pytest.mark.asyncio
    async def test_flag_off_leaves_token_in_place(self, monkeypatch):
        """Default posture. Installed apps register their FCM token once per
        process, so clearing before that build ships would leave a driver who
        signs out and back in without restarting with no ride offers."""
        update_one = AsyncMock()
        monkeypatch.setattr("routes.auth.get_app_settings", AsyncMock(return_value={}))
        monkeypatch.setattr("routes.auth.db.update_one", update_one)

        await _clear_push_token_on_logout(DRIVER_ONLY, "driver")

        update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_on_clears_token(self, monkeypatch):
        update_one = AsyncMock()
        monkeypatch.setattr("routes.auth.get_app_settings", AsyncMock(return_value={"logout_clears_push_token": True}))
        monkeypatch.setattr("routes.auth.db.update_one", update_one)

        await _clear_push_token_on_logout(DRIVER_ONLY, "driver")

        update_one.assert_awaited_once_with("users", {"id": "u1"}, {"fcm_token_driver": None, "fcm_token": None})

    @pytest.mark.asyncio
    async def test_settings_failure_does_not_clear(self, monkeypatch):
        update_one = AsyncMock()
        monkeypatch.setattr("routes.auth.get_app_settings", AsyncMock(side_effect=RuntimeError("settings down")))
        monkeypatch.setattr("routes.auth.db.update_one", update_one)

        await _clear_push_token_on_logout(DRIVER_ONLY, "driver")

        update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_failure_does_not_propagate(self, monkeypatch):
        """The session is already revoked by this point — a token-clear
        failure must not turn a successful logout into a 500."""
        monkeypatch.setattr("routes.auth.get_app_settings", AsyncMock(return_value={"logout_clears_push_token": True}))
        monkeypatch.setattr("routes.auth.db.update_one", AsyncMock(side_effect=RuntimeError("db down")))

        await _clear_push_token_on_logout(DRIVER_ONLY, "driver")


class TestLegacyTokenOnly:
    """Rows written before migration 102 added the per-app columns have only
    the legacy `fcm_token`. It is still a live delivery target, so skipping it
    would defeat the point of clearing on logout (review finding L1)."""

    SINGLE_ROLE_LEGACY = {"id": "u4", "is_driver": True, "is_rider": False, "fcm_token": "legacy-tok"}
    DUAL_ROLE_LEGACY = {"id": "u5", "is_driver": True, "is_rider": True, "fcm_token": "legacy-tok"}

    def test_single_role_legacy_token_is_cleared(self):
        assert _push_token_columns_to_clear(self.SINGLE_ROLE_LEGACY, "driver") == {"fcm_token": None}

    def test_single_role_legacy_cleared_without_client_type(self):
        assert _push_token_columns_to_clear(self.SINGLE_ROLE_LEGACY, None) == {"fcm_token": None}

    def test_dual_role_legacy_token_is_left_alone(self):
        """Ambiguous: the token carries no client_type and both apps could be
        using it. Clearing it could silently kill the other app's pushes."""
        assert _push_token_columns_to_clear(self.DUAL_ROLE_LEGACY, "driver") == {}

    def test_no_token_at_all_stays_a_no_op(self):
        assert _push_token_columns_to_clear({"id": "u6", "is_driver": True, "is_rider": False}, "driver") == {}
