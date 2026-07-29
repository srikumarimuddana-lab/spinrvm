"""Places API (New) request-payload builders.

These are pure functions, but they encode API contracts that only fail at
Google's edge — the HTTP mocks elsewhere in the suite return 200 for any
body, so a malformed payload passes every other test and 400s in
production. Pin the constraints here.
"""

from backend.utils.google_places_new import build_autocomplete_payload, build_text_search_payload

NEAR_LAT, NEAR_LNG = 50.41, -104.65
RADIUS = 25_000.0


class TestTextSearchPayload:
    def test_restriction_and_bias_are_mutually_exclusive(self):
        """searchText rejects a body with both:
            400 INVALID_ARGUMENT — "Location_restriction and location_bias
            cannot be set at the same time."
        Sending both took out every named-place lookup ("canadian tire",
        "walmart") for riders whose location was known.
        """
        payload = build_text_search_payload("canadian tire", NEAR_LAT, NEAR_LNG, RADIUS)
        assert "locationRestriction" in payload
        assert "locationBias" not in payload

    def test_restriction_is_a_rectangle_containing_the_rider(self):
        # Text Search (New) accepts only a rectangle here (not a circle),
        # and it is a HARD filter — the rider's point must be inside it.
        rect = build_text_search_payload("walmart", NEAR_LAT, NEAR_LNG, RADIUS)["locationRestriction"]["rectangle"]
        assert rect["low"]["latitude"] < NEAR_LAT < rect["high"]["latitude"]
        assert rect["low"]["longitude"] < NEAR_LNG < rect["high"]["longitude"]

    def test_no_bias_point_sends_neither_field(self):
        payload = build_text_search_payload("walmart", None, None, RADIUS)
        assert "locationRestriction" not in payload
        assert "locationBias" not in payload

    def test_query_and_region_always_present(self):
        payload = build_text_search_payload("canadian tire", None, None, RADIUS)
        assert payload["textQuery"] == "canadian tire"
        assert payload["regionCode"] == "CA"


class TestAutocompletePayload:
    def test_uses_circle_restriction_plus_origin_not_bias(self):
        """Autocomplete (New) does accept `origin` alongside a circular
        locationRestriction — that pairing is legal and is why this builder
        never hit the searchText conflict. Pinned so the two builders don't
        get 'harmonised' onto the invalid combination."""
        payload = build_autocomplete_payload("canadian", None, f"{NEAR_LAT},{NEAR_LNG}", int(RADIUS))
        assert "circle" in payload["locationRestriction"]
        assert payload["origin"] == {"latitude": NEAR_LAT, "longitude": NEAR_LNG}
        assert "locationBias" not in payload
