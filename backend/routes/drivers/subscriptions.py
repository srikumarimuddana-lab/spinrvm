"""Driver subscription plans, Stripe checkout, invoices, expiry loop.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    APIRouter,
    Decimal,
    Depends,
    HTTPException,
    Query,
    Request,
    RideStatus,
    asyncio,
    datetime,
    db_supabase,
    dollars_to_cents,
    get_current_user,
    logger,
    os,
    socket,
    stripe,
    timedelta,
    timezone,
    uuid,
)

router = APIRouter()


# ============================================================
# Spinr Pass — Driver Subscription
# ============================================================


@router.get("/subscription/plans")
async def get_subscription_plans(current_user: dict = Depends(get_current_user)):
    """Get available subscription plans for the driver's service area.

    Respects the per-area kill switch: if the driver's service area has
    spinr_pass_enabled=false, returns an empty list so the driver never
    sees subscription options.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    # Check the area-level kill switch — when Spinr Pass is disabled for
    # the driver's area, return a friendly free-ride message instead of plans.
    if driver and driver.get("service_area_id"):
        area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )
        if area and area.get("spinr_pass_enabled") is False:
            return {
                "plans": [],
                "free_mode": True,
                "message": "No subscription needed — you're riding free right now! Drive on and enjoy the open road.",
            }

    plans = await db_supabase.get_rows("subscription_plans", {"is_active": True}, limit=50)

    # Filter by driver's service area if plans have area restrictions
    if driver:
        driver_area = driver.get("service_area_id")
        filtered = []
        for p in plans:
            plan_areas = p.get("service_areas")
            if plan_areas is None or (driver_area and driver_area in plan_areas):
                filtered.append(p)
            elif not plan_areas:  # empty list = all areas
                filtered.append(p)
        plans = filtered

    # Filter by driver's vehicle type — only show plans that cover this vehicle.
    # A null/empty vehicle_types list means the plan accepts all types.
    if driver:
        driver_vt = driver.get("vehicle_type_id")
        plans = [p for p in plans if not p.get("vehicle_types") or driver_vt in p["vehicle_types"]]

    return {"plans": plans, "free_mode": False, "message": None}


@router.get("/subscription/current")
async def get_current_subscription(current_user: dict = Depends(get_current_user)):
    """Get driver's active subscription."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        return {"has_subscription": False, "subscription": None}

    sub = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "driver_subscriptions",
            {
                "driver_id": driver["id"],
                "status": "active",
            },
            limit=1,
        )
    )

    if not sub:
        return {"has_subscription": False, "subscription": None}

    # Check if expired. parse_iso_utc always returns a UTC-aware datetime (or
    # None if unparseable), so the comparison below is aware-vs-aware. The old
    # code stripped tzinfo off `exp` and then compared it against an aware
    # `datetime.now(timezone.utc)`, which raised TypeError for EVERY pass (the
    # Postgres timestamptz always carries an offset). That exception was caught
    # and logged as a warning, so this whole expired-flip branch was dead code
    # and a lapsed-but-still-`active` row displayed as active with a quota.
    if sub.get("expires_at"):
        try:
            from ...utils.datetime_utils import parse_iso_utc  # type: ignore
        except ImportError:
            from utils.datetime_utils import parse_iso_utc  # type: ignore

        exp = parse_iso_utc(sub["expires_at"])
        if exp is None:
            # Unparseable expiry is a data-integrity problem, not something to
            # paper over by showing the pass as active — surface it loudly. The
            # go-online gate and the expiry sweeper still enforce independently.
            logger.error(
                "get_current_subscription: unparseable expires_at on active pass",
                extra={"subscription_id": sub.get("id")},
            )
            raise HTTPException(status_code=503, detail="Subscription state unavailable")
        if exp < datetime.now(timezone.utc):
            await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
            return {
                "has_subscription": False,
                "subscription": None,
                "expired": True,
            }

    # Quota state for the current calendar day (America/Regina). The same
    # window powers go-online / dispatch / accept enforcement, so the numbers
    # the driver sees here match the ones enforced on the rides.
    try:
        from ...utils.spinr_pass import (
            _pass_is_hourly,
            area_timezone,
            completed_today,
            compute_quota,
            hours_until,
            quota_window_for_sub,
        )
    except ImportError:
        from utils.spinr_pass import (  # type: ignore
            _pass_is_hourly,
            area_timezone,
            completed_today,
            compute_quota,
            hours_until,
            quota_window_for_sub,
        )

    # Same service-area timezone the enforcement gates use, so the countdown the
    # driver sees matches when their rides actually reset. This is display only,
    # so a transient lookup error degrades to the Regina default rather than 500
    # the screen (enforcement, which must be exact, lets the error propagate).
    try:
        _tz = await area_timezone(driver.get("service_area_id"))
    except Exception:
        logger.warning("get_current_subscription: area timezone lookup failed; using default", exc_info=True)
        _tz = None
    # 1-day pass → count across its own 24h window; longer pass → calendar day.
    # Read the same window for the count and the countdown so the numbers shown
    # here match exactly what's enforced on go-online / accept.
    _window = quota_window_for_sub(sub, tz=_tz)
    _hourly = _pass_is_hourly(sub)
    today_rides = await completed_today(driver["id"], tz=_tz, window_start=_window[0])
    quota = compute_quota(sub.get("rides_per_day", -1), today_rides, tz=_tz, window=_window, hourly=_hourly)

    return {
        "has_subscription": True,
        "subscription": sub,
        "today_rides": today_rides,
        "rides_remaining": quota["rides_remaining"],
        "can_accept_rides": quota["can_accept_rides"],
        # Multi-day pass: refills at the next local midnight. 1-day pass: this
        # is the pass's own expiry (the allowance never refills mid-pass).
        "quota_resets_at": quota["quota_resets_at"],
        "hours_until_reset": quota["hours_until_reset"],
        # True for a 1-day pass so the app can word "ends in Xh" not "resets".
        "hourly_pass": quota["hourly"],
        # Pass itself ends (must renew) at expires_at.
        "hours_until_expiry": hours_until(sub.get("expires_at")),
    }


async def _cancel_stripe_subscription(stripe_subscription_id: str | None, *, raise_on_error: bool = False) -> None:
    """Cancel a Stripe Subscription so a recurring plan stops billing.

    No-op when there's no Stripe subscription (one-off / dev-mode rows) or
    Stripe isn't configured.

    ``raise_on_error`` controls failure handling:
      - False (default): best-effort. Used on plan-switch activation, where a
        paid replacement already exists and the reconcile loop /
        customer.subscription.* webhooks backstop a lingering old subscription;
        a failure must not block activation.
      - True: re-raise on failure. Used by the driver-initiated cancel so the
        caller can refuse to mark the row cancelled — otherwise the app would
        show "cancelled" while Stripe keeps billing (and a later invoice.paid
        would silently re-activate the row).
    """
    if not stripe_subscription_id:
        return
    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        # There IS a Stripe subscription to cancel (guarded above) but no key —
        # e.g. key removed mid-rotation. With raise_on_error the caller must NOT
        # mark the row cancelled while Stripe keeps billing, so fail loudly.
        if raise_on_error:
            raise RuntimeError("stripe_secret_key not configured — cannot cancel Stripe subscription")
        return
    try:
        await asyncio.to_thread(lambda: stripe.Subscription.delete(stripe_subscription_id, api_key=stripe_secret))
        logger.info(f"[SUBSCRIBE] Stripe subscription {stripe_subscription_id} cancelled")
    except Exception:
        logger.exception(f"[SUBSCRIBE] Failed to cancel Stripe subscription {stripe_subscription_id}")
        if raise_on_error:
            raise


async def _compute_subscription_tax(driver_id: str, plan_price: Decimal) -> dict:
    """Return pre-tax subtotal plus per-component tax amounts for a subscription charge.

    Reads the driver's service area's subscription_tax_config (migration 185).
    Raises on DB error — callers at checkout time must propagate the failure
    so we never issue a Stripe session with silently-zeroed tax.
    """
    _ZERO = Decimal("0")
    _q2 = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
    subtotal = _q2(plan_price)
    gst = pst = hst = _ZERO
    province = "SK"
    _drv = await db_supabase.find_one("drivers", {"id": driver_id})
    _area_id = (_drv or {}).get("service_area_id")
    if _area_id:
        _area = await db_supabase.find_one("service_areas", {"id": _area_id})
        _cfg = (_area or {}).get("subscription_tax_config") or {}
        if _cfg.get("enabled", True):
            province = str(_cfg.get("province", "SK") or "SK")
            gst = _q2(subtotal * Decimal(str(_cfg.get("gst_rate", 5) or 0)) / Decimal("100"))
            pst = _q2(subtotal * Decimal(str(_cfg.get("pst_rate", 6) or 0)) / Decimal("100"))
            hst = _q2(subtotal * Decimal(str(_cfg.get("hst_rate", 0) or 0)) / Decimal("100"))
    tax_total = gst + pst + hst
    return {
        "subtotal": subtotal,
        "gst_amount": gst,
        "pst_amount": pst,
        "hst_amount": hst,
        "tax_total": tax_total,
        "total": subtotal + tax_total,
        "province": province,
    }


@router.get("/subscription/checkout-return")
async def subscription_checkout_return(session_id: str = "", to: str = ""):
    """PUBLIC https bounce for the Stripe Checkout return → driver app.

    Stripe Checkout requires an https success/cancel URL (it rejects custom app
    schemes), but the driver app returns via a custom scheme that
    WebBrowser.openAuthSessionAsync intercepts. Stripe redirects here over https;
    this page immediately redirects the in-app browser to the app's custom scheme
    (e.g. spinr-driver://subscription/success?session_id=...), handing control
    back to the app. No auth — the OS browser opening this carries no JWT, and it
    exposes nothing sensitive (the session_id is already the user's own).

    `to` is strictly allowlisted to our app schemes so this can never be abused
    as an open redirect.

    Returns a server-side **302** to the app scheme, NOT an HTML meta/JS redirect.
    WebBrowser.openAuthSessionAsync (ASWebAuthenticationSession on iOS, Chrome
    Custom Tab on Android) reliably intercepts a server redirect to the callback
    scheme; a JS/meta redirect to a custom scheme is BLOCKED by Chrome Custom Tabs
    without a user gesture, which left the driver stranded in the browser after
    paying (they had to tap the manual fallback link). See the openAuthSessionAsync
    call in driver-app/app/driver/subscription.tsx.
    """
    import re as _re

    from fastapi.responses import RedirectResponse

    _allowed_prefixes = ("spinr-driver://", "exp://")
    _safe = bool(
        to and any(to.startswith(p) for p in _allowed_prefixes) and _re.fullmatch(r"[A-Za-z0-9:/._%@!$&()*+,;=~-]+", to)
    )
    _target = to if _safe else "spinr-driver://subscription/success"
    _sid = session_id if _re.fullmatch(r"[A-Za-z0-9_]+", session_id or "") else ""
    _sep = "&" if "?" in _target else "?"
    _dest = f"{_target}{_sep}session_id={_sid}" if _sid else _target
    # Observability: confirms Stripe actually reached the bounce (if this never
    # logs after a payment, the problem is upstream — PUBLIC_API_BASE_URL / Stripe
    # can't reach this host). No PII: scheme + flags only, not the session id.
    logger.info(
        "subscription checkout-return bounce hit -> 302",
        extra={
            "scheme": _target.split("://", 1)[0],
            "allowlisted_to": _safe,
            "has_session_id": bool(_sid),
        },
    )
    return RedirectResponse(url=_dest, status_code=302)


@router.post("/subscription/subscribe")
async def subscribe_to_plan(request: Request, current_user: dict = Depends(get_current_user)):
    """Subscribe driver to a plan.

    **With Stripe configured** (`stripe_secret_key` in app_settings):
    creates a Stripe Checkout Session and returns `{checkout_url}`.
    The driver-app opens this URL in the browser; after payment,
    Stripe redirects back via the app's deep-link scheme and the
    webhook activates the subscription.

    **Without Stripe** (dev/internal testing): creates and activates
    the subscription immediately with `payment_status: "paid"`, same
    as the pre-checkout behavior. This preserves the internal test
    flow where no real payment is needed.
    """
    data = await request.json()
    plan_id = data.get("plan_id")

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Block subscription if Spinr Pass is disabled for this area
    if driver.get("service_area_id"):
        area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )
        if area and area.get("spinr_pass_enabled") is False:
            raise HTTPException(
                status_code=403,
                detail="Spinr Pass is not available in your service area",
            )

    plan = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("subscription_plans", {"id": plan_id, "is_active": True}, limit=1)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    # Reject checkout before Stripe if this plan's vehicle_types don't cover
    # the driver. Mirrors the go-online gate so a driver can't subscribe to an
    # incompatible plan and then be silently blocked at go-online time.
    allowed_vt = plan.get("vehicle_types")
    if allowed_vt:
        driver_vt = driver.get("vehicle_type_id")
        if driver_vt not in allowed_vt:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Your vehicle type is not compatible with this plan. "
                    "Please choose a plan that supports your vehicle."
                ),
            )

    # Reject checkout if this plan's service_areas don't cover the driver's area.
    # Also accepts plans covering the parent area (child areas inherit parent scope).
    allowed_plan_areas = plan.get("service_areas")
    if allowed_plan_areas and driver.get("service_area_id") not in allowed_plan_areas:
        if driver.get("service_area_id"):
            _checkout_area = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
            _checkout_parent_id = (_checkout_area or {}).get("parent_service_area_id")
        else:
            _checkout_parent_id = None
        if not (_checkout_parent_id and _checkout_parent_id in allowed_plan_areas):
            raise HTTPException(
                status_code=422,
                detail=(
                    "This plan is not available for your service area. Please choose a plan that covers your area."
                ),
            )

    # Check for existing active subscription
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "driver_subscriptions",
            {
                "driver_id": driver["id"],
                "status": "active",
            },
            limit=1,
        )
    )
    if existing:
        # Do NOT cancel `existing` here. For the paid Checkout path the driver
        # must keep their current pass until the new payment is confirmed —
        # _activate_subscription (called by the checkout webhook / verify-session)
        # cancels the prior active subscription and bumps the subscriber count.
        # Dev/immediate-activation mode handles `existing` at the end of this fn.
        pass

    now = datetime.now(timezone.utc)
    # Anchor expiry to the driver's location timezone: a multi-day pass ends at
    # local midnight on its Nth day; a 1-day pass is 24h by the hour.
    try:
        from ...utils.spinr_pass import area_timezone, compute_pass_expiry
    except ImportError:
        from utils.spinr_pass import area_timezone, compute_pass_expiry  # type: ignore
    try:
        _sub_tz = await area_timezone(driver.get("service_area_id"))
    except Exception:
        # Payment-path DB read — surface at error (we still fall back to Regina
        # so the purchase isn't blocked, but the failure must not be hidden).
        logger.error("[SUBSCRIBE] area timezone lookup failed; using default for expiry", exc_info=True)
        _sub_tz = None
    expires = compute_pass_expiry(plan.get("duration_days", 30), now=now, tz=_sub_tz)
    plan_price = Decimal(str(plan.get("price", 0) or 0))
    subscription_id = str(uuid.uuid4())

    # Try Stripe Checkout if configured; otherwise dev-mode instant activation.
    checkout_url = None
    stripe_session_id = None
    payment_status = "pending"

    # Dual-import (package vs top-level run modes) — must be OUTSIDE the broad
    # Stripe try below, else an ImportError in top-level mode is swallowed as a
    # 502 before we can reach the no-Stripe/free-plan activation path.
    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings

    try:
        _settings = await get_app_settings()
        _stripe_secret = _settings.get("stripe_secret_key", "")
        _price_id = plan.get("stripe_price_id")
        _metadata = {
            "driver_id": driver["id"],
            "subscription_id": subscription_id,
            "plan_id": plan_id,
            # scope lets utils/stripe_reconcile.py recognise a subscription
            # PaymentIntent and not flag it as a STRIPE_ORPHAN ride.
            "scope": "driver_subscription",
        }
        # The client supplies its app-scheme return URL (spinr-driver:// in
        # production builds, exp://... in Expo Go) that the in-app browser
        # (openAuthSessionAsync) intercepts. Stripe Checkout, however, REQUIRES an
        # https success/cancel URL and rejects custom app schemes — so we point
        # Stripe at our https /subscription/checkout-return bounce, which then
        # redirects the in-app browser back to the app scheme. This needs no
        # AASA/assetlinks (the final hop is the custom scheme, handled by
        # openAuthSessionAsync), only a valid https URL for Stripe.
        _RETURN_PREFIXES = ("spinr-driver://", "exp://")
        _client_return: str = data.get("success_url", "")
        if _client_return and any(_client_return.startswith(p) for p in _RETURN_PREFIXES):
            _app_success = _client_return
        else:
            _app_success = "spinr-driver://subscription/success"
        _app_cancel = _app_success.replace("/success", "/cancel")

        try:
            from ...core.config import settings as _config
        except ImportError:
            from core.config import settings as _config  # type: ignore
        from urllib.parse import quote as _quote

        _bounce = f"{_config.PUBLIC_API_BASE_URL.rstrip('/')}/api/v1/drivers/subscription/checkout-return"
        # {CHECKOUT_SESSION_ID} is a Stripe template placeholder substituted by
        # Stripe at redirect time (success only).
        _success_url = f"{_bounce}?to={_quote(_app_success, safe='')}&session_id={{CHECKOUT_SESSION_ID}}"
        _cancel_url = f"{_bounce}?to={_quote(_app_cancel, safe='')}"

        if _stripe_secret and _price_id:
            # Guard against a Stripe Price that disagrees with the DB price the
            # driver was shown (admin edited plan.price after attaching a Price,
            # or pasted the wrong Price id). Retrieve it and refuse on an
            # amount/currency mismatch so we never bill a surprise amount.
            try:
                _price_obj = stripe.Price.retrieve(_price_id, api_key=_stripe_secret)
            except Exception as _price_err:
                logger.exception(f"[SUBSCRIBE] Could not retrieve Stripe Price {_price_id} for plan {plan_id}")
                raise HTTPException(
                    status_code=502,
                    detail="Payment service unavailable. Please try again later.",
                ) from _price_err
            _expected_cents = dollars_to_cents(plan_price)
            _price_cents = _price_obj.get("unit_amount")
            _price_currency = (_price_obj.get("currency") or "").lower()
            if _price_cents != _expected_cents or _price_currency != "cad":
                logger.error(
                    "[SUBSCRIBE] Stripe Price %s mismatch for plan %s: stripe=%s/%s plan=%s/cad — refusing to charge",
                    _price_id,
                    plan_id,
                    _price_cents,
                    _price_currency,
                    _expected_cents,
                    extra={"domain": "payments"},
                )
                raise HTTPException(
                    status_code=409,
                    detail="This subscription plan is misconfigured. Please contact support.",
                )

            # Also validate the billing cadence: a same-price monthly Price on a
            # weekly plan (or vice versa) would bill on a different period than
            # the driver is shown. Compare the Price's recurring interval (in
            # days) to the plan's duration_days with a small tolerance for the
            # calendar-month/year approximations.
            _INTERVAL_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
            _recurring = _price_obj.get("recurring") or {}
            _price_days = _INTERVAL_DAYS.get(_recurring.get("interval"), 0) * (_recurring.get("interval_count") or 1)
            _plan_days = plan.get("duration_days") or 0
            if not _recurring.get("interval") or abs(_price_days - _plan_days) > 3:
                logger.error(
                    "[SUBSCRIBE] Stripe Price %s interval mismatch for plan %s: "
                    "stripe=%sx%s (~%sd) plan=%sd — refusing to charge",
                    _price_id,
                    plan_id,
                    _recurring.get("interval"),
                    _recurring.get("interval_count"),
                    _price_days,
                    _plan_days,
                    extra={"domain": "payments"},
                )
                raise HTTPException(
                    status_code=409,
                    detail="This subscription plan is misconfigured. Please contact support.",
                )

            # Recurring billing: Stripe Subscription mode. Stripe auto-renews
            # the driver's card each period and fires invoice.paid (renewal)
            # / invoice.payment_failed (dunning) / customer.subscription.*
            # which routes/webhooks.py reconciles. Requires a recurring Price
            # created in the Stripe dashboard and stored on the plan.
            _session = await asyncio.to_thread(
                lambda: stripe.checkout.Session.create(
                    mode="subscription",
                    line_items=[{"price": _price_id, "quantity": 1}],
                    metadata=_metadata,
                    # Mirror our ids onto the Stripe Subscription so renewal
                    # invoices and subscription events are self-identifying.
                    subscription_data={"metadata": _metadata},
                    success_url=_success_url,
                    cancel_url=_cancel_url,
                    api_key=_stripe_secret,
                )
            )
            checkout_url = _session.url
            stripe_session_id = _session.id
            payment_status = "pending"
            logger.info(
                f"[SUBSCRIBE] Recurring checkout session created: session={stripe_session_id} "
                f"subscription={subscription_id} driver={driver['id']} price={_price_id}"
            )
        elif _stripe_secret and plan_price > 0:
            # One-off Checkout (no recurring Price on this plan). The webhook
            # (checkout.session.completed) activates the subscription; renewal
            # is expiry-driven (driver re-subscribes each period).
            # Compute province tax and charge the inclusive total to Stripe.
            _tax = await _compute_subscription_tax(driver["id"], plan_price)
            _amount_cents = dollars_to_cents(_tax["total"])
            _session = await asyncio.to_thread(
                lambda: stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    mode="payment",
                    customer_creation="always",
                    line_items=[
                        {
                            "price_data": {
                                "currency": "cad",
                                "product_data": {
                                    "name": plan.get("name", "Spinr Pass"),
                                    **({} if not plan.get("description") else {"description": plan["description"]}),
                                },
                                "unit_amount": _amount_cents,
                            },
                            "quantity": 1,
                        }
                    ],
                    metadata=_metadata,
                    # Propagate our ids onto the underlying PaymentIntent so the
                    # nightly reconciler (stripe_reconcile.py) can identify a
                    # subscription charge instead of flagging it STRIPE_ORPHAN if
                    # checkout.session.completed is ever missed.
                    payment_intent_data={"metadata": _metadata},
                    success_url=_success_url,
                    cancel_url=_cancel_url,
                    api_key=_stripe_secret,
                )
            )
            checkout_url = _session.url
            stripe_session_id = _session.id
            payment_status = "pending"
            logger.info(
                f"[SUBSCRIBE] One-off checkout session created: session={stripe_session_id} "
                f"subscription={subscription_id} driver={driver['id']}"
            )
        else:
            # Dev/demo mode: no Stripe or free plan — activate immediately.
            payment_status = "paid"
            logger.info(
                f"[SUBSCRIBE] Dev mode (no Stripe/free plan): subscription {subscription_id} for driver {driver['id']}"
            )
    except HTTPException:
        # Intentional rejections (e.g. Price mismatch 409) must propagate with
        # their own status, not be masked as a generic 502.
        raise
    except Exception as _stripe_err:
        logger.exception(f"[SUBSCRIBE] Stripe Checkout creation failed for driver {driver['id']}")
        raise HTTPException(
            status_code=502,
            detail="Payment service unavailable. Please try again later.",
        ) from _stripe_err

    # Supersede any older pending checkout rows for this driver before
    # inserting the new one. Without this, a double-tap / retry / plan-change
    # leaves multiple valid Checkout URLs, and a stale session completing late
    # could activate over a newer choice (_activate_subscription only acts on
    # rows still in "pending", so superseding neutralises the old ones).
    if checkout_url:
        stale_pending = (
            await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": driver["id"], "status": "pending"},
                columns="id,stripe_session_id",
                limit=20,
            )
            or []
        )
        for row in stale_pending:
            # Expire the stale Stripe Checkout Session first so it can never be
            # paid — otherwise a superseded one-off session that completes later
            # charges the driver's card for a pass they won't receive (one-off
            # sessions carry no subscription to cancel). Best-effort: a session
            # that's already completed/expired raises, which we ignore.
            _stale_session = row.get("stripe_session_id")
            if _stale_session and _stripe_secret:
                try:
                    # Default-arg binding: the lambda runs inside this loop
                    # iteration, but bind explicitly so a later refactor can't
                    # introduce the classic late-binding bug (ruff B023).
                    await asyncio.to_thread(
                        lambda _sid=_stale_session: stripe.checkout.Session.expire(_sid, api_key=_stripe_secret)
                    )
                except Exception:
                    logger.info(
                        f"[SUBSCRIBE] Could not expire superseded checkout session {_stale_session} "
                        "(already completed/expired?) — continuing",
                    )
            await db_supabase.update_one(
                "driver_subscriptions",
                {"id": row["id"]},
                {"status": "superseded"},
            )

    # Create the subscription row (pending payment if Checkout, or paid in dev mode)
    subscription = {
        "id": subscription_id,
        "driver_id": driver["id"],
        "plan_id": plan_id,
        "plan_name": plan.get("name"),
        "price": plan.get("price"),
        "rides_per_day": plan.get("rides_per_day", -1),
        "status": "active" if payment_status == "paid" else "pending",
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "payment_status": payment_status,
        "stripe_session_id": stripe_session_id,
        "expiry_warned": False,
        "created_at": now.isoformat(),
    }

    try:
        await db_supabase.insert_one("driver_subscriptions", subscription)
    except Exception as _insert_err:
        # Distinguish the expected concurrency conflict from a real DB failure:
        # the partial unique index (migration 153) allows at most one pending
        # checkout per driver, so if a pending row already exists this was a
        # concurrent subscribe → 409. Otherwise the insert genuinely failed
        # (schema/network/RLS) → surface 503, not a misleading "checkout in
        # progress". Either way, expire the Stripe session we just created so it
        # can't be paid, and log at error (payment-path failure).
        if checkout_url and stripe_session_id and _stripe_secret:
            try:
                await asyncio.to_thread(
                    lambda: stripe.checkout.Session.expire(stripe_session_id, api_key=_stripe_secret)
                )
            except Exception:
                logger.info(f"[SUBSCRIBE] Could not expire race-loser session {stripe_session_id}")
        existing_pending = (
            await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": driver["id"], "status": "pending"},
                columns="id",
                limit=1,
            )
            or []
        )
        if existing_pending:
            logger.warning(
                f"[SUBSCRIBE] Concurrent pending checkout for driver {driver['id']} — returning 409",
            )
            raise HTTPException(
                status_code=409,
                detail="A checkout is already in progress. Please finish or cancel it first.",
            ) from _insert_err
        logger.error(
            f"[SUBSCRIBE] Subscription insert failed for driver {driver['id']}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not start checkout. Please try again.",
        ) from _insert_err

    if checkout_url:
        # Payment pending — defer cancelling the driver's existing pass and
        # bumping the subscriber count to _activate_subscription (runs on the
        # checkout webhook / verify-session once payment is confirmed). This
        # keeps their current pass valid if they abandon Checkout, and avoids
        # double-counting (abandoned sessions would otherwise inflate the count
        # and successful payments would count twice).
        return {
            "success": True,
            "checkout_url": checkout_url,
            "subscription_id": subscription_id,
            # The driver app reads `session_id` to poll verify-session if the
            # deep-link return is delayed/missed after openURL().
            "session_id": stripe_session_id,
        }

    # Dev/immediate-activation mode: no payment step, so the activation path
    # never runs — cancel the prior active subscription and bump the count here.
    if existing:
        await _cancel_stripe_subscription(existing.get("stripe_subscription_id"))
        await db_supabase.update_one(
            "driver_subscriptions",
            {"id": existing["id"]},
            {
                "status": RideStatus.CANCELLED,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    await db_supabase.update_one(
        "subscription_plans",
        {"id": plan_id},
        {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1},
    )
    # Dev/immediate activation realizes revenue without a Stripe invoice —
    # record it in the ledger so admin stats see it.
    _dev_tax = await _compute_subscription_tax(driver["id"], plan_price)
    await _record_subscription_payment(
        driver_id=driver["id"],
        subscription_id=subscription_id,
        plan_id=plan_id,
        plan_name=plan.get("name"),
        amount=_dev_tax["total"],
        billing_reason="dev",
        subtotal=_dev_tax["subtotal"],
        gst_amount=_dev_tax["gst_amount"],
        pst_amount=_dev_tax["pst_amount"],
        hst_amount=_dev_tax["hst_amount"],
        tax_total=_dev_tax["tax_total"],
        province=_dev_tax["province"],
    )
    return {"success": True, "subscription": subscription, "mode": "dev"}


@router.get("/subscription/verify-session")
async def verify_subscription_session(
    session_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Verify a Stripe Checkout Session and return the subscription status.

    Called by the driver-app after the Stripe Checkout deep-link returns
    to the app. The webhook may or may not have fired by this point — if
    it has, the subscription is already active; if not, we check the
    Stripe session directly and activate it here (idempotent).

    Returns `{status: "active"}` if payment succeeded or `{status: "pending"}`
    if Stripe hasn't confirmed yet (caller should poll).
    """
    sub = await _deps.db.find_one("driver_subscriptions", {"stripe_session_id": session_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription session not found")

    # Authorize: the session must belong to the calling driver. Session ids can
    # leak via redirect URLs, browser history, or support logs, so a non-owner
    # gets the same 404 as a non-existent session (no existence oracle).
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver or sub.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Subscription session not found")

    # Already activated (webhook beat us)
    if sub.get("status") == "active" and sub.get("payment_status") == "paid":
        return {"status": "active", "subscription": sub}

    # Check with Stripe directly
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    app_settings = await get_app_settings() or {}
    stripe_key = app_settings.get("stripe_secret_key", "")
    if not stripe_key:
        return {"status": "pending"}

    try:
        session = await asyncio.to_thread(lambda: stripe.checkout.Session.retrieve(session_id, api_key=stripe_key))
    except Exception as e:
        logger.error(f"[SUBSCRIBE] verify-session Stripe error: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Could not verify subscription status with payment provider. Please try again.",
        ) from e

    if session.payment_status == "paid":
        # Activate first (idempotent; a no-op if the row was superseded by a
        # newer checkout), then re-read to decide whether to link. Pass the
        # session's actual mode so ledger recording matches what was created.
        await _activate_subscription(sub["id"], sub.get("plan_id"), session.get("mode"))
        sub = await _deps.db.find_one("driver_subscriptions", {"id": sub["id"]})
        stripe_subscription_id = session.get("subscription")

        if sub and sub.get("status") == "active":
            # Persist the Stripe Subscription id (recurring plans) so renewal /
            # dunning / cancellation webhooks can match this row even if the
            # checkout webhook was delayed or missed. Only on an active row —
            # never link a superseded one.
            if stripe_subscription_id and not sub.get("stripe_subscription_id"):
                await _deps.db.update_one(
                    "driver_subscriptions",
                    {"id": sub["id"]},
                    {"stripe_subscription_id": stripe_subscription_id},
                )
            return {"status": "active", "subscription": sub}

        # Superseded by a newer checkout — don't link/activate; cancel the
        # orphaned Stripe subscription so the driver isn't billed for it.
        if stripe_subscription_id:
            await _cancel_stripe_subscription(stripe_subscription_id)
        return {"status": "superseded"}

    return {"status": "pending"}


async def _record_subscription_payment(
    *,
    driver_id: str,
    subscription_id: str | None,
    plan_id: str | None,
    plan_name: str | None,
    amount,
    billing_reason: str,
    subtotal: Decimal | None = None,
    gst_amount: Decimal | None = None,
    pst_amount: Decimal | None = None,
    hst_amount: Decimal | None = None,
    tax_total: Decimal | None = None,
    province: str | None = None,
    stripe_invoice_id: str | None = None,
    stripe_session_id: str | None = None,
    stripe_payment_intent_id: str | None = None,
    stripe_invoice_url: str | None = None,
) -> str | None:
    """Append a realized-payment row to the subscription_payments ledger.

    Returns the new row id (used by resend-invoice), or None on failure/skip.
    The ledger (migration 151) is the source of truth for admin subscription
    revenue/transaction stats — driver_subscriptions tracks current STATE, this
    tracks money MOVED, so recurring renewals are captured (not just the first
    charge). Recurring inserts dedupe on the unique stripe_invoice_id index, so
    a replay is benign. Never raises — a ledger failure must not block the
    activation/webhook flow that already moved the money.
    """
    try:
        amt = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amt <= 0:
            return None  # nothing realized — don't clutter the ledger
        row_id = str(uuid.uuid4())
        row: dict = {
            "id": row_id,
            "driver_id": driver_id,
            "subscription_id": subscription_id,
            "plan_id": plan_id,
            "plan_name": plan_name,
            "amount": str(amt),
            "currency": "cad",
            "billing_reason": billing_reason,
            "stripe_invoice_id": stripe_invoice_id,
            "stripe_session_id": stripe_session_id,
            "stripe_payment_intent_id": stripe_payment_intent_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Tax columns (migration 186) — stored when computed at checkout.
        def _q2(v):
            return str(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if v is not None else None

        if subtotal is not None:
            row["subtotal"] = _q2(subtotal)
        if gst_amount is not None:
            row["gst_amount"] = _q2(gst_amount)
        if pst_amount is not None:
            row["pst_amount"] = _q2(pst_amount)
        if hst_amount is not None:
            row["hst_amount"] = _q2(hst_amount)
        if tax_total is not None:
            row["tax_total"] = _q2(tax_total)
        if province:
            row["province"] = province
        # Stripe receipt link (migration 188) — only present on recurring charges;
        # written conditionally so dev/one-off rows omit the column cleanly.
        if stripe_invoice_url:
            row["stripe_invoice_url"] = stripe_invoice_url
        await db_supabase.insert_one("subscription_payments", row)
        return row_id
    except Exception as _ledger_err:
        # Distinguish a benign duplicate (unique stripe_invoice_id replay) from a
        # real write failure. A duplicate means the row already exists — nothing
        # lost — so log at debug. Any other error means the ledger row that drives
        # admin revenue/stats is now missing, which must be surfaced at error so
        # ops/reconciliation can repair it. Never raise — the money already moved.
        _msg = str(_ledger_err).lower()
        _is_duplicate = "duplicate" in _msg or "unique" in _msg or "23505" in _msg
        if _is_duplicate:
            logger.debug(
                "subscription_payments duplicate ignored driver=%s invoice=%s",
                driver_id,
                stripe_invoice_id,
            )
        else:
            logger.error(
                "subscription_payments insert FAILED (revenue row lost — repair needed) driver=%s reason=%s invoice=%s",
                driver_id,
                billing_reason,
                stripe_invoice_id,
                exc_info=True,
            )


# ── Pre-retrofit invoice shell ──────────────────────────────────────────────
# Kept verbatim so `branded_receipt_enabled = false` restores exactly what
# drivers were receiving. Delete once the retrofit has been seen in real
# inboxes and the flag is retired.
_LEGACY_INVOICE_HEADER = (
    '<tr><td style="background:#ee2b2b;padding:28px 32px;">\n'
    '    <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px;">Spinr</h1>\n'
    '    <p style="color:rgba(255,255,255,0.8);margin:4px 0 0;font-size:13px;">Subscription Invoice</p>\n'
    "  </td></tr>"
)
_LEGACY_INVOICE_FOOTER = (
    '<tr><td style="padding:16px 32px 24px;border-top:1px solid #f0f0f0;text-align:center;">\n'
    '    <p style="color:#bbb;font-size:11px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK, Canada</p>\n'
    '    <p style="color:#bbb;font-size:11px;margin:4px 0 0;">support@spinr.ca · www.spinr.ca</p>\n'
    "  </td></tr>"
)


async def _branded_invoice_company():
    """Resolved company identity, or None to keep the pre-retrofit shell.

    Shares `branded_receipt_enabled` with the ride receipt deliberately: the two
    are the same retrofit, and an operator turning one off because it renders
    badly means both.

    Never raises — an invoice with the old-looking shell beats no invoice.
    """
    try:
        from ...settings_loader import get_app_settings
        from ...utils.company_details import load_company_details
    except ImportError:  # pragma: no cover - direct module imports in tests
        from settings_loader import get_app_settings  # type: ignore
        from utils.company_details import load_company_details  # type: ignore
    try:
        settings = await get_app_settings()
        if not bool(settings.get("branded_receipt_enabled", True)):
            return None
        return await load_company_details()
    except Exception as exc:
        logger.warning("invoice branding: falling back to the legacy shell: %s", exc)
        return None


async def _send_subscription_invoice_email(
    *,
    driver_id: str,
    plan_name: str,
    duration_label: str,
    subtotal: Decimal,
    gst_amount: Decimal,
    pst_amount: Decimal,
    hst_amount: Decimal,
    tax_total: Decimal,
    total: Decimal,
    province: str = "SK",
    billing_reason: str,
    payment_date: str,
    invoice_number: str | None = None,
    stripe_invoice_url: str | None = None,
) -> bool:
    """Email the driver a rich HTML invoice (+ PDF attachment) for their Spinr Pass charge.

    Returns True on success, False on any failure.  Never raises — the caller
    decides whether to surface the failure (resend endpoint raises 502; activation
    flow logs and continues).
    """
    try:
        driver = await _deps.db.find_one("drivers", {"id": driver_id})
        if not driver:
            return False
        user = await _deps.db.find_one("users", {"id": driver.get("user_id")})
        email = (user or {}).get("email", "")
        if not email:
            return False

        driver_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"
        billing_label = "Auto-renewal" if billing_reason == "subscription_cycle" else "One-time purchase"
        inv_number = invoice_number or f"SPX-{driver_id[:6].upper()}-{payment_date.replace(' ', '').replace(',', '')}"

        # ── Tax line rows for HTML ────────────────────────────────────────────
        def _row(label: str, amount: Decimal, bold: bool = False, color: str = "#666") -> str:
            style = f"color:{color};font-weight:{'700' if bold else '400'}"
            return (
                f"<tr>"
                f"<td style='padding:6px 0;font-size:14px;{style}'>{label}</td>"
                f"<td style='padding:6px 0;font-size:14px;text-align:right;{style}'>${amount:.2f} CAD</td>"
                f"</tr>"
            )

        def _pct_label(amt: Decimal, base: Decimal) -> str:
            if base <= 0:
                return ""
            from decimal import ROUND_HALF_UP as _RHU

            pct = (amt / base * 100).quantize(Decimal("0.01"), rounding=_RHU).normalize()
            return f" ({pct}%)"

        tax_rows_html = ""
        if gst_amount > 0:
            tax_rows_html += _row(f"GST{_pct_label(gst_amount, subtotal)} — Federal", gst_amount)
        if pst_amount > 0:
            tax_rows_html += _row(f"PST{_pct_label(pst_amount, subtotal)} — {province}", pst_amount)
        if hst_amount > 0:
            tax_rows_html += _row(f"HST{_pct_label(hst_amount, subtotal)} — {province}", hst_amount)

        stripe_link_html = (
            f"<p style='margin:8px 0 0;font-size:13px;'>"
            f"<a href='{stripe_invoice_url}' style='color:#ee2b2b;'>View Stripe receipt →</a></p>"
            if stripe_invoice_url
            else ""
        )

        # Shell only — the line items, tax rows and total are identical either
        # way. An invoice is a tax document a driver may file, so its content
        # must not depend on a presentation switch. See migration 288.
        try:
            from ...utils.email_layout import BRAND_RED as _LAYOUT_BRAND_RED
            from ...utils.email_layout import footer_html as _layout_footer_html
            from ...utils.email_layout import header_html as _layout_header_html
        except ImportError:  # pragma: no cover - direct module imports in tests
            from utils.email_layout import BRAND_RED as _LAYOUT_BRAND_RED  # type: ignore
            from utils.email_layout import footer_html as _layout_footer_html  # type: ignore
            from utils.email_layout import header_html as _layout_header_html  # type: ignore

        _inv_company = await _branded_invoice_company()
        if _inv_company is not None:
            _inv_header = _layout_header_html(_inv_company, "Subscription Invoice")
            _inv_footer = _layout_footer_html(_inv_company)
            _inv_accent = _LAYOUT_BRAND_RED
        else:
            _inv_header = _LEGACY_INVOICE_HEADER
            _inv_footer = _LEGACY_INVOICE_FOOTER
            _inv_accent = "#ee2b2b"

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:24px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08);">
  <!-- Header -->
  {_inv_header}

  <!-- Greeting -->
  <tr><td style="padding:28px 32px 0;">
    <p style="color:#1a1a1a;font-size:16px;margin:0;">Hi {driver_name},</p>
    <p style="color:#888;font-size:14px;margin:6px 0 0;">
      Thank you for your Spinr Pass subscription. Your invoice is attached as a PDF.
    </p>
  </td></tr>

  <!-- Amount callout -->
  <tr><td style="padding:20px 32px;">
    <div style="background:#fef2f2;border-radius:12px;padding:20px;text-align:center;">
      <p style="color:{_inv_accent};font-size:40px;font-weight:800;margin:0;">${total:.2f} CAD</p>
      <p style="color:#999;font-size:12px;margin:6px 0 0;">{payment_date} · {billing_label}</p>
      <p style="color:#999;font-size:11px;margin:4px 0 0;letter-spacing:0.4px;">Invoice <strong style="color:#1a1a1a">{inv_number}</strong></p>
    </div>
  </td></tr>

  <!-- Line items -->
  <tr><td style="padding:0 32px 24px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr style="border-bottom:2px solid #f0f0f0;">
        <td style="padding:8px 0;font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:0.6px;">Description</td>
        <td style="padding:8px 0;font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:0.6px;text-align:right;">Amount (CAD)</td>
      </tr>
      {_row(f"Spinr Pass {plan_name} — {duration_label}", subtotal, color="#1a1a1a")}
      <tr><td colspan="2" style="padding:4px 0;border-top:1px solid #f0f0f0;"></td></tr>
      {tax_rows_html}
      <tr><td colspan="2" style="padding:4px 0;border-top:2px solid #1a1a1a;"></td></tr>
      {_row("Total", total, bold=True, color="#1a1a1a")}
    </table>
  </td></tr>

  <!-- Payment note -->
  <tr><td style="padding:0 32px 28px;">
    <div style="background:#f9f9f9;border-radius:10px;padding:16px;">
      <p style="margin:0;font-size:13px;color:#666;">Payment successfully charged to your card on file.</p>
      {stripe_link_html}
    </div>
  </td></tr>

  <!-- Footer -->
  {_inv_footer}
</table>
</body></html>"""

        # ── PDF attachment ────────────────────────────────────────────────────
        attachments = None
        try:
            try:
                from ...utils.subscription_invoice_pdf import generate_subscription_invoice_pdf
            except ImportError:
                from utils.subscription_invoice_pdf import generate_subscription_invoice_pdf  # type: ignore

            pdf_bytes = generate_subscription_invoice_pdf(
                # Same identity as the email body; a PDF naming a different
                # company than the mail it arrived in is worse than either
                # being stale alone.
                company=_inv_company,
                invoice_number=inv_number,
                payment_date=payment_date,
                driver_name=driver_name,
                driver_email=email,
                plan_name=plan_name,
                duration_label=duration_label,
                billing_reason=billing_reason,
                subtotal=subtotal,
                gst_amount=gst_amount,
                pst_amount=pst_amount,
                hst_amount=hst_amount,
                tax_total=tax_total,
                total=total,
                province=province,
                stripe_invoice_url=stripe_invoice_url,
            )
            safe_plan = plan_name.replace(" ", "-")
            attachments = [
                {
                    "filename": f"Spinr-Pass-Invoice-{safe_plan}-{payment_date.replace(' ', '-').replace(',', '')}.pdf",
                    "content": pdf_bytes,
                    "mime": "application/pdf",
                }
            ]
        except Exception:
            logger.error("[SUBSCRIBE] Invoice PDF generation failed — sending HTML only", exc_info=True)

        try:
            from ...utils.email_provider import send_transactional_email
        except ImportError:
            from utils.email_provider import send_transactional_email  # type: ignore

        _ok = await send_transactional_email(
            to=email,
            subject=f"Your Spinr Pass Invoice — {plan_name} ({payment_date})",
            html=html,
            default_from="invoices@spinr.ca",
            log_id=driver_id,
            email_type="subscription_invoice",
            attachments=attachments,
        )
        if _ok:
            logger.info(
                "[SUBSCRIBE] Invoice email sent driver=%s plan=%s total=%s",
                driver_id,
                plan_name,
                total,
                extra={"domain": "payments", "driver_id": driver_id},
            )
        else:
            logger.error(
                "[SUBSCRIBE] Invoice email delivery failed driver=%s plan=%s",
                driver_id,
                plan_name,
                extra={"domain": "payments", "driver_id": driver_id},
            )
        return bool(_ok)
    except Exception as _mail_err:
        logger.error(
            "[SUBSCRIBE] Invoice email failed driver=%s: %s",
            driver_id,
            _mail_err,
            exc_info=True,
            extra={"domain": "payments", "driver_id": driver_id},
        )
        return False


async def _activate_subscription(subscription_id: str, plan_id: str | None = None, checkout_mode: str | None = None):
    """Activate a pending subscription after payment confirmation.

    Called by both the webhook handler and the verify-session endpoint
    (whichever runs first). Idempotent — skips if already active.

    ``checkout_mode`` is the Stripe Checkout Session's actual mode
    ("payment" for one-off, "subscription" for recurring). It decides whether
    to write the one-off ledger row here — based on what was actually created,
    NOT the plan's current stripe_price_id, which an admin could flip between
    checkout start and payment completion.
    """
    sub = await _deps.db.find_one("driver_subscriptions", {"id": subscription_id})
    # Only a still-pending checkout row activates. A row that is already
    # active (idempotent replay), cancelled, or superseded by a newer checkout
    # is terminal — never (re)activate it, so a stale session completing late
    # can't replace the driver's current plan.
    if not sub or sub.get("status") != "pending":
        return

    driver_id = sub.get("driver_id")

    # Recompute the period from activation time so a driver who completes
    # Checkout hours after creating it (Stripe sessions live up to 24h) doesn't
    # lose paid access — matters most on daily/weekly one-off plans. (Recurring
    # plans get expires_at overwritten by invoice.paid's authoritative period
    # end on the next event.)
    plan = await _deps.db.find_one("subscription_plans", {"id": plan_id}) if plan_id else None
    now = datetime.now(timezone.utc)
    # Fetch the driver once here so the expiry below can be anchored to their
    # location timezone (reused for the activation push further down). This runs
    # before the atomic claim, so a DB error must NOT abort activation — the
    # driver already paid. Degrade to no row (→ Regina expiry, no push) and log.
    try:
        _drv = await _deps.db.find_one("drivers", {"id": driver_id}) if driver_id else None
    except Exception:
        logger.error("[SUBSCRIBE] driver lookup failed during activation; continuing", exc_info=True)
        _drv = None
    try:
        from ...utils.spinr_pass import area_timezone, compute_pass_expiry, pass_duration_label
    except ImportError:
        from utils.spinr_pass import area_timezone, compute_pass_expiry, pass_duration_label  # type: ignore
    try:
        _act_tz = await area_timezone((_drv or {}).get("service_area_id"))
    except Exception:
        # Payment-path DB read — surface at error; expiry falls back to Regina.
        logger.error("[SUBSCRIBE] activation tz lookup failed; using default for expiry", exc_info=True)
        _act_tz = None
    activate_updates = {
        "status": "active",
        "payment_status": "paid",
        "started_at": now.isoformat(),
    }
    if plan:
        # Multi-day pass → local midnight ending its Nth day; 1-day → 24h by hour.
        # Pairs with started_at above (missing duration_days → 30-day fallback) so
        # the row's lifetime is always self-consistent.
        activate_updates["expires_at"] = compute_pass_expiry(plan.get("duration_days"), now=now, tz=_act_tz).isoformat()

    # Atomic claim: flip pending→active filtering on status='pending'. Only the
    # caller that wins (webhook vs verify-session can race) gets a row back and
    # runs the one-time side effects below; the loser matches 0 rows and returns,
    # so cancel-prior / count-increment / push never run twice.
    claimed = await _deps.db.update_one(
        "driver_subscriptions",
        {"id": subscription_id, "status": "pending"},
        {"$set": activate_updates},
    )
    if not claimed:
        return

    # Cancel any prior active subscription for this driver (plan switch).
    # NOTE: the atomic claim above already flipped THIS row to active, so an
    # unordered limit-1 lookup could return it. Fetch all active rows and pick
    # one that isn't the row we just activated, so the prior pass is always
    # retired.
    _active_rows = (
        await _deps.db.get_rows("driver_subscriptions", {"driver_id": driver_id, "status": "active"}, limit=10) or []
    )
    existing = next((r for r in _active_rows if r.get("id") != subscription_id), None)
    if existing:
        try:
            # Durable cancel: raise on failure so we don't claim the old pass is
            # cancelled while Stripe keeps billing it.
            await _cancel_stripe_subscription(existing.get("stripe_subscription_id"), raise_on_error=True)
            await _deps.db.update_one(
                "driver_subscriptions",
                {"id": existing["id"]},
                {"$set": {"status": RideStatus.CANCELLED, "cancelled_at": now.isoformat()}},
            )
        except Exception:
            # The new pass is already active (driver paid), so we can't block,
            # but the old Stripe subscription may still bill. Mark the old row
            # cancel_pending (terminal — won't be reactivated) and log loudly so
            # ops can cancel it in Stripe. (No auto-retry yet — tracked follow-up.)
            logger.error(
                "[SUBSCRIBE] Prior Stripe subscription cancel failed on plan switch — "
                "old row marked cancel_pending; manual Stripe cancel needed. driver=%s old_row=%s",
                driver_id,
                existing["id"],
                exc_info=True,
                extra={"domain": "payments", "driver_id": driver_id},
            )
            await _deps.db.update_one(
                "driver_subscriptions",
                {"id": existing["id"]},
                {"$set": {"status": "cancel_pending", "cancelled_at": now.isoformat()}},
            )

    # Increment subscriber count (reuse the plan already fetched above).
    if plan:
        await _deps.db.update_one(
            "subscription_plans",
            {"id": plan_id},
            {"$set": {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1}},
        )

    # Record one-off Checkout revenue in the ledger. Recurring (mode=subscription)
    # checkouts are NOT recorded here — their invoice.paid webhook writes the
    # ledger, so recording here too would double-count the first charge.
    # Decide from the ACTUAL session mode when known (not the plan's current
    # stripe_price_id, which an admin could flip mid-checkout); fall back to the
    # plan flag only when the mode wasn't passed.
    if checkout_mode is not None:
        _is_one_off = checkout_mode == "payment"
    else:
        _is_one_off = bool(plan) and not plan.get("stripe_price_id")
    if plan and _is_one_off:
        _one_off_tax = await _compute_subscription_tax(driver_id, Decimal(str(plan.get("price") or 0)))
        await _record_subscription_payment(
            driver_id=driver_id,
            subscription_id=subscription_id,
            plan_id=plan_id,
            plan_name=sub.get("plan_name") or plan.get("name"),
            amount=_one_off_tax["total"],
            billing_reason="one_off",
            subtotal=_one_off_tax["subtotal"],
            gst_amount=_one_off_tax["gst_amount"],
            pst_amount=_one_off_tax["pst_amount"],
            hst_amount=_one_off_tax["hst_amount"],
            tax_total=_one_off_tax["tax_total"],
            province=_one_off_tax["province"],
            stripe_session_id=sub.get("stripe_session_id"),
        )

    logger.info(f"[SUBSCRIBE] Subscription {subscription_id} activated for driver {driver_id}")

    # Push notification + invoice email to driver. Reuse the row fetched above.
    if driver_id:
        driver = _drv
        if driver and driver.get("user_id"):
            try:
                await _deps.send_push_notification(
                    driver["user_id"],
                    "Spinr Pass Activated! 🎉",
                    f"Your {sub.get('plan_name', 'Spinr Pass')} subscription is now active. Go online and start earning!",
                )
            except Exception as push_err:
                logger.warning(f"[SUBSCRIBE] Push notification failed: {push_err}")

        # Invoice email — one-off and dev plans only. Recurring plans receive
        # their invoice email from the invoice.paid webhook (which has the
        # authoritative Stripe invoice URL and amount for every charge).
        if plan and _is_one_off:
            _dur_label = pass_duration_label(plan.get("duration_days", 30))
            await _send_subscription_invoice_email(
                driver_id=driver_id,
                plan_name=plan.get("name", "Spinr Pass"),
                duration_label=_dur_label,
                subtotal=_one_off_tax["subtotal"],
                gst_amount=_one_off_tax["gst_amount"],
                pst_amount=_one_off_tax["pst_amount"],
                hst_amount=_one_off_tax["hst_amount"],
                tax_total=_one_off_tax["tax_total"],
                total=_one_off_tax["total"],
                province=_one_off_tax["province"],
                billing_reason="one_off",
                payment_date=now.strftime("%B %d, %Y"),
            )


@router.get("/subscription/payments")
async def get_subscription_payment_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Return paginated Spinr Pass payment history for the authenticated driver.

    Only returns rows for the calling driver's own account — driver_id is
    resolved from the authenticated user's token, never from a query param.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payments = (
        await db_supabase.get_rows(
            "subscription_payments",
            {"driver_id": driver["id"]},
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
        or []
    )

    total = await db_supabase.count_documents("subscription_payments", {"driver_id": driver["id"]})

    def _payment_row(p: dict) -> dict:
        """Serialize a subscription_payments row with tax breakdown.

        Prefers stored tax columns (migration 186). Falls back to back-computing
        GST+PST from the total for legacy rows written before migration 186.
        """

        def _q2(v):
            return Decimal(str(v)).quantize(Decimal("0.01"))

        total_d = _q2(p.get("amount") or 0)
        if p.get("subtotal") is not None:
            subtotal_d = _q2(p["subtotal"])
            gst_d = _q2(p.get("gst_amount") or 0)
            pst_d = _q2(p.get("pst_amount") or 0)
            hst_d = _q2(p.get("hst_amount") or 0)
        else:
            # Legacy rows written before migration 186 — return zero tax rather
            # than fabricating amounts that were never collected.
            subtotal_d = total_d
            gst_d = pst_d = hst_d = Decimal("0")
        return {
            "id": p["id"],
            "plan_id": p.get("plan_id"),
            "plan_name": p.get("plan_name"),
            "amount": str(total_d),
            "subtotal": str(subtotal_d),
            "gst_amount": str(gst_d),
            "pst_amount": str(pst_d),
            "hst_amount": str(hst_d),
            "province": p.get("province") or "SK",
            "currency": (p.get("currency") or "cad").upper(),
            "billing_reason": p.get("billing_reason"),
            "stripe_invoice_id": p.get("stripe_invoice_id"),
            "created_at": p.get("created_at"),
        }

    return {
        "payments": [_payment_row(p) for p in payments],
        "total": total,
        "has_more": (offset + len(payments)) < total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/subscription/payments/{payment_id}/resend-invoice")
async def resend_subscription_invoice(
    payment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Re-send the invoice email for a specific Spinr Pass payment.

    Drivers can trigger a resend from the payment history screen.
    Only the owning driver may request resend — payment_id is verified
    against their driver account, never taken on trust.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payment = await db_supabase.find_one("subscription_payments", {"id": payment_id})
    if not payment or payment.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Payment not found")

    plan = None
    if payment.get("plan_id"):
        plan = await db_supabase.find_one("subscription_plans", {"id": payment["plan_id"]})
    try:
        from ...utils.spinr_pass import pass_duration_label
    except ImportError:
        from utils.spinr_pass import pass_duration_label  # type: ignore
    _dur_label = pass_duration_label((plan or {}).get("duration_days", 30))

    def _q2(v):
        return Decimal(str(v)).quantize(Decimal("0.01"))

    total_d = _q2(payment.get("amount") or 0)
    if payment.get("subtotal") is not None:
        subtotal_d = _q2(payment["subtotal"])
        gst_d = _q2(payment.get("gst_amount") or 0)
        pst_d = _q2(payment.get("pst_amount") or 0)
        hst_d = _q2(payment.get("hst_amount") or 0)
        tax_total_d = _q2(payment.get("tax_total") or 0)
        province = payment.get("province") or "SK"
    else:
        # Legacy row (before migration 186) — zero tax rather than fabricating amounts.
        subtotal_d = total_d
        gst_d = pst_d = hst_d = tax_total_d = Decimal("0")
        province = "SK"

    from datetime import datetime as _dt

    _raw_date = payment.get("created_at") or ""
    try:
        _payment_date = _dt.fromisoformat(_raw_date.replace("Z", "+00:00")).strftime("%B %d, %Y")
    except Exception:
        _payment_date = _dt.now(timezone.utc).strftime("%B %d, %Y")

    _sent = await _send_subscription_invoice_email(
        driver_id=driver["id"],
        plan_name=payment.get("plan_name") or "Spinr Pass",
        duration_label=_dur_label,
        subtotal=subtotal_d,
        gst_amount=gst_d,
        pst_amount=pst_d,
        hst_amount=hst_d,
        tax_total=tax_total_d,
        total=total_d,
        province=province,
        billing_reason=payment.get("billing_reason") or "one_off",
        payment_date=_payment_date,
        invoice_number=f"SPX-{payment['id'][:8].upper()}",
    )
    if not _sent:
        raise HTTPException(status_code=502, detail="Invoice email could not be delivered")
    return {"success": True}


@router.post("/subscription/cancel")
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel driver's active subscription."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    sub = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "driver_subscriptions",
            {
                "driver_id": driver["id"],
                "status": "active",
            },
            limit=1,
        )
    )
    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription")

    # Stop Stripe billing for recurring plans first. If Stripe cancellation
    # fails we must NOT mark the row cancelled — otherwise the app shows
    # "cancelled" while Stripe keeps billing (and a later invoice.paid would
    # silently re-activate it). Surface a retryable error instead.
    try:
        await _cancel_stripe_subscription(sub.get("stripe_subscription_id"), raise_on_error=True)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail="Could not cancel your subscription with the payment provider. Please try again.",
        ) from e

    await db_supabase.update_one(
        "driver_subscriptions",
        {"id": sub["id"]},
        {
            "status": RideStatus.CANCELLED,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"success": True}


# ── G5: Subscription expiry warning background task ──────────────


async def check_expiring_subscriptions():
    """Background task: handles Spinr Pass expiry end-to-end.

    Two jobs, both run every 6 hours from the FastAPI lifespan:

    1. **Warn** drivers whose active subscription expires within 72 h (3 days)
       via ``expiry_warned_3d``, then again within 24 h via ``expiry_warned``.
       Both are claim-flags so only one push fires per window per subscription.
    2. **Enforce** expiry on subscriptions whose ``expires_at`` is in the
       past — but only when the admin toggle
       ``require_driver_subscription`` is on. Enforcement:
         - mark the sub row ``status='expired'``
         - if the driver is currently ``is_online``, flip them offline,
           clear Redis presence, disconnect any active WebSocket, and
           push a notification explaining why.
         - broadcast ``driver_status_changed`` to admin monitoring.

    Without step 2, a subscription-gated driver could stay online and
    keep accepting rides past their paid-for period: the Go-Online path
    re-checks the sub, but drivers who were already online when their
    sub expired would never hit that gate until they voluntarily tapped
    off and back on.
    """
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    try:
        from ...utils.redis_client import redis_set_nx as _redis_set_nx  # type: ignore
    except ImportError:
        try:
            from utils.redis_client import redis_set_nx as _redis_set_nx  # type: ignore
        except ImportError:
            _redis_set_nx = None  # type: ignore

    while True:
        # Single-replica enforcement: only the pod that wins the lock runs
        # the expiry check per 6-hour window. Prevents N offline-kick push
        # notifications being sent to the same driver on multi-replica deploys.
        if _redis_set_nx is not None:
            try:
                lock_acquired = await _redis_set_nx(
                    "spinr:subscription:expiry:lock",
                    f"{socket.gethostname()}:{os.getpid()}",
                    6 * 3600 + 300,  # 6h + 5 min grace
                )
            except Exception as _lock_err:
                # redis_set_nx now raises on a real (Redis-configured-but-
                # unavailable) error instead of silently falling back
                # per-replica (2026-08-11 P1 fix). This lock only prevents
                # redundant duplicate notifications, not an unsafe state —
                # proceed with the tick (every replica may notify once, an
                # over-notification, not an under-enforcement) rather than
                # go dark on subscription expiry enforcement for 6h.
                logger.error("[SUB-EXPIRY] leader lock unavailable (%s), proceeding without it", _lock_err)
                lock_acquired = True
        else:
            lock_acquired = True  # no Redis in dev → run on all replicas
        if not lock_acquired:
            await asyncio.sleep(6 * 3600)
            continue
        try:
            now = datetime.now(timezone.utc)
            window_24h = now + timedelta(hours=24)
            window_3d = now + timedelta(hours=72)

            # Retry subscriptions stuck in cancel_pending — a plan-switch Stripe
            # cancel that failed transiently. On success, finalize as cancelled;
            # a row still failing stays cancel_pending and is logged for ops.
            try:
                stuck_cancels = (
                    await _deps.db.get_rows("driver_subscriptions", {"status": "cancel_pending"}, limit=200) or []
                )
                for pc in stuck_cancels:
                    try:
                        await _cancel_stripe_subscription(pc.get("stripe_subscription_id"), raise_on_error=True)
                        await _deps.db.update_one(
                            "driver_subscriptions",
                            {"id": pc["id"]},
                            {"$set": {"status": "cancelled"}},
                        )
                        logger.info(
                            "[SUB-EXPIRY] cancel_pending resolved row=%s",
                            pc["id"],
                            extra={"domain": "payments"},
                        )
                    except Exception:
                        logger.error(
                            "[SUB-EXPIRY] cancel_pending retry still failing row=%s — manual Stripe cancel needed",
                            pc["id"],
                            exc_info=True,
                            extra={"domain": "payments"},
                        )
            except Exception:
                logger.error("[SUB-EXPIRY] cancel_pending sweep query failed", exc_info=True)

            active_subs = await _deps.db.get_rows("driver_subscriptions", {"status": "active"}, limit=500)

            # Only enforce the offline flip when the admin has turned on
            # the subscription gate — otherwise expiry is purely advisory.
            try:
                app_settings = await get_app_settings()
                require_sub = bool(app_settings.get("require_driver_subscription", False))
            except Exception as e:
                logger.error(
                    f"[SUB-EXPIRY] get_app_settings failed, skipping enforcement this tick: {e}",
                    exc_info=True,
                )
                require_sub = False

            warned_count = 0
            enforced_count = 0
            for sub in active_subs:
                expires_at = sub.get("expires_at")
                if not expires_at:
                    continue

                if isinstance(expires_at, str):
                    try:
                        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if expires_dt.tzinfo is None:
                            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                else:
                    expires_dt = expires_at
                    if expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)

                # ── Enforcement branch: already expired ──────────────────
                if expires_dt <= now:
                    try:
                        await _deps.db.update_one(
                            "driver_subscriptions",
                            {"id": sub["id"]},
                            {"status": "expired"},
                        )
                    except Exception as e:
                        logger.error(
                            f"[SUB-EXPIRY] Failed to mark sub {sub['id']} expired: {e}",
                            exc_info=True,
                        )
                        continue

                    if not require_sub:
                        # Gate is off — just flip the row state, leave the
                        # driver alone. (They'll re-gate on next Go Online
                        # if the admin turns enforcement back on.)
                        continue

                    driver = await _deps.db.find_one("drivers", {"id": sub["driver_id"]})
                    if not driver or not driver.get("is_online"):
                        continue

                    try:
                        await _deps.db.update_one(
                            "drivers",
                            {"id": driver["id"]},
                            {
                                "is_online": False,
                                "is_available": False,
                                "updated_at": now.isoformat(),
                                "last_status_changed_at": now.isoformat(),
                            },
                        )
                    except Exception as e:
                        logger.error(f"[SUB-EXPIRY] Failed to flip driver {driver['id']} offline: {e}")
                        continue
                    # M-5: SGI insurance period audit — driver was online
                    # (period 1) and we just forced them offline (period 0).
                    await _deps.record_period_transition(driver["id"], 0)

                    try:
                        await _deps.clear_presence(driver["id"])
                    except Exception as e:
                        logger.error(
                            f"[SUB-EXPIRY] clear_presence failed for {driver['id']}: {e}",
                            exc_info=True,
                        )

                    if driver.get("user_id"):
                        _deps.manager.disconnect(f"driver_{driver['user_id']}")

                    try:
                        await _deps.db.insert_one(
                            "driver_activity_log",
                            {
                                "id": str(uuid.uuid4()),
                                "driver_id": driver["id"],
                                "event_type": "went_offline",
                                "title": "Went offline (Spinr Pass expired)",
                                "description": "System flipped driver offline — active subscription expired and Spinr Pass is required to drive.",
                                "metadata": {
                                    "reason": "subscription_expired",
                                    "source": "check_expiring_subscriptions",
                                    "subscription_id": sub["id"],
                                },
                                "actor": "system",
                                "created_at": now.isoformat(),
                            },
                        )
                    except Exception as e:
                        logger.error(
                            f"[SUB-EXPIRY] activity log insert failed for {driver['id']}: {e}",
                            exc_info=True,
                        )

                    if driver.get("user_id"):
                        try:
                            await _deps.send_push_notification(
                                driver["user_id"],
                                "Spinr Pass expired",
                                "Your Spinr Pass has expired and you've been set offline. Renew from your dashboard to keep driving.",
                                {
                                    "type": "subscription_expired",
                                    "driver_id": driver["id"],
                                },
                            )
                        except Exception as e:
                            logger.warning(f"[SUB-EXPIRY] Push failed for driver {driver['id']}: {e}")

                    try:
                        await _deps.manager.broadcast_to_admins(
                            {
                                "type": "driver_status_changed",
                                "driver_id": driver["id"],
                                "is_online": False,
                            }
                        )
                    except Exception:  # noqa: S110
                        logger.warning(
                            "check_expiring_subscriptions: admin broadcast failed for driver %s",
                            driver["id"],
                            exc_info=True,
                        )

                    enforced_count += 1
                    continue

                # ── Warning branch: 24-hour final notice ─────────────────
                if sub.get("expiry_warned"):
                    continue

                if now < expires_dt <= window_24h:
                    driver = await _deps.db.find_one("drivers", {"id": sub["driver_id"]})
                    if driver and driver.get("user_id"):
                        hours_left = max(1, int((expires_dt - now).total_seconds() / 3600))
                        plan_name = sub.get("plan_name", "Spinr Pass")
                        try:
                            await _deps.send_push_notification(
                                driver["user_id"],
                                "Spinr Pass Expiring Soon",
                                f"Your {plan_name} plan expires in ~{hours_left} hours. Renew now to keep driving!",
                                {
                                    "type": "subscription_expiring",
                                    "hours_left": str(hours_left),
                                },
                            )
                            warned_count += 1
                        except Exception as e:
                            logger.warning(f"[SUB-EXPIRY] Push failed for driver {sub['driver_id']}: {e}")

                    await _deps.db.update_one(
                        "driver_subscriptions",
                        {"id": sub["id"]},
                        {"$set": {"expiry_warned": True}},
                    )

            # ── 3-day advance warning: dedicated targeted query ──────────
            # Query only the subs in the (24h, 72h] expiry window that
            # haven't been warned yet, bypassing the 500-row active_subs cap
            # so no expiring row is silently skipped on a busy platform.
            subs_3d = (
                await _deps.db.get_rows(
                    "driver_subscriptions",
                    {
                        "$and": [
                            {"status": "active"},
                            {"expiry_warned_3d": False},
                            {"expires_at": {"$gt": window_24h.isoformat()}},
                            {"expires_at": {"$lte": window_3d.isoformat()}},
                        ]
                    },
                    limit=500,
                )
                or []
            )
            warned_3d_count = 0
            for sub_3d in subs_3d:
                try:
                    _exp_str = sub_3d.get("expires_at")
                    if not _exp_str:
                        continue
                    expires_3d = datetime.fromisoformat(str(_exp_str).replace("Z", "+00:00"))
                    if expires_3d.tzinfo is None:
                        expires_3d = expires_3d.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                # Claim atomically: also recheck status and the expiry window
                # so a renewal or cancellation between the query and this write
                # does not mark a stale or renewed row as warned.
                claimed = await _deps.db.update_one(
                    "driver_subscriptions",
                    {
                        "id": sub_3d["id"],
                        "expiry_warned_3d": False,
                        "status": "active",
                        "$and": [
                            {"expires_at": {"$gt": window_24h.isoformat()}},
                            {"expires_at": {"$lte": window_3d.isoformat()}},
                        ],
                    },
                    {"$set": {"expiry_warned_3d": True}},
                )
                if claimed is None:
                    continue
                drv_3d = await _deps.db.find_one("drivers", {"id": sub_3d["driver_id"]})
                if drv_3d and drv_3d.get("user_id"):
                    days_left = max(1, int((expires_3d - now).total_seconds() / 86400))
                    plan_name = sub_3d.get("plan_name", "Spinr Pass")
                    try:
                        await _deps.send_push_notification(
                            drv_3d["user_id"],
                            "Spinr Pass Expiring in 3 Days",
                            f"Your {plan_name} plan expires in ~{days_left} day{'s' if days_left != 1 else ''}. Renew now to keep driving!",
                            {"type": "subscription_expiring_3d", "days_left": str(days_left)},
                        )
                        warned_3d_count += 1
                    except Exception as e:
                        logger.warning(
                            "[SUB-EXPIRY] 3d push failed for driver %s: %s",
                            sub_3d["driver_id"],
                            e,
                        )

            logger.info(
                "[SUB-EXPIRY] Check complete. %d scanned, %d 24h warned, %d 3d warned, %d enforced offline.",
                len(active_subs),
                warned_count,
                warned_3d_count,
                enforced_count,
            )

        except Exception as e:
            logger.error(f"[SUB-EXPIRY] Background check error: {e}", exc_info=True)

        try:
            from utils.loop_monitor import record_heartbeat as _lm_hb

            _lm_hb("subscription_expiry (6h)")
        except ImportError:
            pass

        await asyncio.sleep(6 * 3600)
