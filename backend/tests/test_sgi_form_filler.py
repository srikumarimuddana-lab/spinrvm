"""Unit tests for sgi_form_filler.py + sgi_field_maps.py against the real
SGI AcroForm templates. Exercises the actual PDF fill (not mocked) since the
whole point of this module is matching exact field names/export states in a
real government form — a mock would hide exactly the class of bug this
caught during development (a field defined but never populated, and
fabricated column names that silently defaulted to False).
"""

import io

from pypdf import PdfReader

from backend.services.data_transfer import sgi_field_maps, sgi_form_filler


def _read_back(pdf_bytes: bytes) -> dict:
    return PdfReader(io.BytesIO(pdf_bytes)).get_fields()


def test_fill_driver_details_form_row_one_and_two():
    rows = [
        sgi_field_maps.driver_to_driver_details_row(
            {"name": "Jane Doe", "license_number": "D1", "license_class": "5"},
        ),
        sgi_field_maps.driver_to_driver_details_row(
            {"name": "John Smith", "license_number": "D2", "license_class": "5"}, action="remove"
        ),
    ]
    fields = _read_back(sgi_form_filler.fill_driver_details_form(rows))
    assert fields["Full name"].get("/V") == "Jane Doe"
    assert fields["Licence number"].get("/V") == "D1"
    assert fields["AddOrRemove"].get("/V") == "/0"
    assert fields["Verified driver history"].get("/V") == "/Yes"
    assert fields["Full name_2"].get("/V") == "John Smith"
    assert fields["AddOrRemove_2"].get("/V") == "/1"


def test_fill_driver_details_form_address_split_across_dedicated_fields():
    # Regression: the company address was previously crammed as one
    # "STREET, CITY, PROVINCE, COUNTRY, POSTAL" string into "Street
    # address" only, leaving the template's own dedicated City/town,
    # Provincestate, and Postalzip code fields at their stale placeholder
    # values — a generated PDF with two disagreeing addresses across its
    # own fields. Each component must now land in its own field, and the
    # street-address value must contain no city/province/postal/country.
    fields = _read_back(sgi_form_filler.fill_driver_details_form([]))
    assert fields["Street address"].get("/V") == "#200, 1956 Broad Street"
    assert fields["City/town"].get("/V") == "Regina"
    assert fields["Provincestate"].get("/V") == "SK"
    assert fields["Postalzip code"].get("/V") == "S4P 1Y1"
    street = fields["Street address"].get("/V")
    for leaked in ("Regina", "Saskatchewan", "SK", "Canada", "S4P"):
        assert leaked.lower() not in street.lower(), f"{leaked!r} leaked into Street address: {street!r}"


def test_fill_vehicle_details_form_address_split_across_dedicated_fields():
    fields = _read_back(sgi_form_filler.fill_vehicle_details_form([]))
    assert fields["StreetAddress"].get("/V") == "#200, 1956 Broad Street"
    assert fields["Citytown"].get("/V") == "Regina"
    assert fields["Provincestate"].get("/V") == "SK"
    assert fields["Postalzipcode"].get("/V") == "S4P 1Y1"
    street = fields["StreetAddress"].get("/V")
    for leaked in ("Regina", "Saskatchewan", "SK", "Canada", "S4P"):
        assert leaked.lower() not in street.lower(), f"{leaked!r} leaked into StreetAddress: {street!r}"


def test_fill_driver_details_form_rejects_too_many_rows():
    rows = [{"full_name": f"Driver {i}"} for i in range(sgi_form_filler.MAX_DRIVER_ROWS + 1)]
    try:
        sgi_form_filler.fill_driver_details_form(rows)
        assert False, "expected TooManyRowsError"
    except sgi_form_filler.TooManyRowsError:
        pass


def test_fill_vehicle_details_form_row_five_typo_field():
    """Row 5's YearMakeModel field is misnamed 'YeaMakeModel5' in the real
    government PDF — the filler must target that exact name, not a
    'corrected' one, or the value silently lands nowhere."""
    rows = [{"licence_plate_number": f"PLT{i}", "year_make_model": f"Model {i}"} for i in range(1, 6)]
    fields = _read_back(sgi_form_filler.fill_vehicle_details_form(rows))
    assert fields["YearMakeModel"].get("/V") == "Model 1"
    assert fields["YeaMakeModel5"].get("/V") == "Model 5"
    assert "YearMakeModel5" not in fields  # confirms the typo is real, not a naming assumption


def test_fill_vehicle_details_form_sets_vehicle_action():
    """Regression test for the bug caught during development: vehicle_action
    was defined as a row slot but never actually written to field_values."""
    rows = [{"licence_plate_number": "PLT1", "action": "add"}]
    fields = _read_back(sgi_form_filler.fill_vehicle_details_form(rows))
    assert fields["Vehicle1"].get("/V") == "/Add"


def test_fill_vehicle_details_form_rejects_too_many_rows():
    rows = [{"licence_plate_number": f"PLT{i}"} for i in range(sgi_form_filler.MAX_VEHICLE_ROWS + 1)]
    try:
        sgi_form_filler.fill_vehicle_details_form(rows)
        assert False, "expected TooManyRowsError"
    except sgi_form_filler.TooManyRowsError:
        pass


def test_driver_to_vehicle_details_row_inspection_defaults_true():
    """`valid_inspection` defaults to Yes regardless of expiry data — see
    the docstring on driver_to_vehicle_details_row for why."""
    row = sgi_field_maps.driver_to_vehicle_details_row(
        {"name": "Jane Doe", "vehicle_inspection_expiry_date": "2020-01-01"}
    )
    assert row["valid_inspection"] is True


def test_driver_to_vehicle_details_row_maps_real_owner_name_column():
    """Regression test: an earlier version read `driver.full_name`, which
    doesn't exist on the `drivers` table (the real column is `name`) —
    silently blanking the registered owner's name on every generated form."""
    row = sgi_field_maps.driver_to_vehicle_details_row({"name": "Jane Doe"})
    assert row["registered_owners_name"] == "Jane Doe"


def test_driver_to_driver_details_row_maps_real_columns():
    """Regression test: an earlier version read `driver.full_name` (doesn't
    exist on `drivers` — the real column is `name`), silently blanking the
    driver's name on every generated D00032. `verified_driver_history` and
    `criminal_record_check_attached` default to Yes regardless of input —
    see the docstring on driver_to_driver_details_row for why."""
    row = sgi_field_maps.driver_to_driver_details_row(
        {
            "name": "Jane Doe",
            "license_number": "D12345",
            "license_class": "5",
        }
    )
    assert row["full_name"] == "Jane Doe"
    assert row["licence_number"] == "D12345"
    assert row["verified_driver_history"] is True
    assert row["criminal_record_check_attached"] is True
