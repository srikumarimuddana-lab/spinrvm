"""Row-value mapping for the SGI D00032/D00033 forms.

Focus: the removal `effective_date`. Both mappers used to stamp
`_today_iso()` unconditionally, which is right for an add/change (the filing
takes effect when submitted) but wrong for a removal — SGI needs the date
the driver actually stopped driving, and a removal is routinely filed days
or weeks after the fact.
"""

import pytest

from backend.services.data_transfer import sgi_field_maps as maps

# Pinned, NOT datetime.now(): snapshotting the real date at import time made
# these tests fail whenever the suite straddled UTC midnight — the module
# constant said yesterday while _today_iso() inside the mapper said today.
# Observed on a 2026-08-08 run that crossed into 08-09. The mapper's own clock
# is stubbed below so "today" means one thing for the whole test.
_TODAY = "2026-03-04"


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    monkeypatch.setattr(maps, "_today_iso", lambda: _TODAY)


_DRIVER = {
    "name": "Jane Driver",
    "license_number": "SK1234567",
    "license_class": "5",
    "license_plate": "ABC 123",
    "vehicle_year": 2021,
    "vehicle_make": "Toyota",
    "vehicle_model": "Corolla",
    "vehicle_vin": "VIN123",
}

_DEPARTED = {
    **_DRIVER,
    "deleted_at": "2026-07-20T12:00:00Z",
    "regulator_removal_effective_date": "2026-07-20",
}


class TestRemovalEffectiveDate:
    def test_remove_uses_the_date_the_driver_stopped(self):
        assert maps.driver_to_driver_details_row(_DEPARTED, action="remove")["effective_date"] == "2026-07-20"
        assert maps.driver_to_vehicle_details_row(_DEPARTED, action="remove")["vehicle_date"] == "2026-07-20"

    def test_add_and_change_still_use_today(self):
        """An add/change takes effect on submission — only a removal is
        backdated."""
        for action in ("add", "change"):
            assert maps.driver_to_driver_details_row(_DEPARTED, action=action)["effective_date"] == _TODAY
            assert maps.driver_to_vehicle_details_row(_DEPARTED, action=action)["vehicle_date"] == _TODAY

    def test_remove_falls_back_to_deleted_at(self):
        """Rows tombstoned before regulator_removal_effective_date existed still
        know when the driver left."""
        row = {k: v for k, v in _DEPARTED.items() if k != "regulator_removal_effective_date"}
        assert maps.driver_to_driver_details_row(row, action="remove")["effective_date"] == "2026-07-20"

    def test_remove_falls_back_to_today_when_nothing_is_known(self):
        assert maps.driver_to_driver_details_row(_DRIVER, action="remove")["effective_date"] == _TODAY

    def test_date_is_truncated_to_a_day(self):
        """The column is a DATE but deleted_at is a TIMESTAMPTZ — SGI's field
        takes YYYY-MM-DD, not a full ISO timestamp."""
        row = {**_DRIVER, "regulator_removal_effective_date": "2026-07-20T12:00:00Z"}
        assert maps.driver_to_driver_details_row(row, action="remove")["effective_date"] == "2026-07-20"


class TestRowShapeUnchanged:
    """Guard the rest of the mapping while the date logic moves around it."""

    def test_driver_row_carries_licence_fields(self):
        row = maps.driver_to_driver_details_row(_DRIVER)
        assert row["full_name"] == "Jane Driver"
        assert row["licence_number"] == "SK1234567"
        assert row["licence_class"] == "5"
        assert row["action"] == "add"

    def test_vehicle_row_composes_year_make_model(self):
        row = maps.driver_to_vehicle_details_row(_DRIVER)
        assert row["year_make_model"] == "2021 Toyota Corolla"
        assert row["licence_plate_number"] == "ABC 123"
        assert row["action"] == "add"

    def test_null_columns_render_empty_not_none(self):
        """`.get(key, "")`'s default only fires when the key is MISSING; a NULL
        column has the key present with value None, which used to put the
        literal "None" on the regulator's form."""
        row = maps.driver_to_driver_details_row({"name": None, "license_number": None, "license_class": None})
        assert row["full_name"] == ""
        assert row["licence_number"] == ""
        assert row["licence_class"] == ""
