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


# Per-document outcome recorded on every payload and surfaced in the ZIP's
# documents.csv / raw_data.json. A document with no file in the bundle is
# NEVER silently absent — the manifest always says which of these it was, so
# "the admin opted out" can't be mistaken for "storage is broken" (they
# previously produced an identical, unexplained metadata-only ZIP).
DOC_STATUS_INCLUDED = "included"
DOC_STATUS_EXCLUDED = "excluded_by_request"
DOC_STATUS_NO_KEY = "unavailable_no_storage_key"
DOC_STATUS_FETCH_FAILED = "unavailable_fetch_failed"


async def _fetch_document_bytes(storage_key: str) -> Optional[bytes]:
    """Download a document's raw bytes. Returns None (not raised) on failure so a
    single unreadable document doesn't abort the whole bundle — the caller
    records the miss in the bundle's manifest instead.

    Logged at error, not warning: these bundles back regulatory/insurer-facing
    transfers, so a document the operator asked for and did not get is
    actionable, not a recoverable anomaly (CLAUDE.md "do not silently swallow
    errors"). The bundle continues deliberately — aborting would lose the
    other 99 entities over one bad object — and the miss is reported in the
    manifest rather than dropped."""
    if not supabase:
        logger.error("data-transfer export: no storage client; cannot fetch document key=%s", storage_key)
        return None
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: supabase.storage.from_(DOCUMENT_STORAGE_BUCKET).download(storage_key)
        )
    except Exception as exc:
        logger.error("data-transfer export: failed to fetch document key=%s: %s", storage_key, exc, exc_info=True)
        return None


_RIDE_GPS_FIELDS = ("pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng")


def _strip_ride_gps(ride: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in ride.items() if k not in _RIDE_GPS_FIELDS}


def _wants_file(document_type: Optional[str], include_document_bytes: bool, doc_file_types: Optional[set]) -> bool:
    """Whether this document's raw file belongs in the bundle.

    ``doc_file_types`` (when not None) is the per-document-type allowlist and
    takes precedence: only the types in it get their file, everything else is
    metadata only. ``None`` means "no per-type opinion" and falls back to the
    all-or-nothing ``include_document_bytes`` flag, which is what pre-existing
    API callers send."""
    if doc_file_types is not None:
        return document_type in doc_file_types
    return include_document_bytes


async def gather_entity_bundle(
    entity_type: str,
    entity_id: str,
    doc_types: Optional[list[str]] = None,
    include_ride_gps: bool = True,
    include_document_bytes: bool = True,
    doc_file_types: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Collect everything needed to reconstruct a user/driver in another environment.

    ``entity_type`` is "driver" or "rider". ``entity_id`` is the ``users.id``.
    ``doc_types`` optionally filters ``driver_documents.document_type`` to the
    checked subset; ``None`` means include every document type.

    ``include_ride_gps``/``include_document_bytes`` (PIA recommendation R-B,
    ACTION_ITEMS.md B11) default to the module's original full-fidelity
    behavior — set either to False to opt a lower-sensitivity export out of
    exact pickup/dropoff coordinates or document file bytes. Ride rows and
    document metadata are still included either way (only the specific
    high-sensitivity fields are dropped), so record counts and structure
    stay consistent regardless of these flags.

    ``doc_file_types`` narrows document *files* to specific document types
    while still listing every type selected by ``doc_types`` as metadata —
    e.g. export the background check's actual scan but only the metadata row
    for the driver's licence. It overrides ``include_document_bytes`` when
    provided; ``None`` (the default) keeps the older all-or-nothing behavior
    so existing API callers are unaffected. Narrowing files to the specific
    types an operator actually needs is the data-minimizing choice, so this
    is strictly a finer-grained version of the PIA R-B control, not a way
    around it.
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

    if not include_ride_gps:
        rides = [_strip_ride_gps(r) for r in rides]

    # Attach raw bytes for each document that resolves to a storage key. A
    # document whose bytes can't be fetched still appears in the manifest
    # (metadata intact) but with _content=None — the ZIP builder skips
    # writing a file for it and the import side must not assume every listed
    # document has a payload.
    #
    # Each payload also carries _content_status explaining WHY there is or
    # isn't a file. Opting out (include_document_bytes=False) and a genuine
    # storage failure both leave _content=None, and until this was recorded
    # they produced byte-identical metadata-only ZIPs with no way to tell
    # them apart — the reported "export gives me metadata, not the document"
    # bug was the opt-out case, indistinguishable from a broken bucket.
    file_types = set(doc_file_types) if doc_file_types is not None else None
    doc_payloads = []
    for doc in documents:
        storage_key = _extract_storage_key(doc.get("document_url") or "")
        if not _wants_file(doc.get("document_type"), include_document_bytes, file_types):
            content, status = None, DOC_STATUS_EXCLUDED
        elif not storage_key:
            # An unparseable document_url is a data defect, not an opt-out:
            # the row claims a document exists but points nowhere we can read.
            logger.error(
                "data-transfer export: no storage key for document id=%s type=%s (document_url unparseable)",
                doc.get("id"),
                doc.get("document_type"),
            )
            content, status = None, DOC_STATUS_NO_KEY
        else:
            content = await _fetch_document_bytes(storage_key)
            status = DOC_STATUS_INCLUDED if content is not None else DOC_STATUS_FETCH_FAILED
        doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content, "_content_status": status})

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
    entities: list[tuple[str, str]],
    doc_types: Optional[list[str]] = None,
    include_ride_gps: bool = True,
    include_document_bytes: bool = True,
    doc_file_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Batch gather for multi-entity export. A single entity failing to resolve
    does not abort the batch — it's reported by omission (caller can diff
    requested vs. returned entity_ids) so one bad row doesn't block the rest."""
    results = await asyncio.gather(
        *(
            gather_entity_bundle(t, eid, doc_types, include_ride_gps, include_document_bytes, doc_file_types)
            for t, eid in entities
        ),
        return_exceptions=True,
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
