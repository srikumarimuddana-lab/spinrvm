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


# Only the raw bytes and the internal status marker are stripped from the
# manifest. _storage_key deliberately STAYS: bundle_document_uploader.py
# derives each document's file extension from it on import (a missing key
# falls back to ".bin", which has no entry in that module's _EXT_TO_MIME_TYPE
# and silently skips every document), so it is part of the export/import
# contract, not a
# leaked internal. The public bundled_file column below carries the same
# extension for readers that shouldn't need to know about underscore keys.
_INTERNAL_DOC_KEYS = ("_content", "_content_status")

# Mirrors entity_export_service.DOC_STATUS_* — duplicated as literals rather
# than imported so this module stays dependency-free and unit-testable
# without the Supabase-backed service (see this file's tests).
_STATUS_EXCLUDED = "excluded_by_request"
_STATUS_INCLUDED = "included"
_STATUS_UNAVAILABLE = "unavailable"


def _document_file_path(doc: dict[str, Any]) -> str:
    """Path (within the entity folder) this document's file is written to."""
    key = doc.get("_storage_key") or doc.get("id", "unknown")
    doc_type = doc.get("document_type", "document")
    ext = key.rsplit(".", 1)[-1] if "." in key else "bin"
    return f"documents/{doc_type}_{doc.get('id', '')}.{ext}"


def _has_file(doc: dict[str, Any]) -> bool:
    """Whether this document contributes an actual file to the ZIP.

    Single source of truth for both the manifest and the write loop below —
    if they used separate predicates they could disagree, and a manifest that
    disagrees with the archive it describes is the whole class of bug this
    module is trying to eliminate."""
    return bool(doc.get("_content"))


def _document_status(doc: dict[str, Any]) -> str:
    """Why this document does or doesn't have a file in the bundle.

    Falls back to inferring from _content for payloads built without an
    explicit status (older callers and the pure-function unit tests), so a
    missing status never reads as a successful include.

    A payload claiming "included" while carrying no usable bytes (e.g. a
    zero-byte object) is downgraded rather than trusted: this manifest
    describes THIS archive, and no file was written for it."""
    status = doc.get("_content_status")
    if _has_file(doc):
        return str(status) if status else _STATUS_INCLUDED
    if status and str(status) != _STATUS_INCLUDED:
        return str(status)
    return _STATUS_UNAVAILABLE


def _document_manifest(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document rows without the raw bytes — safe to serialize as CSV/JSON.

    Drops the internal underscore-prefixed keys (raw bytes, source-environment
    storage key) and replaces them with two columns an operator can act on:
    ``file_export_status`` and, when a file was written, ``bundled_file`` —
    the path to it inside this entity's folder."""
    rows = []
    for doc in documents:
        status = _document_status(doc)
        row = {k: v for k, v in doc.items() if k not in _INTERNAL_DOC_KEYS}
        row["file_export_status"] = status
        row["bundled_file"] = _document_file_path(doc) if _has_file(doc) else ""
        rows.append(row)
    return rows


def _document_status_counts(bundles: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for bundle in bundles:
        for doc in bundle.get("documents", []):
            status = _document_status(doc)
            counts[status] = counts.get(status, 0) + 1
    return counts


def _build_readme(bundles: list[dict[str, Any]], generated_on: str) -> str:
    counts = _document_status_counts(bundles)
    total_docs = sum(counts.values())
    included = counts.get(_STATUS_INCLUDED, 0)
    excluded = counts.get(_STATUS_EXCLUDED, 0)
    unavailable = total_docs - included - excluded

    lines = [
        "Spinr Data Transfer Export",
        f"Generated: {generated_on}",
        f"Entities: {len(bundles)}",
        "",
        "Each entity has its own folder containing:",
        "  user.csv, driver_profile.csv     Profile records",
        "  rides.csv                        Ride/trip history",
        "  driver_insurance_periods.csv     Regulatory insurance-period audit trail",
        "  documents.csv                    Document metadata, one row per document",
        "  raw_data.json                    The complete entity export in machine-readable JSON",
    ]
    if included:
        lines.append("  documents/<type>_<id>.<ext>      Document files in their original format")
    lines += [
        "",
        f"Documents: {total_docs} listed, {included} file(s) included in this ZIP.",
    ]

    # The metadata-only case is the one that gets reported as a bug, so say
    # plainly why there are no files and how to get them — an operator should
    # never have to guess whether the export or the storage bucket is broken.
    # Two distinct wordings: no files at all (probably not what they meant)
    # versus a deliberate mixed selection (exactly what they asked for, and
    # saying "file contents were not enabled" there would be simply untrue).
    if excluded and not included:
        lines += [
            "",
            f"NOTE: all {excluded} document(s) are listed as metadata only, with NO file in this ZIP.",
            'No document type had its "File" box ticked on the Export tab. Files are opt-in per',
            "document type for PIPEDA data minimization. To get the actual files (scans/images/PDFs),",
            'run the export again and tick "File" beside each document type you need.',
        ]
    elif excluded:
        lines += [
            "",
            f"NOTE: {excluded} of {total_docs} document(s) are listed as metadata only, with NO file in",
            'this ZIP — their "File" box was not ticked for this export. See file_export_status in',
            "documents.csv for which ones, and re-run ticking those types if you need their files.",
        ]
    if unavailable:
        lines += [
            "",
            f"WARNING: {unavailable} document(s) were requested but could NOT be retrieved from storage.",
            "See each row's file_export_status in documents.csv. This is a fault, not a setting —",
            "report it rather than re-running the export.",
        ]
    lines += [
        "",
        "documents.csv columns:",
        "  file_export_status   included | excluded_by_request | unavailable_*",
        "  bundled_file         path to the file inside this entity's folder (blank if none)",
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

            # A document with no bytes is skipped here but never disappears:
            # its documents.csv row carries file_export_status saying whether
            # it was excluded by request or genuinely unavailable.
            for doc in documents:
                if not _has_file(doc):
                    continue
                zf.writestr(f"{folder}/{_document_file_path(doc)}", doc["_content"])

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
