"""
Tests for the P0-4 surge-lock estimate_token.

See backend/utils/estimate_token.py. The token locks the surge shown at
estimate time so the rider doesn't get bait-and-switched at confirm.
"""

from __future__ import annotations

import pytest

from backend.utils.estimate_token import (
    EstimateTokenError,
    sign_estimate_token,
    verify_estimate_token,
)

RIDER = "rider_ut"
VT = "economy"
PU_LAT, PU_LNG = 52.13, -106.67
DO_LAT, DO_LNG = 52.12, -106.65


def _kwargs():
    return dict(
        rider_id=RIDER,
        vehicle_type_id=VT,
        pickup_lat=PU_LAT,
        pickup_lng=PU_LNG,
        dropoff_lat=DO_LAT,
        dropoff_lng=DO_LNG,
    )


class TestSignAndVerifyRoundTrip:
    def test_signed_token_verifies_and_returns_surge(self):
        tok = sign_estimate_token(surge_multiplier=1.5, total_fare=27.75, **_kwargs())
        payload = verify_estimate_token(tok, **_kwargs())
        assert payload["sm"] == 1.5
        assert payload["tf"] == 27.75
        assert payload["rid"] == RIDER

    def test_flat_surge_roundtrips(self):
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        payload = verify_estimate_token(tok, **_kwargs())
        assert payload["sm"] == 1.0


class TestTamperingRejection:
    def test_signature_mismatch_rejected(self):
        tok = sign_estimate_token(surge_multiplier=1.5, total_fare=27.75, **_kwargs())
        # Corrupt the signature half
        payload_b64, _sig = tok.split(".")
        bad = f"{payload_b64}.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        with pytest.raises(EstimateTokenError, match="signature"):
            verify_estimate_token(bad, **_kwargs())

    def test_rider_id_mismatch_rejected(self):
        """A rider cannot replay another rider's low-surge token."""
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        with pytest.raises(EstimateTokenError, match="rider"):
            verify_estimate_token(tok, **{**_kwargs(), "rider_id": "different_rider"})

    def test_vehicle_type_mismatch_rejected(self):
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        with pytest.raises(EstimateTokenError, match="vehicle"):
            verify_estimate_token(tok, **{**_kwargs(), "vehicle_type_id": "xl"})

    def test_pickup_coord_mismatch_rejected(self):
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        with pytest.raises(EstimateTokenError, match="pickup"):
            verify_estimate_token(tok, **{**_kwargs(), "pickup_lat": 52.99})

    def test_malformed_token_rejected(self):
        for bad in ("", "no-dot", "too.many.dots.here", "invalid!.base64!"):
            with pytest.raises(EstimateTokenError):
                verify_estimate_token(bad, **_kwargs())


class TestExpiry:
    def test_fresh_token_accepted(self):
        tok = sign_estimate_token(surge_multiplier=1.5, total_fare=27.75, now=1000.0, **_kwargs())
        # 30s later — well within the 300s default TTL
        payload = verify_estimate_token(tok, now=1030.0, **_kwargs())
        assert payload["sm"] == 1.5

    def test_expired_token_rejected(self):
        tok = sign_estimate_token(surge_multiplier=1.5, total_fare=27.75, now=1000.0, **_kwargs())
        # 10 minutes later — past the 5-minute default TTL
        with pytest.raises(EstimateTokenError, match="expired"):
            verify_estimate_token(tok, now=1600.0, **_kwargs())


class TestCoordTolerance:
    def test_sub_meter_gps_jitter_tolerated(self):
        """Coords round to 5 decimals (~1m). A jitter of 0.00001 at the
        6th decimal should not invalidate the token — riders' pins move
        slightly between estimate and confirm on real devices."""
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        payload = verify_estimate_token(tok, **{**_kwargs(), "pickup_lat": PU_LAT + 0.000001})
        assert payload["sm"] == 1.0

    def test_material_pickup_move_rejected(self):
        """A 100m jump (rider actually moved) invalidates the token —
        otherwise a rider could get an estimate in a low-surge area and
        confirm from a high-surge one."""
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=18.5, **_kwargs())
        with pytest.raises(EstimateTokenError, match="pickup"):
            verify_estimate_token(tok, **{**_kwargs(), "pickup_lat": PU_LAT + 0.001})
