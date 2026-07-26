"""Bulk rider import — parse, validate, and commit rider CSV uploads.

Columns: customer_id (Stripe cus_…), email, gender, phone, ratings,
temp_email, timeZone.

Follows the same validate-then-commit contract as driver_import_service.
Reports carry only row numbers + field + message — never raw PII.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase

REQUIRED_COLUMNS = {"phone"}

OPTIONAL_COLUMNS = {"customer_id", "email", "gender", "ratings", "temp_email", "timezone"}

HEADER_ALIASES: dict[str, str] = {
    "customerid": "customer_id",
    "customer_id": "customer_id",
    "stripe_customer_id": "customer_id",
    "phonenumber": "phone",
    "phone_number": "phone",
    "no_of_rides": "ratings",
    "email": "email",
    "temp_email": "temp_email",
    "tempemail": "temp_email",
    "gender": "gender",
    "timezone": "timezone",
    "time_zone": "timezone",
    "timzone": "timezone",
    "ratings": "ratings",
    "rating": "ratings",
    "name": "name",
    "full_name": "name",
    "fullname": "name",
    "first_name": "first_name",
    "firstname": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
}


@dataclass
class ImportReportItem:
    row_num: int
    field: str
    message: str


@dataclass
class RiderImportPlan:
    users_to_create: list[dict[str, Any]] = field(default_factory=list)
    users_to_update: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ImportReportItem] = field(default_factory=list)
    errors: list[ImportReportItem] = field(default_factory=list)


def normalize_header(value: str) -> str:
    value = value.strip().lower().replace("?", "")
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    return HEADER_ALIASES.get(value, value)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone.strip()


def read_csv_text(text: str) -> list[dict[str, str]]:
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    reader.fieldnames = [normalize_header(h or "") for h in reader.fieldnames]
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def _prefetch_existing(
    phones: list[str], emails: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Prefetch existing users by phone/email and drivers by phone.

    Returns (users_by_phone, users_by_email, drivers_by_phone).
    """
    users_by_phone: dict[str, dict[str, Any]] = {}
    users_by_email: dict[str, dict[str, Any]] = {}
    drivers_by_phone: dict[str, dict[str, Any]] = {}

    if phones:
        for u in _select_in(
            "users", "id,phone,email,first_name,last_name,is_rider,is_driver,stripe_customer_id", "phone", phones
        ):
            key = u.get("phone")
            if key and key not in users_by_phone:
                users_by_phone[key] = u
        for d in _select_in("drivers", "id,phone,name,user_id", "phone", phones):
            key = d.get("phone")
            if key and key not in drivers_by_phone:
                drivers_by_phone[key] = d

    if emails:
        for u in _select_in(
            "users", "id,phone,email,first_name,last_name,is_rider,is_driver,stripe_customer_id", "email", emails
        ):
            key = (u.get("email") or "").strip().lower()
            if key and key not in users_by_email:
                users_by_email[key] = u

    return users_by_phone, users_by_email, drivers_by_phone


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def build_plan(rows: list[dict[str, str]], batch: str) -> RiderImportPlan:
    plan = RiderImportPlan()

    if not rows:
        plan.errors.append(ImportReportItem(0, "csv", "CSV is empty"))
        return plan

    has_phone = "phone" in rows[0]
    if not has_phone:
        plan.errors.append(ImportReportItem(0, "phone", "CSV is missing required column: phone"))
        return plan

    phones = sorted({normalize_phone(r.get("phone", "")) for r in rows if r.get("phone", "").strip()})
    emails = sorted({(r.get("email") or "").strip().lower() for r in rows if (r.get("email") or "").strip()})
    users_by_phone, users_by_email, drivers_by_phone = _prefetch_existing(phones, emails)

    seen_phones: set[str] = set()

    for idx, row in enumerate(rows, start=1):
        raw_phone = (row.get("phone") or "").strip()
        if not raw_phone:
            plan.errors.append(ImportReportItem(idx, "phone", "phone is required"))
            continue

        phone = normalize_phone(raw_phone)

        if not re.match(r"^\+1\d{10}$", phone):
            plan.errors.append(ImportReportItem(idx, "phone", f"invalid phone format (expected +1XXXXXXXXXX): {phone}"))
            continue

        if phone in seen_phones:
            plan.errors.append(ImportReportItem(idx, "phone", "duplicate phone in CSV"))
            continue
        seen_phones.add(phone)

        email = (row.get("email") or "").strip().lower() or None
        customer_id = (row.get("customer_id") or "").strip() or None
        gender = (row.get("gender") or "").strip() or None
        ratings_raw = (row.get("ratings") or "").strip()
        temp_email = (row.get("temp_email") or "").strip() or None
        tz = (row.get("timezone") or "").strip() or None

        first_name = (row.get("first_name") or "").strip()
        last_name = (row.get("last_name") or "").strip()
        if not first_name and row.get("name"):
            first_name, last_name = _split_name(row["name"])

        if customer_id and not customer_id.startswith("cus_"):
            plan.warnings.append(
                ImportReportItem(
                    idx, "customer_id", f"customer_id '{customer_id}' doesn't look like a Stripe ID (expected cus_…)"
                )
            )

        existing_user = users_by_phone.get(phone)
        if not existing_user and email:
            existing_user = users_by_email.get(email)

        existing_driver = drivers_by_phone.get(phone)

        if existing_user:
            dup_info: dict[str, Any] = {
                "row": idx,
                "phone": phone,
                "existing_user_id": existing_user["id"],
                "is_driver": bool(existing_user.get("is_driver")),
                "is_rider": bool(existing_user.get("is_rider")),
                "has_driver_profile": existing_driver is not None,
            }

            if existing_driver:
                plan.duplicates.append({**dup_info, "match_type": "driver"})
                plan.warnings.append(
                    ImportReportItem(
                        idx,
                        "phone",
                        "phone matches existing DRIVER — will update stripe_customer_id if provided, rider flags already set",
                    )
                )
            else:
                plan.duplicates.append({**dup_info, "match_type": "rider"})
                plan.warnings.append(
                    ImportReportItem(idx, "phone", "phone matches existing user — will update fields if provided")
                )

            update_fields: dict[str, Any] = {"id": existing_user["id"]}
            if customer_id and existing_user.get("stripe_customer_id") != customer_id:
                update_fields["stripe_customer_id"] = customer_id
            if email and not existing_user.get("email"):
                update_fields["email"] = email
            if gender and gender.lower() in ("male", "female", "non-binary", "other", "prefer not to say"):
                update_fields["gender"] = gender
            if not existing_user.get("is_rider"):
                update_fields["is_rider"] = True

            if len(update_fields) > 1:
                plan.users_to_update.append(update_fields)

            continue

        user_id = str(uuid.uuid4())
        user_row: dict[str, Any] = {
            "id": user_id,
            "phone": phone,
            "role": "rider",
            "is_rider": True,
            "is_driver": False,
            "profile_complete": bool(first_name),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if first_name:
            user_row["first_name"] = first_name
        if last_name:
            user_row["last_name"] = last_name
        if email:
            user_row["email"] = email
        if customer_id:
            user_row["stripe_customer_id"] = customer_id
        if gender:
            user_row["gender"] = gender

        plan.users_to_create.append(user_row)

    return plan


def commit_plan(plan: RiderImportPlan) -> None:
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")

    if plan.users_to_create:
        for i in range(0, len(plan.users_to_create), 200):
            batch = plan.users_to_create[i : i + 200]
            supabase.table("users").insert(batch).execute()

    for upd in plan.users_to_update:
        user_id = upd.pop("id")
        if not upd:
            continue
        upd["updated_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("users").update(upd).eq("id", user_id).execute()
