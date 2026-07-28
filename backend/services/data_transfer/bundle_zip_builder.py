"""Build the ZIP bundle for an entity-transfer export.

Same shape as ``routes/drivers/tax_exports.py::_build_export_zip`` (CSV per
category + README + raw_data.json), but per-entity subfolders for multi-select
export and original-format document files instead of a metadata-only CSV row.
"""

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    fieldnames = sorted({k for row in rows for k in row.keys()})
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _object_to_csv(obj: dict[str, Any]) -> str:
    return _rows_to_csv([obj]) if obj else ""


def _entity_folder_name(bundle: dict[str, Any]) -> str:
    label = bundle["driver_profile"].get("id") or bundle["user"].get("id") or bundle["entity_id"]
    return f"{bundle['entity_type']}_{label}"


def _document_manifest(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document rows without the raw bytes — safe to serialize as CSV/JSON."""
    return [{k: v for k, v in d.items() if k not in ("_content",)} for d in documents]


def _build_readme(bundles: list[dict[str, Any]], generated_on: str) -> str:
    lines = [
        "Spinr Data Transfer Export",
        f"Generated: {generated_on}",
        f"Entities: {len(bundles)}",
        "",
        "Each entity has its own folder containing:",
        "  user.csv, driver_profile.csv     Profile records",
        "  rides.csv                        Ride/trip history",
        "  driver_insurance_periods.csv     Regulatory insurance-period audit trail",
        "  documents.csv                    Document metadata (see documents/ for files)",
        "  documents/<original filename>    Document files in their original format",
        "  raw_data.json                    The complete entity export in machine-readable JSON",
        "",
        "Import this ZIP via Admin > Data Transfer > Import on the target environment.",
    ]
    return "\n".join(lines)


def build_export_zip(bundles: list[dict[str, Any]]) -> bytes:
    generated_on = datetime.now(timezone.utc).isoformat()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _build_readme(bundles, generated_on))
        for bundle in bundles:
            folder = _entity_folder_name(bundle)
            documents = bundle.get("documents", [])

            zf.writestr(f"{folder}/user.csv", _object_to_csv(bundle["user"]))
            zf.writestr(f"{folder}/driver_profile.csv", _object_to_csv(bundle["driver_profile"]))
            zf.writestr(f"{folder}/rides.csv", _rows_to_csv(bundle["rides"]))
            zf.writestr(
                f"{folder}/driver_insurance_periods.csv",
                _rows_to_csv(bundle["driver_insurance_periods"]),
            )
            zf.writestr(f"{folder}/documents.csv", _rows_to_csv(_document_manifest(documents)))

            for doc in documents:
                content = doc.get("_content")
                if not content:
                    continue
                key = doc.get("_storage_key") or doc.get("id", "unknown")
                doc_type = doc.get("document_type", "document")
                ext = key.rsplit(".", 1)[-1] if "." in key else "bin"
                zf.writestr(f"{folder}/documents/{doc_type}_{doc.get('id', '')}.{ext}", content)

            raw = {
                "entity_type": bundle["entity_type"],
                "entity_id": bundle["entity_id"],
                "user": bundle["user"],
                "driver_profile": bundle["driver_profile"],
                "notification_preferences": bundle["notification_preferences"],
                "rides": bundle["rides"],
                "driver_insurance_periods": bundle["driver_insurance_periods"],
                "documents": _document_manifest(documents),
            }
            zf.writestr(f"{folder}/raw_data.json", json.dumps(raw, indent=2, default=str))

    return buf.getvalue()
