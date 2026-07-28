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
