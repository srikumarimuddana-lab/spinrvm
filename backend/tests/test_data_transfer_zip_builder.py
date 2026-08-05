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


def _read(zip_bytes, suffix):
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    return zf.read([n for n in zf.namelist() if n.endswith(suffix)][0]).decode()


def test_excluded_by_request_is_distinguishable_from_a_storage_failure():
    """The reported bug: an opt-out export and a broken bucket both produced a
    metadata-only ZIP with nothing saying which it was. The manifest must
    name the reason so an operator can tell a setting from a fault."""
    opted_out = {
        "id": "doc-a",
        "document_type": "background_check",
        "_storage_key": "a.jpg",
        "_content": None,
        "_content_status": "excluded_by_request",
    }
    broken = {
        "id": "doc-b",
        "document_type": "background_check",
        "_storage_key": "b.jpg",
        "_content": None,
        "_content_status": "unavailable_fetch_failed",
    }
    manifest = _read(builder.build_export_zip([_bundle(documents=[opted_out, broken])]), "documents.csv")
    assert "excluded_by_request" in manifest
    assert "unavailable_fetch_failed" in manifest


def test_readme_explains_a_metadata_only_export_and_how_to_get_the_files():
    doc = {
        "id": "doc-a",
        "document_type": "background_check",
        "_storage_key": "a.jpg",
        "_content": None,
        "_content_status": "excluded_by_request",
    }
    readme = _read(builder.build_export_zip([_bundle(documents=[doc])]), "README.txt")
    assert "all 1 document(s) are listed as metadata only" in readme
    assert '"File" box' in readme
    # Must not promise files it did not write.
    assert "documents/<type>_<id>" not in readme


def test_readme_mixed_selection_does_not_claim_files_were_disabled():
    """A deliberate per-type selection (background check's file, licence
    metadata only) is exactly what was asked for — saying "file contents were
    not enabled" there would be plainly untrue."""
    docs = [
        {
            "id": "d1",
            "document_type": "background_check",
            "_storage_key": "1.jpg",
            "_content": b"x",
            "_content_status": "included",
        },
        {
            "id": "d2",
            "document_type": "drivers_license",
            "_storage_key": "2.jpg",
            "_content": None,
            "_content_status": "excluded_by_request",
        },
    ]
    readme = _read(builder.build_export_zip([_bundle(documents=docs)]), "README.txt")
    assert "1 of 2 document(s) are listed as metadata only" in readme
    assert "all 1 document(s)" not in readme
    # It DID write a file, so the documents/ line must be present.
    assert "documents/<type>_<id>" in readme


def test_readme_flags_unretrievable_documents_as_a_fault_not_a_setting():
    doc = {
        "id": "doc-b",
        "document_type": "background_check",
        "_storage_key": "b.jpg",
        "_content": None,
        "_content_status": "unavailable_fetch_failed",
    }
    readme = _read(builder.build_export_zip([_bundle(documents=[doc])]), "README.txt")
    assert "WARNING: 1 document(s) were requested but could NOT be retrieved" in readme


def test_manifest_points_at_the_bundled_file_and_hides_internal_keys():
    doc = {
        "id": "doc-1",
        "document_type": "background_check",
        "_storage_key": "abc123.jpg",
        "_content": b"\xff\xd8\xff fake jpeg",
        "_content_status": "included",
    }
    zip_bytes = builder.build_export_zip([_bundle(documents=[doc])])
    manifest = _read(zip_bytes, "documents.csv")
    assert "documents/background_check_doc-1.jpg" in manifest
    assert "included" in manifest
    # Raw bytes and the internal status marker must not leak into the
    # manifest; _storage_key stays on purpose (import reads it for the file
    # extension — see _INTERNAL_DOC_KEYS).
    assert "_content" not in manifest
    assert "_storage_key" in manifest
    # The path in the manifest must actually resolve inside the ZIP.
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    folder = [n for n in zf.namelist() if n.endswith("documents.csv")][0].split("/")[0]
    assert zf.read(f"{folder}/documents/background_check_doc-1.jpg") == b"\xff\xd8\xff fake jpeg"


def test_readme_reports_included_file_count():
    docs = [
        {
            "id": "d1",
            "document_type": "background_check",
            "_storage_key": "1.jpg",
            "_content": b"x",
            "_content_status": "included",
        },
        {
            "id": "d2",
            "document_type": "insurance",
            "_storage_key": "2.jpg",
            "_content": None,
            "_content_status": "excluded_by_request",
        },
    ]
    readme = _read(builder.build_export_zip([_bundle(documents=docs)]), "README.txt")
    assert "Documents: 2 listed, 1 file(s) included" in readme


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
