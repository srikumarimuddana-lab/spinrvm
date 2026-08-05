"""Unit tests for tabular_writer.py — pure functions, no DB/network."""

import json

from backend.services.data_transfer import tabular_writer


def _bundle(documents):
    return {
        "entity_type": "driver",
        "entity_id": "u1",
        "user": {"id": "u1"},
        "driver_profile": {"id": "drv1"},
        "notification_preferences": [],
        "rides": [],
        "documents": documents,
        "driver_insurance_periods": [],
    }


def test_write_json_never_serializes_raw_document_bytes():
    """json.dumps(default=str) stringifies bytes rather than skipping them,
    so an unfiltered dump wrote every scan into the JSON as "b'...'".
    Document bytes are the most sensitive payload this export carries."""
    doc = {
        "id": "doc-1",
        "document_type": "background_check",
        "_content": b"RAW-SCAN-BYTES",
        "_content_status": "included",
    }
    out = tabular_writer.write_json([_bundle([doc])]).decode()

    assert "RAW-SCAN-BYTES" not in out
    assert "_content" not in out
    parsed = json.loads(out)
    # Metadata survives, and the outcome is still reported.
    assert parsed[0]["documents"][0]["id"] == "doc-1"
    assert parsed[0]["documents"][0]["file_export_status"] == "included"


def test_write_json_reports_status_for_documents_without_bytes():
    doc = {"id": "doc-2", "document_type": "insurance", "_content": None, "_content_status": "excluded_by_request"}
    parsed = json.loads(tabular_writer.write_json([_bundle([doc])]).decode())
    assert parsed[0]["documents"][0]["file_export_status"] == "excluded_by_request"


def test_write_json_handles_bundles_with_no_documents_key():
    parsed = json.loads(tabular_writer.write_json([{"entity_type": "rider", "entity_id": "u2"}]).decode())
    assert parsed[0]["documents"] == []


def test_csv_and_excel_still_summarize_document_counts_only():
    """These formats never carried document bytes; confirm the new JSON
    sanitizer didn't disturb their one-row-per-entity shape."""
    doc = {"id": "d", "document_type": "insurance", "_content": b"x"}
    csv_out = tabular_writer.write_csv([_bundle([doc])]).decode("utf-8-sig")
    assert "documents_count" in csv_out
    assert "x" not in csv_out.split("\n")[1].split(",")[0]
