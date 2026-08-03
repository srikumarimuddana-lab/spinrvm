"""Direct unit coverage of the pure functions in
utils/driver_onboarding_reminder_rules.py.

test_driver_onboarding_reminders.py exercises this module only indirectly
through the check_driver_onboarding_reminders() loop, whose fixtures always
supply a valid timezone, dict-shaped required_documents, and a non-empty
docs list — so several branches (bad timezone names, string-shaped
requirement entries, the global_reqs fallback, JSON parsing edge cases, and
doc_matches_requirement itself) are never reached. These tests call the
rules functions directly.
"""

from datetime import datetime, timezone

from utils.driver_onboarding_reminder_rules import (
    DEFAULT_TIMEZONE,
    VEHICLE_DETAILS,
    VEHICLE_DOCUMENTS,
    _load_list,
    _pretty,
    _zone,
    doc_matches_requirement,
    driver_timezone,
    local_date_for_send_window,
    mandatory_requirements,
    missing_required_document_uploads,
    open_send_windows,
    parse_remindable_statuses,
    reminder_message,
)


class TestZone:
    def test_valid_timezone_name(self):
        assert str(_zone("America/Regina")) == "America/Regina"

    def test_none_falls_back_to_default(self):
        assert str(_zone(None)) == DEFAULT_TIMEZONE

    def test_invalid_timezone_name_falls_back_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            zone = _zone("Not/A_Real_Zone")
        assert str(zone) == DEFAULT_TIMEZONE
        assert "Invalid driver timezone" in caplog.text


class TestDriverTimezone:
    def test_area_timezone_wins(self):
        driver = {"service_area_id": "area-1", "timezone": "America/Vancouver"}
        areas = {"area-1": {"timezone": "America/Regina"}}
        assert driver_timezone(driver, areas) == "America/Regina"

    def test_driver_timezone_used_when_no_area(self):
        driver = {"service_area_id": None, "timezone": "America/Vancouver"}
        assert driver_timezone(driver, {}) == "America/Vancouver"

    def test_defaults_when_nothing_set(self):
        driver = {"service_area_id": None, "timezone": None}
        assert driver_timezone(driver, {}) == DEFAULT_TIMEZONE

    def test_area_missing_from_map_falls_back_to_driver_field(self):
        driver = {"service_area_id": "area-missing", "timezone": "America/Toronto"}
        assert driver_timezone(driver, {}) == "America/Toronto"


class TestLocalDateForSendWindow:
    def test_inside_window_returns_local_date(self):
        driver = {"service_area_id": None, "timezone": "America/Regina"}
        now = datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc)  # 08:05 local
        assert local_date_for_send_window(driver, {}, now) == "2026-06-09"

    def test_outside_window_returns_none(self):
        driver = {"service_area_id": None, "timezone": "America/Regina"}
        now = datetime(2026, 6, 9, 18, 5, tzinfo=timezone.utc)  # 12:05 local
        assert local_date_for_send_window(driver, {}, now) is None


class TestOpenSendWindows:
    def test_default_timezone_always_included(self):
        now = datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc)  # 08:05 Regina
        keys = open_send_windows(set(), now)
        assert f"{DEFAULT_TIMEZONE}:2026-06-09" in keys

    def test_filters_out_falsy_timezone_entries(self):
        now = datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc)
        keys = open_send_windows({"", None, "America/Regina"}, now)
        assert keys == {f"{DEFAULT_TIMEZONE}:2026-06-09"}

    def test_timezone_outside_window_excluded(self):
        # 14:05 UTC is 07:05 in America/Vancouver (UTC-7) — outside the 08:00
        # hour, so only the always-included default timezone appears.
        now = datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc)
        keys = open_send_windows({"America/Vancouver"}, now)
        assert "America/Vancouver:2026-06-09" not in keys
        assert f"{DEFAULT_TIMEZONE}:2026-06-09" in keys


class TestParseRemindableStatuses:
    def test_list_passthrough_lowercased_and_stripped(self):
        assert parse_remindable_statuses([" Pending ", "ACTIVE"]) == frozenset({"pending", "active"})

    def test_csv_string(self):
        assert parse_remindable_statuses("pending, active") == frozenset({"pending", "active"})

    def test_valid_json_array_string(self):
        assert parse_remindable_statuses('["pending", "active"]') == frozenset({"pending", "active"})

    def test_invalid_json_array_string_falls_back_to_default(self, caplog):
        with caplog.at_level("WARNING"):
            result = parse_remindable_statuses('["pending", "active"')  # malformed
        assert result == frozenset({"pending"})
        assert "Invalid driver_onboarding_reminder_statuses" in caplog.text

    def test_non_str_non_list_value_falls_back_to_default(self):
        # Neither the str branch nor a list — hits the trailing
        # `not isinstance(value, list)` guard directly.
        assert parse_remindable_statuses(42) == frozenset({"pending"})

    def test_empty_string_falls_back_to_default(self):
        assert parse_remindable_statuses("") == frozenset({"pending"})

    def test_all_blank_entries_falls_back_to_default(self):
        assert parse_remindable_statuses([" ", "", "   "]) == frozenset({"pending"})

    def test_none_falls_back_to_default(self):
        assert parse_remindable_statuses(None) == frozenset({"pending"})


class TestReminderMessage:
    def test_vehicle_details_message(self):
        title, body, data = reminder_message("driver-1", VEHICLE_DETAILS)
        assert title == "Add your vehicle details"
        assert data["type"] == "driver_vehicle_details_reminder"
        assert data["driver_id"] == "driver-1"

    def test_vehicle_documents_message(self):
        title, body, data = reminder_message("driver-1", VEHICLE_DOCUMENTS)
        assert title == "Upload your vehicle documents"
        assert data["type"] == "driver_vehicle_documents_reminder"

    def test_unknown_kind_defaults_to_documents_message(self):
        title, _, data = reminder_message("driver-1", "something_else")
        assert title == "Upload your vehicle documents"


class TestLoadList:
    def test_list_passthrough(self):
        assert _load_list(["a", "b"]) == ["a", "b"]

    def test_valid_json_array_string(self):
        assert _load_list('["a", "b"]') == ["a", "b"]

    def test_invalid_json_string_returns_empty(self):
        assert _load_list("not json{") == []

    def test_json_string_that_is_not_a_list_returns_empty(self):
        assert _load_list('{"a": 1}') == []

    def test_blank_string_returns_empty(self):
        assert _load_list("   ") == []

    def test_none_returns_empty(self):
        assert _load_list(None) == []

    def test_non_str_non_list_returns_empty(self):
        assert _load_list(42) == []


class TestPretty:
    def test_underscores_and_dashes_become_spaces_titled(self):
        assert _pretty("vehicle_registration-form") == "Vehicle Registration Form"

    def test_falsy_value_defaults_to_document(self):
        assert _pretty(None) == "Document"
        assert _pretty("") == "Document"


class TestMandatoryRequirements:
    DRIVER = {"service_area_id": "area-1"}

    def test_string_items_use_pretty_label(self):
        areas = {"area-1": {"required_documents": ["vehicle_registration"]}}
        reqs = mandatory_requirements(self.DRIVER, areas, [])
        assert reqs == [(None, "vehicle_registration", "Vehicle Registration")]

    def test_dict_items_extract_key_and_label(self):
        areas = {
            "area-1": {
                "required_documents": [
                    {"id": "req-1", "key": "vehicle_registration", "label": "Vehicle Registration", "required": True}
                ]
            }
        }
        reqs = mandatory_requirements(self.DRIVER, areas, [])
        assert reqs == [("req-1", "vehicle_registration", "Vehicle Registration")]

    def test_dict_item_marked_not_required_is_excluded(self):
        areas = {"area-1": {"required_documents": [{"key": "optional_doc", "required": False}]}}
        assert mandatory_requirements(self.DRIVER, areas, []) == []

    def test_dict_item_uses_is_mandatory_when_required_absent(self):
        areas = {"area-1": {"required_documents": [{"key": "optional_doc", "is_mandatory": False}]}}
        assert mandatory_requirements(self.DRIVER, areas, []) == []

    def test_dict_item_label_falls_back_to_pretty_of_key_or_id(self):
        areas = {"area-1": {"required_documents": [{"id": "req-9"}]}}
        reqs = mandatory_requirements(self.DRIVER, areas, [])
        assert reqs == [("req-9", None, "Req 9")]

    def test_falls_back_to_global_requirements_when_area_has_none(self):
        driver = {"service_area_id": None}
        global_reqs = [
            {"id": "g1", "name": "Vehicle Registration", "is_mandatory": True},
            {"id": "g2", "name": "Optional", "is_mandatory": False},
        ]
        reqs = mandatory_requirements(driver, {}, global_reqs)
        assert reqs == [("g1", None, "Vehicle Registration")]

    def test_no_area_and_no_global_reqs_returns_empty(self):
        assert mandatory_requirements({"service_area_id": None}, {}, []) == []


class TestDocMatchesRequirement:
    def test_matches_on_requirement_key(self):
        req = (None, "vehicle_registration", "Vehicle Registration")
        doc = {"requirement_key": "vehicle_registration"}
        assert doc_matches_requirement(doc, req) is True

    def test_matches_on_requirement_id(self):
        req = ("req-1", None, "Vehicle Registration")
        doc = {"requirement_id": "req-1"}
        assert doc_matches_requirement(doc, req) is True

    def test_matches_on_requirement_id_case_insensitive_key_fallback(self):
        req = (None, "vehicle_registration", "Vehicle Registration")
        doc = {"requirement_id": "VEHICLE_REGISTRATION"}
        assert doc_matches_requirement(doc, req) is True

    def test_matches_on_document_type_equals_label(self):
        req = (None, None, "Vehicle Registration")
        doc = {"document_type": "vehicle registration"}
        assert doc_matches_requirement(doc, req) is True

    def test_matches_on_document_type_equals_key_with_spaces(self):
        req = (None, "vehicle_registration", "Vehicle Registration")
        doc = {"document_type": "vehicle registration"}  # underscore-key rendered with spaces
        assert doc_matches_requirement(doc, req) is True

    def test_matches_on_normalized_partial_document_type(self):
        # doc_type retains the key's underscores/spaces but has extra
        # trailing text — the substring check strips spaces/underscores from
        # both sides before comparing, so this still matches.
        req = (None, "vehicle_registration", "Vehicle Registration")
        doc = {"document_type": "vehicle_registration_form"}
        assert doc_matches_requirement(doc, req) is True

    def test_no_match(self):
        req = (None, "vehicle_registration", "Vehicle Registration")
        doc = {"document_type": "drivers_license", "requirement_key": "drivers_license"}
        assert doc_matches_requirement(doc, req) is False

    def test_empty_doc_never_matches(self):
        req = (None, "vehicle_registration", "Vehicle Registration")
        assert doc_matches_requirement({}, req) is False


class TestMissingRequiredDocumentUploads:
    AREAS = {"area-1": {"required_documents": [{"key": "vehicle_registration", "label": "Vehicle Registration"}]}}
    DRIVER = {"service_area_id": "area-1"}

    def test_no_requirements_returns_false(self):
        driver = {"service_area_id": None}
        assert missing_required_document_uploads(driver, [], {}, []) is False

    def test_missing_upload_returns_true(self):
        assert missing_required_document_uploads(self.DRIVER, [], self.AREAS, []) is True

    def test_matching_upload_returns_false(self):
        docs = [{"requirement_key": "vehicle_registration", "status": "approved"}]
        assert missing_required_document_uploads(self.DRIVER, docs, self.AREAS, []) is False

    def test_superseded_upload_is_ignored_and_still_missing(self):
        docs = [{"requirement_key": "vehicle_registration", "status": "superseded"}]
        assert missing_required_document_uploads(self.DRIVER, docs, self.AREAS, []) is True

    def test_rejected_upload_is_ignored_and_still_missing(self):
        docs = [{"requirement_key": "vehicle_registration", "status": "rejected"}]
        assert missing_required_document_uploads(self.DRIVER, docs, self.AREAS, []) is True

    def test_pending_upload_counts_as_satisfying(self):
        docs = [{"requirement_key": "vehicle_registration", "status": "pending"}]
        assert missing_required_document_uploads(self.DRIVER, docs, self.AREAS, []) is False
