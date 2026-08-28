"""Tests for profile completeness utility.

The fixtures below use the REAL column names on `drivers` / `users`. That is
load-bearing, not incidental: the first version of this module scored
`vehicle_plate` (the CSV/import-side spelling) instead of `license_plate` (the
actual column), and because the fixture used the same wrong key the suite was
green at 100% while every real driver was pinned at 90%. A fixture that agrees
with the code but not with the schema tests nothing. See
`test_required_fields_use_real_schema_columns` for the guard.
"""

import pytest

try:
    from utils.profile_completeness import (
        RECOMMENDED_FIELDS,
        REQUIRED_FIELDS,
        _is_filled,
        compute_profile_completeness,
    )
except ImportError:
    from backend.utils.profile_completeness import (  # type: ignore
        RECOMMENDED_FIELDS,
        REQUIRED_FIELDS,
        _is_filled,
        compute_profile_completeness,
    )

pytestmark = pytest.mark.unit


def _full_driver():
    """A driver row with every scored column filled, using real column names."""
    return {
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+15551234567",
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_year": 2022,
        "vehicle_color": "Blue",
        "license_plate": "ABC1234",
        "vehicle_vin": "1HGBH41JXMN109186",
        "stripe_account_id": "acct_123",
        "service_area_id": "sa_1",
        "date_of_birth": "1990-01-01",
    }


def _full_user():
    """A linked `users` row with every personal field filled."""
    return {
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+15551234567",
        "email": "john@example.com",
    }


def _user_without_name():
    """A linked account with contact details but no name on file.

    Isolates the name-resolution tests: the account can't supply a name, so
    scoring has to fall through to the `drivers` mirror.
    """
    user = _full_user()
    del user["first_name"]
    del user["last_name"]
    return user


# ---------- Schema agreement ----------


def test_required_fields_use_real_schema_columns():
    """Pin the exact scored keys so a schema drift shows up as a deliberate diff.

    `license_plate` is the column; `vehicle_plate` is the import-side alias that
    `services/driver_import_service.py` maps onto it. Scoring the alias is the
    bug this whole module shipped with.
    """
    required = {f for f, _label, _cat in REQUIRED_FIELDS}
    assert required == {
        "full_name",
        "phone",
        "email",
        "vehicle_make",
        "vehicle_model",
        "vehicle_year",
        "vehicle_color",
        "license_plate",
        "service_area_id",
        "stripe_account_id",
    }
    assert "vehicle_plate" not in required
    assert {f for f, _label, _cat in RECOMMENDED_FIELDS} == {"date_of_birth", "vehicle_vin"}


def test_realistic_complete_driver_reaches_100():
    """A driver row built from real columns must be reachable at 100%.

    This is the assertion that fails if a scored key stops matching a column.
    """
    result = compute_profile_completeness(_full_driver(), _full_user())
    assert result["score"] == 100, result["missing_required"]


def test_license_plate_is_what_gets_scored():
    driver = _full_driver()
    del driver["license_plate"]
    driver["vehicle_plate"] = "ABC1234"  # import-side alias — not a real column
    result = compute_profile_completeness(driver, _full_user())
    assert result["score"] == 90
    assert [m["field"] for m in result["missing_required"]] == ["license_plate"]


# ---------- Scoring ----------


def test_complete_profile_returns_100():
    result = compute_profile_completeness(_full_driver(), _full_user())
    assert result["score"] == 100
    assert result["missing_required"] == []
    assert result["filled_required"] == result["total_required"]


def test_empty_driver_returns_low_score():
    result = compute_profile_completeness({})
    assert result["score"] == 0
    assert result["filled_required"] == 0
    assert len(result["missing_required"]) == result["total_required"]


def test_missing_vehicle_fields_reduces_score():
    driver = _full_driver()
    user = _full_user()
    # Remove vehicle fields
    del driver["vehicle_make"]
    del driver["vehicle_model"]
    result = compute_profile_completeness(driver, user)
    assert result["score"] < 100
    missing_fields = [m["field"] for m in result["missing_required"]]
    assert "vehicle_make" in missing_fields
    assert "vehicle_model" in missing_fields
    assert result["by_category"]["vehicle"]["complete"] is False


def test_missing_stripe_reduces_score():
    driver = _full_driver()
    user = _full_user()
    del driver["stripe_account_id"]
    result = compute_profile_completeness(driver, user)
    assert result["score"] < 100
    missing_fields = [m["field"] for m in result["missing_required"]]
    assert "stripe_account_id" in missing_fields


def test_recommended_fields_dont_affect_score():
    driver = _full_driver()
    user = _full_user()
    # Remove recommended fields only
    del driver["date_of_birth"]
    del driver["vehicle_vin"]
    result = compute_profile_completeness(driver, user)
    assert result["score"] == 100
    assert len(result["missing_recommended"]) == 2
    rec_fields = [m["field"] for m in result["missing_recommended"]]
    assert "date_of_birth" in rec_fields
    assert "vehicle_vin" in rec_fields


# ---------- Name resolution ----------


def test_user_row_provides_personal_fields():
    driver = _full_driver()
    # Driver carries NO personal fields; the linked account supplies them.
    for key in ("first_name", "last_name", "phone"):
        del driver[key]
    result = compute_profile_completeness(driver, _full_user())
    assert result["score"] == 100
    assert result["by_category"]["personal"]["complete"] is True


def test_no_linked_user_cannot_supply_email():
    """`email` lives ONLY on `users` (admin_update_driver's user_only_fields).

    An unlinked driver row therefore has no knowable email, and scoring it as
    missing is the correct conservative answer — not a fixture quirk.
    """
    result = compute_profile_completeness(_full_driver(), user=None)
    assert result["score"] == 90
    assert [m["field"] for m in result["missing_required"]] == ["email"]


def test_user_name_wins_over_driver_mirror():
    """The account row is authoritative; a stale mirror must not mask it."""
    driver = _full_driver()
    driver["first_name"] = "Stale"
    driver["last_name"] = "Mirror"
    result = compute_profile_completeness(driver, _full_user())
    assert result["by_category"]["personal"]["complete"] is True
    assert result["score"] == 100


def test_legacy_name_atom_is_not_scored_as_a_name():
    """`drivers.name` is a migration-63 rollback artefact, not a name source.

    Its auto-create fallback (routes/drivers/profile.py) writes the driver's
    PHONE NUMBER when the account has no name, so counting it would report a
    complete profile for a row the dashboard renders with a blank name.
    """
    driver = _full_driver()
    del driver["first_name"]
    del driver["last_name"]
    driver["name"] = "+15551234567"  # what the auto-create path actually writes
    result = compute_profile_completeness(driver, _user_without_name())
    assert result["score"] == 90
    assert [m["field"] for m in result["missing_required"]] == ["full_name"]


def test_driver_placeholder_is_not_a_name():
    """`admin_get_drivers` drops the legacy "Driver" placeholder; so must scoring."""
    driver = _full_driver()
    driver["first_name"] = "Driver"
    driver["last_name"] = None
    result = compute_profile_completeness(driver, _user_without_name())
    assert result["score"] == 90
    assert [m["field"] for m in result["missing_required"]] == ["full_name"]


def test_single_word_name_counts_as_filled():
    """Migration 63's backfill leaves `last_name` NULL for a one-word name."""
    driver = _full_driver()
    driver["first_name"] = "Prince"
    driver["last_name"] = None
    result = compute_profile_completeness(driver, _user_without_name())
    assert result["score"] == 100
    assert result["by_category"]["personal"]["complete"] is True


# ---------- Value handling ----------


def test_whitespace_only_treated_as_empty():
    assert _is_filled(None) is False
    assert _is_filled("") is False
    assert _is_filled("   ") is False
    assert _is_filled("\t\n") is False
    assert _is_filled("hello") is True
    assert _is_filled(0) is True

    driver = _full_driver()
    driver["first_name"] = "   "
    driver["last_name"] = "\t"
    driver["phone"] = ""
    driver["email"] = None
    result = compute_profile_completeness(driver, user=None)
    assert result["by_category"]["personal"]["filled"] == 0


def test_by_category_structure():
    result = compute_profile_completeness(_full_driver(), _full_user())
    assert set(result["by_category"].keys()) == {"personal", "vehicle", "service", "banking"}
    for _cat, data in result["by_category"].items():
        assert "filled" in data
        assert "total" in data
        assert "complete" in data
        assert "missing" in data
        assert isinstance(data["complete"], bool)
        assert isinstance(data["missing"], list)
