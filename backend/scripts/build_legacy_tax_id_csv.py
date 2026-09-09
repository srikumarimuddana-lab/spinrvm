#!/usr/bin/env python3
"""Build the `phone,sin,gst_bn` CSV that /admin/tax-ids/import/validate requires,
from the raw MongoDB export's `banks.csv` + `drivers.csv`.

Why this exists: banks.csv (Mongo-ObjectId-keyed) carries every legacy driver's
SIN and GST/HST business number, but it's keyed by `driver_id` (a Mongo
ObjectId), not a phone number -- and `backend/routes/admin/tax_id_import.py`
matches rows to Spinr drivers by phone. This script performs the exact same
`driver_id` -> `drivers.csv._id` -> phone join that
`services/driver_import_service.join_legacy_bank_sin_dob` already uses (and
has already been production-verified) for the sibling SIN/DOB backfill, reusing
that function directly rather than re-deriving the join, then adds the one
field that tool doesn't need: `gst`.

PII handling (PIPEDA): this script reads/writes real SIN and GST/HST business
numbers. It never writes a row's own contact number, SIN, or GST value to the
terminal -- only aggregate counts. The output CSV is written next to the input files (NOT into this git
repository) and must be deleted locally once it's been uploaded to the admin
tool; it must never be committed, emailed unencrypted, or pasted into a chat
log.

Usage:
    python backend/scripts/build_legacy_tax_id_csv.py \\
        --banks-csv /path/to/banks.csv \\
        --drivers-csv /path/to/drivers.csv \\
        --out /path/to/tax_id_import_ready.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

# Allow running as `python scripts/build_legacy_tax_id_csv.py` or
# `python backend/scripts/build_legacy_tax_id_csv.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from services.driver_import_service import join_legacy_bank_sin_dob, read_mongo_export_csv
except ImportError:
    from backend.services.driver_import_service import join_legacy_bank_sin_dob, read_mongo_export_csv

try:
    from routes.drivers.payouts import _GST_BN_RE
except ImportError:
    from backend.routes.drivers.payouts import _GST_BN_RE

try:
    from utils.sin import validate_sin
except ImportError:
    from backend.utils.sin import validate_sin

_OUT_HEADER = ["phone", "sin", "gst_bn"]


def build_rows(bank_rows: list[dict[str, str]], driver_rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict]:
    """Returns (output_rows, stats). Never includes a value in `stats` that
    could identify a row (no phone/SIN/GST, no row indices) -- counts only."""
    joined = join_legacy_bank_sin_dob(bank_rows, driver_rows)
    assert len(joined) == len(bank_rows)  # join preserves 1:1 order, see its own docstring

    stats = {
        "banks_rows": len(bank_rows),
        "unmatched_no_phone": 0,
        "skipped_no_sin_or_gst": 0,
        "sin_present": 0,
        "sin_fails_format": 0,
        "gst_present": 0,
        "gst_fails_format": 0,
        "rows_written": 0,
    }
    out_rows: list[dict[str, str]] = []

    for joined_row, bank_row in zip(joined, bank_rows, strict=True):
        phone = joined_row["phone"]
        if not phone:
            stats["unmatched_no_phone"] += 1
            continue

        sin_raw = joined_row["sin_raw"] or ""
        gst_raw = (bank_row.get("gst") or "").strip()
        # Same normalization tax_id_import.py's own _build_plan applies before
        # matching against _GST_BN_RE -- doing it here means what this script
        # writes is exactly what the tool will see.
        gst_norm = gst_raw.replace(" ", "").upper()

        if sin_raw:
            stats["sin_present"] += 1
            try:
                validate_sin(sin_raw)
            except ValueError:
                stats["sin_fails_format"] += 1
        if gst_norm:
            stats["gst_present"] += 1
            if not _GST_BN_RE.match(gst_norm):
                stats["gst_fails_format"] += 1

        if not sin_raw and not gst_norm:
            stats["skipped_no_sin_or_gst"] += 1
            continue

        out_rows.append({"phone": phone, "sin": sin_raw, "gst_bn": gst_norm})
        stats["rows_written"] += 1

    return out_rows, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--banks-csv", required=True, type=Path)
    parser.add_argument("--drivers-csv", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    bank_rows = read_mongo_export_csv(args.banks_csv)
    driver_rows = read_mongo_export_csv(args.drivers_csv)

    out_rows, stats = build_rows(bank_rows, driver_rows)

    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_OUT_HEADER)
        writer.writeheader()
        writer.writerows(out_rows)

    no_driver_match = stats["unmatched_no_phone"]
    print(f"banks.csv rows read:              {stats['banks_rows']}")
    print(f"  no matching driver record:      {no_driver_match}")
    print(f"  had neither SIN nor GST:        {stats['skipped_no_sin_or_gst']}")
    print(f"  written to output:              {stats['rows_written']}")
    print(f"SIN present:                      {stats['sin_present']} ({stats['sin_fails_format']} look format-invalid)")
    print(f"GST BN present:                   {stats['gst_present']} ({stats['gst_fails_format']} look format-invalid)")
    print(f"Wrote: {args.out}")
    print(
        "Reminder: this file contains real SIN and GST/HST numbers. Upload it to "
        "the admin tool, then delete this local copy -- do not commit it or paste "
        "its contents anywhere."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
