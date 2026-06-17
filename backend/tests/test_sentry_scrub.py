"""Tests for the Sentry before_send/before_breadcrumb PII scrubber (C1)."""

import pytest

from utils.sentry_scrub import scrub_breadcrumb, scrub_event

pytestmark = pytest.mark.unit


def test_scrub_event_redacts_phone_in_message():
    event = {"message": "lookup failed for 306-555-1234"}
    out = scrub_event(event)
    assert "306-555-1234" not in out["message"]
    assert "[PHONE]" in out["message"]


def test_scrub_event_redacts_email_and_coords_in_exception_value():
    event = {"exception": {"values": [{"type": "ValueError", "value": "user a@b.com at 52.1234,-106.6543 not found"}]}}
    out = scrub_event(event)
    val = out["exception"]["values"][0]["value"]
    assert "a@b.com" not in val
    assert "52.1234,-106.6543" not in val
    assert "[EMAIL]" in val and "[COORDS]" in val


def test_scrub_event_redacts_logentry_message():
    event = {"logentry": {"message": "contact b@c.ca"}}
    out = scrub_event(event)
    assert "b@c.ca" not in out["logentry"]["message"]


def test_scrub_event_stamps_surface_tag_without_overwriting():
    assert scrub_event({})["tags"]["surface"] == "backend"
    # An explicitly-set surface tag is preserved.
    out = scrub_event({"tags": {"surface": "worker"}})
    assert out["tags"]["surface"] == "worker"


def test_scrub_event_is_lossless_on_clean_and_malformed_events():
    clean = {"message": "ride completed", "exception": {"values": []}}
    assert scrub_event(clean)["message"] == "ride completed"
    # Non-dict / missing keys must not raise; event is returned as-is.
    weird = {"message": 12345, "logentry": None, "exception": "nope"}
    assert scrub_event(weird) is weird


def test_scrub_breadcrumb_redacts_message():
    crumb = {"message": "sms to 306.555.9999 failed"}
    out = scrub_breadcrumb(crumb)
    assert "306.555.9999" not in out["message"]
    # Missing message key is a no-op, not a crash.
    assert scrub_breadcrumb({"category": "http"}) == {"category": "http"}
