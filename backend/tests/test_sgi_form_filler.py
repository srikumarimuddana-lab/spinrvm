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
            {"full_name": "Jane Doe", "license_number": "D1", "license_class": "5", "spinr_approved": True},
        ),
        sgi_field_maps.driver_to_driver_details_row(
            {"full_name": "John Smith", "license_number": "D2", "license_class": "5"}, action="remove"
        ),
    ]
    fields = _read_back(sgi_form_filler.fill_driver_details_form(rows))
    assert fields["Full name"].get("/V") == "Jane Doe"
    assert fields["Licence number"].get("/V") == "D1"
    assert fields["AddOrRemove"].get("/V") == "/0"
    assert fields["Verified driver history"].get("/V") == "/Yes"
    assert fields["Full name_2"].get("/V") == "John Smith"
    assert fields["AddOrRemove_2"].get("/V") == "/1"


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


def test_driver_to_vehicle_details_row_expired_inspection_is_false():
    """Regression test for the bug caught during development: fabricated
    column names silently evaluated to False forever. This confirms the
    real column (vehicle_inspection_expiry_date) is read and an expired
    date correctly resolves to False, not just "always False either way"."""
    row = sgi_field_maps.driver_to_vehicle_details_row(
        {"full_name": "Jane Doe", "vehicle_inspection_expiry_date": "2020-01-01"}
    )
    assert row["valid_inspection"] is False


def test_driver_to_vehicle_details_row_unexpired_inspection_is_true():
    row = sgi_field_maps.driver_to_vehicle_details_row(
        {"full_name": "Jane Doe", "vehicle_inspection_expiry_date": "2099-01-01"}
    )
    assert row["valid_inspection"] is True


def test_driver_to_driver_details_row_maps_real_columns():
    row = sgi_field_maps.driver_to_driver_details_row(
        {
            "full_name": "Jane Doe",
            "license_number": "D12345",
            "license_class": "5",
            "spinr_approved": True,
            "background_check_expiry_date": "2099-01-01",
        }
    )
    assert row["full_name"] == "Jane Doe"
    assert row["licence_number"] == "D12345"
    assert row["verified_driver_history"] is True
    assert row["criminal_record_check_attached"] is True
