"""Driver detail and online/available status transitions (catch-all routes).

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, _shared
from ._deps import (  # noqa: F401
    AccountDisabledException,
    Any,
    APIRouter,
    Body,
    Depends,
    Dict,
    ErrorCode,
    ErrorKeys,
    HTTPException,
    Optional,
    RideStatus,
    SpinrException,
    asyncio,
    datetime,
    db_supabase,
    get_current_user,
    logger,
    parse_iso_utc,
    reset_miss_streak,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    serialize_doc,
)

router = APIRouter()


# ─── Catch-all driver ID routes MUST be last to avoid shadowing named routes ───


@router.get("/{driver_id}")
async def get_driver(driver_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    requester_role = current_user.get("role", "")
    is_admin = requester_role in {"admin", "super_admin", "operations", "support"}
    is_self = driver.get("user_id") == current_user["id"]

    if is_admin or is_self:
        return serialize_doc(await _shared._decrypt_driver_pii(driver))

    # Rider with an active ride assigned to this driver gets a safe public projection.
    active_ride = None
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver_id,
                "rider_id": current_user["id"],
                "status": {"$in": list(RideStatus.active_statuses())},
            },
            limit=1,
        )
        active_ride = rides[0] if rides else None
    except Exception:
        logger.error("get_driver: active-ride check failed driver=%s", driver_id, exc_info=True)

    if not active_ride:
        raise HTTPException(status_code=403, detail="Not authorized")

    return {
        "id": driver["id"],
        "name": driver.get("name"),
        "rating": driver.get("rating"),
        "vehicle_make": driver.get("vehicle_make"),
        "vehicle_model": driver.get("vehicle_model"),
        "vehicle_color": driver.get("vehicle_color"),
        "license_plate": driver.get("license_plate"),
    }


# A pending offer normally resolves within ~15s (accept / decline / the
# in-process timeout task). If that task dies with its process (crash,
# restart, DB outage — 2026-07-04), the row stays 'pending' forever and
# every busy-check would park the driver unavailable indefinitely: the
# claim reaper skips "busy" drivers and go-online keeps re-asserting
# unavailable. Anything older than this is an orphan, not a live claim.
STALE_PENDING_OFFER_SECONDS = 90


def _fresh_pending_offers(offers: list | None) -> list:
    """Pending offers young enough to be live claims. Missing/unparseable
    offered_at counts as fresh (never grant availability on ambiguity);
    the schema default (now()) makes that a defensive-only path. Stale rows
    are ignored here and expired by the claim reaper."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_PENDING_OFFER_SECONDS)
    fresh = []
    for offer in offers or []:
        ts = parse_iso_utc(offer.get("offered_at"))
        if ts is None or ts > cutoff:
            fresh.append(offer)
    return fresh


@router.put("/{driver_id}/status")
async def update_driver_status(
    driver_id: str,
    # `embed=True` so FastAPI accepts `{"is_online": true}` as the JSON body
    # instead of interpreting a bare primitive as the whole body. Without the
    # explicit Body() wrapper, FastAPI treats non-Pydantic primitives as
    # query parameters, and the mobile client (which posts it as body) got
    # a 422 and surfaced it as "Request failed".
    is_online: bool = Body(..., embed=True),
    # Optional GPS coords sent by the driver app on Go Online. Closes the
    # window between the status flip and the first /drivers/location-batch
    # POST during which the driver would otherwise sit at the registration
    # default (0, 0) and be invisible to riders/admins.
    lat: Optional[float] = Body(None, embed=True),
    lng: Optional[float] = Body(None, embed=True),
    current_user: dict = Depends(get_current_user),
):
    """Toggle the driver's online flag (driver-facing "Go online" / "Go offline").

    Driver online/available flag contract
    -------------------------------------
    ``is_online``
        Driver-toggled. ``True`` when the driver has tapped "Go online".
        Independent of ride state; remains ``True`` while the driver is on
        an active trip.
    ``is_available``
        System-computed. ``True`` only when (``is_online`` AND not currently
        on an active ride AND not in offer-pending state). Used by dispatch
        to decide who to offer the next ride to.

    Invariant: ``is_available == True`` implies ``is_online == True``. The
    inverse does NOT hold — an online driver mid-trip is ``is_online=True,
    is_available=False``. Dispatch reads ``is_available``; admin filters
    typically read ``is_online``. Never set ``is_available = True`` without
    ``is_online = True``.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Ban check: prevent banned drivers from going online
    if is_online and driver.get("status") == "banned":
        raise AccountDisabledException(
            message="Your account has been permanently suspended due to policy violations.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )
    if is_online and driver.get("status") == "suspended":
        raise AccountDisabledException(
            message="Your account is currently suspended. Please contact support.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )
    if is_online and driver.get("status") == "needs_review":
        raise SpinrException(
            message="Your account is under review. Please wait for admin approval before going online.",
            error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
            status_code=400,
            message_key=ErrorKeys.DRIVER_DOCUMENTS_PENDING,
            action_hint="Wait for verification",
        )
    if is_online and driver.get("status") not in ("active",):
        # Pending, rejected, or any unknown status
        if not driver.get("is_verified", False) and driver.get("status") != "active":
            raise SpinrException(
                message="Your driver profile has not been verified yet. Please wait for admin approval.",
                error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                status_code=400,
                message_key=ErrorKeys.DRIVER_DOCUMENTS_PENDING,
                action_hint="Wait for verification",
            )

    if not is_online:
        # Prevent driver from going offline while actively carrying a rider.
        # The ride remains assigned to this driver regardless of their online
        # flag, but rejecting the toggle avoids a confusing UI state where the
        # driver shows as "offline" yet has an active trip.
        active_ride = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "rides",
                {
                    "driver_id": driver_id,
                    "status": {
                        "$in": [
                            RideStatus.DRIVER_ACCEPTED,
                            RideStatus.DRIVER_ARRIVED,
                            RideStatus.IN_PROGRESS,
                        ]
                    },
                },
                limit=1,
            )
        )
        if active_ride:
            raise HTTPException(
                status_code=409,
                detail="Cannot go offline during an active trip. Please complete the current ride first.",
            )

    if is_online:
        now = datetime.now(timezone.utc)

        # Prefer the dynamic driver_documents collection over the legacy
        # top-level expiry fields on the drivers row. The legacy fields are
        # only written once during onboarding and never refreshed when a
        # driver re-uploads a document, which used to leave drivers stuck
        # offline even after admin re-approval.
        try:
            approved_docs = await db_supabase.get_rows(
                "driver_documents",
                {
                    "driver_id": driver_id,
                    "status": "approved",
                },
                limit=200,
            )
        except Exception:
            approved_docs = []

        def _parse_expiry(val):
            # Return a tz-AWARE UTC datetime (or None). Comparisons below
            # are against `now = datetime.now(timezone.utc)`, which is
            # aware — mixing naive + aware throws TypeError at compare.
            if not val:
                return None
            if isinstance(val, datetime):
                return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            if isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
            return None

        # For each mandatory requirement, the latest approved doc wins. If
        # it has an expiry and that expiry is in the past, block.
        # Requirements come from the driver's service-area required_documents
        # (slug-keyed) — the global document_requirements table is legacy and
        # misses slug-based uploads where requirement_id is NULL.
        mandatory_reqs: list = []
        if driver.get("service_area_id"):
            try:
                area_row = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
                )
                if area_row:
                    mandatory_reqs = [r for r in (area_row.get("required_documents") or []) if r.get("required", True)]
            except Exception:
                mandatory_reqs = []

        def _matches_req(doc: Dict[str, Any], req: Dict[str, Any]) -> bool:
            req_key = (req.get("key") or "").lower()
            req_label = (req.get("label") or "").lower()
            req_id = req.get("id")
            dkey = (doc.get("requirement_key") or "").lower()
            if dkey and dkey == req_key:
                return True
            drid = doc.get("requirement_id")
            if drid and (drid == req_id or (isinstance(drid, str) and drid.lower() == req_key)):
                return True
            dt = (doc.get("document_type") or "").lower()
            if dt and (dt == req_label or dt == req_key.replace("_", " ")):
                return True
            if dt and req_key and req_key.replace("_", "") in dt.replace(" ", "").replace("_", ""):
                return True
            return False

        covered_legacy_fields = set()
        for req_row in mandatory_reqs:
            req_name = req_row.get("label") or req_row.get("key") or "Document"
            # Pick the most recent approved doc for this requirement.
            docs = [d for d in approved_docs if _matches_req(d, req_row)]
            if not docs:
                continue
            docs.sort(key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
            latest = docs[0]
            exp = _parse_expiry(latest.get("expiry_date") or latest.get("expires_at"))
            if exp and exp < now:
                raise SpinrException(
                    message=f"{req_name} has expired. Please update your documents before going online.",
                    error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                    status_code=400,
                    message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                    action_hint="Renew documents in Profile",
                )
            # This requirement was covered by a fresh doc — do not re-check
            # the legacy column for the same thing below.
            nm = (req_name or "").lower()
            if "license" in nm or "driving" in nm or "permit" in nm:
                covered_legacy_fields.add("license_expiry_date")
            if "insurance" in nm:
                covered_legacy_fields.add("insurance_expiry_date")
            if "inspection" in nm:
                covered_legacy_fields.add("vehicle_inspection_expiry_date")
            if "background" in nm:
                covered_legacy_fields.add("background_check_expiry_date")

        # Legacy fallback: only enforce top-level expiry columns that were
        # NOT already satisfied by a fresh approved doc above.
        expiry_checks = [
            ("license_expiry_date", "Driving license"),
            ("insurance_expiry_date", "Vehicle insurance"),
            ("vehicle_inspection_expiry_date", "Vehicle inspection"),
            ("background_check_expiry_date", "Background check"),
        ]
        for field, label in expiry_checks:
            if field in covered_legacy_fields:
                continue
            expiry_val = driver.get(field)
            if expiry_val:
                if isinstance(expiry_val, str):
                    try:
                        expiry_val = datetime.fromisoformat(expiry_val.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                # Treat naive datetimes as UTC — matches _parse_expiry above
                # so both paths produce tz-aware values comparable to `now`.
                if expiry_val.tzinfo is None:
                    expiry_val = expiry_val.replace(tzinfo=timezone.utc)
                if expiry_val < now:
                    raise SpinrException(
                        message=f"{label} has expired ({field}). Please update your documents before going online.",
                        error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                        status_code=400,
                        message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                        action_hint="Renew documents in Profile",
                    )

        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).

        # Check active Spinr Pass subscription.
        # Enforcement triggers when EITHER:
        #   a) the global app setting `require_driver_subscription` is True, OR
        #   b) the driver's service area has `subscription_required=True`
        # When neither is set (default) we skip the DB query entirely — the
        # driver_subscriptions table may not exist yet pre-launch.
        try:
            from ...settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        app_settings = await get_app_settings()
        require_sub = bool(app_settings.get("require_driver_subscription", False))

        if not require_sub and driver.get("service_area_id"):
            _driver_area = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
            if _driver_area and _driver_area.get("subscription_required"):
                require_sub = True
            # Finding F: inherit subscription_required from parent area (airport sub-regions
            # should require the same pass as the parent city area).
            elif _driver_area and _driver_area.get("parent_service_area_id"):
                _parent_area = await db_supabase.find_one(
                    "service_areas", {"id": _driver_area["parent_service_area_id"]}
                )
                if _parent_area and _parent_area.get("subscription_required"):
                    require_sub = True

        if require_sub:
            try:
                sub = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "driver_subscriptions",
                        {
                            "driver_id": driver_id,
                            "status": "active",
                        },
                        limit=1,
                    )
                )
            except Exception as e:
                # The table doesn't exist yet (PGRST205) or the query failed
                # for some other reason. Fail loudly with a clear message so
                # the operator knows the admin toggle was flipped before the
                # subscription infrastructure was ready.
                logger.error(f"driver_subscriptions lookup failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Spinr Pass enforcement is enabled in settings but the "
                        "driver_subscriptions table is not available. Disable "
                        'the "Require Spinr Pass to go online" toggle in admin '
                        "settings, or finish the subscription setup first."
                    ),
                ) from e

            if not sub:
                raise SpinrException(
                    message="You need an active Spinr Pass subscription to go online. Subscribe from your dashboard.",
                    error_code=ErrorCode.PAYMENT_FAILED,
                    status_code=402,
                    message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                    action_hint="Activate Spinr Pass",
                )

            # Check expiry on the active subscription row. parse_iso_utc
            # returns None on malformed values — we let those through rather
            # than blocking a driver from going online because of a data bug.
            if sub.get("expires_at"):
                exp = parse_iso_utc(sub["expires_at"])
                if exp is not None and exp < datetime.now(timezone.utc):
                    await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
                    raise SpinrException(
                        message="Your Spinr Pass has expired. Please renew to go online.",
                        error_code=ErrorCode.PAYMENT_FAILED,
                        status_code=402,
                        message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                        action_hint="Activate Spinr Pass",
                    )

            # Plan scope enforcement: check vehicle_types and service_areas allowlists.
            # A null/empty list means all types/areas allowed.
            if sub.get("plan_id"):
                try:
                    plan = await db_supabase.find_one("subscription_plans", {"id": sub["plan_id"]})
                    allowed_vt = plan.get("vehicle_types") if plan else None
                    if allowed_vt:
                        driver_vt = driver.get("vehicle_type_id")
                        if driver_vt not in allowed_vt:
                            raise SpinrException(
                                message=(
                                    "Your vehicle type is not covered by your active Spinr Pass plan. "
                                    "Please switch to a compatible plan or update your vehicle."
                                ),
                                error_code=ErrorCode.PAYMENT_FAILED,
                                status_code=402,
                                message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                                action_hint="Update Spinr Pass",
                            )
                    # Finding C: service-area scope — a pass for another area must
                    # not satisfy enforcement in this driver's area.
                    # Also accept plans covering the parent area (child areas inherit).
                    allowed_areas = plan.get("service_areas") if plan else None
                    if allowed_areas and driver.get("service_area_id") not in allowed_areas:
                        if driver.get("service_area_id"):
                            _sa_row = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
                            _go_parent_id = (_sa_row or {}).get("parent_service_area_id")
                        else:
                            _go_parent_id = None
                        if not (_go_parent_id and _go_parent_id in allowed_areas):
                            raise SpinrException(
                                message=(
                                    "Your Spinr Pass plan does not cover this service area. "
                                    "Please subscribe to a plan for your area."
                                ),
                                error_code=ErrorCode.PAYMENT_FAILED,
                                status_code=402,
                                message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                                action_hint="Subscribe to Spinr Pass",
                            )
                except SpinrException:
                    raise
                except Exception as e:
                    logger.error(
                        "plan scope check failed for driver=%s plan=%s: %s",
                        driver_id,
                        sub.get("plan_id"),
                        e,
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Could not verify subscription plan restrictions. Please try again.",
                    ) from e

        # Daily ride-allowance gate — applies to ANY driver holding a finite
        # Spinr Pass, not only in pass-required areas. Once the pass's rides for
        # the local calendar day are used up the driver stays offline until the
        # next midnight, so they can't toggle back online and pull more offers.
        # assert_quota_available fetches the active pass itself, is a no-op for
        # unlimited / no-pass / rides-remaining, and fails open so a missing
        # table (pre-launch) never wrongly blocks a driver in a non-gated area.
        try:
            from ...utils.spinr_pass import assert_quota_available
        except ImportError:
            from utils.spinr_pass import assert_quota_available  # type: ignore
        # Anchor the quota day on the driver's service-area timezone (Regina
        # fallback) so the reset matches their local calendar day under DST.
        await assert_quota_available(driver_id, area_id=driver.get("service_area_id"))

    logger.info(
        f"[GO-ONLINE] handler CALL update_one driver_id={driver_id} "
        f"requested_is_online={is_online} "
        f"pre_update_row_is_online={driver.get('is_online')} "
        f"pre_update_row_is_available={driver.get('is_available')}"
    )
    # last_status_changed_at (migration 42) gets bumped only when
    # is_online actually flips — idempotent toggles (same value) shouldn't
    # reset the "online since / offline since" clock the admin UI shows.
    # On PGRST204 (column missing pre-migration) fall back to the legacy
    # payload so the flip itself still lands.
    _now_iso = datetime.now(timezone.utc).isoformat()
    # is_available is system-computed (see docstring): online AND not on an
    # active ride AND not offer-pending. Writing is_available = is_online
    # unconditionally let a mid-trip "Go online" re-tap (or an app-relaunch
    # re-assert) put a busy driver back into the dispatch pool — dispatch
    # candidates filter on is_available alone, so that meant a second
    # concurrent ride offered to a driver who already has one. A DB error in
    # either lookup propagates (503) rather than guessing: dispatch
    # double-booking is worse than one failed toggle.
    is_available = is_online
    if is_online:
        _busy_ride = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver_id,
                "status": {
                    "$in": [
                        RideStatus.DRIVER_ASSIGNED,
                        RideStatus.DRIVER_ACCEPTED,
                        RideStatus.DRIVER_ARRIVED,
                        RideStatus.IN_PROGRESS,
                    ]
                },
            },
            limit=1,
        )
        if _busy_ride:
            is_available = False
        else:
            # Batch dispatch holds no ride row pre-acceptance — the claim
            # lives in ride_offers (claim_driver already set
            # is_available=False; don't re-assert it mid-offer). Only FRESH
            # offers count (see _fresh_pending_offers): an orphaned pending
            # row must not park the driver forever.
            _pending_offers = await db_supabase.get_rows(
                "ride_offers",
                {"driver_id": driver_id, "status": "pending"},
                limit=5,
                columns="id,offered_at",
            )
            if _fresh_pending_offers(_pending_offers):
                is_available = False
    _base = {"is_online": is_online, "is_available": is_available, "updated_at": _now_iso}
    # If the driver app supplied current GPS on Go Online, persist it in the
    # same write so the rider/admin queries see a real location immediately
    # instead of the registration default (0, 0). Guarded on is_online so the
    # Go Offline path doesn't overwrite a perfectly good last-known location.
    if is_online and lat is not None and lng is not None and (lat != 0 or lng != 0):
        _base["lat"] = lat
        _base["lng"] = lng
    # Invariant guardrail: is_available => is_online (see handler docstring).
    # This is a sanity check on the payload we are about to write — never
    # gate user behaviour on the assert; if it ever trips, the bug is in the
    # logic that built _base, not in the driver's request.
    assert not (_base["is_available"] and not _base["is_online"]), "is_available implies is_online"
    status_flipped = bool(driver.get("is_online")) != bool(is_online)
    # Durable intent timestamps (migration 97). Only written on an actual
    # flip — re-tapping Go Online while already online does NOT bump
    # went_online_at; that timestamp is "when this intent began", not "last
    # refresh time". Readers compose these with the Redis presence key to
    # derive effective_online, replacing the retired presence_sweeper.
    _intent_payload = {}
    if status_flipped:
        _intent_payload["last_status_changed_at"] = _now_iso
        if is_online:
            _intent_payload["went_online_at"] = _now_iso
        else:
            _intent_payload["went_offline_at"] = _now_iso
    _payload = {**_base, **_intent_payload}
    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, _payload)
    except Exception as _col_exc:
        # db_supabase.run_sync wraps PostgREST APIErrors in DatabaseError, so
        # str(_col_exc) is the generic "Database operation failed" sentinel —
        # the real column-missing text lives in details['original'] and the
        # __cause__ chain. Inspect both before deciding whether to fall back.
        if not status_flipped:
            raise
        _detail = ""
        _details_attr = getattr(_col_exc, "details", None)
        if isinstance(_details_attr, dict):
            _detail = str(_details_attr.get("original") or "")
        _cause_text = str(getattr(_col_exc, "__cause__", "") or "")
        _combined = f"{_col_exc} {_detail} {_cause_text}".lower()
        _missing_intent = any(
            col in _combined for col in ("last_status_changed_at", "went_online_at", "went_offline_at", "pgrst204")
        )
        if _missing_intent:
            logger.warning(
                f"[GO-ONLINE] intent timestamp column(s) missing; retrying minimal. original={_detail or _col_exc}"
            )
            await db_supabase.update_one("drivers", {"id": driver_id}, _base)
        else:
            raise

    # Verify the update actually landed. db_supabase.update_one silently
    # returns None if the write matched zero rows (RLS deny, schema cache miss,
    # wrong key role, etc.), which would otherwise leak out as a fake
    # {success: true} response and a driver-app that claims "You're online"
    # while the DB row never changes. Re-read the row and raise loudly if the
    # flag did not flip.
    verify = await db_supabase.get_driver_by_id(driver_id)
    logger.info(
        f"[GO-ONLINE] handler VERIFY driver_id={driver_id} "
        f"post_update_is_online={verify.get('is_online') if verify else 'ROW_GONE'} "
        f"post_update_is_available={verify.get('is_available') if verify else 'ROW_GONE'} "
        f"post_update_updated_at={verify.get('updated_at') if verify else 'ROW_GONE'}"
    )
    if verify is None:
        logger.error(f"[go-online] driver row disappeared immediately after update: driver_id={driver_id}")
        raise HTTPException(status_code=500, detail="Driver row missing after status update.")
    if bool(verify.get("is_online")) != bool(is_online):
        logger.error(
            f"[go-online] silent no-op: driver_id={driver_id} "
            f"requested is_online={is_online} but DB still shows "
            f"is_online={verify.get('is_online')}. "
            f"Likely causes: SUPABASE_SERVICE_ROLE_KEY in backend .env is "
            f"the anon key (not service_role), or RLS is enabled on drivers "
            f"with no permissive UPDATE policy for the role in use."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Status update did not apply. Backend Supabase credentials "
                "may be misconfigured — verify SUPABASE_SERVICE_ROLE_KEY."
            ),
        )

    # M-5: SGI insurance period audit — only on actual flips. Idempotent
    # toggles (driver re-asserts the same state) shouldn't open a new
    # period row; the helper's no-op branch would absorb it but we save
    # the round-trip by gating on status_flipped.
    if status_flipped:
        await _deps.record_period_transition(driver_id, 1 if is_online else 0)

    # Presence: Go Online is the strongest possible liveness signal — the
    # driver is actively using the app. Refresh the TTL now so dispatch can
    # see them immediately without waiting for the next WS heartbeat. Go
    # Offline clears the key so admin monitoring and dispatch drop them
    # from the pool without waiting for the 90 s TTL to expire.
    if is_online:
        await _deps.mark_present(driver_id)
        await reset_miss_streak(driver_id)
    else:
        await _deps.clear_presence(driver_id)

    # Post-write claim re-check: the pre-write busy/offer reads and the row
    # write above are not one atomic operation, so dispatch_service
    # claim_driver() firing in that gap (a reconnect re-assert racing a live
    # dispatch tick) gets stomped by our is_available=True — the driver
    # re-enters the pool while already offered/assigned a ride. Re-read both
    # claim sources now that the write landed and, if a claim slipped in,
    # downgrade using claim_driver's own conditional shape so this repair can
    # never stomp a newer state. Residual exposure: a claim whose ride/offer
    # row is not yet visible when these reads run (single round-trip window);
    # closing that fully requires the claim marker to land before the
    # availability flag, which is a dispatch-path redesign, not fixable here.
    if _base["is_available"]:
        _busy_recheck, _offers_recheck = await asyncio.gather(
            db_supabase.get_rows(
                "rides",
                {
                    "driver_id": driver_id,
                    "status": {
                        "$in": [
                            RideStatus.DRIVER_ASSIGNED,
                            RideStatus.DRIVER_ACCEPTED,
                            RideStatus.DRIVER_ARRIVED,
                            RideStatus.IN_PROGRESS,
                        ]
                    },
                },
                limit=1,
            ),
            db_supabase.get_rows(
                "ride_offers",
                {"driver_id": driver_id, "status": "pending"},
                limit=5,
                columns="id,offered_at",
            ),
        )
        if _busy_recheck or _fresh_pending_offers(_offers_recheck):
            await db_supabase.update_one(
                "drivers",
                {"id": driver_id, "is_available": True},
                {"$set": {"is_available": False}},
            )
            logger.info(
                f"[GO-ONLINE] repair: dispatch claim landed during status write; "
                f"is_available downgraded for driver_id={driver_id}"
            )

    return {"success": True, "is_online": is_online}
