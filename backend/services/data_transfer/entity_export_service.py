"""Gather a full-fidelity export bundle for a user or driver.

Unlike the PIPEDA self-export in ``routes/drivers/tax_exports.py`` (which
redacts fields for the account holder's own download), this gathers data for
an *admin* moving a record between Spinr's own environments — no redaction,
because the operator already has full admin visibility into the source data.
Document bytes are fetched too (self-export only lists document metadata).
"""

import asyncio
import logging
from typing import Any, Optional

try:
    from ... import db_supabase
    from ...documents import _extract_storage_key
    from ...routes.drivers._shared import _decrypt_driver_pii
    from ...supabase_client import supabase
except ImportError:
    import db_supabase
    from documents import _extract_storage_key
    from routes.drivers._shared import _decrypt_driver_pii
    from supabase_client import supabase

logger = logging.getLogger(__name__)

DOCUMENT_STORAGE_BUCKET = "driver-documents"


class EntityNotFoundError(Exception):
    pass


async def _fetch_document_bytes(storage_key: str) -> Optional[bytes]:
    """Download a document's raw bytes. Returns None (not raised) on failure so a
    single unreadable document doesn't abort the whole bundle — the caller
    records the miss in the bundle's manifest instead."""
    if not supabase:
        return None
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: supabase.storage.from_(DOCUMENT_STORAGE_BUCKET).download(storage_key)
        )
    except Exception as exc:
        logger.warning("data-transfer export: failed to fetch document key=%s: %s", storage_key, exc)
        return None


async def gather_entity_bundle(
    entity_type: str, entity_id: str, doc_types: Optional[list[str]] = None
) -> dict[str, Any]:
    """Collect everything needed to reconstruct a user/driver in another environment.

    ``entity_type`` is "driver" or "rider". ``entity_id`` is the ``users.id``.
    ``doc_types`` optionally filters ``driver_documents.document_type`` to the
    checked subset; ``None`` means include every document type.
    """
    if entity_type not in ("driver", "rider"):
        raise ValueError(f"Unknown entity_type: {entity_type!r}")

    user_rows, driver_rows, notification_prefs = await asyncio.gather(
        db_supabase.get_rows("users", {"id": entity_id}, limit=1),
        db_supabase.get_rows("drivers", {"user_id": entity_id}, limit=1) if entity_type == "driver" else _empty_list(),
        db_supabase.get_rows("notification_preferences", {"user_id": entity_id}, limit=1),
    )
    if not user_rows:
        raise EntityNotFoundError(f"No user found for id={entity_id}")
    user = user_rows[0]
    driver = driver_rows[0] if driver_rows else {}
    # license_number is vault-encrypted at rest as a vault.secrets UUID scoped
    # to THIS Supabase project — copying that token verbatim into another
    # project's drivers row (the whole point of this export) would point at
    # a secret that doesn't exist there. Decrypt to the real value here;
    # entity_import_service re-encrypts it against the target project's own
    # vault on commit. Consistent with this export being full-fidelity,
    # unredacted, admin-to-admin (see module docstring).
    if driver:
        driver = await _decrypt_driver_pii(driver)
    driver_id = driver.get("id", "")

    rides: list = []
    documents: list = []
    insurance_periods: list = []
    if driver_id:
        rides, documents, insurance_periods = await asyncio.gather(
            db_supabase.get_rows("rides", {"driver_id": driver_id}, limit=500, order="created_at", desc=True),
            db_supabase.get_rows("driver_documents", {"driver_id": driver_id}, limit=200),
            db_supabase.get_rows("driver_insurance_periods", {"driver_id": driver_id}, limit=2000),
        )
    else:
        rides = await db_supabase.get_rows("rides", {"rider_id": entity_id}, limit=500, order="created_at", desc=True)

    if doc_types:
        documents = [d for d in documents if d.get("document_type") in doc_types]

    # Attach raw bytes for each document that resolves to a storage key. A
    # document whose bytes can't be fetched still appears in the manifest
    # (metadata intact) but with document_bytes=None — the ZIP builder skips
    # writing a file for it and the import side must not assume every listed
    # document has a payload.
    doc_payloads = []
    for doc in documents:
        storage_key = _extract_storage_key(doc.get("document_url") or "")
        content = await _fetch_document_bytes(storage_key) if storage_key else None
        doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content})

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "user": user,
        "driver_profile": driver,
        "notification_preferences": notification_prefs or [],
        "rides": rides or [],
        "documents": doc_payloads,
        "driver_insurance_periods": insurance_periods or [],
    }


async def gather_entity_bundles(
    entities: list[tuple[str, str]], doc_types: Optional[list[str]] = None
) -> list[dict[str, Any]]:
    """Batch gather for multi-entity export. A single entity failing to resolve
    does not abort the batch — it's reported by omission (caller can diff
    requested vs. returned entity_ids) so one bad row doesn't block the rest."""
    results = await asyncio.gather(
        *(gather_entity_bundle(t, eid, doc_types) for t, eid in entities), return_exceptions=True
    )
    bundles = []
    for (entity_type, entity_id), result in zip(entities, results):
        if isinstance(result, Exception):
            logger.warning("data-transfer export: skipping %s/%s: %s", entity_type, entity_id, result)
            continue
        bundles.append(result)
    return bundles


async def _empty_list() -> list:
    return []
