"""Write an entity-export bundle as CSV, Excel, or JSON instead of a ZIP.

Used when the admin picks a single tabular format instead of the full
document+history ZIP (e.g. "just give me a CSV of these driver profiles").
Reuses the same CSV-cell-sanitization convention as
``admin-dashboard/src/lib/export-csv.ts`` (OWASP CSV-injection guard) on the
Python side, since these files may also be opened directly in Excel.
"""

import csv
import io
import json
from typing import Any

_CSV_INJECTION_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value: Any) -> Any:
    """Prefix formula-injection leads with a single-quote, matching the
    frontend's sanitizeCsvCell (export-csv.ts) so a value round-tripped
    through either path renders the same way in Excel/Sheets."""
    if isinstance(value, str) and value.startswith(_CSV_INJECTION_LEADS):
        return "'" + value
    return value


def _flatten_bundles(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per entity: profile fields flattened, nested lists summarized
    to counts (a spreadsheet row isn't the place for a driver's full ride
    history — use the ZIP/JSON export for that)."""
    rows = []
    for bundle in bundles:
        row: dict[str, Any] = {
            "entity_type": bundle["entity_type"],
            "entity_id": bundle["entity_id"],
            "rides_count": len(bundle.get("rides", [])),
            "documents_count": len(bundle.get("documents", [])),
            "insurance_periods_count": len(bundle.get("driver_insurance_periods", [])),
        }
        for k, v in (bundle.get("user") or {}).items():
            row[f"user_{k}"] = v
        for k, v in (bundle.get("driver_profile") or {}).items():
            row[f"driver_{k}"] = v
        rows.append(row)
    return rows


def write_csv(bundles: list[dict[str, Any]]) -> bytes:
    rows = _flatten_bundles(bundles)
    if not rows:
        return b""
    fieldnames = sorted({k for row in rows for k in row.keys()})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _sanitize_csv_cell(v) for k, v in row.items()})
    return buf.getvalue().encode("utf-8-sig")


def write_json(bundles: list[dict[str, Any]]) -> bytes:
    return json.dumps(bundles, indent=2, default=str).encode("utf-8")


def write_excel(bundles: list[dict[str, Any]]) -> bytes:
    from openpyxl import Workbook  # noqa: PLC0415 — optional dep, only needed for this format

    rows = _flatten_bundles(bundles)
    wb = Workbook()
    ws = wb.active
    ws.title = "entities"

    if rows:
        fieldnames = sorted({k for row in rows for k in row.keys()})
        ws.append(fieldnames)
        for row in rows:
            ws.append([_sanitize_csv_cell(row.get(f)) for f in fieldnames])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
