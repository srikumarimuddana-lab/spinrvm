"""P2: raw GPS must not reach logs/Sentry; scrubber must catch E.164 phones,
labelled coords, and nested structured payloads."""

from __future__ import annotations

import pytest


class TestGeohash:
    def test_precision_5_length(self):
        from backend.utils.pii import geohash

        assert len(geohash(52.1332, -106.6700)) == 5

    def test_distinct_cities_distinct_hash(self):
        from backend.utils.pii import geohash

        assert geohash(52.1332, -106.6700) != geohash(50.4452, -104.6189)

    def test_nearby_points_share_prefix(self):
        from backend.utils.pii import geohash

        a = geohash(52.1332, -106.6700)
        b = geohash(52.1350, -106.6710)
        assert a[:3] == b[:3]  # same ~150km-ish coarse cell prefix

    def test_invalid_returns_placeholder(self):
        from backend.utils.pii import geohash

        assert geohash(None, None) == "?"
        assert geohash("x", 1) == "?"
        assert geohash(200, 500) == "?"  # out of range


class TestAiPiiPatterns:
    def test_e164_phone_scrubbed(self):
        from backend.ai.pii import scrub_pii

        assert "[PHONE]" in scrub_pii("call +13065551234 now")
        assert "3065551234" not in scrub_pii("call 3065551234 now")

    def test_separator_phone_still_scrubbed(self):
        from backend.ai.pii import scrub_pii

        assert "[PHONE]" in scrub_pii("(306) 555-1234")

    def test_labelled_coords_scrubbed(self):
        from backend.ai.pii import scrub_pii

        out = scrub_pii("driver at lat=52.1332 lng=-106.6700 now")
        assert "[COORDS]" in out
        assert "52.1332" not in out

    def test_comma_coords_scrubbed(self):
        from backend.ai.pii import scrub_pii

        assert "[COORDS]" in scrub_pii("52.1332,-106.6700")

    def test_email_scrubbed(self):
        from backend.ai.pii import scrub_pii

        assert "[EMAIL]" in scrub_pii("reach me at rider@example.com")


class TestSentryDeepScrub:
    def test_extra_dict_is_scrubbed(self):
        from backend.utils.sentry_scrub import scrub_event

        event = {"extra": {"detail": "phone +13065551234 at lat=52.13 lng=-106.67"}}
        out = scrub_event(event)
        assert "[PHONE]" in out["extra"]["detail"]
        assert "[COORDS]" in out["extra"]["detail"]

    def test_nested_extra_scrubbed(self):
        from backend.utils.sentry_scrub import scrub_event

        event = {"extra": {"a": {"b": ["+13065551234"]}}}
        out = scrub_event(event)
        assert out["extra"]["a"]["b"][0] == "[PHONE]"

    def test_breadcrumb_data_scrubbed(self):
        from backend.utils.sentry_scrub import scrub_breadcrumb

        crumb = {"data": {"url": "user rider@example.com"}}
        out = scrub_breadcrumb(crumb)
        assert "[EMAIL]" in out["data"]["url"]

    def test_scrub_never_drops_event(self):
        from backend.utils.sentry_scrub import scrub_event

        # A cyclic structure must not hang or raise — the event is returned.
        event: dict = {"extra": {}}
        event["extra"]["self"] = event["extra"]
        assert scrub_event(event) is event
