"""Coverage for utils/analytics.py (A1c, Sub-tier B).

Mixpanel/Amplitude analytics wrapper. Had no dedicated test file; only
22.70% coverage. Neither `mixpanel` nor `amplitude` packages are installed
in this environment, so the "configured successfully" branches are
exercised by injecting fake modules into `sys.modules` before constructing
the service — the same technique test_ws_health.py already uses for
stubbing optional/heavy imports.

Test-only change — no application code modified.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ── MixpanelService ──────────────────────────────────────────────────────


class TestMixpanelServiceUnconfigured:
    def test_empty_token_is_unconfigured_mock_mode(self):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="")
        assert svc.configured is False

    def test_missing_package_falls_back_to_mock_mode(self, monkeypatch):
        """mixpanel is not installed in this environment — a token alone
        must degrade gracefully, not raise."""
        monkeypatch.delitem(sys.modules, "mixpanel", raising=False)
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="real-token")
        assert svc.configured is False

    def test_unconfigured_track_returns_true_without_client(self):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="")
        assert svc.track("user-1", "Event") is True

    def test_unconfigured_people_set_returns_true(self):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="")
        assert svc.people_set("user-1", {"plan": "plus"}) is True

    def test_unconfigured_people_increment_returns_true(self):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="")
        assert svc.people_increment("user-1", "trips", 1) is True

    def test_unconfigured_alias_returns_true(self):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="")
        assert svc.alias("anon-1", "user-1") is True


class TestMixpanelServiceConfigured:
    @pytest.fixture
    def fake_mixpanel_module(self, monkeypatch):
        fake_client = MagicMock()
        fake_module = MagicMock()
        fake_module.Mixpanel.return_value = fake_client
        monkeypatch.setitem(sys.modules, "mixpanel", fake_module)
        return fake_client

    def test_configured_when_package_available(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="real-token")
        assert svc.configured is True

    def test_track_calls_client_and_returns_true(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        svc = MixpanelService(token="real-token")
        assert svc.track("user-1", "Event", {"a": 1}) is True
        fake_mixpanel_module.track.assert_called_once_with("user-1", "Event", {"a": 1})

    def test_track_exception_returns_false(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        fake_mixpanel_module.track.side_effect = RuntimeError("boom")
        svc = MixpanelService(token="real-token")
        assert svc.track("user-1", "Event") is False

    def test_people_set_exception_returns_false(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        fake_mixpanel_module.people_set.side_effect = RuntimeError("boom")
        svc = MixpanelService(token="real-token")
        assert svc.people_set("user-1", {}) is False

    def test_people_increment_exception_returns_false(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        fake_mixpanel_module.people_increment.side_effect = RuntimeError("boom")
        svc = MixpanelService(token="real-token")
        assert svc.people_increment("user-1", "trips", 1) is False

    def test_alias_exception_returns_false(self, fake_mixpanel_module):
        from backend.utils.analytics import MixpanelService

        fake_mixpanel_module.alias.side_effect = RuntimeError("boom")
        svc = MixpanelService(token="real-token")
        assert svc.alias("anon-1", "user-1") is False


# ── AmplitudeService ─────────────────────────────────────────────────────


class TestAmplitudeServiceUnconfigured:
    def test_empty_key_is_unconfigured_mock_mode(self):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="")
        assert svc.configured is False

    def test_missing_package_falls_back_to_mock_mode(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "amplitude", raising=False)
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="real-key")
        assert svc.configured is False

    def test_unconfigured_track_returns_true(self):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="")
        assert svc.track("user-1", "Event") is True

    def test_unconfigured_identify_returns_true(self):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="")
        assert svc.identify("user-1", {"plan": "plus"}) is True

    def test_unconfigured_group_identify_returns_true(self):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="")
        assert svc.group_identify("company", "acme-corp", {"size": "50"}) is True


class TestAmplitudeServiceConfigured:
    @pytest.fixture
    def fake_amplitude_module(self, monkeypatch):
        fake_client = MagicMock()
        fake_module = MagicMock()
        fake_module.Amplitude.return_value = fake_client
        fake_module.BaseEvent = MagicMock()
        fake_module.Identify = MagicMock()
        monkeypatch.setitem(sys.modules, "amplitude", fake_module)
        return fake_client

    def test_configured_when_package_available(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="real-key")
        assert svc.configured is True
        fake_amplitude_module.init.assert_called_once_with("real-key")

    def test_track_calls_client_and_returns_true(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="real-key")
        assert svc.track("user-1", "Event", {"a": 1}) is True
        fake_amplitude_module.track.assert_called_once()

    def test_track_exception_returns_false(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        fake_amplitude_module.track.side_effect = RuntimeError("boom")
        svc = AmplitudeService(api_key="real-key")
        assert svc.track("user-1", "Event") is False

    def test_identify_calls_client_and_returns_true(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="real-key")
        assert svc.identify("user-1", {"plan": "plus"}) is True

    def test_identify_exception_returns_false(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        fake_amplitude_module.track.side_effect = RuntimeError("boom")
        svc = AmplitudeService(api_key="real-key")
        assert svc.identify("user-1", {"plan": "plus"}) is False

    def test_group_identify_calls_client_and_returns_true(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        svc = AmplitudeService(api_key="real-key")
        assert svc.group_identify("company", "acme-corp", {"size": "50"}) is True

    def test_group_identify_exception_returns_false(self, fake_amplitude_module):
        from backend.utils.analytics import AmplitudeService

        fake_amplitude_module.track.side_effect = RuntimeError("boom")
        svc = AmplitudeService(api_key="real-key")
        assert svc.group_identify("company", "acme-corp", {"size": "50"}) is False


# ── AnalyticsService ─────────────────────────────────────────────────────


class TestAnalyticsService:
    def test_both_providers_disabled_are_none(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        assert svc.mixpanel is None
        assert svc.amplitude is None

    def test_track_returns_true_when_mock_mode_providers_succeed(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=True, enable_amplitude=True)
        assert svc.track("user-1", "Event", {"a": 1}) is True

    def test_track_returns_false_when_both_providers_disabled(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        assert svc.track("user-1", "Event") is False

    def test_identify_user_delegates_to_both_providers(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=True, enable_amplitude=True)
        assert svc.identify_user("user-1", {"plan": "plus"}) is True

    def test_track_ride_requested_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", (uid, name, props)) or True)
        svc.track_ride_requested("user-1", {"ride_id": "r1"})
        assert captured["call"] == ("user-1", "Ride Requested", {"ride_id": "r1"})

    def test_track_ride_completed_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_ride_completed("user-1", {})
        assert captured["call"] == "Ride Completed"

    def test_track_ride_cancelled_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_ride_cancelled("user-1", {})
        assert captured["call"] == "Ride Cancelled"

    def test_track_payment_processed_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_payment_processed("user-1", {})
        assert captured["call"] == "Payment Processed"

    def test_track_signup_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_signup("user-1", {})
        assert captured["call"] == "User Signup"

    def test_track_login_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_login("user-1", {})
        assert captured["call"] == "User Login"

    def test_track_driver_offline_uses_correct_event_name(self, monkeypatch):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        captured = {}
        monkeypatch.setattr(svc, "track", lambda uid, name, props=None: captured.setdefault("call", name) or True)
        svc.track_driver_offline("driver-1")
        assert captured["call"] == "Driver Offline"


class TestTrackDriverOnlineGeohashValidation:
    """PIPEDA guard (ACTION_ITEMS B1): raw GPS must never reach third-party
    analytics — only a geohash string is accepted."""

    def test_valid_geohash_succeeds(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        assert svc.track_driver_online("driver-1", "c3v3q") is False  # both providers disabled -> False, but no raise

    def test_non_string_raises_type_error(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        with pytest.raises(TypeError):
            svc.track_driver_online("driver-1", {"lat": 52.13, "lng": -106.66})

    def test_empty_string_raises_value_error(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        with pytest.raises(ValueError):
            svc.track_driver_online("driver-1", "")

    def test_stringified_coordinates_raise_value_error(self):
        """The exact PIPEDA-motivating case: a lat/lng string must be
        rejected, not silently forwarded as if it were a geohash."""
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        with pytest.raises(ValueError):
            svc.track_driver_online("driver-1", "52.13,-106.66")

    def test_uppercase_geohash_is_accepted_case_insensitively(self):
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        # Must not raise — uppercase input is lowercased before validation.
        svc.track_driver_online("driver-1", "C3V3Q")

    def test_invalid_geohash_characters_raise_value_error(self):
        """'a', 'i', 'l', 'o' are excluded from the geohash base-32 alphabet."""
        from backend.utils.analytics import AnalyticsService

        svc = AnalyticsService(enable_mixpanel=False, enable_amplitude=False)
        with pytest.raises(ValueError):
            svc.track_driver_online("driver-1", "abcdefail")


class TestGlobalAnalyticsSingleton:
    def test_get_analytics_creates_singleton(self, monkeypatch):
        from backend.utils import analytics

        monkeypatch.setattr(analytics, "_analytics", None)
        first = analytics.get_analytics()
        second = analytics.get_analytics()
        assert first is second

    def test_init_analytics_replaces_singleton(self, monkeypatch):
        from backend.utils import analytics

        monkeypatch.setattr(analytics, "_analytics", None)
        analytics.get_analytics()
        new_instance = analytics.init_analytics(mixpanel_token="tok", amplitude_api_key="key")
        assert analytics._analytics is new_instance
