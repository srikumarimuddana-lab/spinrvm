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
    from ..documents import _extract_signed_url
    from ..supabase_client import supabase
    from ..utils.driver_code import generate_driver_code
    from ..utils.sin import sin_last4, validate_sin
    from ..validators import validate_vin
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from documents import _extract_signed_url
    from supabase_client import supabase
    from utils.driver_code import generate_driver_code
    from utils.sin import sin_last4, validate_sin  # type: ignore
    from validators import validate_vin

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
    # Existing drivers (matched on old_driver_id + this importer's source) whose
    # vehicle fields changed in the re-uploaded CSV. Each entry:
    #   {"id": driver_id, "old_driver_id": str, "changes": {col: val}, "vin_plain": str|None}
    # ``vin_plain`` is the plaintext VIN to (re)encrypt at commit; None means VIN
    # is unchanged. Only vehicle fields are touched — approval/status/expiry are
    # never overwritten by a re-import.
    drivers_to_update: list[dict[str, Any]] = field(default_factory=list)
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
        "decal_number": "decal_number",
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


# Same shape SendOTPRequest/VerifyOTPRequest (schemas.py) require at signup —
# reused here so a CSV row that couldn't be normalized to a valid Canadian/US
# E.164 number is caught at import time, not discovered as a broken login
# later. `normalize_phone` above already returns the raw (un-normalizable)
# input unchanged when it can't confidently reshape it, so this regex is what
# actually enforces the format.
_PHONE_RE = re.compile(r"^\+1\d{10}$")
# Deliberately permissive (one-time CLI-operator input, not a live user-facing
# form) — this only rejects structurally-broken values (no '@', no domain
# dot), not the full RFC 5322 grammar.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def canonical_requirement_key(value: str) -> str:
    key = slug(value)
    return DOCUMENT_REQUIREMENT_ALIASES.get(key, DOCUMENT_REQUIREMENT_ALIASES.get(value.strip().lower(), key))


def storage_signed_url(storage_key: str) -> str:
    """Signed URL for a just-uploaded import document.

    Delegates to documents._extract_signed_url rather than re-deriving the
    response shape locally. The local version only read ``res.data``, the
    LEGACY shape — current supabase-py returns a plain dict, which has no
    ``.data``, so this raised "create_signed_url returned no URL" on every
    call and took the whole bulk import down with it at commit time.
    documents.py had already been fixed for exactly this (see its docstring
    on Railway 500s after supabase-py flipped the return type); the fix was
    never propagated here. One implementation now, so the next shape change
    is a one-line fix instead of a hunt.
    """
    res = supabase.storage.from_("driver-documents").create_signed_url(storage_key, 3600)
    try:
        return _extract_signed_url(res)
    except RuntimeError as e:
        raise RuntimeError(f"create_signed_url returned no URL for {storage_key}: {e}") from e


def encrypt_pii(value: str | None) -> str | None:
    if not value:
        return None
    res = supabase.rpc("encrypt_driver_pii", {"plaintext": value}).execute()
    return getattr(res, "data", None)


# CSV column -> drivers plaintext column for the re-import vehicle-update path.
# VIN is handled separately (different CSV/column names). vehicle_type/approval/
# status/expiry are intentionally excluded — a re-upload must not undo
# post-import admin changes.
_VEHICLE_UPDATE_COLUMNS = (
    ("vehicle_make", "vehicle_make"),
    ("vehicle_model", "vehicle_model"),
    ("vehicle_color", "vehicle_color"),
    ("vehicle_plate", "license_plate"),
)


def vehicle_field_changes(row: dict[str, str], existing: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Diff a re-uploaded CSV row's vehicle fields against the existing driver.

    Returns ``(changes, vin_plain)`` where ``changes`` is the plaintext-column
    updates and ``vin_plain`` is the plaintext VIN to write (or None if
    unchanged/absent). VIN is stored as plaintext (migration 244), so it is
    compared directly. A blank CSV cell is treated as "no change" — it never
    wipes an existing value, so partially-filled re-uploads only add data.
    """
    changes: dict[str, Any] = {}
    for csv_key, col in _VEHICLE_UPDATE_COLUMNS:
        val = (row.get(csv_key) or "").strip()
        if val and val != (str(existing.get(col)) if existing.get(col) is not None else "").strip():
            changes[col] = val

    year_raw = (row.get("vehicle_year") or "").strip()
    if year_raw.isdigit() and int(year_raw) != existing.get("vehicle_year"):
        changes["vehicle_year"] = int(year_raw)

    vin_plain: str | None = None
    vin_csv = (row.get("vin") or "").strip()
    existing_vin = str(existing.get("vehicle_vin")) if existing.get("vehicle_vin") is not None else ""
    if vin_csv and vin_csv != existing_vin.strip():
        vin_plain = vin_csv

    return changes, vin_plain


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
    # vehicle_types has no display_name column (id, name, description, icon,
    # capacity, is_active, created_at) — selecting it 42703s in production.
    rows = supabase.table("vehicle_types").select("id,name").execute().data or []
    out: dict[str, str] = {}
    for row in rows:
        vid = row.get("id")
        if not vid:
            continue
        for val in (row.get("name"), vid):
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
        driver_cols = (
            "id,phone,legacy_import_metadata,"
            "vehicle_make,vehicle_model,vehicle_color,vehicle_year,license_plate,vehicle_vin"
        )
        for d in _select_in("drivers", driver_cols, "phone", phones):
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
    # Only an APPROVED document counts toward `has_import_documents` below —
    # a document row merely existing (pending or rejected) must not let a
    # driver through as active/verified, bypassing the document-approval
    # gate the rest of the system relies on.
    document_old_ids = {
        r.get("old_driver_id")
        for r in document_rows
        if r.get("old_driver_id") and (r.get("status") or "pending").strip().lower() == "approved"
    }

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
        # A25/A28 P2 (ACTION_ITEMS.md): format-validate CSV-sourced phone/email
        # before they're used to match against existing users/drivers or
        # written to the users/drivers tables — a malformed value here was
        # previously accepted silently (one-time CLI-operator input, no
        # runtime form validation like the app's own signup flow gets).
        if not _PHONE_RE.match(phone):
            plan.errors.append(ImportErrorItem(old_id, "phone", "phone is not a valid 10-digit North American number"))
            continue
        if email and not _EMAIL_RE.match(email):
            plan.errors.append(ImportErrorItem(old_id, "email", "email is not a valid format"))
            continue
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
                existing = existing_drivers[0]
                planned_driver_ids[old_id] = existing["id"]
                resumed_driver_ids.add(existing["id"])
                # Already imported by this importer. Instead of a blanket skip,
                # diff the vehicle fields and queue an update when they changed
                # (e.g. an operator re-uploads with VIN/colour filled in). A row
                # with no vehicle changes is still just skipped.
                changes, vin_plain = vehicle_field_changes(row, existing)
                if vin_plain is not None:
                    try:
                        vin_plain = validate_vin(vin_plain)
                    except ValueError as exc:
                        plan.errors.append(ImportErrorItem(old_id, "vin", str(exc)))
                        continue
                if changes or vin_plain is not None:
                    plan.drivers_to_update.append(
                        {"id": existing["id"], "old_driver_id": old_id, "changes": changes, "vin_plain": vin_plain}
                    )
                    n_changed = len(changes) + (1 if vin_plain is not None else 0)
                    plan.warnings.append(
                        ImportErrorItem(
                            old_id, "update", f"driver already imported; updating {n_changed} changed vehicle field(s)"
                        )
                    )
                else:
                    plan.warnings.append(
                        ImportErrorItem(
                            old_id, "resume", "driver already imported by a previous run; no changes to apply"
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

        vin_raw = row.get("vin") or None
        if vin_raw:
            try:
                vin_raw = validate_vin(vin_raw)
            except ValueError as exc:
                plan.errors.append(ImportErrorItem(old_id, "vin", str(exc)))
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
        # Derive the city from the CSV (if a `city` column is present) or the
        # selected service area's name, so a non-Saskatoon import isn't
        # mislabeled with a Saskatoon city in admin/ride displays.
        city = row.get("city") or str(service_area.get("name") or "").strip() or "Saskatoon"

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
                "_plain_vehicle_vin": vin_raw,
                "_plain_license_number": row.get("license_number") or None,
                "license_class": row.get("license_class") or None,
                "date_of_birth": dob,
                "service_area_id": service_area["id"],
                "city": city,
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
        # A28 P2 (ACTION_ITEMS.md): a document row can otherwise import with
        # status="approved" and an already-past expiry_date. go_online's own
        # expiry re-check (routes/drivers/status.py) is the real runtime
        # gate, so this is defense-in-depth, not the only protection — but
        # there's no reason to let an operator accidentally approve an
        # already-expired document at import time.
        if status == "approved" and expiry and date.fromisoformat(expiry) < date.today():
            plan.errors.append(
                ImportErrorItem(
                    old_id, "expiry_date", f"document is already expired ({expiry}) but status is 'approved'"
                )
            )
            continue
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
        # VIN is stored as plaintext (migration 244); license_number stays
        # vault-encrypted.
        copied["vehicle_vin"] = copied.pop("_plain_vehicle_vin", None)
        copied["license_number"] = encrypt_pii(copied.pop("_plain_license_number", None))
        drivers.append(copied)
    if drivers:
        supabase.table("drivers").insert(drivers).execute()

    # Apply vehicle-only updates to already-imported drivers. All fields
    # (including the plaintext VIN) are written as-is. Approval/status/expiry are
    # never in ``changes`` by construction.
    for upd in plan.drivers_to_update:
        fields = dict(upd.get("changes") or {})
        if upd.get("vin_plain") is not None:
            fields["vehicle_vin"] = upd["vin_plain"]
        if not fields:
            continue
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("drivers").update(fields).eq("id", upd["id"]).execute()

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
    print(f"  drivers to update: {len(plan.drivers_to_update)}")
    print(f"  documents planned: {len(plan.docs_to_insert)}")
    print(f"  files planned: {len(plan.files_to_upload)}")
    print(f"  warnings: {len(plan.warnings)}")
    print(f"  errors: {len(plan.errors)}")
    for item in plan.warnings[:50]:
        print(f"WARNING old_driver_id={item.old_driver_id} field={item.field}: {item.message}")
    for item in plan.errors[:100]:
        print(f"ERROR old_driver_id={item.old_driver_id} field={item.field}: {item.message}")


# ─────────────────────────────────────────────────────────────────────────
# Legacy SIN + date-of-birth backfill (banks.csv from the raw Mongo export)
#
# Separate from build_plan/commit_plan above: this backfills two columns
# (drivers.sin, drivers.date_of_birth) on drivers this importer already
# created, rather than creating new driver rows. Source is the old app's
# `banks.csv` (Mongo ObjectId-keyed) joined against `drivers.csv` from the
# same export to resolve a phone number, per
# docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md.
#
# Deliberately excludes account_number/transit_number/institute_number —
# nothing in the live payout path reads raw banking numbers today (Stripe
# Connect collects them directly from the driver; the one local table that
# looks like a destination, `bank_accounts`, already discards the full
# number down to last4, and its only payout consumer is hardcoded
# `_STANDARD_CASHOUT_DISABLED = True`). Importing them would build new
# storage for sensitive data nothing reads — see the audit doc and this
# session's user decision before changing that scope.
#
# Safety rules, all enforced below:
#   - only touches drivers already carrying this module's own
#     ``IMPORT_SOURCE`` in ``legacy_import_metadata`` — a phone-number
#     coincidence can never touch an organic (non-legacy) driver's SIN/DOB.
#   - never clobbers an existing ``sin`` or ``date_of_birth`` — if the
#     driver already has one on file (self-entered or from an earlier
#     backfill), that value wins.
#   - reuses ``encrypt_pii`` (the same vault RPC ``commit_plan`` already
#     uses for ``license_number``) for SIN; DOB is stored plain, matching
#     the existing ``date_of_birth`` column (migration 221) and
#     ``build_plan``'s own DOB handling above.
#   - report items carry only ``old_driver_id``/``field``/``message``, same
#     as everywhere else in this module — never a raw phone, SIN, or DOB.

LEGACY_BANK_SIN_DOB_SOURCE = "legacy_mongo_banks_sin_dob_import"


@dataclass
class SinDobImportPlan:
    updates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ImportErrorItem] = field(default_factory=list)
    errors: list[ImportErrorItem] = field(default_factory=list)
    skipped_unmatched: int = 0
    skipped_not_legacy_driver: int = 0
    skipped_already_on_file: int = 0
    skipped_duplicate_match: int = 0


def join_legacy_bank_sin_dob(
    bank_rows: list[dict[str, str]], mongo_driver_rows: list[dict[str, str]]
) -> list[dict[str, str | None]]:
    """Resolve each ``banks.csv`` row to a phone number via ``drivers.csv``.

    ``banks.csv.driver_id`` is a Mongo ObjectId referencing ``drivers.csv._id``
    in the same export — verified 100% joinable
    (docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md,
    finding #2). Rows whose ``driver_id`` has no match in ``mongo_driver_rows``
    come back with ``phone: None`` and are reported as unmatched by the caller.
    """
    phone_by_object_id = {r["_id"]: r.get("phone", "") for r in mongo_driver_rows if r.get("_id")}
    out: list[dict[str, str | None]] = []
    for row in bank_rows:
        old_driver_id = (row.get("driver_id") or "").strip()
        phone_raw = phone_by_object_id.get(old_driver_id) if old_driver_id else None
        out.append(
            {
                "old_driver_id": old_driver_id or None,
                "phone": normalize_phone(phone_raw) if phone_raw else None,
                "sin_raw": (row.get("sin") or "").strip() or None,
                # banks.csv stores DOB as a full ISO datetime
                # ("1992-08-03T00:00:00.000"), not the plain date DATE_FORMATS
                # expects — truncate at "T" so iso_date's "%Y-%m-%d" branch
                # matches, same as every other DOB source in this module.
                "date_of_birth_raw": (row.get("date_of_birth") or "").split("T", 1)[0],
            }
        )
    return out


def plan_legacy_sin_dob_import(
    bank_rows: list[dict[str, str]], mongo_driver_rows: list[dict[str, str]]
) -> SinDobImportPlan:
    plan = SinDobImportPlan()
    joined = join_legacy_bank_sin_dob(bank_rows, mongo_driver_rows)

    phones = sorted({r["phone"] for r in joined if r["phone"]})
    drivers_by_phone: dict[str, dict[str, Any]] = {}
    if phones:
        cols = "id,phone,sin,date_of_birth,legacy_import_metadata"
        for d in _select_in("drivers", cols, "phone", phones):
            key = d.get("phone")
            if key and key not in drivers_by_phone:
                drivers_by_phone[key] = d

    seen_driver_ids: set[str] = set()
    for row in joined:
        old_id = row["old_driver_id"] or "unknown"

        if not row["phone"]:
            plan.warnings.append(
                ImportErrorItem(old_id, "phone", "driver_id has no matching row in the Mongo drivers export")
            )
            plan.skipped_unmatched += 1
            continue

        driver = drivers_by_phone.get(row["phone"])
        if not driver:
            plan.warnings.append(ImportErrorItem(old_id, "phone", "no Spinr driver with this phone number"))
            plan.skipped_unmatched += 1
            continue

        meta = driver.get("legacy_import_metadata") or {}
        if meta.get("source") != IMPORT_SOURCE:
            plan.warnings.append(
                ImportErrorItem(
                    old_id, "legacy_import_metadata", "matched driver is not a known legacy-imported driver; skipped"
                )
            )
            plan.skipped_not_legacy_driver += 1
            continue

        if driver["id"] in seen_driver_ids:
            plan.warnings.append(
                ImportErrorItem(old_id, "phone", "duplicate phone match within this batch; first row wins")
            )
            plan.skipped_duplicate_match += 1
            continue
        seen_driver_ids.add(driver["id"])

        update: dict[str, Any] = {"id": driver["id"], "old_driver_id": old_id}

        if row["sin_raw"]:
            if driver.get("sin"):
                plan.skipped_already_on_file += 1
            else:
                try:
                    update["_plain_sin"] = validate_sin(row["sin_raw"])
                except ValueError as exc:
                    plan.warnings.append(ImportErrorItem(old_id, "sin", f"invalid SIN, skipped: {exc}"))

        if row["date_of_birth_raw"]:
            dob_iso = iso_date(row["date_of_birth_raw"])
            if not dob_iso:
                plan.warnings.append(ImportErrorItem(old_id, "date_of_birth", "could not parse date"))
            elif driver.get("date_of_birth"):
                pass  # already on file — never clobber, not counted as an error
            else:
                update["date_of_birth"] = dob_iso

        if "_plain_sin" in update or "date_of_birth" in update:
            plan.updates.append(update)

    return plan


def apply_legacy_sin_dob_import(plan: SinDobImportPlan, *, batch: str) -> list[str]:
    """Write ``plan.updates`` to ``drivers``. Idempotent: re-running after a
    partial failure only ever affects drivers still missing sin/date_of_birth,
    since ``plan_legacy_sin_dob_import`` already excludes anything on file.

    Each update is guarded with ``.is_(<column>, "null")`` on exactly the
    column(s) it writes — the same pattern
    ``stripe_mapping_import_service.commit_plan`` already uses for this exact
    race. The plan-time snapshot in ``plan_legacy_sin_dob_import`` only proves
    the column was null *when planned*; without this guard, a driver who
    self-enters their SIN via ``routes/drivers/profile.py`` during the
    (possibly minutes-long) batch loop would have that value silently
    overwritten by the stale plan snapshot's legacy value when this loop
    reaches their row. A guard miss is never retried onto another row — it's
    reported back as a conflict (0 rows matched, self-entry won) so the
    caller can log it, never treated as success.

    Returns the ``old_driver_id`` of every update whose guard didn't match
    (self-entry won the race in between plan and apply) — empty on a clean run.
    """
    if plan.errors:
        raise RuntimeError("refusing to apply with validation errors")

    now_iso = datetime.now(timezone.utc).isoformat()
    conflicts: list[str] = []
    for upd in plan.updates:
        fields: dict[str, Any] = {"updated_at": now_iso}
        plain_sin = upd.get("_plain_sin")
        if plain_sin:
            fields["sin"] = encrypt_pii(plain_sin)
            fields["sin_last4"] = sin_last4(plain_sin)
            fields["sin_collected_at"] = now_iso
        if upd.get("date_of_birth"):
            fields["date_of_birth"] = upd["date_of_birth"]

        driver_id = upd["id"]
        existing = supabase.table("drivers").select("legacy_import_metadata").eq("id", driver_id).execute().data
        meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        # sin_written/dob_written distinguish "this batch actually wrote the
        # SIN" from "this batch only backfilled DOB for a driver whose SIN
        # was already on file" — a plain marker-key presence check cannot
        # tell those apart (see sin_source() below), and conflating them
        # would mislabel a self-entered SIN as legacy-imported.
        meta[LEGACY_BANK_SIN_DOB_SOURCE] = {
            "batch": batch,
            "imported_at": now_iso,
            "sin_written": bool(plain_sin),
            "dob_written": bool(upd.get("date_of_birth")),
        }
        fields["legacy_import_metadata"] = meta

        query = supabase.table("drivers").update(fields).eq("id", driver_id)
        if plain_sin:
            query = query.is_("sin", "null")
        if upd.get("date_of_birth"):
            query = query.is_("date_of_birth", "null")
        res = query.execute()
        if not res.data:
            conflicts.append(upd["old_driver_id"])

    return conflicts


def sin_source(driver: dict[str, Any] | None) -> str | None:
    """Derive SIN provenance for display, without changing what
    ``sin_collected_at`` means or is written as (see
    docs/audit/2026-08-19-legacy-migration-data-quality-audit.md,
    "sin_collected_at misrepresents provenance").

    - ``"legacy_import"`` — this driver's SIN was written by
      ``apply_legacy_sin_dob_import`` (the ``banks.csv`` backfill), detected
      via the ``sin_written`` flag on its ``legacy_import_metadata`` marker.
      The marker key alone is not enough: it is also stamped when a batch
      only backfills date_of_birth for a driver whose SIN was already
      self-entered, which must NOT be reported as legacy-imported.
    - ``"self_entry"`` — ``sin_collected_at`` is set and the above doesn't
      apply; the driver supplied it via ``routes/drivers/profile.py``.
    - ``None`` — no SIN on file (or no driver row).

    Pure function, no DB access — callers pass a driver row (or dict subset)
    that already has ``sin_collected_at`` and ``legacy_import_metadata``.
    """
    if not driver:
        return None
    meta = driver.get("legacy_import_metadata") or {}
    marker = meta.get(LEGACY_BANK_SIN_DOB_SOURCE) if isinstance(meta, dict) else None
    if isinstance(marker, dict) and marker.get("sin_written"):
        return "legacy_import"
    if driver.get("sin_collected_at"):
        return "self_entry"
    return None


def print_sin_dob_report(plan: SinDobImportPlan, *, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "COMMIT"
    print(f"{mode} report — legacy SIN/DOB backfill")
    print(f"  drivers to update: {len(plan.updates)}")
    print(f"  skipped (no matching driver): {plan.skipped_unmatched}")
    print(f"  skipped (not a known legacy driver): {plan.skipped_not_legacy_driver}")
    print(f"  skipped (already on file): {plan.skipped_already_on_file}")
    print(f"  skipped (duplicate match within batch): {plan.skipped_duplicate_match}")
    print(f"  warnings: {len(plan.warnings)}")
    print(f"  errors: {len(plan.errors)}")
    for item in plan.warnings[:50]:
        print(f"WARNING old_driver_id={item.old_driver_id} field={item.field}: {item.message}")
    for item in plan.errors[:100]:
        print(f"ERROR old_driver_id={item.old_driver_id} field={item.field}: {item.message}")
