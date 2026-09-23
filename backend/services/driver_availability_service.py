"""Driver availability snapshots and epoch-fenced status commands."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

try:
    from .. import db_supabase
    from ..repositories import driver_availability_repo
except ImportError:  # pragma: no cover - backend top-level import mode
    import db_supabase  # type: ignore
    from repositories import driver_availability_repo  # type: ignore


_ACTIVE_RIDE_STATUSES = {"driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}
_SUPPORTED_COMMANDS = {"go_online", "go_offline", "stop_requests"}
_EXPIRY_FIELDS = (
    ("license_expiry_date", "LICENSE_EXPIRED"),
    ("insurance_expiry_date", "INSURANCE_EXPIRED"),
    ("vehicle_inspection_expiry_date", "DOCUMENT_EXPIRED"),
    ("background_check_expiry_date", "DOCUMENT_EXPIRED"),
)


class AvailabilityLookupError(RuntimeError):
    """Availability could not be authoritatively assembled."""


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
            except ValueError:
                return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return None


async def _eligibility_reason(driver: dict[str, Any], server_time: datetime) -> str | None:
    status = driver.get("status")
    if status in ("banned", "suspended"):
        return "ACCOUNT_SUSPENDED"
    if status == "needs_review":
        return "ACCOUNT_REVIEW"
    if status != "active":
        return "ACCOUNT_INELIGIBLE"

    area_id = driver.get("service_area_id")
    covered_legacy_fields: set[str] = set()
    if area_id:
        try:
            areas = await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1)
            area = areas[0] if areas else None
            requirements = [r for r in (area or {}).get("required_documents") or [] if r.get("required", True)]
            docs = await db_supabase.get_rows(
                "driver_documents", {"driver_id": driver["id"], "status": "approved"}, limit=200
            ) if requirements else []
        except Exception as exc:
            raise AvailabilityLookupError("eligibility lookup unavailable") from exc

        for requirement in requirements:
            key = str(requirement.get("key") or "").lower()
            label = str(requirement.get("label") or "").lower()
            req_id = requirement.get("id")
            matches = []
            for document in docs:
                doc_key = str(document.get("requirement_key") or "").lower()
                doc_type = str(document.get("document_type") or "").lower()
                doc_id = document.get("requirement_id")
                if (doc_key and doc_key == key) or (doc_id and (doc_id == req_id or str(doc_id).lower() == key)):
                    matches.append(document)
                elif doc_type and (doc_type == label or doc_type == key.replace("_", " ")):
                    matches.append(document)
                elif doc_type and key and key.replace("_", "") in doc_type.replace(" ", "").replace("_", ""):
                    matches.append(document)
            if not matches:
                continue
            matches.sort(key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
            expiry = _as_utc(matches[0].get("expiry_date") or matches[0].get("expires_at"))
            if expiry and expiry < server_time:
                return "DOCUMENT_EXPIRED"
            requirement_name = str(requirement.get("label") or requirement.get("key") or "").lower()
            if "license" in requirement_name or "driving" in requirement_name or "permit" in requirement_name:
                covered_legacy_fields.add("license_expiry_date")
            if "insurance" in requirement_name:
                covered_legacy_fields.add("insurance_expiry_date")
            if "inspection" in requirement_name:
                covered_legacy_fields.add("vehicle_inspection_expiry_date")
            if "background" in requirement_name:
                covered_legacy_fields.add("background_check_expiry_date")

    for field, code in _EXPIRY_FIELDS:
        if field in covered_legacy_fields:
            continue
        expiry = _as_utc(driver.get(field))
        if expiry and expiry < server_time:
            return code
    return None


async def _read_snapshot(user_id: str) -> dict[str, Any]:
    try:
        raw = await driver_availability_repo.get_driver_availability_snapshot(user_id)
        if not isinstance(raw, dict) or not isinstance(raw.get("driver"), dict):
            raise AvailabilityLookupError("driver profile missing")
        if raw.get("driver_count") != 1:
            raise AvailabilityLookupError("driver profile is ambiguous")
        server_time = _as_utc(raw.get("server_time"))
        if server_time is None:
            raise AvailabilityLookupError("database clock unavailable")
        return {**raw, "_server_time": server_time}
    except AvailabilityLookupError:
        raise
    except Exception as exc:
        raise AvailabilityLookupError("availability snapshot unavailable") from exc


async def get_driver_availability(
    user_id: str, authenticated_session_id: str | None = None
) -> dict[str, Any]:
    """Return an eligibility-aware snapshot ordered by the database clock."""
    raw = await _read_snapshot(user_id)
    driver = raw["driver"]
    server_time: datetime = raw["_server_time"]
    try:
        blocked_reason = await _eligibility_reason(driver, server_time)
    except AvailabilityLookupError:
        raise
    except Exception as exc:
        raise AvailabilityLookupError("eligibility lookup unavailable") from exc

    active_ride = raw.get("active_ride")
    pending_offer = raw.get("pending_offer")
    is_online = bool(driver.get("is_online"))
    accepting = bool(driver.get("accepting_requests"))
    reason = blocked_reason
    controller_mismatch = bool(
        raw.get("protocol_enabled")
        and driver.get("controller_session_id")
        and (not authenticated_session_id or driver.get("controller_session_id") != authenticated_session_id)
    )
    if active_ride and active_ride.get("status") in _ACTIVE_RIDE_STATUSES:
        availability_state, reason = "paused", "ACTIVE_TRIP"
    elif blocked_reason:
        availability_state = "blocked"
    elif raw.get("offer_reconciliation_required"):
        availability_state, reason = "reconnecting", "RECOVERY_REQUIRED"
    elif controller_mismatch:
        availability_state = "reconnecting"
        reason = "SESSION_RECONCILE_REQUIRED" if not authenticated_session_id else "SESSION_SUPERSEDED"
    elif pending_offer:
        availability_state, reason = "paused", "OFFER_PENDING"
    elif is_online and accepting and bool(driver.get("is_available")):
        ready_until = _as_utc(driver.get("ready_until"))
        last_contact = _as_utc(driver.get("last_contact_at"))
        if raw.get("protocol_enabled") and (ready_until is None or ready_until <= server_time):
            availability_state, reason = "paused", "READY_TIMEOUT"
        elif raw.get("protocol_enabled") and (last_contact is None or (server_time - last_contact).total_seconds() > 90):
            availability_state, reason = "reconnecting", "PRESENCE_UNAVAILABLE"
        else:
            availability_state, reason = "ready", None
    elif is_online:
        availability_state = "paused"
        reason = {
            "pause_idle": "READY_TIMEOUT",
            "pause_unreachable": "PRESENCE_UNAVAILABLE",
            "pause_misses": "MISSED_OFFERS",
            "stop_requests": "REQUESTS_STOPPED",
            "controller_displaced": "SESSION_SUPERSEDED",
        }.get(driver.get("availability_reason"), "REQUESTS_PAUSED")
    else:
        pause_reason = {
            "pause_idle": "READY_TIMEOUT",
            "pause_unreachable": "PRESENCE_UNAVAILABLE",
            "pause_misses": "MISSED_OFFERS",
            "controller_displaced": "SESSION_SUPERSEDED",
        }.get(driver.get("availability_reason"))
        availability_state, reason = ("paused", pause_reason) if pause_reason else ("offline", "OFFLINE_INTENT")

    def safe_ride(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        return {key: value.get(key) for key in ("id", "status", "updated_at")}

    def safe_offer(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        return {key: value.get(key) for key in ("id", "ride_id", "offered_at", "expires_at", "ride_status")}

    return {
        "protocol_enabled": bool(raw.get("protocol_enabled")),
        "state_version": str(driver.get("state_version", 0)),
        "online_epoch": str(driver.get("online_epoch", 0)),
        "is_online": is_online,
        "accepting_requests": accepting,
        "is_available": bool(driver.get("is_available")),
        "availability_state": availability_state,
        "reason_code": reason,
        "eligibility_reason": blocked_reason,
        "controller_session_id": driver.get("controller_session_id"),
        "server_time": raw["server_time"],
        "snapshot_issued_at": raw["server_time"],
        "last_contact_at": driver.get("last_contact_at"),
        "ready_until": driver.get("ready_until"),
        "active_ride": safe_ride(active_ride),
        "pending_offer": safe_offer(pending_offer),
        "offer_reconciliation_required": bool(raw.get("offer_reconciliation_required")),
    }


async def change_driver_availability(
    user_id: str, command: dict[str, Any], authenticated_session_id: str | None
) -> dict[str, Any]:
    """Commit one allowlisted status command, then return its fresh snapshot."""
    action = command.get("action")
    raw_epoch = command.get("online_epoch")
    try:
        epoch = int(raw_epoch) if isinstance(raw_epoch, str) and raw_epoch.isdecimal() else raw_epoch
    except (TypeError, ValueError):
        epoch = None
    request_id = command.get("request_id")
    if action not in _SUPPORTED_COMMANDS:
        return {"code": "INVALID_AVAILABILITY_COMMAND"}
    if (
        type(epoch) is not int
        or not 0 <= epoch <= 9_223_372_036_854_775_807
        or not isinstance(request_id, str)
        or not request_id.strip()
        or len(request_id) > 128
    ):
        return {"code": "AVAILABILITY_UPGRADE_REQUIRED"}
    if not authenticated_session_id:
        return {"code": "SESSION_RECONCILE_REQUIRED"}

    raw = await _read_snapshot(user_id)
    driver = raw["driver"]
    if not raw.get("protocol_enabled"):
        return {"code": "AVAILABILITY_V2_DISABLED"}
    if action == "go_online":
        eligibility_reason = await _eligibility_reason(driver, raw["_server_time"])
        if eligibility_reason:
            return {"code": "ELIGIBILITY_BLOCKED", "reason_code": eligibility_reason}
    try:
        result = await driver_availability_repo.transition_driver_availability(
            str(driver["id"]), epoch, authenticated_session_id, action, request_id.strip()
        )
    except Exception as exc:
        raise AvailabilityLookupError("availability command unavailable") from exc
    if result.get("code") != "OK":
        return result
    snapshot = await get_driver_availability(user_id, authenticated_session_id)
    return {**snapshot, "code": "OK", "transition": result}
