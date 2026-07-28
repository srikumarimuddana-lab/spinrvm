"""Parse and validate a Data Transfer export ZIP for re-import.

Consumes exactly the bundle shape ``bundle_zip_builder.build_export_zip``
produces: one folder per entity (``<driver|rider>_<id>/raw_data.json`` +
``documents/*``). This is a distinct format from the legacy CSV import
handled by ``driver_import_service.py``/``rider_import_service.py`` (which
have their own column-mapping schema and CLI-shared code path) — the two
are not merged, since a ZIP bundle and a hand-built CSV have nothing in
common beyond "creates users".

Follows the same dry-run validate -> commit contract and idempotency
convention (``legacy_import_metadata.old_driver_id`` + ``source``) as
``driver_import.py`` so the admin UI's mental model transfers directly.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from ... import db_supabase
    from ...routes.drivers._shared import _vault_encrypt
    from . import bundle_document_uploader
except ImportError:
    import db_supabase

    from . import bundle_document_uploader


IMPORT_SOURCE = "data_transfer_bundle_import"


@dataclass
class ImportReportItem:
    entity_id: str
    field: str
    message: str


@dataclass
class BundleEntity:
    entity_type: str
    entity_id: str
    user: dict[str, Any]
    driver_profile: dict[str, Any]
    rides: list[dict[str, Any]]
    driver_insurance_periods: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    document_files: dict[str, bytes]  # documents.csv row "id" (or filename) -> raw bytes


@dataclass
class ImportPlan:
    entities: list[BundleEntity] = field(default_factory=list)
    # entity_id -> "new" | "existing_match" | "conflict"
    resolutions: dict[str, str] = field(default_factory=dict)
    warnings: list[ImportReportItem] = field(default_factory=list)
    errors: list[ImportReportItem] = field(default_factory=list)

    @property
    def to_create(self) -> list[BundleEntity]:
        return [e for e in self.entities if self.resolutions.get(e.entity_id) == "new"]

    @property
    def can_commit(self) -> bool:
        return len(self.errors) == 0


class BundleParseError(ValueError):
    pass


def parse_bundle_zip(zip_bytes: bytes) -> list[BundleEntity]:
    """Extract every entity folder's raw_data.json + document files from the
    ZIP. Raises BundleParseError on a structurally invalid ZIP; a folder
    missing raw_data.json is skipped with no exception (surfaced later as a
    validation error per-entity, not a hard parse failure) so one malformed
    folder in an otherwise-good multi-entity ZIP doesn't block the rest."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise BundleParseError("Not a valid ZIP file") from e

    folders: dict[str, dict[str, Any]] = {}
    for name in zf.namelist():
        parts = name.split("/", 1)
        if len(parts) != 2 or not parts[0] or parts[0] == "README.txt":
            continue
        folder, rel = parts
        folders.setdefault(folder, {"files": {}})
        if rel == "raw_data.json":
            try:
                folders[folder]["raw_data"] = json.loads(zf.read(name))
            except (json.JSONDecodeError, KeyError):
                continue
        elif rel.startswith("documents/"):
            folders[folder]["files"][rel[len("documents/") :]] = zf.read(name)

    entities: list[BundleEntity] = []
    for folder, payload in folders.items():
        raw = payload.get("raw_data")
        if not raw:
            continue
        entities.append(
            BundleEntity(
                entity_type=raw.get("entity_type", ""),
                entity_id=raw.get("entity_id", folder),
                user=raw.get("user") or {},
                driver_profile=raw.get("driver_profile") or {},
                rides=raw.get("rides") or [],
                driver_insurance_periods=raw.get("driver_insurance_periods") or [],
                documents=raw.get("documents") or [],
                document_files=payload["files"],
            )
        )
    return entities


async def _find_existing_match(entity: BundleEntity) -> Optional[dict[str, Any]]:
    """Idempotency check: has this exact source entity already been imported?
    Matches on legacy_import_metadata.old_driver_id + source, same convention
    as driver_import_service, checked against the drivers table for driver
    entities and against users.legacy_import_metadata for riders."""
    table = "drivers" if entity.entity_type == "driver" else "users"
    filter_col = "phone" if not entity.driver_profile.get("id") else "id"
    phone = (entity.user.get("phone") or "").strip()
    if not phone:
        return None
    rows = await db_supabase.get_rows(table, {"phone": phone}, limit=5)
    for row in rows or []:
        meta = row.get("legacy_import_metadata") or {}
        if meta.get("old_driver_id") == entity.entity_id and meta.get("source") == IMPORT_SOURCE:
            return row
    return None


async def build_plan(entities: list[BundleEntity]) -> ImportPlan:
    plan = ImportPlan(entities=entities)
    if not entities:
        plan.errors.append(ImportReportItem("", "bundle", "No importable entities found in the ZIP"))
        return plan

    for entity in entities:
        if entity.entity_type not in ("driver", "rider"):
            plan.errors.append(
                ImportReportItem(entity.entity_id, "entity_type", f"Unrecognized entity_type: {entity.entity_type!r}")
            )
            continue
        phone = (entity.user.get("phone") or "").strip()
        if not phone:
            plan.errors.append(ImportReportItem(entity.entity_id, "phone", "Bundle user record has no phone number"))
            continue

        existing = await _find_existing_match(entity)
        if existing:
            plan.resolutions[entity.entity_id] = "existing_match"
            plan.warnings.append(
                ImportReportItem(
                    entity.entity_id, "resume", "Already imported from this bundle source — will be skipped"
                )
            )
            continue

        # A phone already in use by a *different* (non-bundle-sourced) account
        # is a real conflict — importing would either collide or silently
        # create a duplicate identity, neither acceptable.
        other_rows = await db_supabase.get_rows("users", {"phone": phone}, limit=1)
        if other_rows:
            plan.resolutions[entity.entity_id] = "conflict"
            plan.errors.append(
                ImportReportItem(
                    entity.entity_id, "phone", "Phone already belongs to a different existing account on this system"
                )
            )
            continue

        plan.resolutions[entity.entity_id] = "new"

    return plan


async def commit_plan(plan: ImportPlan) -> dict[str, int]:
    """Insert the 'new' entities' user + driver profile rows, then replay
    their documents and insurance-period audit trail under the freshly
    created driver_id. A document-upload or insurance-period-replay failure
    for one entity is logged and skipped (see bundle_document_uploader) —
    it does not roll back that entity's already-created profile, since a
    driver record with a metadata gap is recoverable via manual re-upload,
    while rolling back a profile the operator is actively relying on is not
    an improvement."""
    if not plan.can_commit:
        raise ValueError("commit_plan called on a plan with unresolved errors")

    created_users = 0
    created_drivers = 0
    documents_replayed = 0
    insurance_periods_replayed = 0
    for entity in plan.to_create:
        new_user_id = str(uuid.uuid4())
        user_record = {k: v for k, v in entity.user.items() if k not in ("id", "created_at", "updated_at")}
        user_record["id"] = new_user_id
        await db_supabase.insert_one("users", user_record)
        created_users += 1

        if entity.entity_type == "driver" and entity.driver_profile:
            new_driver_id = str(uuid.uuid4())
            driver_record = {
                k: v for k, v in entity.driver_profile.items() if k not in ("id", "created_at", "updated_at")
            }
            driver_record["id"] = new_driver_id
            driver_record["user_id"] = new_user_id
            driver_record["legacy_import_metadata"] = {
                "old_driver_id": entity.entity_id,
                "source": IMPORT_SOURCE,
            }
            # The bundle carries license_number as plaintext (decrypted at
            # export time — see entity_export_service.gather_entity_bundle).
            # Re-encrypt it against THIS environment's own vault before
            # writing; a vault.secrets UUID minted in the source project is
            # meaningless here. Fail-closed by design (_vault_encrypt raises
            # 503 rather than storing plaintext PII) — an import that can't
            # encrypt the licence number should fail loudly, not silently
            # write it unprotected.
            if driver_record.get("license_number"):
                driver_record["license_number"] = await _vault_encrypt(
                    str(driver_record["license_number"]), "license_number"
                )
            await db_supabase.insert_one("drivers", driver_record)
            created_drivers += 1

            documents_replayed += await bundle_document_uploader.replay_documents(
                new_driver_id, entity.documents, entity.document_files
            )
            insurance_periods_replayed += await bundle_document_uploader.replay_insurance_periods(
                new_driver_id, entity.driver_insurance_periods
            )

    return {
        "created_users": created_users,
        "created_drivers": created_drivers,
        "documents_replayed": documents_replayed,
        "insurance_periods_replayed": insurance_periods_replayed,
    }
