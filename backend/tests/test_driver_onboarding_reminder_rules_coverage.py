"""Coverage-focused unit tests for `utils/driver_onboarding_reminder_rules.py`.

A1c Sub-tier C: test-only change, no application code modified. This module
is pure logic (no I/O), so every test below is a plain function call plus
assertion — nothing here touches Supabase, Redis, or the network.

Written by reading the source only; per task instructions pytest was never
run against this file. Targets the previously-uncovered lines: 47-49
(`_zone` ZoneInfoNotFoundError fallback), 124-128 (`parse_remindable_statuses`
JSON-array branch, success + failure), 154-160 (`_load_list` string branch),
164 (`_pretty`), 176 (`mandatory_requirements` string-item branch), 184
(`mandatory_requirements` global-fallback branch), 192-198
(`doc_matches_requirement` body), and 214
(`missing_required_document_uploads` empty-requirements short-circuit).

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 8): an
area that explicitly marks all of its required_documents entries as
not-required previously still fell back to the global mandatory-document
list, silently overriding what looked like an intentional opt-out. Now
distinguishes "no required_documents configured" (falls back to global)
from "required_documents configured but all filtered out" (respects the
area's explicit empty list) — see
`test_mandatory_requirements_area_opt_out_respected` below.
"""

from datetime import datetime, timezone

from utils.driver_onboarding_reminder_rules import (
    DEFAULT_REMINDABLE_STATUSES,
    DEFAULT_TIMEZONE,
    VEHICLE_DETAILS,
    VEHICLE_DOCUMENTS,
    _load_list,
    _pretty,
    _zone,
    as_utc,
    doc_matches_requirement,
    driver_status,
    driver_timezone,
    local_date_for_send_window,
    mandatory_requirements,
    missing_required_document_uploads,
    open_send_windows,
    parse_remindable_statuses,
    reminder_cap_reached,
    reminder_message,
    should_skip_driver,
)

# ---------------------------------------------------------------------------
# as_utc
# ---------------------------------------------------------------------------


def test_as_utc_none_defaults_to_now():
    result = as_utc(None)
    assert result.tzinfo is timezone.utc


def test_as_utc_naive_datetime_is_tagged_utc_not_converted():
    naive = datetime(2026, 1, 15, 12, 0, 0)
    result = as_utc(naive)
    assert result == datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_as_utc_aware_datetime_is_converted():
    from datetime import timedelta
    from datetime import timezone as tz

    plus_five = datetime(2026, 1, 15, 12, 0, 0, tzinfo=tz(timedelta(hours=5)))
    result = as_utc(plus_five)
    assert result == datetime(2026, 1, 15, 7, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _zone (lines 47-49: ZoneInfoNotFoundError fallback)
# ---------------------------------------------------------------------------


def test_zone_valid_name():
    assert _zone("America/Toronto").key == "America/Toronto"


def test_zone_none_defaults():
    assert _zone(None).key == DEFAULT_TIMEZONE


def test_zone_invalid_name_falls_back_to_default(caplog):
    """Covers lines 47-49: bogus tz name -> ZoneInfoNotFoundError -> warning + default."""
    result = _zone("Not/A/Real/Zone")
    assert result.key == DEFAULT_TIMEZONE


# ---------------------------------------------------------------------------
# driver_timezone
# ---------------------------------------------------------------------------


def test_driver_timezone_from_area():
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"timezone": "America/Toronto"}}
    assert driver_timezone(driver, areas) == "America/Toronto"


def test_driver_timezone_area_missing_falls_back_to_driver_field():
    driver = {"service_area_id": "a1", "timezone": "America/Vancouver"}
    areas = {}
    assert driver_timezone(driver, areas) == "America/Vancouver"


def test_driver_timezone_area_present_but_no_timezone_key():
    driver = {"service_area_id": "a1", "timezone": "America/Vancouver"}
    areas = {"a1": {}}
    assert driver_timezone(driver, areas) == "America/Vancouver"


def test_driver_timezone_no_service_area_no_driver_field_defaults():
    driver = {}
    areas = {}
    assert driver_timezone(driver, areas) == DEFAULT_TIMEZONE


# ---------------------------------------------------------------------------
# local_date_for_send_window
# ---------------------------------------------------------------------------


def test_local_date_for_send_window_inside_window():
    # America/Regina is fixed UTC-6 year-round (SK does not observe DST).
    driver = {"timezone": "America/Regina"}
    now = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)  # 08:00 local
    assert local_date_for_send_window(driver, {}, now) == "2026-01-15"


def test_local_date_for_send_window_outside_window():
    driver = {"timezone": "America/Regina"}
    now = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)  # 09:00 local
    assert local_date_for_send_window(driver, {}, now) is None


# ---------------------------------------------------------------------------
# open_send_windows
# ---------------------------------------------------------------------------


def test_open_send_windows_includes_default_timezone_even_when_empty_input():
    now = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)  # 08:00 Regina
    result = open_send_windows(set(), now)
    assert result == {f"{DEFAULT_TIMEZONE}:2026-01-15"}


def test_open_send_windows_empty_when_no_timezone_in_window():
    now = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)  # 18:00 prev day Regina
    result = open_send_windows(set(), now)
    assert result == set()


def test_open_send_windows_mixed_timezones():
    now = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    result = open_send_windows({"America/Regina", "Asia/Kolkata"}, now)
    # Regina: UTC-6 -> 08:00 local -> in window.
    # Kolkata: UTC+5:30 -> 19:30 local -> not in window.
    assert result == {"America/Regina:2026-01-15"}


# ---------------------------------------------------------------------------
# driver_status
# ---------------------------------------------------------------------------


def test_driver_status_normal():
    assert driver_status({"status": "Pending"}) == "pending"


def test_driver_status_missing_defaults_pending():
    assert driver_status({}) == "pending"


def test_driver_status_empty_string_defaults_pending():
    assert driver_status({"status": ""}) == "pending"


def test_driver_status_whitespace_and_case():
    assert driver_status({"status": "  ACTIVE  "}) == "active"


# ---------------------------------------------------------------------------
# should_skip_driver
# ---------------------------------------------------------------------------


def _valid_driver(**overrides):
    driver = {
        "id": "driver-1",
        "user_id": "user-1",
        "status": "pending",
        "deleted_at": None,
    }
    driver.update(overrides)
    return driver


def test_should_skip_driver_missing_id():
    assert should_skip_driver(_valid_driver(id=None)) is True


def test_should_skip_driver_missing_user_id():
    assert should_skip_driver(_valid_driver(user_id=None)) is True


def test_should_skip_driver_deleted():
    assert should_skip_driver(_valid_driver(deleted_at="2026-01-01T00:00:00Z")) is True


def test_should_skip_driver_status_not_remindable():
    assert should_skip_driver(_valid_driver(status="active")) is True
    assert should_skip_driver(_valid_driver(status="rejected")) is True
    assert should_skip_driver(_valid_driver(status="suspended")) is True
    assert should_skip_driver(_valid_driver(status="banned")) is True
    assert should_skip_driver(_valid_driver(status="needs_review")) is True


def test_should_skip_driver_pending_is_not_skipped():
    assert should_skip_driver(_valid_driver(status="pending")) is False


def test_should_skip_driver_custom_statuses_param():
    driver = _valid_driver(status="needs_review")
    assert should_skip_driver(driver, remindable_statuses={"needs_review"}) is False
    assert should_skip_driver(driver, remindable_statuses={"pending"}) is True


def test_default_remindable_statuses_is_pending_only():
    assert DEFAULT_REMINDABLE_STATUSES == frozenset({"pending"})


# ---------------------------------------------------------------------------
# reminder_cap_reached
# ---------------------------------------------------------------------------


def test_reminder_cap_reached_uncapped_when_zero_or_negative():
    assert reminder_cap_reached(already_sent=999, max_per_type=0) is False
    assert reminder_cap_reached(already_sent=999, max_per_type=-1) is False


def test_reminder_cap_reached_below_cap():
    assert reminder_cap_reached(already_sent=6, max_per_type=7) is False


def test_reminder_cap_reached_at_cap():
    assert reminder_cap_reached(already_sent=7, max_per_type=7) is True


def test_reminder_cap_reached_above_cap():
    assert reminder_cap_reached(already_sent=8, max_per_type=7) is True


# ---------------------------------------------------------------------------
# parse_remindable_statuses (lines 124-128)
# ---------------------------------------------------------------------------


def test_parse_remindable_statuses_list_input():
    result = parse_remindable_statuses(["Pending", " needs_review ", "pending"])
    assert result == frozenset({"pending", "needs_review"})


def test_parse_remindable_statuses_valid_json_array_string():
    """Covers lines 124-125: text.startswith('[') and json.loads succeeds."""
    result = parse_remindable_statuses('["pending", "needs_review"]')
    assert result == frozenset({"pending", "needs_review"})


def test_parse_remindable_statuses_invalid_json_array_string_falls_back(caplog):
    """Covers lines 124, 126-128: malformed JSON starting with '[' -> default."""
    result = parse_remindable_statuses("[this is not json")
    assert result == DEFAULT_REMINDABLE_STATUSES


def test_parse_remindable_statuses_csv_string():
    result = parse_remindable_statuses("pending, needs_review")
    assert result == frozenset({"pending", "needs_review"})


def test_parse_remindable_statuses_non_list_after_parse_defaults():
    # A JSON object string does not start with '[' so it goes through the
    # CSV split path and becomes a single non-matching "sentence" — still
    # produces a one-item list, exercising the value.split(',') branch.
    result = parse_remindable_statuses('{"pending": true}')
    assert result == frozenset({'{"pending": true}'})


def test_parse_remindable_statuses_non_list_non_string_defaults():
    """Covers line 131-132: not isinstance(value, list) -> default."""
    assert parse_remindable_statuses(None) == DEFAULT_REMINDABLE_STATUSES
    assert parse_remindable_statuses(42) == DEFAULT_REMINDABLE_STATUSES


def test_parse_remindable_statuses_empty_string_defaults():
    assert parse_remindable_statuses("") == DEFAULT_REMINDABLE_STATUSES


def test_parse_remindable_statuses_all_blank_items_default():
    assert parse_remindable_statuses([" ", ""]) == DEFAULT_REMINDABLE_STATUSES


# ---------------------------------------------------------------------------
# reminder_message
# ---------------------------------------------------------------------------


def test_reminder_message_vehicle_details():
    title, body, data = reminder_message("driver-1", VEHICLE_DETAILS)
    assert title == "Add your vehicle details"
    assert data == {
        "type": "driver_vehicle_details_reminder",
        "driver_id": "driver-1",
        "deeplink": "/vehicle-info",
    }
    assert "vehicle info" in body


def test_reminder_message_vehicle_documents():
    title, body, data = reminder_message("driver-1", VEHICLE_DOCUMENTS)
    assert title == "Upload your vehicle documents"
    assert data["type"] == "driver_vehicle_documents_reminder"
    assert data["deeplink"] == "/documents"


def test_reminder_message_unknown_kind_uses_documents_default():
    title, _, data = reminder_message("driver-9", "some_unknown_kind")
    assert title == "Upload your vehicle documents"
    assert data["type"] == "driver_vehicle_documents_reminder"


# ---------------------------------------------------------------------------
# _load_list (lines 154-160)
# ---------------------------------------------------------------------------


def test_load_list_passthrough_list():
    value = [1, 2, 3]
    assert _load_list(value) is value


def test_load_list_valid_json_list_string():
    assert _load_list('["a", "b"]') == ["a", "b"]


def test_load_list_valid_json_non_list_string_returns_empty():
    """Covers line 159 taking the False branch: parsed JSON is not a list."""
    assert _load_list('{"a": 1}') == []
    assert _load_list("5") == []


def test_load_list_invalid_json_string_returns_empty():
    """Covers lines 156-158: json.loads raises -> []."""
    assert _load_list("[not valid json") == []


def test_load_list_blank_string_returns_empty():
    assert _load_list("   ") == []


def test_load_list_non_string_non_list_returns_empty():
    """Covers line 160: fallthrough for e.g. None/int/dict."""
    assert _load_list(None) == []
    assert _load_list(42) == []
    assert _load_list({"not": "a list"}) == []


# ---------------------------------------------------------------------------
# _pretty (line 164)
# ---------------------------------------------------------------------------


def test_pretty_replaces_separators_and_titlecases():
    assert _pretty("drivers_license") == "Drivers License"
    assert _pretty("vehicle-registration") == "Vehicle Registration"


def test_pretty_falsy_defaults_to_document():
    assert _pretty(None) == "Document"
    assert _pretty("") == "Document"


# ---------------------------------------------------------------------------
# mandatory_requirements (lines 176, 184)
# ---------------------------------------------------------------------------


def test_mandatory_requirements_string_items():
    """Covers line 176: string entries in required_documents."""
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": ["drivers_license", "vehicle-registration"]}}
    result = mandatory_requirements(driver, areas, [])
    assert result == [
        (None, "drivers_license", "Drivers License"),
        (None, "vehicle-registration", "Vehicle Registration"),
    ]


def test_mandatory_requirements_dict_items_required_and_is_mandatory_variants():
    driver = {"service_area_id": "a1"}
    items = [
        {"key": "license", "label": "Driver License", "id": "req-1", "required": True},
        {"key": "insurance", "id": "req-2"},  # no required/is_mandatory -> default True
        {"key": "skip_required_false", "required": False},  # excluded
        {"key": "skip_is_mandatory_false", "is_mandatory": False},  # excluded via fallback
        {"requirement_key": "reg_key_only"},  # key via requirement_key; label via _pretty
    ]
    areas = {"a1": {"required_documents": items}}
    result = mandatory_requirements(driver, areas, [])
    assert result == [
        ("req-1", "license", "Driver License"),
        ("req-2", "insurance", "Insurance"),
        (None, "reg_key_only", "Reg Key Only"),
    ]


def test_mandatory_requirements_falls_back_to_global_when_area_has_no_docs():
    """Covers line 184: area configured with no required_documents at all."""
    driver = {"service_area_id": "a1"}
    areas = {"a1": {}}
    global_reqs = [
        {"id": "g1", "name": "Vehicle Insurance", "is_mandatory": True},
        {"id": "g2", "name": "Excluded Doc", "is_mandatory": False},
        {"name": "No Id Doc"},  # is_mandatory absent -> included
    ]
    result = mandatory_requirements(driver, areas, global_reqs)
    assert result == [
        ("g1", None, "Vehicle Insurance"),
        (None, None, "No Id Doc"),
    ]


def test_mandatory_requirements_no_service_area_uses_global():
    driver = {}
    areas = {}
    global_reqs = [{"id": "g1", "name": "Doc1", "is_mandatory": True}]
    result = mandatory_requirements(driver, areas, global_reqs)
    assert result == [("g1", None, "Doc1")]


def test_mandatory_requirements_area_opt_out_respected():
    """Fixed (2026-08-03, see module docstring above).

    When every item in an area's `required_documents` is explicitly marked
    not-required, the area's explicit (now-empty) list is respected instead
    of silently falling through to the global mandatory-document list — an
    area operator can now configure "nothing is required here"."""
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": [{"key": "opt_doc", "required": False}]}}
    global_reqs = [{"id": "g1", "name": "Global Mandatory Doc", "is_mandatory": True}]
    result = mandatory_requirements(driver, areas, global_reqs)
    assert result == []


# ---------------------------------------------------------------------------
# doc_matches_requirement (lines 192-198)
# ---------------------------------------------------------------------------


def test_doc_matches_requirement_by_requirement_key():
    doc = {"requirement_key": "License"}
    req = (None, "license", "License")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_by_requirement_id_equal_req_id():
    doc = {"requirement_id": "req-5"}
    req = ("req-5", None, "Doc")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_by_requirement_id_matches_key_case_insensitive():
    doc = {"requirement_id": "LICENSE"}
    req = (None, "license", "License")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_by_document_type_equals_label():
    doc = {"document_type": "Vehicle Registration"}
    req = (None, None, "Vehicle Registration")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_by_document_type_equals_key_with_spaces():
    doc = {"document_type": "vehicle registration"}
    req = (None, "vehicle_registration", "Some Other Label")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_by_substring():
    doc = {"document_type": "proof_of_insurance_pdf"}
    req = (None, "insurance", "Insurance Proof")
    assert doc_matches_requirement(doc, req) is True


def test_doc_matches_requirement_no_match():
    doc = {"document_type": "random_thing", "requirement_key": "other"}
    req = ("req-1", "license", "License")
    assert doc_matches_requirement(doc, req) is False


def test_doc_matches_requirement_all_empty():
    assert doc_matches_requirement({}, (None, None, "")) is False


# ---------------------------------------------------------------------------
# missing_required_document_uploads (line 214)
# ---------------------------------------------------------------------------


def test_missing_required_document_uploads_no_requirements_returns_false():
    """Covers line 214: reqs is empty -> short-circuit False."""
    assert missing_required_document_uploads({}, [], {}, []) is False


def test_missing_required_document_uploads_all_present():
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": ["license"]}}
    docs = [{"document_type": "license"}]
    assert missing_required_document_uploads(driver, docs, areas, []) is False


def test_missing_required_document_uploads_one_missing():
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": ["license", "insurance"]}}
    docs = [{"document_type": "license"}]
    assert missing_required_document_uploads(driver, docs, areas, []) is True


def test_missing_required_document_uploads_ignores_superseded_and_rejected():
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": ["license"]}}
    docs = [{"document_type": "license", "status": "superseded"}]
    assert missing_required_document_uploads(driver, docs, areas, []) is True

    docs_rejected = [{"document_type": "license", "status": "rejected"}]
    assert missing_required_document_uploads(driver, docs_rejected, areas, []) is True


def test_missing_required_document_uploads_pending_status_counts():
    driver = {"service_area_id": "a1"}
    areas = {"a1": {"required_documents": ["license"]}}
    docs = [{"document_type": "license", "status": "pending"}]
    assert missing_required_document_uploads(driver, docs, areas, []) is False
