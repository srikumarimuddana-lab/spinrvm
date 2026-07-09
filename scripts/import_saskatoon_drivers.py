#!/usr/bin/env python3
"""Bulk-import legacy Saskatoon drivers and document files.

Dry-run first:
  python3 scripts/import_saskatoon_drivers.py \
    --drivers-csv bulk-import-saskatoon/drivers.csv \
    --documents-csv bulk-import-saskatoon/documents.csv \
    --files-root bulk-import-saskatoon \
    --service-area-name Saskatoon \
    --dry-run

Commit after the dry-run report is clean:
  python3 scripts/import_saskatoon_drivers.py ... --commit

The importer intentionally prints old_driver_id values, not raw PII. Keep CSVs
and reports in a secure location because the input files contain PIPEDA-covered
personal information.
"""

from __future__ import annotations

import argparse
import csv
import mimetypes
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from supabase_client import supabase  # noqa: E402
from utils.driver_code import generate_driver_code  # noqa: E402

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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")
        reader.fieldnames = [normalize_header(h or "") for h in reader.fieldnames]
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


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
        rows = supabase.table("service_areas").select("id,name,province,required_documents,regulatory_authority,regulatory_region").eq("id", service_area_id).limit(1).execute().data or []
    else:
        rows = supabase.table("service_areas").select("id,name,province,required_documents,regulatory_authority,regulatory_region").ilike("name", f"%{service_area_name}%").limit(5).execute().data or []
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
    region = (row.get("regulatory_region") or row.get("province") or service_area.get("regulatory_region") or service_area.get("province") or "").strip().upper()
    area_name = str(service_area.get("name") or "").lower()
    if not region:
        region = "SK" if "saskatoon" in area_name or "saskatchewan" in area_name else ""
    if not authority:
        authority = (service_area.get("regulatory_authority") or "").strip()
    if not authority:
        authority = "SGI" if region == "SK" else "Provincial / municipal authority"
    return authority, region


def build_plan(
    driver_rows: list[dict[str, str]],
    document_rows: list[dict[str, str]],
    files_root: Path,
    service_area: dict[str, Any],
    import_batch: str,
) -> ImportPlan:
    plan = ImportPlan()
    validate_required_columns(driver_rows, plan)
    if plan.errors:
        return plan

    required_docs = service_area.get("required_documents") or []
    allowed_doc_keys = {str(d.get("key")) for d in required_docs if d.get("key")}
    vt_map = vehicle_type_map()
    seen_old_ids: set[str] = set()
    planned_driver_ids: dict[str, str] = {}

    for row in driver_rows:
        old_id = row.get("old_driver_id") or "<missing>"
        if old_id in seen_old_ids:
            plan.errors.append(ImportErrorItem(old_id, "old_driver_id", "duplicate old_driver_id"))
            continue
        seen_old_ids.add(old_id)

        service_scope = (row.get("service_area_id") or row.get("service_area_slug") or row.get("service_area") or "saskatoon").lower()
        if service_scope not in {"saskatoon", str(service_area.get("id", "")).lower(), str(service_area.get("name", "")).lower()}:
            plan.errors.append(ImportErrorItem(old_id, "service_area", "row is not scoped to Saskatoon"))
            continue

        phone = normalize_phone(row.get("phone", ""))
        email = (row.get("email") or "").strip().lower()
        existing_users = supabase.table("users").select("id").eq("phone", phone).limit(1).execute().data or []
        if email and not existing_users:
            existing_users = supabase.table("users").select("id").eq("email", email).limit(1).execute().data or []
        existing_drivers = supabase.table("drivers").select("id").eq("phone", phone).limit(1).execute().data or []
        if existing_users or existing_drivers:
            plan.errors.append(ImportErrorItem(old_id, "phone/email", "matching user or driver already exists; handle manually before import"))
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

        first_name, last_name = split_name(row.get("full_name", ""))
        user_id = str(uuid.uuid4())
        driver_id = str(uuid.uuid4())
        planned_driver_ids[old_id] = driver_id

        regulatory_approved = parse_bool(row.get("regulatory_authority_approved", ""))
        regulatory_authority, regulatory_region = regulatory_authority_defaults(row, service_area)
        is_approved = parse_bool(row.get("spinr_approved", "")) is True and regulatory_approved is True
        driver_status = "needs_review" if not is_approved else "active"

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
                "is_verified": is_approved,
                "is_online": False,
                "is_available": False,
                "sgi_approved": regulatory_approved if regulatory_authority == "SGI" else None,
                "sgi_approved_at": datetime.now(timezone.utc).isoformat() if regulatory_authority == "SGI" and regulatory_approved else None,
                "regulatory_authority": regulatory_authority,
                "regulatory_region": regulatory_region or None,
                "regulatory_authority_approved": regulatory_approved,
                "regulatory_authority_approved_at": datetime.now(timezone.utc).isoformat() if regulatory_approved else None,
                "work_authorization_status": work_auth_status(row),
                "is_permanent_resident": parse_bool(row.get("permanent_resident", "")),
                "is_citizen": parse_bool(row.get("citizen", "")),
                "decals_sent": parse_bool(row.get("decals_sent", "")),
                "legacy_import_metadata": {
                    "batch": import_batch,
                    "old_driver_id": old_id,
                    "source": "legacy_saskatoon_driver_import",
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
        if old_id not in planned_driver_ids:
            plan.errors.append(ImportErrorItem(old_id, "old_driver_id", "document row has no importable driver row"))
            continue
        key = canonical_requirement_key(row.get("requirement_key") or row.get("document_type") or "")
        if allowed_doc_keys and key not in allowed_doc_keys:
            plan.errors.append(ImportErrorItem(old_id, "requirement_key", f"'{key}' is not configured on Saskatoon required_documents"))
            continue
        file_path = files_root / (row.get("file_path") or "")
        if not file_path.is_file():
            plan.errors.append(ImportErrorItem(old_id, "file_path", "document file not found"))
            continue
        doc_id = str(uuid.uuid4())
        ext = file_path.suffix.lower() or ".bin"
        side = row.get("side") or None
        storage_key = f"saskatoon-import/{import_batch}/{old_id}/{key}/{side or 'main'}-{doc_id}{ext}"
        signed_placeholder = f"storage://driver-documents/{storage_key}"
        expiry = iso_date(row.get("expiry_date", ""))
        if row.get("expiry_date") and row.get("expiry_date", "").strip().lower() not in {"indefinite", "valid"} and not expiry:
            plan.errors.append(ImportErrorItem(old_id, "expiry_date", "could not parse document expiry date"))
            continue
        plan.docs_to_insert.append(
            {
                "id": doc_id,
                "driver_id": planned_driver_ids[old_id],
                "requirement_id": None,
                "requirement_key": key,
                "document_type": row.get("document_type") or key.replace("_", " ").title(),
                "document_url": signed_placeholder,
                "side": side,
                "status": row.get("status") or "pending",
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
    supabase.table("users").insert(plan.users_to_insert).execute()
    drivers = []
    for driver in plan.drivers_to_insert:
        copied = dict(driver)
        copied["vehicle_vin"] = encrypt_pii(copied.pop("_plain_vehicle_vin", None))
        copied["license_number"] = encrypt_pii(copied.pop("_plain_license_number", None))
        drivers.append(copied)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk import legacy Saskatoon drivers and document files")
    parser.add_argument("--drivers-csv", type=Path, required=True)
    parser.add_argument("--documents-csv", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--service-area-id")
    parser.add_argument("--service-area-name", default="Saskatoon")
    parser.add_argument("--batch", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--commit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if supabase is None:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")
    drivers = read_csv(args.drivers_csv)
    docs = read_csv(args.documents_csv)
    service_area = get_service_area(args.service_area_id, args.service_area_name)
    plan = build_plan(drivers, docs, args.files_root, service_area, args.batch)
    print_report(plan, dry_run=args.dry_run)
    if plan.errors:
        return 2
    if args.commit:
        commit_plan(plan)
        print("Committed Saskatoon driver import successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
