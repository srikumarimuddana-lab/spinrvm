"""
Profile completeness utility for driver onboarding.

Pure function module — no DB, no network calls.
Takes a driver dict and optional user dict, returns completeness score 0-100.
"""

from typing import Any, Dict, List, Optional

# Field definitions: (field_name, label, category)
#
# Every entry here must name a REAL column on `drivers` (or `users`, for the
# personal fields) or a key in _FIELD_RESOLVERS below. A key that matches no
# column reads as permanently missing and pins every driver below 100% — see
# the `license_plate` note in the test module.
REQUIRED_FIELDS = [
    ("full_name", "Full Name", "personal"),
    ("phone", "Phone Number", "personal"),
    ("email", "Email Address", "personal"),
    ("vehicle_make", "Vehicle Make", "vehicle"),
    ("vehicle_model", "Vehicle Model", "vehicle"),
    ("vehicle_year", "Vehicle Year", "vehicle"),
    ("vehicle_color", "Vehicle Color", "vehicle"),
    # `license_plate`, NOT `vehicle_plate`: the latter is the CSV/import-side
    # spelling that driver_import_service maps ONTO this column
    # (services/driver_import_service.py:359, :726). The column the admin
    # dashboard writes is `license_plate` (routes/admin/drivers.py's
    # admin_update_driver allowlist).
    ("license_plate", "License Plate", "vehicle"),
    ("service_area_id", "Service Area", "service"),
    ("stripe_account_id", "Stripe Account", "banking"),
]

RECOMMENDED_FIELDS = [
    ("date_of_birth", "Date of Birth", "personal"),
    ("vehicle_vin", "Vehicle VIN", "vehicle"),
]

# Fields that live on the `users` account row and are only mirrored (or absent)
# on `drivers`. `name` is deliberately NOT here — `users` has no `name` column;
# the display name is composed, see _resolve_full_name.
_USER_FIELDS = {"phone", "email"}

# The legacy placeholder `admin_get_drivers` drops rather than render.
_NAME_PLACEHOLDER = "Driver"


def _is_filled(value: Any) -> bool:
    """Return False for None, empty string, or whitespace-only string."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def _resolve_full_name(driver: Dict, user: Optional[Dict]) -> Optional[str]:
    """
    Compose the display name the way ``admin_get_drivers`` renders it: the
    linked account's first/last wins, the `drivers` mirror is the fallback, and
    the mirror's legacy "Driver" placeholder is not a name.

    Deliberately does NOT read the `drivers.name` atom. Migration 63
    (``63_phase3b_field_alignment.sql``) split that column into
    `first_name`/`last_name` and kept it only so the migration stays
    revertible. It can still hold the "Driver" placeholder, and
    ``routes/drivers/profile.py``'s auto-create path writes the driver's PHONE
    NUMBER into it when the account carries no name — so scoring the atom would
    report "complete" for a profile the dashboard renders as blank.

    A single-word name is legitimate (migration 63's backfill leaves
    `last_name` NULL for one), so first name alone counts as filled.
    """
    if user:
        composed = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
        if composed:
            return composed
    first = driver.get("first_name")
    if first == _NAME_PLACEHOLDER:
        first = None
    return f"{first or ''} {driver.get('last_name') or ''}".strip() or None


# Derived fields: resolved by a function rather than read from one column.
_FIELD_RESOLVERS = {"full_name": _resolve_full_name}


def _get_field(field: str, driver: Dict, user: Optional[Dict]) -> Any:
    """
    Retrieve a field value: derived fields go through their resolver, personal
    fields (phone, email) check the user dict first, everything else reads the
    driver dict.
    """
    resolver = _FIELD_RESOLVERS.get(field)
    if resolver is not None:
        return resolver(driver, user)
    if field in _USER_FIELDS and user is not None:
        val = user.get(field)
        if _is_filled(val):
            return val
    return driver.get(field)


def compute_profile_completeness(driver: Dict, user: Optional[Dict] = None) -> Dict[str, Any]:
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

    score = round((filled_required / total_required) * 100) if total_required > 0 else 100

    return {
        "score": score,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "by_category": by_category,
        "total_required": total_required,
        "filled_required": filled_required,
    }
