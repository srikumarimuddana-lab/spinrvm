"""Tests for profile completeness utility."""

import pytest

try:
    from utils.profile_completeness import compute_profile_completeness, _is_filled
except ImportError:
    from backend.utils.profile_completeness import compute_profile_completeness, _is_filled

pytestmark = pytest.mark.unit


def _full_driver():
    """Return a fully filled driver dict."""
    return {
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_year": 2022,
        "vehicle_color": "Blue",
        "vehicle_plate": "ABC1234",
        "vehicle_vin": "1HGBH41JXMN109186",
        "stripe_account_id": "acct_123",
        "service_area_id": "sa_1",
        "date_of_birth": "1990-01-01",
    }


def _full_user():
    """Return a fully filled user dict."""
    return {
        "name": "John Doe",
        "phone": "+15551234567",
        "email": "john@example.com",
    }


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


def test_user_dict_provides_personal_fields():
    driver = _full_driver()
    # Driver has NO personal fields, user provides them
    user = _full_user()
    result = compute_profile_completeness(driver, user)
    assert result["score"] == 100
    assert result["by_category"]["personal"]["complete"] is True


def test_driver_fallback_when_no_user():
    driver = _full_driver()
    driver["name"] = "Jane Doe"
    driver["phone"] = "+15559999999"
    driver["email"] = "jane@example.com"
    result = compute_profile_completeness(driver, user=None)
    assert result["score"] == 100
    assert result["by_category"]["personal"]["complete"] is True


def test_whitespace_only_treated_as_empty():
    assert _is_filled(None) is False
    assert _is_filled("") is False
    assert _is_filled("   ") is False
    assert _is_filled("\t\n") is False
    assert _is_filled("hello") is True
    assert _is_filled(0) is True

    driver = _full_driver()
    driver["name"] = "   "
    driver["phone"] = ""
    driver["email"] = None
    result = compute_profile_completeness(driver, user=None)
    assert result["by_category"]["personal"]["filled"] == 0


def test_by_category_structure():
    result = compute_profile_completeness(_full_driver(), _full_user())
    assert set(result["by_category"].keys()) == {"personal", "vehicle", "service", "banking"}
    for cat, data in result["by_category"].items():
        assert "filled" in data
        assert "total" in data
        assert "complete" in data
        assert "missing" in data
        assert isinstance(data["complete"], bool)
        assert isinstance(data["missing"], list)
