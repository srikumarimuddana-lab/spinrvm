"""Driver bulk-import core — shared by the CLI script and the admin HTTP flow.

This module holds the pure parsing/validation helpers, the ``build_plan`` /
``commit_plan`` pipeline, and the dataclasses that both callers use:

- ``scripts/import_saskatoon_drivers.py`` — the terminal CLI (drivers + a local
  files-root of document files).
- ``backend/routes/admin/driver_import.py`` — the admin dashboard flow (drivers
  CSV only; document files are uploaded per-driver afterwards).

Reports intentionally carry only ``old_driver_id`` / ``field`` / ``message`` —
never raw PII (names, phones, DOBs). CSVs and reports contain PIPEDA-covered
personal information; keep them in a secure location.

Re-running after a partial failure is safe for drivers: rows whose driver was
already created by this importer (matched on ``legacy_import_metadata``
old_driver_id + source) are skipped with a warning, and their documents are
deduplicated against existing ``driver_documents`` rows, so a crashed commit can
be resumed by simply committing again. Caveat: the commit order is users ->
drivers -> files -> documents, so a failure between the users and drivers
inserts leaves user rows without drivers; those surface as "matching user ...
already exists" errors and need manual cleanup before resuming.
"""

from __future__ import annotations

import csv
import io
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ..supabase_client import supabase
    from ..utils.driver_code import generate_driver_code
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from supabase_client import supabase
    from utils.driver_code import generate_driver_code

REQUIRED_DRIVER_COLUMNS = {
    "old_driver_id",
    "full_name",
    "phone",
    "email",
    "vehicle_plate",
    "vehicle_type",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
}

DOCUMENT_REQUIREMENT_ALIASES = {
    "criminal_record_check": "background_check",
    "crimininal_record_check": "background_check",  # common typo from source sheet
    "criminal record check": "background_check",
    "car_insurance": "insurance",
    "car insurance": "insurance",
    "vehicle_inspection": "vehicle_inspection",
    "vehicle inspection": "vehicle_inspection",
    "drivers_abstract": "drivers_abstract",
    "drivers abstract": "drivers_abstract",
    "work_authorization": "work_authorization",
    "work authorization": "work_authorization",
    "driving_license": "drivers_license",
    "driving license": "drivers_license",
    "drivers_license": "drivers_license",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d-%m-%y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%m/%d/%Y",
)

TRUTHY = {"y", "yes", "true", "1", "approved", "valid"}
FALSY = {"n", "no", "false", "0", "not approved", "invalid"}

# Statuses backend/documents.py actually recognizes (review flow only ever
# writes/filters these). Anything else from the source sheet would create
# documents invisible to the admin review UI.
VALID_DOC_STATUSES = {"pending", "approved", "rejected"}

IMPORT_SOURCE = "legacy_saskatoon_driver_import"


@dataclass
class ImportErrorItem:
    old_driver_id: str
    field: str
    message: str


@dataclass
class ImportPlan:
    users_to_insert: list[dict[str, Any]] = field(default_factory=list)
    drivers_to_insert: list[dict[str, Any]] = field(default_factory=list)
    docs_to_insert: list[dict[str, Any]] = field(default_factory=list)
    files_to_upload: list[tuple[Path, str, str]] = field(default_factory=list)  # path, storage_key, doc_id
    warnings: list[ImportErrorItem] = field(default_factory=list)
    errors: list[ImportErrorItem] = field(default_factory=list)


def normalize_header(value: str) -> str:
    value = value.strip().lower().replace("?", "")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    aliases = {
        "name": "full_name",
        "date_of_birth": "date_of_birth",
        "dob": "date_of_birth",
        "driving_license_number": "license_number",
        "driving_licence_number": "license_number",
        "license_no": "license_number",
        "licence_no": "license_number",
        "license_class": "license_class",
        "approved_from_sgi": "regulatory_authority_approved",
        "sgi_approved": "regulatory_authority_approved",
        "regulatory_approved": "regulatory_authority_approved",
        "regulatory_authority_approved": "regulatory_authority_approved",
        "regulatory_authority": "regulatory_authority",
        "regulatory_region": "regulatory_region",
        "spinr_approved": "spinr_approved",
        "vehicle_plate": "vehicle_plate",
        "plate": "vehicle_plate",
        "vin": "vin",
        "car_year": "vehicle_year",
        "car_make": "vehicle_make",
        "car_model": "vehicle_model",
        "crimininal_record_check": "criminal_record_check_expiry",
        "criminal_record_check": "criminal_record_check_expiry",
        "car_insurance": "insurance_expiry",
        "vehicle_inspection": "vehicle_inspection_expiry",
        "drivers_abstract": "drivers_abstract_status",
        "pr": "permanent_resident",
        "citizen": "citizen",
        "decals_sent": "decals_sent",
    }
    return aliases.get(value, value)


def parse_csv_rows(reader: csv.DictReader) -> list[dict[str, str]]:
    """Normalize headers + strip values for an already-opened DictReader."""
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    reader.fieldnames = [normalize_header(h or "") for h in reader.fieldnames]
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return parse_csv_rows(csv.DictReader(f))


def read_csv_text(text: str) -> list[dict[str, str]]:
    """Parse CSV content from an in-memory string (admin upload flow)."""
    # utf-8-sig decoding is handled by the caller; strip a leading BOM if present.
    if text.startswith("﻿"):
        text = text[1:]
    return parse_csv_rows(csv.DictReader(io.StringIO(text)))


def parse_bool(value: str) -> bool | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return None


def parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw or raw.lower() in {"indefinite", "valid", "n/a", "na", "none"}:
        return None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if parsed.year < 100:
                parsed = parsed.replace(year=parsed.year + 2000)
            return parsed
        except ValueError:
            continue
    return None


def iso_date(value: str) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def date_is_ambiguous(value: str) -> bool:
    """True when *value* parses to more than one distinct date across the
    accepted formats — e.g. "03/04/25" is Apr 3 under the day-first formats
    tried first, but Mar 4 under month-first. parse_date() silently picks the
    first match, and these dates gate go_online (document expiry), so callers
    should surface a warning and have the operator verify the source format.
    """
    raw = (value or "").strip()
    if not raw or raw.lower() in {"indefinite", "valid", "n/a", "na", "none"}:
        return False
    seen: set[date] = set()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if parsed.year < 100:
            parsed = parsed.replace(year=parsed.year + 2000)
        seen.add(parsed)
    return len(seen) > 1


# Driver-row date columns checked for day-first/month-first ambiguity. The
# warning message deliberately omits the raw and parsed values: date_of_birth
# is PII and the report must stay PII-free (see module docstring).
DRIVER_DATE_FIELDS = (
    "date_of_birth",
    "license_expiry",
    "insurance_expiry",
    "vehicle_inspection_expiry",
    "criminal_record_check_expiry",
    "work_authorization_expiry",
)


def split_name(full_name: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s*\([^)]*\)", "", full_name).strip()
    parts = cleaned.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone.strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def canonical_requirement_key(value: str) -> str:
    key = slug(value)
    return DOCUMENT_REQUIREMENT_ALIASES.get(key, DOCUMENT_REQUIREMENT_ALIASES.get(value.strip().lower(), key))


def storage_signed_url(storage_key: str) -> str:
    res = supabase.storage.from_("driver-documents").create_signed_url(storage_key, 3600)
    data = getattr(res, "data", None)
    if isinstance(data, dict):
        url = data.get("signedURL") or data.get("signedUrl") or data.get("signed_url")
        if url:
            return url
    url = getattr(data, "signed_url", None) or getattr(data, "signedURL", None)
    if not url:
        raise RuntimeError(f"create_signed_url returned no URL for {storage_key}")
    return url


def encrypt_pii(value: str | None) -> str | None:
    if not value:
        return None
    res = supabase.rpc("encrypt_driver_pii", {"plaintext": value}).execute()
    return getattr(res, "data", None)


def get_service_area(service_area_id: str | None, service_area_name: str) -> dict[str, Any]:
    if service_area_id:
        rows = (
            supabase.table("service_areas")
            .select("id,name,province,required_documents,regulatory_authority,regulatory_region")
            .eq("id", service_area_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    else:
        rows = (
            supabase.table("service_areas")
            .select("id,name,province,required_documents,regulatory_authority,regulatory_region")
            .ilike("name", f"%{service_area_name}%")
            .limit(5)
            .execute()
            .data
            or []
        )
    if not rows:
        raise RuntimeError("Saskatoon service area was not found; pass --service-area-id explicitly")
    if len(rows) > 1 and not service_area_id:
        names = ", ".join(f"{r.get('name')} ({r.get('id')})" for r in rows)
        raise RuntimeError(f"Multiple service areas matched; pass --service-area-id. Matches: {names}")
    return rows[0]


def vehicle_type_map() -> dict[str, str]:
    rows = supabase.table("vehicle_types").select("id,name,display_name").execute().data or []
    out: dict[str, str] = {}
    for row in rows:
        vid = row.get("id")
        if not vid:
            continue
        for val in (row.get("name"), row.get("display_name"), vid):
            if val:
                out[slug(str(val))] = vid
    return out


def validate_required_columns(rows: list[dict[str, str]], plan: ImportPlan) -> None:
    if not rows:
        plan.errors.append(ImportErrorItem("<file>", "drivers_csv", "drivers CSV is empty"))
        return
    missing = REQUIRED_DRIVER_COLUMNS - set(rows[0].keys())
    for col in sorted(missing):
        plan.errors.append(ImportErrorItem("<file>", col, "drivers CSV is missing required column"))


def work_auth_status(row: dict[str, str]) -> str:
    if parse_bool(row.get("citizen", "")):
        return "citizen"
    if parse_bool(row.get("permanent_resident", "")):
        return "permanent_resident"
    raw = (row.get("work_authorization_expiry") or "").strip().lower()
    if raw == "indefinite":
        return "indefinite"
    if raw:
        return "expiring"
    return "unknown"


def regulatory_authority_defaults(row: dict[str, str], service_area: dict[str, Any]) -> tuple[str, str]:
    authority = (row.get("regulatory_authority") or "").strip()
    region = (
        (
            row.get("regulatory_region")
            or row.get("province")
            or service_area.get("regulatory_region")
            or service_area.get("province")
            or ""
        )
        .strip()
        .upper()
    )
    area_name = str(service_area.get("name") or "").lower()
    if not region:
        region = "SK" if "saskatoon" in area_name or "saskatchewan" in area_name else ""
    if not authority:
        authority = (service_area.get("regulatory_authority") or "").strip()
    if not authority:
        authority = "SGI" if region == "SK" else "Provincial / municipal authority"
    return authority, region


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    """Batched ``SELECT ... WHERE column IN (values)`` to avoid N+1 lookups."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def _prefetch_existing(
    driver_rows: list[dict[str, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Prefetch existing users (by phone, by email) and drivers (by phone).

    Replaces the per-row ``.eq(...).limit(1)`` lookups in build_plan with three
    batched ``.in_()`` queries. First-seen wins per key so it mirrors the
    original ``.limit(1)`` "pick one" behaviour. Returns three maps keyed by
    normalized phone / lowercased email / normalized phone respectively.
    """
    phones = sorted({normalize_phone(r.get("phone", "")) for r in driver_rows})
    emails = sorted({(r.get("email") or "").strip().lower() for r in driver_rows if (r.get("email") or "").strip()})

    users_by_phone: dict[str, dict[str, Any]] = {}
    users_by_email: dict[str, dict[str, Any]] = {}
    drivers_by_phone: dict[str, dict[str, Any]] = {}

    if phones:
        for u in _select_in("users", "id,phone,email", "phone", phones):
            key = u.get("phone")
            if key is not None and key not in users_by_phone:
                users_by_phone[key] = u
        for d in _select_in("drivers", "id,phone,legacy_import_metadata", "phone", phones):
            key = d.get("phone")
            if key is not None and key not in drivers_by_phone:
                drivers_by_phone[key] = d
    if emails:
        for u in _select_in("users", "id,phone,email", "email", emails):
            key = (u.get("email") or "").strip().lower()
            if key and key not in users_by_email:
                users_by_email[key] = u

    return users_by_phone, users_by_email, drivers_by_phone


def build_plan(
    driver_rows: list[dict[str, str]],
    document_rows: list[dict[str, str]],
    files_root: Path | None,
    service_area: dict[str, Any],
    import_batch: str,
) -> ImportPlan:
    """Validate CSV rows into an ImportPlan.

    ``files_root`` is the directory holding document files for the CLI flow.
    Pass ``None`` for the admin HTTP flow (drivers CSV only); any document row
    is then rejected with a validation error since the web flow uploads
    document files individually per-driver rather than from a local folder.
    """
    plan = ImportPlan()
    validate_required_columns(driver_rows, plan)
    if plan.errors:
        return plan

    required_docs = service_area.get("required_documents") or []
    allowed_doc_keys = {str(d.get("key")) for d in required_docs if d.get("key")}
    vt_map = vehicle_type_map()
    users_by_phone, users_by_email, drivers_by_phone = _prefetch_existing(driver_rows)
    seen_old_ids: set[str] = set()
    planned_driver_ids: dict[str, str] = {}
    resumed_driver_ids: set[str] = set()
    existing_docs_cache: dict[str, set[tuple[str, str | None]]] = {}
    document_old_ids = {r.get("old_driver_id") for r in document_rows if r.get("old_driver_id")}

    for row in driver_rows:
        old_id = row.get("old_driver_id") or "<missing>"
        if old_id in seen_old_ids:
            plan.errors.append(ImportErrorItem(old_id, "old_driver_id", "duplicate old_driver_id"))
            continue
        seen_old_ids.add(old_id)

        service_scope = (
            row.get("service_area_id") or row.get("service_area_slug") or row.get("service_area") or "saskatoon"
        ).lower()
        if service_scope not in {
            "saskatoon",
            str(service_area.get("id", "")).lower(),
            str(service_area.get("name", "")).lower(),
        }:
            plan.errors.append(ImportErrorItem(old_id, "service_area", "row is not scoped to Saskatoon"))
            continue

        phone = normalize_phone(row.get("phone", ""))
        email = (row.get("email") or "").strip().lower()
        # Prefetched maps replace per-row queries; preserve "match by phone,
        # else by email" for users and "match by phone" for drivers.
        matched_user = users_by_phone.get(phone) or (users_by_email.get(email) if email else None)
        existing_users = [matched_user] if matched_user else []
        matched_driver = drivers_by_phone.get(phone)
        existing_drivers = [matched_driver] if matched_driver else []
        if existing_users or existing_drivers:
            # Resume path: a driver row created by a previous run of THIS
            # importer for the same old_driver_id is not a conflict — the run
            # may have died between the driver insert and the document pass.
            # Reuse the existing driver id so the document loop can fill in
            # whatever is missing, and skip the user/driver inserts.
            meta = (existing_drivers[0].get("legacy_import_metadata") or {}) if existing_drivers else {}
            if existing_drivers and meta.get("source") == IMPORT_SOURCE and str(meta.get("old_driver_id")) == old_id:
                planned_driver_ids[old_id] = existing_drivers[0]["id"]
                resumed_driver_ids.add(existing_drivers[0]["id"])
                plan.warnings.append(
                    ImportErrorItem(
                        old_id, "resume", "driver already imported by a previous run; skipping user/driver insert"
                    )
                )
                continue
            plan.errors.append(
                ImportErrorItem(
                    old_id, "phone/email", "matching user or driver already exists; handle manually before import"
                )
            )
            continue

        vt_key = slug(row.get("vehicle_type", ""))
        vehicle_type_id = vt_map.get(vt_key)
        if not vehicle_type_id:
            plan.errors.append(ImportErrorItem(old_id, "vehicle_type", f"no vehicle_types row matched '{vt_key}'"))
            continue

        dob = iso_date(row.get("date_of_birth", ""))
        if row.get("date_of_birth") and not dob:
            plan.errors.append(ImportErrorItem(old_id, "date_of_birth", "could not parse date"))
            continue

        for date_field in DRIVER_DATE_FIELDS:
            if date_is_ambiguous(row.get(date_field, "")):
                plan.warnings.append(
                    ImportErrorItem(
                        old_id,
                        date_field,
                        "date parses differently day-first vs month-first; verify the source sheet's format before commit",
                    )
                )

        first_name, last_name = split_name(row.get("full_name", ""))
        user_id = str(uuid.uuid4())
        driver_id = str(uuid.uuid4())
        planned_driver_ids[old_id] = driver_id

        regulatory_approved = parse_bool(row.get("regulatory_authority_approved", ""))
        regulatory_authority, regulatory_region = regulatory_authority_defaults(row, service_area)
        is_approved = parse_bool(row.get("spinr_approved", "")) is True and regulatory_approved is True
        # Web imports intentionally create drivers before per-driver document
        # uploads happen. Keep those drivers under review until approved
        # document rows exist, even if the CSV says Spinr + regulator approved.
        has_import_documents = files_root is not None and old_id in document_old_ids
        driver_status = "active" if is_approved and has_import_documents else "needs_review"
        is_verified = is_approved and has_import_documents

        plan.users_to_insert.append(
            {
                "id": user_id,
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
                "email": email or None,
                "role": "driver",
                "is_driver": True,
                "profile_complete": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        plan.drivers_to_insert.append(
            {
                "id": driver_id,
                "user_id": user_id,
                "driver_code": generate_driver_code(),
                "name": row.get("full_name"),
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "vehicle_type_id": vehicle_type_id,
                "vehicle_make": row.get("vehicle_make", ""),
                "vehicle_model": row.get("vehicle_model", ""),
                "vehicle_color": row.get("vehicle_color", ""),
                "vehicle_year": int(row["vehicle_year"]) if row.get("vehicle_year", "").isdigit() else None,
                "license_plate": row.get("vehicle_plate", ""),
                "vehicle_vin": None,
                "license_number": None,
                "_plain_vehicle_vin": row.get("vin") or None,
                "_plain_license_number": row.get("license_number") or None,
                "license_class": row.get("license_class") or None,
                "date_of_birth": dob,
                "service_area_id": service_area["id"],
                "city": "Saskatoon",
                "status": driver_status,
                "is_verified": is_verified,
                "is_online": False,
                "is_available": False,
                "sgi_approved": regulatory_approved if regulatory_authority == "SGI" else None,
                "sgi_approved_at": datetime.now(timezone.utc).isoformat()
                if regulatory_authority == "SGI" and regulatory_approved
                else None,
                "regulatory_authority": regulatory_authority,
                "regulatory_region": regulatory_region or None,
                "regulatory_authority_approved": regulatory_approved,
                "regulatory_authority_approved_at": datetime.now(timezone.utc).isoformat()
                if regulatory_approved
                else None,
                "work_authorization_status": work_auth_status(row),
                "is_permanent_resident": parse_bool(row.get("permanent_resident", "")),
                "is_citizen": parse_bool(row.get("citizen", "")),
                "decals_sent": parse_bool(row.get("decals_sent", "")),
                "legacy_import_metadata": {
                    "batch": import_batch,
                    "old_driver_id": old_id,
                    "source": IMPORT_SOURCE,
                    "address_present": bool(row.get("address")),
                    "drivers_abstract_status": row.get("drivers_abstract_status") or None,
                },
                "license_expiry_date": iso_date(row.get("license_expiry", "")),
                "insurance_expiry_date": iso_date(row.get("insurance_expiry", "")),
                "vehicle_inspection_expiry_date": iso_date(row.get("vehicle_inspection_expiry", "")),
                "background_check_expiry_date": iso_date(row.get("criminal_record_check_expiry", "")),
                "work_eligibility_expiry_date": iso_date(row.get("work_authorization_expiry", "")),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    for row in document_rows:
        old_id = row.get("old_driver_id") or "<missing>"
        if files_root is None:
            # Admin HTTP flow: document files are uploaded per-driver after the
            # metadata import, not from a local folder.
            plan.errors.append(
                ImportErrorItem(
                    old_id,
                    "documents_csv",
                    "web import does not accept a documents CSV; upload document files individually per driver after import",
                )
            )
            continue
        if old_id not in planned_driver_ids:
            plan.errors.append(ImportErrorItem(old_id, "old_driver_id", "document row has no importable driver row"))
            continue
        key = canonical_requirement_key(row.get("requirement_key") or row.get("document_type") or "")
        if allowed_doc_keys and key not in allowed_doc_keys:
            plan.errors.append(
                ImportErrorItem(old_id, "requirement_key", f"'{key}' is not configured on Saskatoon required_documents")
            )
            continue
        status = (row.get("status") or "pending").strip().lower()
        if status not in VALID_DOC_STATUSES:
            plan.errors.append(
                ImportErrorItem(
                    old_id,
                    "status",
                    f"'{status}' is not a valid driver_documents status (allowed: {sorted(VALID_DOC_STATUSES)})",
                )
            )
            continue
        side = row.get("side") or None
        driver_id = planned_driver_ids[old_id]
        if driver_id in resumed_driver_ids:
            # Resumed driver: skip documents a previous run already inserted so
            # a re-run converges instead of duplicating rows.
            if driver_id not in existing_docs_cache:
                doc_rows = (
                    supabase.table("driver_documents")
                    .select("requirement_key,side")
                    .eq("driver_id", driver_id)
                    .execute()
                    .data
                    or []
                )
                existing_docs_cache[driver_id] = {
                    (str(d.get("requirement_key")), d.get("side") or None) for d in doc_rows
                }
            if (key, side) in existing_docs_cache[driver_id]:
                plan.warnings.append(
                    ImportErrorItem(old_id, key, "document already imported by a previous run; skipping")
                )
                continue
        file_path = files_root / (row.get("file_path") or "")
        if not file_path.is_file():
            plan.errors.append(ImportErrorItem(old_id, "file_path", "document file not found"))
            continue
        doc_id = str(uuid.uuid4())
        ext = file_path.suffix.lower() or ".bin"
        storage_key = f"saskatoon-import/{import_batch}/{old_id}/{key}/{side or 'main'}-{doc_id}{ext}"
        signed_placeholder = f"storage://driver-documents/{storage_key}"
        expiry = iso_date(row.get("expiry_date", ""))
        if (
            row.get("expiry_date")
            and row.get("expiry_date", "").strip().lower() not in {"indefinite", "valid"}
            and not expiry
        ):
            plan.errors.append(ImportErrorItem(old_id, "expiry_date", "could not parse document expiry date"))
            continue
        if date_is_ambiguous(row.get("expiry_date", "")):
            plan.warnings.append(
                ImportErrorItem(
                    old_id,
                    "expiry_date",
                    "date parses differently day-first vs month-first; verify the source sheet's format before commit",
                )
            )
        plan.docs_to_insert.append(
            {
                "id": doc_id,
                "driver_id": driver_id,
                "requirement_id": None,
                "requirement_key": key,
                "document_type": row.get("document_type") or key.replace("_", " ").title(),
                "document_url": signed_placeholder,
                "side": side,
                "status": status,
                "expiry_date": expiry,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        plan.files_to_upload.append((file_path, storage_key, doc_id))

    return plan


def commit_plan(plan: ImportPlan) -> None:
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")
    if plan.users_to_insert:
        supabase.table("users").insert(plan.users_to_insert).execute()
    drivers = []
    for driver in plan.drivers_to_insert:
        copied = dict(driver)
        copied["vehicle_vin"] = encrypt_pii(copied.pop("_plain_vehicle_vin", None))
        copied["license_number"] = encrypt_pii(copied.pop("_plain_license_number", None))
        drivers.append(copied)
    if drivers:
        supabase.table("drivers").insert(drivers).execute()

    signed_by_doc: dict[str, str] = {}
    for file_path, storage_key, doc_id in plan.files_to_upload:
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        supabase.storage.from_("driver-documents").upload(
            path=storage_key,
            file=file_path.read_bytes(),
            file_options={"content-type": content_type},
        )
        signed_by_doc[doc_id] = storage_signed_url(storage_key)

    docs = []
    for doc in plan.docs_to_insert:
        copied = dict(doc)
        copied["document_url"] = signed_by_doc[copied["id"]]
        docs.append(copied)
    if docs:
        supabase.table("driver_documents").insert(docs).execute()


def print_report(plan: ImportPlan, *, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "COMMIT"
    print(f"{mode} report")
    print(f"  users planned: {len(plan.users_to_insert)}")
    print(f"  drivers planned: {len(plan.drivers_to_insert)}")
    print(f"  documents planned: {len(plan.docs_to_insert)}")
    print(f"  files planned: {len(plan.files_to_upload)}")
    print(f"  warnings: {len(plan.warnings)}")
    print(f"  errors: {len(plan.errors)}")
    for item in plan.warnings[:50]:
        print(f"WARNING old_driver_id={item.old_driver_id} field={item.field}: {item.message}")
    for item in plan.errors[:100]:
        print(f"ERROR old_driver_id={item.old_driver_id} field={item.field}: {item.message}")
