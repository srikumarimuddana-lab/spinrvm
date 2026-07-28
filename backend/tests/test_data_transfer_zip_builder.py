"""Unit tests for bundle_zip_builder.py — pure functions, no DB/network."""

import json
import zipfile
from io import BytesIO

from backend.services.data_transfer import bundle_zip_builder as builder


def _bundle(entity_type="driver", entity_id="d1", documents=None):
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "user": {"id": entity_id, "full_name": "Jane Doe", "phone": "3065551234"},
        "driver_profile": {"id": "driver-row-1", "license_plate": "ABC123"} if entity_type == "driver" else {},
        "notification_preferences": [],
        "rides": [{"id": "ride-1", "status": "completed"}],
        "documents": documents or [],
        "driver_insurance_periods": [{"period": 1, "started_at": "2026-01-01T00:00:00Z"}],
    }


def test_single_entity_zip_has_expected_files():
    zip_bytes = builder.build_export_zip([_bundle()])
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    names = zf.namelist()
    assert "README.txt" in names
    folder = [n for n in names if n.startswith("driver_")][0].split("/")[0]
    assert f"{folder}/user.csv" in names
    assert f"{folder}/driver_profile.csv" in names
    assert f"{folder}/rides.csv" in names
    assert f"{folder}/driver_insurance_periods.csv" in names
    assert f"{folder}/documents.csv" in names
    assert f"{folder}/raw_data.json" in names


def test_multi_entity_zip_has_one_folder_per_entity():
    bundles = [_bundle(entity_id="d1"), _bundle(entity_type="rider", entity_id="r1")]
    zip_bytes = builder.build_export_zip(bundles)
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    top_level_folders = {n.split("/")[0] for n in zf.namelist() if "/" in n}
    assert any(f.startswith("driver_") for f in top_level_folders)
    assert any(f.startswith("rider_") for f in top_level_folders)


def test_document_with_bytes_is_written_as_a_file():
    doc = {
        "id": "doc-1",
        "document_type": "drivers_license",
        "_storage_key": "abc123.pdf",
        "_content": b"%PDF-1.4 fake content",
    }
    zip_bytes = builder.build_export_zip([_bundle(documents=[doc])])
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    doc_files = [n for n in zf.namelist() if "/documents/" in n]
    assert len(doc_files) == 1
    assert zf.read(doc_files[0]) == b"%PDF-1.4 fake content"


def test_document_without_content_is_skipped_but_listed_in_manifest():
    """A document whose bytes couldn't be fetched at export time (see
    entity_export_service's _content=None fallback) must not produce a
    dangling/empty file — but its metadata still appears in documents.csv."""
    doc = {"id": "doc-2", "document_type": "insurance", "_storage_key": "missing.pdf", "_content": None}
    zip_bytes = builder.build_export_zip([_bundle(documents=[doc])])
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    doc_files = [n for n in zf.namelist() if "/documents/" in n]
    assert doc_files == []
    manifest_name = [n for n in zf.namelist() if n.endswith("documents.csv")][0]
    manifest = zf.read(manifest_name).decode()
    assert "doc-2" in manifest
    assert "insurance" in manifest


def test_raw_data_json_never_contains_the_private_content_key():
    """_content carries raw document bytes — must never leak into the
    human/machine-readable JSON manifest (that's what the documents/ files
    are for)."""
    doc = {"id": "doc-3", "document_type": "insurance", "_storage_key": "k.pdf", "_content": b"secret bytes"}
    zip_bytes = builder.build_export_zip([_bundle(documents=[doc])])
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    raw_name = [n for n in zf.namelist() if n.endswith("raw_data.json")][0]
    raw = json.loads(zf.read(raw_name))
    assert "_content" not in raw["documents"][0]
    assert "_content" not in json.dumps(raw)  # belt-and-suspenders: no stray leak anywhere in the payload
