"""Unit tests for the ride-offer fare-banner: signing token + PNG renderer."""

import time

import pytest

from utils.offer_card import coarsen_address, first_name, render_offer_card
from utils.offer_card_token import (
    OfferCardTokenError,
    sign_offer_card_token,
    verify_offer_card_token,
)

pytestmark = pytest.mark.unit


# ── Token ───────────────────────────────────────────────────────────────


def test_token_round_trip():
    tok = sign_offer_card_token(ride_id="r1", driver_id="d1")
    payload = verify_offer_card_token(tok, ride_id="r1")
    assert payload["rd"] == "r1"
    assert payload["dr"] == "d1"


def test_token_ride_mismatch_rejected():
    tok = sign_offer_card_token(ride_id="r1", driver_id="d1")
    with pytest.raises(OfferCardTokenError):
        verify_offer_card_token(tok, ride_id="r2")


def test_token_expired_rejected():
    past = time.time() - 1000
    tok = sign_offer_card_token(ride_id="r1", driver_id="d1", ttl_seconds=1, now=past)
    with pytest.raises(OfferCardTokenError):
        verify_offer_card_token(tok, ride_id="r1")


def test_token_tampered_signature_rejected():
    tok = sign_offer_card_token(ride_id="r1", driver_id="d1")
    body, _sig = tok.split(".", 1)
    forged = body + ".AAAA"
    with pytest.raises(OfferCardTokenError):
        verify_offer_card_token(forged, ride_id="r1")


def test_token_malformed_rejected():
    with pytest.raises(OfferCardTokenError):
        verify_offer_card_token("not-a-token", ride_id="r1")


# ── PII coarsening ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1855 Victoria Ave #3, Regina", "Victoria Ave, Regina"),
        ("320 4th Ave N, Apt 12, Saskatoon", "4th Ave N, Saskatoon"),
        ("12 Main St", "Main St"),
        ("Regina International Airport", "Regina International Airport"),
        ("", ""),
        (None, ""),
    ],
)
def test_coarsen_address(raw, expected):
    assert coarsen_address(raw) == expected


def test_coarsen_drops_house_number_pii():
    # The exact street number must never survive into the banner.
    assert "1855" not in coarsen_address("1855 Victoria Ave #3, Regina")


def test_first_name_only():
    assert first_name("Sarah Johnson") == "Sarah"
    assert first_name("Mike") == "Mike"
    assert first_name("") == ""
    assert first_name(None) == ""


# ── Renderer ────────────────────────────────────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_render_returns_png():
    png = render_offer_card(
        fare=17.20,
        distance_km=4.2,
        duration_minutes=12,
        rider_first_name="Sarah Johnson",
        rider_rating=4.9,
        pickup_area="Victoria Ave, Regina",
        dropoff_area="4th Ave N, Saskatoon",
        surge_multiplier=1.5,
        total_bonus=2.50,
    )
    assert png is not None
    assert png[:8] == _PNG_MAGIC


def test_render_minimal_fields():
    # No surge, bonus, rating, or addresses — must still render.
    png = render_offer_card(fare=9.5)
    assert png is not None
    assert png[:8] == _PNG_MAGIC
