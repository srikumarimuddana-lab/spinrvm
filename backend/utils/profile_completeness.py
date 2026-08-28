"""
Profile completeness utility for driver onboarding.

Pure function module — no DB, no network calls.
Takes a driver dict and optional user dict, returns completeness score 0-100.
"""

from typing import Any, Dict, List, Optional


# Field definitions: (field_name, label, category)
REQUIRED_FIELDS = [
    ("name", "Full Name", "personal"),
    ("phone", "Phone Number", "personal"),
    ("email", "Email Address", "personal"),
    ("vehicle_make", "Vehicle Make", "vehicle"),
    ("vehicle_model", "Vehicle Model", "vehicle"),
    ("vehicle_year", "Vehicle Year", "vehicle"),
    ("vehicle_color", "Vehicle Color", "vehicle"),
    ("vehicle_plate", "License Plate", "vehicle"),
    ("service_area_id", "Service Area", "service"),
    ("stripe_account_id", "Stripe Account", "banking"),
]

RECOMMENDED_FIELDS = [
    ("date_of_birth", "Date of Birth", "personal"),
    ("vehicle_vin", "Vehicle VIN", "vehicle"),
]

# Fields that can come from the user dict
_USER_FIELDS = {"name", "phone", "email"}


def _is_filled(value: Any) -> bool:
    """Return False for None, empty string, or whitespace-only string."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def _get_field(field: str, driver: Dict, user: Optional[Dict]) -> Any:
    """
    Retrieve a field value, checking the user dict first for personal fields
    (name, phone, email), then falling back to the driver dict.
    """
    if field in _USER_FIELDS and user is not None:
        val = user.get(field)
        if _is_filled(val):
            return val
    return driver.get(field)


def compute_profile_completeness(
    driver: Dict, user: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Compute profile completeness for a driver.

    Args:
        driver: dict of driver record fields.
        user: optional dict of linked user record fields.

    Returns a dict with:
        score: int 0-100
        missing_required: list of {field, label, category}
        missing_recommended: list of {field, label, category}
        by_category: {category: {filled, total, complete, missing}}
        total_required: int
        filled_required: int
    """
    missing_required: List[Dict[str, str]] = []
    missing_recommended: List[Dict[str, str]] = []

    # Track per-category counts (required fields only for score)
    categories: Dict[str, Dict[str, Any]] = {}
    filled_required = 0
    total_required = len(REQUIRED_FIELDS)

    # Process required fields
    for field, label, category in REQUIRED_FIELDS:
        if category not in categories:
            categories[category] = {"filled": 0, "total": 0, "missing": []}
        categories[category]["total"] += 1

        value = _get_field(field, driver, user)
        if _is_filled(value):
            filled_required += 1
            categories[category]["filled"] += 1
        else:
            missing_required.append({"field": field, "label": label, "category": category})
            categories[category]["missing"].append(field)

    # Process recommended fields (don't affect score)
    for field, label, category in RECOMMENDED_FIELDS:
        value = _get_field(field, driver, user)
        if not _is_filled(value):
            missing_recommended.append({"field": field, "label": label, "category": category})

    # Build by_category with complete flag
    by_category = {}
    for cat, data in categories.items():
        by_category[cat] = {
            "filled": data["filled"],
            "total": data["total"],
            "complete": data["filled"] == data["total"],
            "missing": data["missing"],
        }

    score = int((filled_required / total_required) * 100) if total_required > 0 else 0

    return {
        "score": score,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "by_category": by_category,
        "total_required": total_required,
        "filled_required": filled_required,
    }
