"""Fill the real SGI (Saskatchewan Government Insurance) AcroForm PDFs —
"Passenger for Hire – Driver Details" (D00032, 86 fields, 2 pages) and
"Transportation Network Company – Vehicle Details" (D00033, 128 fields, 3
pages) — from driver/vehicle records, for compliance submission to SGI.

Templates live at ``backend/static/sgi_forms/`` and already carry Spinr's
company-section fields pre-filled (company name, SGI customer number,
address, primary contact) from the real forms Spinr uses — only the
per-driver / per-vehicle repeating rows are filled here.

Row-slot naming in the real PDFs is NOT uniform (confirmed by inspecting
every field name in both templates) — row 1 of some field types uses a bare
name, others use an explicit "1" suffix, and one field name in the vehicle
form has a typo ("YeaMakeModel5" instead of "YearMakeModel5"). The
``_DRIVER_ROW_FIELDS``/``_VEHICLE_ROW_FIELDS`` slot generators below encode
these exactly as observed — do not "correct" the typo, since pypdf fills by
exact AcroForm field name and the form itself has to be resubmitted to SGI
in its original shape.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader, PdfWriter

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "static" / "sgi_forms"
DRIVER_TEMPLATE = TEMPLATE_DIR / "D00032_driver_details_template.pdf"
VEHICLE_TEMPLATE = TEMPLATE_DIR / "D00033_vehicle_details_template.pdf"

MAX_DRIVER_ROWS = 10
MAX_VEHICLE_ROWS = 16

# Company-section values, set explicitly on every generated form rather than
# relying on the template's baked-in defaults. D00033 is a 3-page AcroForm
# with an independent company-name/customer-number field per page
# (CompanyName/CompanyName2/CompanyName3); the template only ships page 1
# filled in, so pages 2-3 rendered blank on every generated form until this
# was set explicitly here. Update these constants (not the PDF templates)
# if Spinr's registered company name, SGI customer number, or address
# changes.
#
# Address is split into its own street/city/province/postal parts rather
# than one combined string: both real templates (confirmed via
# PdfReader.get_fields()) already ship dedicated "Provincestate"/
# "Postalzip[ ]code"/"Cit[y/town]" fields alongside the street-address
# field. Previously this module set only the street-address field to the
# full "STREET, CITY, PROVINCE, COUNTRY, POSTAL" string and left those
# dedicated fields untouched at the template's own stale placeholder
# values (a different address entirely) — producing a generated PDF with
# two different, disagreeing addresses across its own fields. Neither
# template has a "Country" field at all, so country is dropped rather than
# crammed into a field that isn't meant to hold it.
_COMPANY_NAME = "Spinr Mobility Inc."
_COMPANY_STREET = "#200, 1956 Broad Street"
_COMPANY_CITY = "Regina"
_COMPANY_PROVINCE = "SK"
_COMPANY_POSTAL = "S4P 1Y1"
_SGI_CUSTOMER_NUMBER = "88816996"

# D00032 has a single company-info section (one page's worth of fields).
_DRIVER_COMPANY_FIELDS = {
    "Company name": _COMPANY_NAME,
    "Street address": _COMPANY_STREET,
    "City/town": _COMPANY_CITY,
    "Provincestate": _COMPANY_PROVINCE,
    "Postalzip code": _COMPANY_POSTAL,
    "SGI customer number": _SGI_CUSTOMER_NUMBER,
}
# D00033 repeats the company-name/customer-number block once per page, but
# confirmed via PdfReader.get_fields() the address block (StreetAddress/
# Citytown/Provincestate/Postalzipcode) has no page-2/3 counterpart at all
# — one address block covers the whole form.
_VEHICLE_COMPANY_FIELDS = {
    "CompanyName": _COMPANY_NAME,
    "CompanyName2": _COMPANY_NAME,
    "CompanyName3": _COMPANY_NAME,
    "StreetAddress": _COMPANY_STREET,
    "Citytown": _COMPANY_CITY,
    "Provincestate": _COMPANY_PROVINCE,
    "Postalzipcode": _COMPANY_POSTAL,
    "SGICustomerNumber": _SGI_CUSTOMER_NUMBER,
    "SGICustomerNumber2": _SGI_CUSTOMER_NUMBER,
    "SGICustomerNumber3": _SGI_CUSTOMER_NUMBER,
}

# AddOrRemove's AcroForm export states are numeric indices ('/0', '/1',
# '/2'), not the "Add"/"Remove"/"Change" labels printed next to the
# checkboxes on the form (confirmed via PdfReader.get_fields()['AddOrRemove']
# ['/_States_']) — order matches the label order on the PDF.
_ADD_REMOVE_CHANGE_INDEX = {"add": "/0", "remove": "/1", "change": "/2"}
# Vehicle<N>'s export states ARE the literal labels (unlike AddOrRemove) —
# confirmed separately via get_fields()['Vehicle1']['/_States_'].
_VEHICLE_ACTION_STATE = {"add": "/Add", "remove": "/Remove", "change": "/Change"}


class TooManyRowsError(ValueError):
    pass


def _driver_row_fields(row: int) -> dict[str, str]:
    """Field names for driver-details row `row` (1-indexed)."""
    suffix = "" if row == 1 else f"_{row}"
    date_field = "EffectiveDate_af_date" if row == 1 else f"Date{row}_af_date"
    return {
        "full_name": f"Full name{suffix}",
        "add_or_remove": f"AddOrRemove{suffix}",
        "licence_number": f"Licence number{suffix}",
        "licence_class": f"Licence class{suffix}",
        "verified_driver_history": f"Verified driver history{suffix}",
        "criminal_record_check_attached": f"Criminal record check attached{suffix}",
        "effective_date": date_field,
    }


def _vehicle_row_fields(row: int) -> dict[str, str]:
    """Field names for vehicle-details row `row` (1-indexed). Encodes the
    real form's inconsistent row-1 naming and the YeaMakeModel5 typo."""
    year_make_model = "YearMakeModel" if row == 1 else ("YeaMakeModel5" if row == 5 else f"YearMakeModel{row}")
    registered_owner = "RegisteredOwnersName" if row == 1 else f"RegisteredOwnersName{row}"
    return {
        "licence_plate_number": f"LicencePlateNumber{row}",
        "vin": f"VIN{row}",
        "year_make_model": year_make_model,
        "registered_owners_name": registered_owner,
        "vehicle_action": f"Vehicle{row}",
        "valid_inspection": f"ValidInspection{row}",
        "vehicle_date": f"VechicleDate{row}_af_date",
    }


def _fill(template_path: Path, field_values: dict[str, Any]) -> bytes:
    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values, auto_regenerate=False)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def fill_driver_details_form(drivers: list[dict[str, Any]]) -> bytes:
    """Fill D00032 with up to MAX_DRIVER_ROWS drivers. Each dict may carry:
    full_name, licence_number, licence_class, effective_date (ISO string),
    add (bool, default True = 'Add' action), verified_driver_history (bool),
    criminal_record_check_attached (bool)."""
    if len(drivers) > MAX_DRIVER_ROWS:
        raise TooManyRowsError(f"{len(drivers)} drivers requested; the D00032 template has {MAX_DRIVER_ROWS} rows")

    field_values: dict[str, Any] = dict(_DRIVER_COMPANY_FIELDS)
    for i, driver in enumerate(drivers, start=1):
        slots = _driver_row_fields(i)
        field_values[slots["full_name"]] = driver.get("full_name") or ""
        field_values[slots["licence_number"]] = driver.get("licence_number") or ""
        field_values[slots["licence_class"]] = driver.get("licence_class") or ""
        field_values[slots["effective_date"]] = driver.get("effective_date") or ""
        field_values[slots["add_or_remove"]] = _ADD_REMOVE_CHANGE_INDEX.get(driver.get("action", "add"), "/0")
        field_values[slots["verified_driver_history"]] = "/Yes" if driver.get("verified_driver_history") else "/No"
        field_values[slots["criminal_record_check_attached"]] = (
            "/Yes" if driver.get("criminal_record_check_attached") else "/No"
        )

    # The real template PDF ships with stale baked-in /V values on EVERY
    # row slot (confirmed via PdfReader.get_fields() — e.g. AddOrRemove_2/
    # _3/_4 all default to "Add" selected, Date_af_date defaults to a
    # leftover "Feb-20-2026" from whatever real filled-out form the
    # template was captured from). Rows beyond len(drivers) were never
    # written above, so they'd silently keep showing that stale
    # data/selection on the generated PDF. Explicitly blank every unused
    # row rather than leaving the template's leftover values in place.
    for i in range(len(drivers) + 1, MAX_DRIVER_ROWS + 1):
        slots = _driver_row_fields(i)
        field_values[slots["full_name"]] = ""
        field_values[slots["licence_number"]] = ""
        field_values[slots["licence_class"]] = ""
        field_values[slots["effective_date"]] = ""
        field_values[slots["add_or_remove"]] = "/Off"
        field_values[slots["verified_driver_history"]] = "/Off"
        field_values[slots["criminal_record_check_attached"]] = "/Off"

    return _fill(DRIVER_TEMPLATE, field_values)


def fill_vehicle_details_form(vehicles: list[dict[str, Any]]) -> bytes:
    """Fill D00033 with up to MAX_VEHICLE_ROWS vehicles. Each dict may carry:
    licence_plate_number, vin, year_make_model, registered_owners_name,
    vehicle_date (ISO string), valid_inspection (bool), action
    ("add"|"remove"|"change", default "add")."""
    if len(vehicles) > MAX_VEHICLE_ROWS:
        raise TooManyRowsError(f"{len(vehicles)} vehicles requested; the D00033 template has {MAX_VEHICLE_ROWS} rows")

    field_values: dict[str, Any] = dict(_VEHICLE_COMPANY_FIELDS)
    for i, vehicle in enumerate(vehicles, start=1):
        slots = _vehicle_row_fields(i)
        field_values[slots["licence_plate_number"]] = vehicle.get("licence_plate_number") or ""
        field_values[slots["vin"]] = vehicle.get("vin") or ""
        field_values[slots["year_make_model"]] = vehicle.get("year_make_model") or ""
        field_values[slots["registered_owners_name"]] = vehicle.get("registered_owners_name") or ""
        field_values[slots["vehicle_date"]] = vehicle.get("vehicle_date") or ""
        field_values[slots["valid_inspection"]] = "/Yes" if vehicle.get("valid_inspection") else "/No"
        field_values[slots["vehicle_action"]] = _VEHICLE_ACTION_STATE.get(vehicle.get("action", "add"), "/Add")

    # Same stale-template-default problem as fill_driver_details_form above
    # — every VechicleDateN_af_date field ships baked to a leftover
    # "Feb-20-2026", and Vehicle1/ValidInspection1 ship pre-selected.
    # Blank every row beyond len(vehicles) explicitly.
    for i in range(len(vehicles) + 1, MAX_VEHICLE_ROWS + 1):
        slots = _vehicle_row_fields(i)
        field_values[slots["licence_plate_number"]] = ""
        field_values[slots["vin"]] = ""
        field_values[slots["year_make_model"]] = ""
        field_values[slots["registered_owners_name"]] = ""
        field_values[slots["vehicle_date"]] = ""
        field_values[slots["valid_inspection"]] = "/Off"
        field_values[slots["vehicle_action"]] = "/Off"

    return _fill(VEHICLE_TEMPLATE, field_values)


FORM_FILLERS: dict[str, Callable[[list[dict[str, Any]]], bytes]] = {
    "driver_details": fill_driver_details_form,
    "vehicle_details": fill_vehicle_details_form,
}
FORM_MAX_ROWS: dict[str, int] = {
    "driver_details": MAX_DRIVER_ROWS,
    "vehicle_details": MAX_VEHICLE_ROWS,
}
