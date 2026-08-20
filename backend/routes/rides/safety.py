"""In-ride emergency (SOS) and safety check-in endpoints.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

import re

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    Field,
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
    # One key per SOS *press*, reused across that press's retry attempts.
    # Optional: older clients omit it and keep the previous insert-always
    # behavior.
    #
    # Deliberately NOT constrained with min_length/pattern. This is optional
    # metadata on an emergency endpoint: a strict constraint turns a malformed
    # key into a 422 that rejects the whole SOS, identically on all three
    # retries, and a client bug would then silently disable a rider's panic
    # button. Fail open instead -- validate in the handler and drop a bad key
    # (losing dedup, keeping the alert). max_length is kept only as a cheap
    # bound on payload size.
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


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
    # Idempotency (migration 315). The client retries this call on any failure
    # because an SOS must survive a flaky connection -- but a request that
    # landed and whose response was lost is indistinguishable, client-side,
    # from one that never arrived. Without this guard every such retry created
    # another incident row AND re-sent the "URGENT" SMS to the reporter's
    # emergency contacts. Mirrors the claim_stripe_event() posture on the
    # payments side.
    #
    # Checked BEFORE the insert and before any notification fires, so a replay
    # returns the original incident having triggered zero new side effects.
    # Scoped to (reporter, key): two people on the same ride must both be able
    # to raise an alarm. Best-effort at this layer -- concurrent replicas can
    # both miss here, which is why migration 315 also carries a UNIQUE index.
    # Validated here rather than by pydantic so a malformed key degrades to
    # "no dedup" instead of 422-ing the emergency (see EmergencyRequest).
    _idem_key: Optional[str] = None
    if body.idempotency_key:
        _candidate = body.idempotency_key.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", _candidate):
            _idem_key = _candidate
        else:
            logger.warning(
                f"[SOS] Ignoring malformed idempotency_key for ride {ride_id} "
                f"user {current_user['id']} -- proceeding without deduplication"
            )

    if _idem_key:
        try:
            _prior = await _deps.db_supabase.get_rows(
                "safety_incidents",
                {
                    "reported_by_user_id": current_user["id"],
                    "sos_idempotency_key": _idem_key,
                },
                limit=1,
            )
        except Exception as exc:
            # Never let the dedup lookup block the alert: a failed read must
            # fall through to the normal path (at worst a duplicate incident,
            # which is strictly better than a dropped SOS).
            logger.error(
                f"[SOS] Idempotency lookup failed ride_id={ride_id} user_id={current_user['id']}: {exc}",
                exc_info=True,
            )
            _prior = None
        if _prior:
            _existing = _prior[0]
            logger.info(
                f"[SOS] Duplicate suppressed for ride {ride_id} user {current_user['id']} "
                f"-- returning incident {_existing.get('id')}"
            )
            return {
                "success": True,
                "incident_id": _existing.get("id"),
                # Replayed, not re-sent. Report what the ORIGINAL call achieved
                # rather than fabricating a fresh count -- the per-contact
                # outcome of that first send is not re-derivable here, so the
                # client is told this was a duplicate and should keep showing
                # the result it already has.
                "duplicate": True,
                "contacts_notified": 0,
                "contacts": [],
            }

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
    if _idem_key:
        incident["sos_idempotency_key"] = _idem_key

    try:
        await _deps.db_supabase.insert_one("safety_incidents", incident)
    except Exception as exc:
        # Two recoverable failures are possible once an idempotency key is in
        # play, and neither may be allowed to surface as "alert not sent".
        _recovered_id = None
        if _idem_key:
            # (1) UNIQUE-index collision (23505). A concurrent request with the
            # same key -- a client retry firing while the first is still in
            # flight -- already recorded this press. The alert HAS gone out;
            # returning 503 here would tell the user their SOS failed while
            # their contacts were being texted. Re-read and return the original.
            try:
                _raced = await _deps.db_supabase.get_rows(
                    "safety_incidents",
                    {
                        "reported_by_user_id": current_user["id"],
                        "sos_idempotency_key": _idem_key,
                    },
                    limit=1,
                )
            except Exception:  # pragma: no cover - best effort
                _raced = None
            if _raced:
                logger.info(
                    f"[SOS] Insert raced a concurrent duplicate for ride {ride_id} "
                    f"user {current_user['id']} -- returning incident {_raced[0].get('id')}"
                )
                return {
                    "success": True,
                    "incident_id": _raced[0].get("id"),
                    "duplicate": True,
                    "contacts_notified": 0,
                    "contacts": [],
                }

            # (2) Column does not exist yet. If this build reaches production
            # before migration 315 is applied, PostgREST rejects the whole
            # payload for referencing an unknown column -- which would break
            # SOS outright for every key-sending client, on every retry.
            # Migration 313's header documents this repo being bitten by
            # exactly that failure mode before. Retry once without the key:
            # we lose deduplication (pre-315 behavior) and keep the alert.
            incident.pop("sos_idempotency_key", None)
            try:
                await _deps.db_supabase.insert_one("safety_incidents", incident)
                _recovered_id = incident["id"]
                logger.error(
                    f"[SOS] Insert with sos_idempotency_key failed for ride {ride_id}; "
                    f"succeeded without it. Migration 315 is likely not applied on this "
                    f"database -- dedup is INACTIVE. Original error: {exc}",
                    exc_info=True,
                )
            except Exception:  # pragma: no cover - fall through to the 503 below
                _recovered_id = None

        if _recovered_id is None:
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
    _notified_contact_ids: set = set()
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
                _notified_contact_ids.add(contact.get("id"))
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
            "contacts": [],
            "notification_warning": "Emergency contacts could not be reached — please call them directly.",
        }

    # Per-contact status (B16): the driver-app Safety overlay's "✓ Notified"
    # list needs to know which specific contacts were reached, not just the
    # aggregate count above (kept unchanged for backward compatibility with
    # existing callers/tests). Built from data the loop above already
    # computed -- no extra DB/SMS work, purely additive.
    contacts_status = [
        {"id": c.get("id"), "name": c.get("name", ""), "notified": c.get("id") in _notified_contact_ids}
        for c in contacts
    ]

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": contacts_notified,
        "contacts": contacts_status,
    }


# ---------------------------------------------------------------------------
# Ride-less emergency (ACTION_ITEMS.md B15(c))
# ---------------------------------------------------------------------------
#
# trigger_emergency above requires an active ride and 404s without one, so a
# rider who feels unsafe before booking or after drop-off has had no in-app
# SOS path -- only a client-side prompt to call 911 directly. This is a new
# sibling endpoint, not a refactor of trigger_emergency: it duplicates that
# function's side-effect bundle (safety_incidents insert with ride_id=None,
# admin WS broadcast, notify_safety_team, page_sos_on_call, confirmation
# push, emergency-contact SMS) rather than sharing a helper, so the in-ride
# SOS path's diff for this whole piece of work is zero and needs no
# re-verification. See agents/runs/sos-rideless-path/decisions.md for the
# full reasoning and the accepted duplication tradeoff.
#
# Dark-launched behind AppSettings.rideless_sos_enabled (migration 350,
# default false). Checked here, server-side, so the endpoint 404s when the
# flag is off even if a client somehow calls it anyway -- fail-closed
# defense in depth, not just "the client won't call it".
#
# IMPORTANT: the SMS/push copy below is a DRAFT. Per decisions.md, exact
# wording sent to emergency contacts for a ride-less alert is a required
# Product + Trust & Safety sign-off gate -- it must not reuse trigger_emergency's
# "...during a Spinr ride" phrasing (false for this path) and must not claim
# anything the current infrastructure doesn't back up (domain-safety.md).
# This flag must not be enabled in ANY environment, dark-launch included,
# until that sign-off happens.


class RidelessEmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Same idempotency contract as EmergencyRequest -- see that model's
    # docstring for why this is deliberately unconstrained by pydantic.
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


@router.post("/emergency")
@ride_action_limit
async def trigger_emergency_rideless(
    body: RidelessEmergencyRequest,
    request: Request = None,
    # Same rationale as trigger_emergency: SOS is never gated behind an auth
    # refresh. There is no ride to check membership against here, so the
    # caller's identity IS the authorization -- current_user is the only
    # input that decides who this alert is filed for.
    current_user: dict = Depends(get_current_user_allow_expired),
):
    """Trigger a ride-less emergency alert (no active ride required).

    Mirrors trigger_emergency's side-effect bundle with ride_id=None. See
    the module-level comment above for why this is a separate function
    rather than a shared refactor, and for the required sign-off before the
    rideless_sos_enabled flag may be turned on anywhere.
    """
    app_settings = await _deps.get_app_settings()
    if not (app_settings or {}).get("rideless_sos_enabled", False):
        # Fail closed: 404 rather than 403, matching the precedent in
        # schemas.py's legacy-consent dark-launch gate comment ("POST /accept
        # 404s" when its flag is off) -- an unlaunched endpoint should look
        # like it doesn't exist, not like a permission the caller lacks.
        raise HTTPException(status_code=404, detail="Not found")

    is_rider = not current_user.get("is_driver", False)

    # Idempotency -- identical logic to trigger_emergency, reusing the same
    # migration-315 UNIQUE index, which has no ride_id component
    # ((reported_by_user_id, sos_idempotency_key) WHERE sos_idempotency_key
    # IS NOT NULL) so it already works correctly for ride_id=NULL rows.
    _idem_key: Optional[str] = None
    if body.idempotency_key:
        _candidate = body.idempotency_key.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", _candidate):
            _idem_key = _candidate
        else:
            logger.warning(
                f"[SOS-rideless] Ignoring malformed idempotency_key for user "
                f"{current_user['id']} -- proceeding without deduplication"
            )

    if _idem_key:
        try:
            _prior = await _deps.db_supabase.get_rows(
                "safety_incidents",
                {
                    "reported_by_user_id": current_user["id"],
                    "sos_idempotency_key": _idem_key,
                },
                limit=1,
            )
        except Exception as exc:
            logger.error(
                f"[SOS-rideless] Idempotency lookup failed user_id={current_user['id']}: {exc}",
                exc_info=True,
            )
            _prior = None
        if _prior:
            _existing = _prior[0]
            logger.info(
                f"[SOS-rideless] Duplicate suppressed for user {current_user['id']} "
                f"-- returning incident {_existing.get('id')}"
            )
            return {
                "success": True,
                "incident_id": _existing.get("id"),
                "duplicate": True,
                "contacts_notified": 0,
                "contacts": [],
            }

    now_iso = datetime.now(timezone.utc).isoformat()
    incident = {
        "id": str(uuid.uuid4()),
        "ride_id": None,
        "reported_by_user_id": current_user["id"],
        "role": "rider" if is_rider else "driver",
        "category": "sos_button_rideless",
        "description": body.message or "Emergency assistance requested",
        "status": "open",
        "latitude": body.latitude,
        "longitude": body.longitude,
        "reported_at": now_iso,
        "created_at": now_iso,
    }
    if _idem_key:
        incident["sos_idempotency_key"] = _idem_key

    try:
        await _deps.db_supabase.insert_one("safety_incidents", incident)
    except Exception as exc:
        _recovered_id = None
        if _idem_key:
            try:
                _raced = await _deps.db_supabase.get_rows(
                    "safety_incidents",
                    {
                        "reported_by_user_id": current_user["id"],
                        "sos_idempotency_key": _idem_key,
                    },
                    limit=1,
                )
            except Exception:  # pragma: no cover - best effort
                _raced = None
            if _raced:
                logger.info(
                    f"[SOS-rideless] Insert raced a concurrent duplicate for user "
                    f"{current_user['id']} -- returning incident {_raced[0].get('id')}"
                )
                return {
                    "success": True,
                    "incident_id": _raced[0].get("id"),
                    "duplicate": True,
                    "contacts_notified": 0,
                    "contacts": [],
                }

            incident.pop("sos_idempotency_key", None)
            try:
                await _deps.db_supabase.insert_one("safety_incidents", incident)
                _recovered_id = incident["id"]
                logger.error(
                    f"[SOS-rideless] Insert with sos_idempotency_key failed for user "
                    f"{current_user['id']}; succeeded without it. Migration 315 is "
                    f"likely not applied on this database -- dedup is INACTIVE. "
                    f"Original error: {exc}",
                    exc_info=True,
                )
            except Exception:  # pragma: no cover - fall through to the 503 below
                _recovered_id = None

        if _recovered_id is None:
            logger.error(
                f"[SOS-rideless] Failed to persist emergency incident user_id={current_user['id']}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=503, detail="Unable to send emergency alert. Please try again or call 911."
            ) from exc

    try:
        await _deps.manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.error(f"emergency_alert admin broadcast failed (rideless): {_exc}", exc_info=True)
    logger.critical(f"RIDELESS EMERGENCY ALERT TRIGGERED by user {current_user['id']}")

    try:
        await notify_safety_team(incident)
    except Exception:  # pragma: no cover — best effort, never block the SMS path below
        logger.error(
            f"notify_safety_team failed for rideless SOS incident={incident['id']}",
            exc_info=True,
        )

    try:
        await page_sos_on_call(incident)
    except Exception:  # pragma: no cover — best effort, never block the SMS path below
        logger.error(
            f"page_sos_on_call failed for rideless SOS incident={incident['id']}",
            exc_info=True,
        )

    # Confirmation push. Copy is identical to trigger_emergency's -- it never
    # claimed anything ride-specific to begin with ("Your emergency alert
    # reached our safety team and emergency contacts. If you're in immediate
    # danger, call 911."), so it's equally true here.
    try:
        _deps.spawn(
            _deps.send_push_notification(
                current_user["id"],
                "SOS Alert Received",
                "Your emergency alert reached our safety team and emergency contacts. "
                "If you're in immediate danger, call 911.",
                data={"type": "sos_confirmation", "ride_id": "", "incident_id": incident["id"]},
                priority="safety",
                target_app="rider" if is_rider else "driver",
            )
        )
    except Exception:  # pragma: no cover - best effort, never block the SMS path below
        logger.error(
            f"SOS confirmation push failed to spawn (rideless) for incident={incident['id']}",
            exc_info=True,
        )

    # Notify emergency contacts via SMS. Copy corrected from trigger_emergency's
    # "...during a Spinr ride" phrasing, which would be false here -- see the
    # module-level DRAFT-copy note above.
    contacts_notified = 0
    _notified_contact_ids: set = set()
    try:
        sms_settings = app_settings
        contacts_rows = await _deps.db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=5)
        contacts = list(contacts_rows) if contacts_rows else []

        user = await _deps.db_supabase.get_user_by_id(current_user["id"])
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "A Spinr user"

        location_text = " Location shared with emergency services." if body.latitude and body.longitude else ""
        sms_body = (
            f"URGENT: {user_name} triggered an emergency alert via the Spinr app."
            f"{location_text} Call them or emergency services immediately."
        )

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
                logger.error(f"SOS SMS failed for contact {contact.get('id')} (rideless): {type(result).__name__}")
            elif result.get("success"):
                contacts_notified += 1
                _notified_contact_ids.add(contact.get("id"))
            else:
                logger.error(f"SOS SMS failed for contact {contact.get('id')} (rideless): {result.get('error')}")

        if contacts:
            logger.info(
                f"SOS-rideless: notified {contacts_notified}/{len(contacts)} emergency contacts "
                f"for user {current_user['id']}"
            )
    except Exception as e:
        logger.error(f"SOS-rideless emergency contact notification failed: {e}", exc_info=True)
        return {
            "success": True,
            "incident_id": incident["id"],
            "contacts_notified": 0,
            "contacts": [],
            "notification_warning": "Emergency contacts could not be reached — please call them directly.",
        }

    contacts_status = [
        {"id": c.get("id"), "name": c.get("name", ""), "notified": c.get("id") in _notified_contact_ids}
        for c in contacts
    ]

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": contacts_notified,
        "contacts": contacts_status,
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
