"""
ACTION_ITEMS B1: no analytics interface accepts raw GPS coordinates.

Pins the geohash-only contract of AnalyticsService.track_driver_online
(backend/utils/analytics.py): a lat/lng dict raises TypeError, a
stringified coordinate raises ValueError, and only an {"area_geohash": ...}
property — never lat/lng keys — is forwarded to providers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.utils.analytics import AnalyticsService


def _service() -> AnalyticsService:
    # Providers disabled — the contract under test is the signature/guard,
    # not Mixpanel/Amplitude delivery.
    return AnalyticsService(enable_mixpanel=False, enable_amplitude=False)


@pytest.mark.unit
class TestTrackDriverOnlineGeohashOnly:
    def test_accepts_geohash_and_forwards_area_geohash_property(self):
        svc = _service()
        with patch.object(svc, "track", return_value=True) as mock_track:
            assert svc.track_driver_online("drv-001", "9zfje") is True
        mock_track.assert_called_once_with("drv-001", "Driver Online", {"area_geohash": "9zfje"})

    def test_forwarded_properties_never_contain_lat_lng_keys(self):
        svc = _service()
        with patch.object(svc, "track", return_value=True) as mock_track:
            svc.track_driver_online("drv-001", "9ZFJE")
        props = mock_track.call_args.args[2]
        assert set(props) == {"area_geohash"}
        assert props["area_geohash"] == "9zfje"  # normalized to lowercase

    def test_latlng_dict_raises_type_error(self):
        svc = _service()
        with pytest.raises(TypeError):
            svc.track_driver_online("drv-001", {"lat": 52.1332, "lng": -106.6700})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "not_a_geohash",
        [
            "52.1332,-106.6700",  # stringified coordinates
            "52.1332",  # single coordinate
            "-106.67",  # negative coordinate
            "",  # empty
            "lat=52 lng=-106",  # key=value smuggling
        ],
    )
    def test_non_geohash_string_raises_value_error(self, not_a_geohash):
        svc = _service()
        with pytest.raises(ValueError):
            svc.track_driver_online("drv-001", not_a_geohash)
