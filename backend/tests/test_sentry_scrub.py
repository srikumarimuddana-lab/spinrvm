"""Tests for the Sentry before_send/before_breadcrumb PII scrubber (C1)."""

import pytest

from utils.sentry_scrub import scrub_breadcrumb, scrub_event, tags_from_log_extra

pytestmark = pytest.mark.unit


def test_tags_from_log_extra_promotes_known_keys():
    tags = tags_from_log_extra(
        {"domain": "payments", "ride_id": "r1", "driver_id": "d1", "noise": "ignored"}
    )
    assert tags["domain"] == "payments"
    assert tags["ride_id"] == "r1"
    assert tags["driver_id"] == "d1"
    assert tags["surface"] == "backend"  # always stamped
    assert "noise" not in tags  # only whitelisted keys promoted


def test_tags_from_log_extra_stamps_surface_on_empty_or_bad_input():
    assert tags_from_log_extra({}) == {"surface": "backend"}
    assert tags_from_log_extra(None) == {"surface": "backend"}
    # An explicit surface is preserved.
    assert tags_from_log_extra({"surface": "worker"})["surface"] == "worker"


def test_tags_from_log_extra_coerces_values_to_str():
    tags = tags_from_log_extra({"ride_id": 123})
    assert tags["ride_id"] == "123"


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
