"""Driver availability snapshots and epoch-fenced status commands."""

from __future__ import annotations

import uuid
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


async def driver_availability_v2_enabled() -> bool:
    try:
        rows = await db_supabase.get_rows(
            "settings", {"id": "app_settings"}, limit=1, columns="driver_availability_v2_enabled"
        )
    except Exception as exc:
        raise AvailabilityLookupError("availability rollout flag unavailable") from exc
    return bool(rows and rows[0].get("driver_availability_v2_enabled", False))


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


async def _scoped_dispatch_evidence_fresh(driver: dict[str, Any], server_time: datetime) -> tuple[bool, str | None]:
    captured_at = _as_utc(driver.get("location_captured_at"))
    if (
        captured_at is None
        or (server_time - captured_at).total_seconds() < -5
        or (server_time - captured_at).total_seconds() > 60
    ):
        return False, "LOCATION_STALE"
    session_id = driver.get("controller_session_id")
    epoch = driver.get("online_epoch")
    if not isinstance(session_id, str) or type(epoch) is not int or epoch < 0:
        return False, "PRESENCE_UNAVAILABLE"
    try:
        try:
            from ..utils.driver_presence import get_scoped_driver_presence
        except ImportError:  # pragma: no cover
            from utils.driver_presence import get_scoped_driver_presence  # type: ignore
        evidence = await get_scoped_driver_presence(str(driver["id"]), session_id, epoch)
    except Exception:
        return False, "PRESENCE_UNAVAILABLE"
    if not evidence:
        return False, "PRESENCE_UNAVAILABLE"
    contact_deadline = _as_utc(evidence.get("contact_valid_until"))
    location_deadline = _as_utc(evidence.get("location_valid_until"))
    if contact_deadline is None or contact_deadline <= server_time:
        return False, "PRESENCE_UNAVAILABLE"
    if location_deadline is None or location_deadline <= server_time:
        return False, "LOCATION_STALE"
    return True, None


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
            docs = (
                await db_supabase.get_rows(
                    "driver_documents", {"driver_id": driver["id"], "status": "approved"}, limit=200
                )
                if requirements
                else []
            )
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

    # ── Subscription / entitlement checks ────────────────────────────
    driver_id = driver.get("id")
    area_id = driver.get("service_area_id")
    _sub_required = False
    if area_id:
        try:
            _areas = await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1)
            _area = _areas[0] if _areas else None
            _sub_required = bool(_area and _area.get("subscription_required"))
            if not _sub_required and _area and _area.get("parent_service_area_id"):
                _parents = await db_supabase.get_rows("service_areas", {"id": _area["parent_service_area_id"]}, limit=1)
                _parent = _parents[0] if _parents else None
                _sub_required = bool(_parent and _parent.get("subscription_required"))
        except Exception as exc:
            raise AvailabilityLookupError("subscription area lookup unavailable") from exc
    if not _sub_required:
        try:
            _settings = await db_supabase.get_rows(
                "settings", {"id": "app_settings"}, limit=1, columns="require_driver_subscription"
            )
            _sub_required = bool(_settings and _settings[0].get("require_driver_subscription"))
        except Exception:  # noqa: S110 — global flag missing is not fatal
            pass

    if _sub_required and driver_id:
        try:
            _subs = await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": driver_id, "status": "active"},
                limit=1,
                columns="id,expires_at",
            )
            _has_active = False
            for _s in _subs or []:
                _exp = _as_utc(_s.get("expires_at"))
                if _exp is None or _exp > server_time:
                    _has_active = True
                    break
            if not _has_active:
                return "SUBSCRIPTION_REQUIRED"
        except Exception as exc:
            raise AvailabilityLookupError("subscription lookup unavailable") from exc

    # Quota check — fail open (logged, ignored)
    if driver_id:
        try:
            try:
                from ..utils.spinr_pass import quota_status as _quota_status
            except ImportError:
                from utils.spinr_pass import quota_status as _quota_status  # type: ignore
            _qs = await _quota_status(driver_id)
            if _qs == "exhausted":
                return "QUOTA_EXHAUSTED"
        except Exception:  # noqa: S110 — quota check failure is non-fatal
            pass

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


async def get_driver_availability(user_id: str, authenticated_session_id: str | None = None) -> dict[str, Any]:
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
    # A2(a): with the flag off, an online driver must not show paused/REQUESTS_PAUSED.
    accepting = bool(driver.get("accepting_requests")) if raw.get("protocol_enabled") else is_online
    reason = blocked_reason
    verification_reason = "DRIVER_UNVERIFIED" if driver.get("is_verified") is not True else None
    # F2-8b: session/controller checks use current_session_id from the snapshot.
    # The newest login must never be told its session ended (Addendum A3).
    has_controller = bool(raw.get("protocol_enabled") and driver.get("controller_session_id"))
    current_session_id = raw.get("current_session_id")
    if has_controller and (not authenticated_session_id or not current_session_id):
        session_state = "SESSION_RECONCILE_REQUIRED"
    elif has_controller and authenticated_session_id != current_session_id:
        session_state = "SESSION_SUPERSEDED"
    elif has_controller and driver.get("controller_session_id") != authenticated_session_id and is_online:
        session_state = "REQUESTS_PAUSED"
    else:
        session_state = None
    if active_ride and active_ride.get("status") in _ACTIVE_RIDE_STATUSES:
        availability_state, reason = "paused", "ACTIVE_TRIP"
    elif blocked_reason:
        availability_state = "blocked"
    elif is_online and verification_reason:
        availability_state, reason = "blocked", verification_reason
    elif raw.get("offer_reconciliation_required"):
        availability_state, reason = "reconnecting", "RECOVERY_REQUIRED"
    elif session_state == "SESSION_RECONCILE_REQUIRED":
        availability_state, reason = "reconnecting", "SESSION_RECONCILE_REQUIRED"
    elif session_state == "SESSION_SUPERSEDED":
        availability_state, reason = "reconnecting", "SESSION_SUPERSEDED"
    elif session_state == "REQUESTS_PAUSED":
        availability_state, reason = "paused", "REQUESTS_PAUSED"
    elif pending_offer:
        availability_state, reason = "paused", "OFFER_PENDING"
    elif is_online and accepting and bool(driver.get("is_available")):
        ready_until = _as_utc(driver.get("ready_until"))
        last_contact = _as_utc(driver.get("last_contact_at"))
        # F2-4: READY_TIMEOUT only when readiness is enforced.
        if raw.get("readiness_enforced") and (ready_until is None or ready_until <= server_time):
            availability_state, reason = "paused", "READY_TIMEOUT"
        elif raw.get("protocol_enabled") and (
            last_contact is None or (server_time - last_contact).total_seconds() > 90
        ):
            availability_state, reason = "reconnecting", "PRESENCE_UNAVAILABLE"
        elif raw.get("protocol_enabled"):
            evidence_ready, evidence_reason = await _scoped_dispatch_evidence_fresh(driver, server_time)
            if evidence_ready:
                availability_state, reason = "ready", None
            elif evidence_reason == "PRESENCE_UNAVAILABLE":
                availability_state, reason = "reconnecting", evidence_reason
            else:
                availability_state, reason = "paused", evidence_reason
        else:
            availability_state, reason = "ready", None
    elif is_online:
        availability_state = "paused"
        reason = {
            "pause_policy": "POLICY_BLOCKED",
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
        offer = {key: value.get(key) for key in ("id", "ride_id", "offered_at", "expires_at", "ride_status")}
        # v2 envelope (C1, T6-7): only offers created by the v3 claim carry
        # online_epoch; legacy offers keep the original five keys.
        if value.get("online_epoch") is not None:
            offer.update(
                {
                    "offer_protocol": "v2",
                    "offer_id": value.get("offer_id") or value.get("id"),
                    "claim_id": value.get("claim_id"),
                    "online_epoch": str(value.get("online_epoch")),
                    "server_time": value.get("offered_at"),
                }
            )
        return offer

    return {
        "protocol_enabled": bool(raw.get("protocol_enabled")),
        "state_version": str(driver.get("state_version", 0)),
        "online_epoch": str(driver.get("online_epoch", 0)),
        "is_online": is_online,
        "accepting_requests": accepting,
        "is_available": bool(driver.get("is_available")),
        "availability_state": availability_state,
        "reason_code": reason,
        "eligibility_reason": blocked_reason or (verification_reason if is_online else None),
        "controller_session_id": driver.get("controller_session_id"),
        "server_time": raw["server_time"],
        "snapshot_issued_at": raw["server_time"],
        "last_contact_at": driver.get("last_contact_at"),
        "ready_until": driver.get("ready_until"),
        "readiness_enforced": bool(raw.get("readiness_enforced")),
        "readiness_prompt_at": raw.get("readiness_prompt_at"),
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
    # A token session can never act as a trusted system actor.
    if not authenticated_session_id or str(authenticated_session_id).startswith(
        driver_availability_repo.SYSTEM_ACTOR_PREFIX
    ):
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


async def pause_driver_for_policy(
    user_id: str,
    *,
    blocking_statuses: set[str],
    request_id: str,
) -> dict[str, Any]:
    """Pause offers after a trusted caller has committed a blocking policy state.

    Runs as the trusted ``system:policy`` actor, so it works whether the
    driver's current session is NULL, current, or not the controller.
    Re-read and recheck the blocking status on the single stale-epoch retry;
    never adopt an epoch captured by a stale event or callback.
    """
    for attempt in range(2):
        raw = await _read_snapshot(user_id)
        if not raw.get("protocol_enabled"):
            return {"code": "AVAILABILITY_V2_DISABLED"}
        driver = raw["driver"]
        if driver.get("status") not in blocking_statuses:
            return {"code": "POLICY_STATE_CHANGED"}
        try:
            result = await driver_availability_repo.transition_driver_availability(
                str(driver["id"]),
                int(driver.get("online_epoch") or 0),
                driver_availability_repo.system_actor("policy"),
                "pause_policy",
                request_id if attempt == 0 else str(uuid.uuid4()),
            )
        except Exception as exc:
            raise AvailabilityLookupError("policy availability pause unavailable") from exc
        if result.get("code") != "ONLINE_EPOCH_STALE" or attempt == 1:
            return result
    return {"code": "ONLINE_EPOCH_STALE"}
