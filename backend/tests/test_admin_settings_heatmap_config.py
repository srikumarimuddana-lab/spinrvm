"""The admin Settings surface must actually persist the heatmap config.

Regression suite for the AD-05 / HM-13 silent no-op: the admin "Heatmap
Config" tab PUT seven keys to ``/api/admin/settings``, but none of them was
declared on ``SettingsUpdateRequest``. Because that model is
``extra="ignore"``, every key was dropped at validation while the endpoint
still returned 200 and wrote an audit row with ``changed_keys: []`` — so the
UI reported success, the audit log corroborated it, and nothing changed.
Worse, one of the dropped keys is ``heatmap_k_floor``, a PIPEDA
k-anonymity control.

Mirrors the structure of ``test_admin_settings_company_app_name.py`` (same
failure class, same three-layer check: request model accepts it, AppSettings
defaults it, partial saves don't clobber). Adds bounds assertions because
these values are read on a per-request driver path where an out-of-range
value is a self-inflicted DoS or a disabled privacy floor.

Layer 3 (the DB CHECK constraint) lives in migration
``311_settings_heatmap_config.sql``; layer 1 (runtime clamps at the read
site) is covered in ``test_drivers_shared_status_profile_coverage.py``.
"""

import pytest
from pydantic import ValidationError

from routes.admin.settings import SettingsUpdateRequest as SettingsUpdate
from schemas import AppSettings

pytestmark = [pytest.mark.unit]

# Every key the admin Heatmap Config tab sends.
HEATMAP_KEYS = {
    "driver_heatmap_enabled": False,
    "driver_heatmap_v2_enabled": True,
    "heatmap_internal_driver_ids": ["user-1", "user-2"],
    "heatmap_k_floor": 5,
    "heatmap_cell_lat_deg": 0.006,
    "heatmap_cell_lng_deg": 0.008,
    "heatmap_decay_half_life_days": 2.5,
    "heatmap_refresh_seconds": 120,
}


def test_every_heatmap_key_survives_validation():
    """The whole tab payload must round-trip — this is the actual B1 bug."""
    dumped = SettingsUpdate(**HEATMAP_KEYS).model_dump(exclude_none=True)
    for key, value in HEATMAP_KEYS.items():
        assert key in dumped, f"{key} was silently dropped by SettingsUpdateRequest"
        assert dumped[key] == value


def test_heatmap_keys_are_omitted_when_untouched():
    """exclude_none=True is how a partial save avoids clobbering other fields."""
    dumped = SettingsUpdate().model_dump(exclude_none=True)
    for key in HEATMAP_KEYS:
        assert key not in dumped


def test_app_settings_defaults_match_historical_hardcoded_fallbacks():
    """An unconfigured row must reproduce today's behaviour byte-for-byte."""
    s = AppSettings()
    assert s.driver_heatmap_enabled is True  # feature on unless killed
    assert s.driver_heatmap_v2_enabled is False  # v2 ships dark
    assert s.heatmap_internal_driver_ids == []
    assert s.heatmap_k_floor == 3
    assert s.heatmap_cell_lat_deg == 0.004
    assert s.heatmap_cell_lng_deg == 0.006
    assert s.heatmap_decay_half_life_days == 3.0
    assert s.heatmap_refresh_seconds == 90


def test_kill_switch_is_savable_as_false():
    """The whole point of a kill switch is being able to set it off."""
    dumped = SettingsUpdate(driver_heatmap_enabled=False).model_dump(exclude_none=True)
    assert dumped["driver_heatmap_enabled"] is False


def test_empty_allowlist_is_savable():
    """Clearing the dark-launch allowlist must not be indistinguishable from untouched."""
    dumped = SettingsUpdate(heatmap_internal_driver_ids=[]).model_dump(exclude_none=True)
    assert dumped["heatmap_internal_driver_ids"] == []


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("heatmap_k_floor", 0),  # would disable the k-anonymity floor entirely
        ("heatmap_k_floor", -1),
        ("heatmap_k_floor", 51),
        ("heatmap_refresh_seconds", 1),  # fleet-wide 1s polling = self-DoS
        ("heatmap_refresh_seconds", 0),
        ("heatmap_refresh_seconds", 601),
        ("heatmap_cell_lat_deg", 0),  # ZeroDivisionError in the cell-key math
        ("heatmap_cell_lng_deg", 0),
        ("heatmap_cell_lat_deg", 0.5),  # one cell for the whole province
        ("heatmap_decay_half_life_days", 0),
        ("heatmap_decay_half_life_days", 90),
    ],
)
def test_out_of_range_values_are_rejected(field, bad_value):
    with pytest.raises(ValidationError):
        SettingsUpdate(**{field: bad_value})


def test_allowlist_length_is_capped():
    """An unbounded paste must not be parkable in the settings row."""
    with pytest.raises(ValidationError):
        SettingsUpdate(heatmap_internal_driver_ids=[f"u{i}" for i in range(501)])
