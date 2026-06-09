"""Pure eligibility rules for driver onboarding reminder pushes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/Regina"
LOCAL_SEND_HOUR = 8


def as_utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid driver timezone %r; falling back to %s", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def driver_timezone(driver: dict[str, Any], areas: dict[str, dict[str, Any]]) -> str:
    area = areas.get(str(driver.get("service_area_id"))) if driver.get("service_area_id") else None
    return (area or {}).get("timezone") or driver.get("timezone") or DEFAULT_TIMEZONE


def local_date_for_send_window(
    driver: dict[str, Any],
    areas: dict[str, dict[str, Any]],
    now: datetime,
) -> str | None:
    local_now = now.astimezone(_zone(driver_timezone(driver, areas)))
    return local_now.date().isoformat() if local_now.hour == LOCAL_SEND_HOUR else None


def _load_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _pretty(value: Any) -> str:
    return str(value or "Document").replace("_", " ").replace("-", " ").strip().title()


def mandatory_requirements(
    driver: dict[str, Any],
    areas: dict[str, dict[str, Any]],
    global_reqs: list[dict[str, Any]],
) -> list[tuple[str | None, str | None, str]]:
    area = areas.get(str(driver.get("service_area_id"))) if driver.get("service_area_id") else None
    out: list[tuple[str | None, str | None, str]] = []
    for item in _load_list((area or {}).get("required_documents")):
        if isinstance(item, str):
            out.append((None, item, _pretty(item)))
        elif isinstance(item, dict) and item.get("required", item.get("is_mandatory", True)) is not False:
            key = item.get("key") or item.get("requirement_key")
            req_id = item.get("id")
            label = item.get("label") or item.get("name") or _pretty(key or req_id)
            out.append((str(req_id) if req_id else None, str(key) if key else None, str(label)))
    if out:
        return out
    return [
        (str(row["id"]) if row.get("id") else None, None, str(row.get("name") or "Document"))
        for row in global_reqs
        if row.get("is_mandatory") is not False
    ]


def doc_matches_requirement(doc: dict[str, Any], req: tuple[str | None, str | None, str]) -> bool:
    req_id, key, label = req
    key_l = (key or "").lower()
    label_l = (label or "").lower()
    doc_key = (doc.get("requirement_key") or "").lower()
    doc_req = doc.get("requirement_id")
    doc_type = (doc.get("document_type") or "").lower()
    return bool(
        (key_l and doc_key == key_l)
        or (doc_req and (str(doc_req) == str(req_id) or (key_l and str(doc_req).lower() == key_l)))
        or (doc_type and (doc_type == label_l or (key_l and doc_type == key_l.replace("_", " "))))
        or (doc_type and key_l and key_l.replace("_", "") in doc_type.replace(" ", "").replace("_", ""))
    )


def missing_required_document_uploads(
    driver: dict[str, Any],
    docs: list[dict[str, Any]],
    areas: dict[str, dict[str, Any]],
    global_reqs: list[dict[str, Any]],
) -> bool:
    reqs = mandatory_requirements(driver, areas, global_reqs)
    if not reqs:
        return False
    uploaded = [doc for doc in docs if (doc.get("status") or "pending") not in {"superseded", "rejected"}]
    return any(not any(doc_matches_requirement(doc, req) for doc in uploaded) for req in reqs)
