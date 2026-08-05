"""Unit tests for entity_export_service.py, with db_supabase/_decrypt_driver_pii/
supabase storage monkeypatched — mirrors test_driver_import_service.py's
monkeypatch.setattr(svc, ...) pattern, applied to the higher-level
db_supabase.get_rows wrapper this service calls instead of raw supabase.table().
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.data_transfer import entity_export_service as svc


def _install_fake_db(monkeypatch, tables: dict):
    """tables: {table_name: list[dict]} — get_rows filters by a single
    equality key (id/user_id/driver_id/rider_id), matching this service's
    actual filter shapes; good enough for these tests without reimplementing
    the real Postgres filter DSL."""

    async def fake_get_rows(table, filters=None, limit=None, order=None, desc=False, columns="*"):
        rows = tables.get(table, [])
        if filters:
            for key, value in filters.items():
                rows = [r for r in rows if r.get(key) == value]
        if limit:
            rows = rows[:limit]
        return rows

    monkeypatch.setattr(svc.db_supabase, "get_rows", AsyncMock(side_effect=fake_get_rows))


def _install_fake_decrypt(monkeypatch, transform=lambda d: {**d, "license_number": "PLAINTEXT-123"}):
    monkeypatch.setattr(svc, "_decrypt_driver_pii", AsyncMock(side_effect=lambda d: transform(d)))


def _install_fake_storage(monkeypatch, content_by_key: dict):
    fake_supabase = MagicMock()

    def fake_download(key):
        if key not in content_by_key:
            raise RuntimeError("not found")
        return content_by_key[key]

    fake_supabase.storage.from_.return_value.download.side_effect = fake_download
    monkeypatch.setattr(svc, "supabase", fake_supabase)


@pytest.mark.anyio
async def test_gather_entity_bundle_raises_when_user_not_found(monkeypatch):
    _install_fake_db(monkeypatch, {"users": []})
    _install_fake_decrypt(monkeypatch)
    with pytest.raises(svc.EntityNotFoundError):
        await svc.gather_entity_bundle("driver", "missing-id")


@pytest.mark.anyio
async def test_gather_entity_bundle_rejects_unknown_entity_type(monkeypatch):
    with pytest.raises(ValueError):
        await svc.gather_entity_bundle("vehicle", "x")


@pytest.mark.anyio
async def test_gather_driver_bundle_decrypts_license_number(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1", "full_name": "Jane Doe"}],
            "drivers": [{"id": "drv1", "user_id": "u1", "license_number": "vault-uuid-abc"}],
            "notification_preferences": [],
            "rides": [{"id": "r1", "driver_id": "drv1"}],
            "driver_documents": [],
            "driver_insurance_periods": [{"period": 1, "driver_id": "drv1"}],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {})

    bundle = await svc.gather_entity_bundle("driver", "u1")

    assert bundle["driver_profile"]["license_number"] == "PLAINTEXT-123"
    assert bundle["rides"] == [{"id": "r1", "driver_id": "drv1"}]
    assert bundle["driver_insurance_periods"] == [{"period": 1, "driver_id": "drv1"}]


@pytest.mark.anyio
async def test_gather_rider_bundle_has_empty_driver_profile_and_uses_rider_id_for_rides(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u2", "full_name": "John Smith"}],
            "notification_preferences": [],
            "rides": [{"id": "r5", "rider_id": "u2"}],
        },
    )
    _install_fake_decrypt(monkeypatch)

    bundle = await svc.gather_entity_bundle("rider", "u2")

    assert bundle["driver_profile"] == {}
    assert bundle["rides"] == [{"id": "r5", "rider_id": "u2"}]
    assert bundle["documents"] == []


@pytest.mark.anyio
async def test_doc_types_filter_only_includes_matching_documents(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": [
                {"id": "doc1", "driver_id": "drv1", "document_type": "insurance", "document_url": ""},
                {"id": "doc2", "driver_id": "drv1", "document_type": "drivers_license", "document_url": ""},
            ],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {})

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_types=["insurance"])

    assert len(bundle["documents"]) == 1
    assert bundle["documents"][0]["id"] == "doc1"


@pytest.mark.anyio
async def test_document_fetch_failure_leaves_content_none_not_raised(monkeypatch):
    """A document whose storage key can't be resolved to bytes must not
    abort the whole bundle -- see the module's own docstring on this."""
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": [
                {
                    "id": "doc1",
                    "driver_id": "drv1",
                    "document_type": "insurance",
                    "document_url": "https://x.supabase.co/storage/v1/object/sign/driver-documents/missing.pdf",
                }
            ],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {})  # no keys present -> download raises -> _content stays None

    bundle = await svc.gather_entity_bundle("driver", "u1")

    assert bundle["documents"][0]["_content"] is None
    assert bundle["documents"][0]["id"] == "doc1"  # metadata intact even though bytes are missing


@pytest.mark.anyio
async def test_include_ride_gps_false_strips_gps_fields_but_keeps_ride_rows(monkeypatch):
    """PIA recommendation R-B (ACTION_ITEMS.md B11): opting out of ride GPS
    must still return the ride rows (record counts unchanged) with only the
    four coordinate fields removed."""
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [
                {
                    "id": "r1",
                    "driver_id": "drv1",
                    "pickup_lat": 52.1,
                    "pickup_lng": -106.6,
                    "dropoff_lat": 52.2,
                    "dropoff_lng": -106.7,
                    "fare": 12.5,
                }
            ],
            "driver_documents": [],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", include_ride_gps=False)

    assert len(bundle["rides"]) == 1
    ride = bundle["rides"][0]
    assert ride["id"] == "r1"
    assert ride["fare"] == 12.5
    for field in ("pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng"):
        assert field not in ride


@pytest.mark.anyio
async def test_include_ride_gps_true_default_keeps_gps_fields(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [{"id": "r1", "driver_id": "drv1", "pickup_lat": 52.1, "pickup_lng": -106.6}],
            "driver_documents": [],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1")

    assert bundle["rides"][0]["pickup_lat"] == 52.1
    assert bundle["rides"][0]["pickup_lng"] == -106.6


@pytest.mark.anyio
async def test_include_document_bytes_false_skips_storage_fetch_but_keeps_metadata(monkeypatch):
    """Document rows must still appear (metadata intact, same as the
    fetch-failure path) but _content stays None and the storage download is
    never even attempted — not just discarded after fetching."""
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": [
                {
                    "id": "doc1",
                    "driver_id": "drv1",
                    "document_type": "insurance",
                    "document_url": "https://x.supabase.co/storage/v1/object/sign/driver-documents/present.pdf",
                }
            ],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    fake_supabase = MagicMock()
    monkeypatch.setattr(svc, "supabase", fake_supabase)

    bundle = await svc.gather_entity_bundle("driver", "u1", include_document_bytes=False)

    assert bundle["documents"][0]["id"] == "doc1"
    assert bundle["documents"][0]["_content"] is None
    fake_supabase.storage.from_.return_value.download.assert_not_called()


@pytest.mark.anyio
async def test_gather_entity_bundles_threads_scope_flags_through(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "notification_preferences": [],
            "rides": [{"id": "r1", "rider_id": "u1", "pickup_lat": 1.0, "pickup_lng": 2.0}],
        },
    )
    _install_fake_decrypt(monkeypatch)

    bundles = await svc.gather_entity_bundles([("rider", "u1")], include_ride_gps=False)

    assert "pickup_lat" not in bundles[0]["rides"][0]


@pytest.mark.anyio
async def test_gather_entity_bundles_skips_a_failing_entity_and_keeps_the_rest(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],  # only u1 exists; u2 will raise EntityNotFoundError
            "drivers": [],
            "notification_preferences": [],
            "rides": [],
        },
    )
    _install_fake_decrypt(monkeypatch)

    bundles = await svc.gather_entity_bundles([("rider", "u1"), ("rider", "u2")])

    assert len(bundles) == 1
    assert bundles[0]["entity_id"] == "u1"


def _two_document_db(monkeypatch):
    """A driver with a background check and a licence, both readable."""
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": [
                {
                    "id": "doc-bg",
                    "driver_id": "drv1",
                    "document_type": "background_check",
                    "document_url": "https://x.supabase.co/storage/v1/object/sign/driver-documents/bg.jpg",
                },
                {
                    "id": "doc-dl",
                    "driver_id": "drv1",
                    "document_type": "drivers_license",
                    "document_url": "https://x.supabase.co/storage/v1/object/sign/driver-documents/dl.jpg",
                },
            ],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {"bg.jpg": b"BGBYTES", "dl.jpg": b"DLBYTES"})


@pytest.mark.anyio
async def test_doc_file_types_bundles_only_the_selected_type_s_file(monkeypatch):
    """The point of per-document selection: pull the background check's actual
    scan while the licence stays a metadata row."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_file_types=["background_check"])

    by_id = {d["id"]: d for d in bundle["documents"]}
    assert by_id["doc-bg"]["_content"] == b"BGBYTES"
    assert by_id["doc-bg"]["_content_status"] == svc.DOC_STATUS_INCLUDED
    # Still listed, still full metadata — just no file.
    assert by_id["doc-dl"]["_content"] is None
    assert by_id["doc-dl"]["_content_status"] == svc.DOC_STATUS_EXCLUDED
    assert by_id["doc-dl"]["document_type"] == "drivers_license"


@pytest.mark.anyio
async def test_empty_doc_file_types_is_metadata_only_not_everything(monkeypatch):
    """An explicit empty list must mean "no files", NOT fall through to the
    include_document_bytes=True default — otherwise a UI sending [] would
    silently export every document's file."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", include_document_bytes=True, doc_file_types=[])

    assert all(d["_content"] is None for d in bundle["documents"])
    assert all(d["_content_status"] == svc.DOC_STATUS_EXCLUDED for d in bundle["documents"])


@pytest.mark.anyio
async def test_doc_file_types_overrides_include_document_bytes(monkeypatch):
    """When both are supplied the per-type list wins — it is always the
    narrower of the two, so this can only ever reduce what is exported."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle(
        "driver", "u1", include_document_bytes=False, doc_file_types=["background_check"]
    )

    by_id = {d["id"]: d for d in bundle["documents"]}
    assert by_id["doc-bg"]["_content"] == b"BGBYTES"
    assert by_id["doc-dl"]["_content"] is None


@pytest.mark.anyio
async def test_doc_file_types_none_keeps_all_or_nothing_behavior(monkeypatch):
    """Back-compat: API callers predating this field send no doc_file_types
    and must keep getting every file when include_document_bytes is true."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", include_document_bytes=True)

    assert all(d["_content"] is not None for d in bundle["documents"])


@pytest.mark.anyio
async def test_gather_entity_bundles_threads_doc_file_types_through(monkeypatch):
    _two_document_db(monkeypatch)

    bundles = await svc.gather_entity_bundles([("driver", "u1")], doc_file_types=["background_check"])

    by_id = {d["id"]: d for d in bundles[0]["documents"]}
    assert by_id["doc-bg"]["_content"] == b"BGBYTES"
    assert by_id["doc-dl"]["_content"] is None


@pytest.mark.anyio
async def test_empty_doc_types_selects_no_documents(monkeypatch):
    """Regression: `if doc_types:` treated [] as falsy and skipped the filter,
    returning EVERY document type when the admin had deselected them all —
    the opposite of the request, and over-collection under PIPEDA."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_types=[])

    assert bundle["documents"] == []


@pytest.mark.anyio
async def test_none_doc_types_still_means_every_type(monkeypatch):
    """The other half of the contract: None is "no filter", not "nothing"."""
    _two_document_db(monkeypatch)

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_types=None)

    assert {d["document_type"] for d in bundle["documents"]} == {"background_check", "drivers_license"}


@pytest.mark.anyio
async def test_zero_byte_download_is_a_failure_not_an_include(monkeypatch):
    """A 0-byte object produces no file in the ZIP, so it must not be
    reported as included — the manifest would name a file that isn't there."""
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": [
                {
                    "id": "doc-empty",
                    "driver_id": "drv1",
                    "document_type": "background_check",
                    "document_url": "https://x.supabase.co/storage/v1/object/sign/driver-documents/empty.jpg",
                }
            ],
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {"empty.jpg": b""})

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_file_types=["background_check"])

    assert bundle["documents"][0]["_content_status"] == svc.DOC_STATUS_FETCH_FAILED


# ---------------------------------------------------------------------------
# Document-type matching — display name vs. canonical key
# ---------------------------------------------------------------------------


def _docs_db(monkeypatch, documents):
    _install_fake_db(
        monkeypatch,
        {
            "users": [{"id": "u1"}],
            "drivers": [{"id": "drv1", "user_id": "u1"}],
            "notification_preferences": [],
            "rides": [],
            "driver_documents": documents,
            "driver_insurance_periods": [],
        },
    )
    _install_fake_decrypt(monkeypatch)
    _install_fake_storage(monkeypatch, {"bg.jpg": b"CRC-SCAN"})


def _doc(document_type, requirement_key=None, key="bg.jpg"):
    return {
        "id": "doc-bg",
        "driver_id": "drv1",
        "document_type": document_type,
        "requirement_key": requirement_key,
        "document_url": f"https://x.supabase.co/storage/v1/object/sign/driver-documents/{key}",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stored_document_type",
    [
        "Background Check",  # documents.py stores req["name"], the DISPLAY name
        "background_check",  # canonical key, if a row ever holds it
        "Criminal Record Check",  # what the bulk-import source sheet calls it
        "criminal_record_check",
        "  BACKGROUND   CHECK  ",  # whitespace/case noise
    ],
)
async def test_selecting_background_check_matches_however_it_is_stored(monkeypatch, stored_document_type):
    """The reported bug: driver_documents.document_type holds the requirement's
    DISPLAY name ("Background Check") while every selector is snake_case
    ("background_check"). Comparing them directly matched nothing, so filtering
    by type excluded every document and produced CSVs with no files."""
    _docs_db(monkeypatch, [_doc(stored_document_type)])

    bundle = await svc.gather_entity_bundle(
        "driver", "u1", doc_types=["background_check"], doc_file_types=["background_check"]
    )

    assert len(bundle["documents"]) == 1, f"{stored_document_type!r} was filtered out"
    assert bundle["documents"][0]["_content"] == b"CRC-SCAN"
    assert bundle["documents"][0]["_content_status"] == svc.DOC_STATUS_INCLUDED


@pytest.mark.anyio
async def test_requirement_key_is_matched_when_display_name_is_unrecognized(monkeypatch):
    """Older/imported rows can carry a free-text display name; the canonical
    slug lives in requirement_key. Either one matching is enough."""
    _docs_db(monkeypatch, [_doc("Police Clearance Letter", requirement_key="background_check")])

    bundle = await svc.gather_entity_bundle(
        "driver", "u1", doc_types=["background_check"], doc_file_types=["background_check"]
    )

    assert len(bundle["documents"]) == 1
    assert bundle["documents"][0]["_content"] == b"CRC-SCAN"


@pytest.mark.anyio
async def test_an_unrelated_document_type_is_still_excluded(monkeypatch):
    """Canonicalization must not turn the filter into a pass-through."""
    _docs_db(monkeypatch, [_doc("Vehicle Inspection", requirement_key="vehicle_inspection")])

    bundle = await svc.gather_entity_bundle("driver", "u1", doc_types=["background_check"])

    assert bundle["documents"] == []


def test_canonical_doc_type_normalizes_and_applies_aliases():
    assert svc._canonical_doc_type("Background Check") == "background_check"
    assert svc._canonical_doc_type("Criminal Record Check") == "background_check"
    assert svc._canonical_doc_type("Car Insurance") == "insurance"
    assert svc._canonical_doc_type("Driving License") == "drivers_license"
    assert svc._canonical_doc_type(None) == ""
