"""In-ride emergency (SOS) and safety check-in endpoints.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    HTTPException,
    Optional,
    Request,
    RideStatus,
    asyncio,
    datetime,
    get_current_user,
    get_current_user_allow_expired,
    logger,
    notify_safety_team,
    page_sos_on_call,
    ride_action_limit,
    timezone,
    uuid,
)

router = APIRouter()


class EmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@router.post("/{ride_id}/emergency")
@ride_action_limit
async def trigger_emergency(
    ride_id: str,
    body: EmergencyRequest,
    request: Request = None,
    # SOS is never gated behind an auth refresh: a signature-valid token
    # that merely expired mid-trip still identifies the caller. Ride
    # membership is enforced below regardless.
    current_user: dict = Depends(get_current_user_allow_expired),
):
    """Trigger an emergency alert for a live ride"""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to trigger emergency for this ride")

    # Consolidated onto safety_incidents (migration 94). The legacy
    # `emergencies` table was never read by anything (no UI surfaced
    # it, no migration even created it), so this is a clean cutover
    # rather than a parallel write. After this, the rider SOS path
    # lives in the same admin Safety queue as the driver report and
    # the auto check-in escalation.
    now_iso = datetime.now(timezone.utc).isoformat()
    incident = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reported_by_user_id": current_user["id"],
        "role": "rider" if is_rider else "driver",
        "category": "sos_button",
        "description": body.message or "Emergency assistance requested",
        "status": "open",
        "latitude": body.latitude,
        "longitude": body.longitude,
        "reported_at": now_iso,
        "created_at": now_iso,
    }

    try:
        await _deps.db_supabase.insert_one("safety_incidents", incident)
    except Exception as exc:
        # Mirrors backend/routes/safety.py's submit_safety_report — a DB
        # failure here must never look like a 500 the client can't react to.
        # SOSButton.tsx retries 3x (1s/2s backoff) on any thrown error from
        # this call and only shows its persistent FAILED/"call 911" state
        # once all attempts are exhausted, so a clean 503 here is what makes
        # that retry path actually fire instead of a bare unhandled 500.
        logger.error(
            f"[SOS] Failed to persist emergency incident ride_id={ride_id} user_id={current_user['id']}: {exc}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503, detail="Unable to send emergency alert. Please try again or call 911."
        ) from exc

    # Notify admin dashboard via WebSocket. Keep the existing
    # emergency_alert event firing for backward compatibility with any
    # listener wired to that name; notify_safety_team below also emits
    # safety_incident_opened which the safety queue UI listens for.
    try:
        await _deps.manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.error(f"emergency_alert admin broadcast failed: {_exc}", exc_info=True)
    logger.critical(f"EMERGENCY ALERT TRIGGERED for ride {ride_id} by user {current_user['id']}")

    # Email the safety distribution list + CRITICAL log line.
    # No field-name bridging needed now that the incident row uses the
    # safety_incidents schema directly (was previously bridging from
    # the legacy `emergencies` shape which used `message` instead of
    # `description` and had no `category`).
    try:
        await notify_safety_team(incident)
    except Exception:  # pragma: no cover — best effort, never block the SMS path below
        logger.error(
            f"notify_safety_team failed for rider SOS ride={ride_id} incident={incident['id']}",
            exc_info=True,
        )

    # Real on-call paging (ACTION_ITEMS.md B15(b)). Additive fourth channel
    # alongside the admin WS broadcast + safety-team email + CRITICAL log
    # above — none of which reaches an on-call person not already watching
    # the dashboard or a log stream. page_on_call is itself best-effort and
    # never raises (see utils/safety_paging.py); this try/except is belt-
    # and-suspenders so a bug in that module can never block the SMS path
    # below, matching the error-handling posture of every other side effect
    # in this function. No-ops (logs at debug, returns False) until an admin
    # configures sos_paging_webhook_url in app_settings — ships dark.
    try:
        await page_sos_on_call(incident)
    except Exception:  # pragma: no cover — best effort, never block the SMS path below
        logger.error(
            f"page_sos_on_call failed for rider SOS ride={ride_id} incident={incident['id']}",
            exc_info=True,
        )

    # Confirm receipt to the rider/driver who triggered the alert (ACTION_ITEMS.md
    # N15/R38). Every other side effect above notifies someone else (admin
    # dashboard, safety-team email, on-call page, emergency contacts below) --
    # the triggering user themselves got nothing beyond the synchronous HTTP
    # response, which SOSButton.tsx already turns into an in-app "Alert Sent"
    # dialog while the app is foregrounded. This closes the gap for the
    # backgrounded/killed-app case with a real push. priority="safety" is one
    # of the three guaranteed-delivery tiers (features.py::send_push_notification
    # docstring) -- bypasses the push opt-out and falls back to the retry queue
    # on a transient failure, same as the dispatch-offer and account-status
    # pushes elsewhere in this package. Copy deliberately does not claim the
    # alert "will get you help" or replace 911 -- it only confirms the alert
    # reached our team, mirroring domain-safety.md's required phrasing ("We'll
    # alert your emergency contacts and our safety team"). Self-swallowing via
    # spawn(): a failure here must never affect the SOS response or block the
    # SMS loop below, matching every other side effect in this function.
    try:
        _deps.spawn(
            _deps.send_push_notification(
                current_user["id"],
                "SOS Alert Received",
                "Your emergency alert reached our safety team and emergency contacts. "
                "If you're in immediate danger, call 911.",
                data={"type": "sos_confirmation", "ride_id": str(ride_id), "incident_id": incident["id"]},
                priority="safety",
                target_app="rider" if is_rider else "driver",
            )
        )
    except Exception:  # pragma: no cover - best effort, never block the SMS path below
        logger.error(
            f"SOS confirmation push failed to spawn for ride={ride_id} incident={incident['id']}",
            exc_info=True,
        )

    # Notify emergency contacts via SMS (Twilio when configured, console log in dev)
    contacts_notified = 0
    try:
        sms_settings = await _deps.get_app_settings()
        contacts_rows = await _deps.db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=5)
        contacts = list(contacts_rows) if contacts_rows else []

        user = await _deps.db_supabase.get_user_by_id(current_user["id"])
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "A Spinr user"

        location_text = " Location shared with emergency services." if body.latitude and body.longitude else ""
        sms_body = (
            f"URGENT: {user_name} triggered an emergency alert during a Spinr ride."
            f"{location_text} Call them or emergency services immediately."
        )

        # Send all contact SMS concurrently. Serial sends stacked up to five
        # Twilio round-trips on the SOS response; gather cuts that to one.
        # Deliberately awaited (not fire-and-forget): the response's
        # contacts_notified count must reflect what actually happened —
        # this is a safety flow, never claim delivery that wasn't attempted.
        _sms_targets = [c for c in contacts if c.get("phone")]
        _sms_results = await asyncio.gather(
            *(
                _deps.send_sms(
                    c["phone"],
                    sms_body,
                    twilio_sid=(sms_settings.get("twilio_account_sid", "") if sms_settings else ""),
                    twilio_token=(sms_settings.get("twilio_auth_token", "") if sms_settings else ""),
                    twilio_from=(sms_settings.get("twilio_from_number", "") if sms_settings else ""),
                )
                for c in _sms_targets
            ),
            return_exceptions=True,
        )
        for contact, result in zip(_sms_targets, _sms_results, strict=False):
            if isinstance(result, BaseException):
                # PIPEDA: never log exception text here — Twilio errors embed
                # the destination number. Type name only; contact id is fine.
                logger.error(f"SOS SMS failed for contact {contact.get('id')}: {type(result).__name__}")
            elif result.get("success"):
                contacts_notified += 1
            else:
                # send_sms guarantees 'error' is a PII-free "type code=N
                # status=N" string (never str(exception) — see sms_service).
                logger.error(f"SOS SMS failed for contact {contact.get('id')}: {result.get('error')}")

        if contacts:
            logger.info(
                f"SOS: notified {contacts_notified}/{len(contacts)} emergency contacts for user {current_user['id']}"
            )
    except Exception as e:
        logger.error(f"SOS emergency contact notification failed: {e}", exc_info=True)
        return {
            "success": True,
            "incident_id": incident["id"],
            "contacts_notified": 0,
            "notification_warning": "Emergency contacts could not be reached — please call them directly.",
        }

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": contacts_notified,
    }


# ---------------------------------------------------------------------------
# Safety check-in response (Feature D — P3)
# ---------------------------------------------------------------------------


@router.post("/{ride_id}/safety-checkin")
@ride_action_limit
async def safety_checkin_response(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider taps 'I'm okay' on the safety check-in push notification.

    Records the response in Redis so the safety_checkin_loop does not escalate
    this ride to the trust-and-safety team.
    """
    try:
        from ...utils.redis_client import redis_set
    except ImportError:
        from utils.redis_client import redis_set  # type: ignore

    user_id = current_user.get("id")

    # Verify the ride belongs to this rider and is still in_progress.
    ride = await _deps.db_supabase.get_rows("rides", {"id": ride_id, "rider_id": user_id}, limit=1)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride[0].get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Ride is not in progress")

    # 4-hour TTL mirrors the sent/escalated keys in safety_checkin_loop.
    await redis_set(f"safety:checkin:ok:{ride_id}", "1", ttl=4 * 3600)

    logger.info(f"[SAFETY_CHECKIN] Rider {user_id} confirmed OK for ride {ride_id}")
    return {"success": True}
