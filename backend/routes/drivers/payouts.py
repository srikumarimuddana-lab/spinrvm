"""Bank accounts, Stripe Connect onboarding, standard and instant payouts.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import earnings
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Decimal,
    Depends,
    Field,
    HTMLResponse,
    HTTPException,
    Query,
    Request,
    RideStatus,
    asyncio,
    datetime,
    db_supabase,
    dollars_to_cents,
    get_current_user,
    idempotent_endpoint,
    json,
    logger,
    re,
    stripe,
    timezone,
    uuid,
)
from ._shared import (  # noqa: F401
    _money_str,
    _vault_decrypt,
    serialize_doc,
)

try:
    from ...services import stripe_kyc_sync as _kyc
    from ...utils.stripe_mode import is_missing_on_key, key_mode, object_mode
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from services import stripe_kyc_sync as _kyc  # type: ignore
    from utils.stripe_mode import is_missing_on_key, key_mode, object_mode  # type: ignore

router = APIRouter()


class BankAccountCreate(BaseModel):
    bank_name: str
    institution_number: str
    transit_number: str
    account_number: str
    account_holder_name: str
    account_type: str = "checking"


class PayoutRequest(BaseModel):
    amount: Decimal = Field(
        ...,
        ge=Decimal("10.00"),
        le=Decimal("50000.00"),
        decimal_places=2,
        description="Payout must be between $10.00 and $50,000.00",
    )


class InstantPayoutRequest(BaseModel):
    # Lower floor than standard payouts because the fee floor below makes
    # micro-cashouts uneconomical on the platform side anyway; ceiling is
    # Stripe's documented Instant Payout cap of $5,000 USD/CAD per request.
    amount: Decimal = Field(
        ...,
        ge=Decimal("5.00"),
        le=Decimal("5000.00"),
        decimal_places=2,
        description="Instant payout must be between $5.00 and $5,000.00",
    )


# Instant payout fee model: 1.5% of gross, with a $0.50 floor and $15 ceiling.
# Matches Uber's Instant Pay and Lyft Express Pay fee structures within
# rounding. Standard scheduled payouts are still free (zero fee).
INSTANT_PAYOUT_FEE_PCT = Decimal("0.015")
INSTANT_PAYOUT_MIN_FEE = Decimal("0.50")
INSTANT_PAYOUT_MAX_FEE = Decimal("15.00")


def compute_instant_payout_fee(amount: Decimal) -> Decimal:
    """Fee charged on an instant payout. Caller subtracts to get net."""
    pct = (amount * INSTANT_PAYOUT_FEE_PCT).quantize(Decimal("0.01"))
    if pct < INSTANT_PAYOUT_MIN_FEE:
        return INSTANT_PAYOUT_MIN_FEE
    if pct > INSTANT_PAYOUT_MAX_FEE:
        return INSTANT_PAYOUT_MAX_FEE
    return pct


@router.get("/bank-account")
async def get_bank_account(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )
    if account:
        return {"has_bank_account": True, "bank_account": serialize_doc(account)}

    if driver.get("stripe_account_onboarded"):
        return {
            "has_bank_account": True,
            "bank_account": {"bank_name": "Stripe Connect", "account_last4": "****"},
        }

    return {"has_bank_account": False, "bank_account": None}


async def _ensure_stripe_account(driver: dict, user: dict, stripe_secret: str) -> str:
    """Return the driver's Stripe Connect (Express) account id, creating it on
    first use. Shared by the hosted-link and embedded-onboarding flows so both
    converge on a single CA individual Express account per driver.

    An account stranded by a test→live key rotation is retired first (the
    mirror columns describing it are reset, the id archived) and a fresh
    Express account is created under the running key. That is the only correct
    outcome: a test-mode Connect account holds no real bank details and no
    verified identity, so there is nothing to carry over — the driver
    re-onboards, which is exactly what this endpoint drives.
    """
    account_id = driver.get("stripe_account_id")
    superseded: str | None = None
    if account_id:
        if not _kyc.account_is_stale_by_mode(driver, stripe_secret):
            return account_id
        await _kyc.retire_stripe_account(driver, account_id, reason="mode_mismatch")
        superseded = account_id
        driver = {**driver, "stripe_account_id": None}
    return await _create_stripe_account(driver, user, stripe_secret, superseded=superseded)


async def _mirror_id_number_provided(driver_id: str, account_id: str) -> None:
    """Record on the drivers row that Stripe now holds an id_number.

    This is the same mirror column stripe_kyc_sync maintains from the
    account.updated webhook — setting it here just gets there first, so the
    next prefill_sin_to_stripe call short-circuits instead of paying an
    Account.retrieve. Best-effort: prefill must never raise, and losing this
    write costs one redundant retrieve, not correctness — the webhook/sync
    will set it anyway."""
    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, {"stripe_id_number_provided": True})
    except Exception:
        logger.error(
            "[SIN-PREFILL] could not mirror stripe_id_number_provided; next prefill will re-check Stripe",
            exc_info=True,
            extra={"driver_id": driver_id, "stripe_account_id": account_id},
        )


async def prefill_sin_to_stripe(driver: dict, account_id: str, stripe_secret: str) -> str:
    """Give Stripe the SIN we already hold, so its form stops asking for it.

    Without this the driver types their SIN twice — once into Stripe's hosted
    page, once into ours — because the AccountLink below deliberately requests
    ``eventually_due`` fields to pull ``individual.id_number`` forward. Being
    asked twice for the single most sensitive number a driver has is the kind
    of thing that costs trust, so we supply it and Stripe stops asking:
    onboarding only collects what is still in ``currently_due``.

    Write-only cuts one way. We cannot *read* ``individual.id_number`` back
    (that is why Spinr had to start collecting it), but it has always been a
    valid *request* parameter — see ``params/_account_update_params.py``.

    This is the second and last place that decrypts ``drivers.sin``, and it is
    materially different from the admin reveal: the plaintext goes straight to
    Stripe over TLS and is never returned to a client, logged, or re-stored.

    Best-effort by design. A driver setting up payouts must not be blocked
    because a pre-fill failed — the worst case is Stripe asking them for it,
    which is exactly where we were before. Returns a short status string for
    the caller to report; never raises.
    """
    if not driver.get("sin"):
        return "no_sin_on_file"

    # Skip Stripe entirely when our mirror already says this account holds an
    # id_number. /stripe-account-session is called repeatedly by the WebView's
    # fetchClientSecret and must stay cheap — without this, every mint paid an
    # Account.retrieve round-trip long after the first one settled the matter.
    # Guarded on the account matching the driver's current row: a repaired
    # (freshly re-created) account carries a different id than the stale
    # in-memory dict, so it always takes the full path below.
    if driver.get("stripe_id_number_provided") and driver.get("stripe_account_id") == account_id:
        return "already_provided"

    try:
        account = await asyncio.to_thread(stripe.Account.retrieve, account_id, api_key=stripe_secret)
        if ((account.get("individual") or {}).get("id_number_provided")) is True:
            # Stripe already has one — from its own form, or from a previous
            # run of this. Writing again would be a pointless round-trip.
            await _mirror_id_number_provided(driver["id"], account_id)
            return "already_provided"

        plain = await _vault_decrypt(str(driver["sin"]), "sin_stripe_prefill")
        # _vault_decrypt hands back its input when it cannot decrypt, so an
        # unchecked call would send Stripe a UUID as somebody's SIN.
        if not plain or plain == str(driver["sin"]) or not (len(plain.strip()) == 9 and plain.strip().isdigit()):
            logger.error(
                "[SIN-PREFILL] stored SIN did not decrypt to a canonical 9-digit value; skipping Stripe pre-fill",
                extra={"driver_id": driver["id"]},
            )
            return "decrypt_failed"

        await asyncio.to_thread(
            stripe.Account.modify,
            account_id,
            individual={"id_number": plain.strip()},
            api_key=stripe_secret,
        )
        await _mirror_id_number_provided(driver["id"], account_id)
        return "prefilled"
    except Exception:
        # Loud, but not fatal. exc_info carries the Stripe error; the SIN is
        # never in it because we never put it in a message.
        logger.error(
            "[SIN-PREFILL] could not pre-fill individual.id_number; the driver will be asked by Stripe",
            exc_info=True,
            extra={"driver_id": driver["id"], "stripe_account_id": account_id},
        )
        return "failed"


async def with_account_repair(driver: dict, user: dict, stripe_secret: str, op, *, before=None):
    """Run ``op(account_id)``, retiring and re-creating a stranded account once.

    Shared by both onboarding entry points (hosted AccountLink and embedded
    AccountSession). The mode pre-check inside :func:`_ensure_stripe_account`
    catches *stamped* rows for free; this catches the unstamped ones — the
    entire pre-migration-286 population — when Stripe answers
    ``resource_missing``/``PermissionError`` for the account we addressed.

    Both callers are the driver actively asking to set up payouts, which is
    why recovering in place is right here: the alternative is an error they can
    do nothing about. Any other Stripe error propagates untouched.
    """
    account_id = await _ensure_stripe_account(driver, user, stripe_secret)
    # `before` runs against whichever account the op will actually address —
    # including the replacement below, which is a brand-new account and needs
    # the pre-fill just as much as the original did.
    if before is not None:
        await before(account_id)
    try:
        return account_id, op(account_id)
    except Exception as exc:
        if not is_missing_on_key(exc, account_id):
            raise
        logger.warning(
            "Stripe Connect account not reachable on the current key — retiring and re-creating",
            extra={"driver_id": driver["id"], "superseded_account_id": account_id},
        )
        await _kyc.retire_stripe_account(driver, account_id, reason="resource_missing")
        fresh = await _create_stripe_account(
            {**driver, "stripe_account_id": None}, user, stripe_secret, superseded=account_id
        )
        if before is not None:
            await before(fresh)
        # One retry only. If the fresh account also fails, the key or the
        # platform is wrong, not this row — let it surface.
        return fresh, op(fresh)


async def _create_stripe_account(driver: dict, user: dict, stripe_secret: str, *, superseded: str | None = None) -> str:
    """Create the driver's CA individual Express account and persist the id."""
    # Idempotency key keyed on the driver makes concurrent / rapid-retry creates
    # converge on ONE Express account instead of leaking duplicate orphans —
    # the embedded onboarding's fetchClientSecret can fire this twice before the
    # first write lands. It also lets a retry recover a create whose DB write
    # below failed (same key → same account within Stripe's 24h window).
    #
    # When replacing a retired account the key also carries the superseded id:
    # replaying the plain `connect-acct-` key inside Stripe's 24 h window would
    # hand back the very account we just retired.
    idem = f"connect-acct-{driver['id']}" if not superseded else f"connect-acct-{driver['id']}-{superseded}"
    account = stripe.Account.create(
        type="express",
        country="CA",
        email=user.get("email"),
        capabilities={"transfers": {"requested": True}},
        business_type="individual",
        api_key=stripe_secret,
        idempotency_key=idem,
    )
    try:
        await db_supabase.update_one(
            "drivers",
            {"id": driver["id"]},
            {
                "stripe_account_id": account.id,
                # Stamp from the object Stripe returned, not the key we sent —
                # evidence over inference. Lets a future key rotation be
                # detected without a Stripe round-trip.
                "stripe_account_id_mode": object_mode(account) or key_mode(stripe_secret),
            },
        )
    except Exception as e:
        # Never return an unpersisted account id — payouts read the DB column, so
        # a lost write would strand the driver on an account nothing points to.
        # Surface loudly; a retry re-creates with the same idempotency key and
        # converges on this same account, then persists it.
        logger.error("Failed to persist stripe_account_id", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not start verification. Please try again.") from e
    return account.id


@router.post("/stripe-onboard")
async def onboard_stripe(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    user = await db_supabase.get_user_by_id(current_user.get("id"))
    if not driver or not user:
        raise HTTPException(status_code=404, detail="Driver/User profile not found")

    # Hard gate: no Stripe onboarding without a SIN on file. Applied before
    # the mock/dev branch too, so dev behaves like production.
    _require_sin_for_onboarding(driver)

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        return {"url": "https://spinr-demo-onboard.com", "mock": True}

    try:
        # NB: no _ensure_stripe_account call here — with_account_repair below
        # makes it, and calling it twice would create two Express accounts.
        #
        # Build return/refresh URLs from the externally-routable API host
        # (Cloudflare CNAME), NOT the app_settings dict. The dict has no
        # "base_url" key, so the old code always fell back to localhost:8000 —
        # which is why drivers landed on http://localhost:8000/api/drivers/...
        # after finishing Stripe's hosted flow. An explicit app_settings
        # "base_url" still wins if an operator sets one.
        try:
            from ...core.config import settings as _config
        except ImportError:
            from core.config import settings as _config  # type: ignore
        api_base = (settings.get("base_url") or _config.PUBLIC_API_BASE_URL).rstrip("/")

        def _account_link(acct: str):
            return stripe.AccountLink.create(
                account=acct,
                refresh_url=f"{api_base}/api/v1/drivers/stripe-refresh",
                return_url=f"{api_base}/api/v1/drivers/stripe-return",
                type="account_onboarding",
                # Pull everything Stripe will *eventually* require into this session —
                # most importantly the SIN (individual.id_number), which for a CA
                # Express individual account is "eventually_due" and is otherwise
                # skipped at initial onboarding. future_requirements="include" also
                # pulls in threshold-gated requirements (Stripe often defers the full
                # SIN until a CAD payout volume threshold). Needed for T4A / CRA
                # platform reporting (Income Tax Act Part XX).
                collection_options={
                    "fields": "eventually_due",
                    "future_requirements": "include",
                },
                api_key=stripe_secret,
            )

        # Hand Stripe the SIN we already hold BEFORE minting the link, so the
        # `eventually_due` collection above no longer surfaces id_number and
        # the driver is not asked for it a second time.
        account_id, account_link = await with_account_repair(
            driver,
            user,
            stripe_secret,
            _account_link,
            before=lambda acct: prefill_sin_to_stripe(driver, acct, stripe_secret),
        )
        # The real onboarded gate is now stripe_details_submitted, set by
        # the account.updated webhook handler in services/stripe_kyc_sync.py.
        # We used to flip stripe_account_onboarded=True here optimistically,
        # which mis-classified every driver who abandoned Stripe's hosted
        # flow halfway through as fully onboarded. Removed.
        return {"url": account_link.url, "mock": False}
    except HTTPException:
        # Preserve the helper's 502 (e.g. failed stripe_account_id persist)
        # instead of masking it as a generic 500.
        raise
    except Exception as e:
        logger.error("Stripe onboarding error", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.") from e


@router.post("/stripe-sync")
async def stripe_sync_status(current_user: dict = Depends(get_current_user)) -> dict:
    """Pull the driver's LIVE Stripe Connect status (Account.retrieve) and
    mirror it onto the drivers row, then return the verification state.

    Called by the driver app immediately after returning from Stripe's hosted
    onboarding so the UI reflects acceptance right away — without waiting on the
    account.updated webhook, which can be delayed or, if the Connect webhook
    isn't configured, never arrive at all."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    try:
        from ...services.stripe_kyc_sync import refresh_driver_kyc
    except ImportError:
        from services.stripe_kyc_sync import refresh_driver_kyc  # type: ignore

    # Opt in to retiring an unreachable account — the driver is here to set
    # up payouts, so detaching a dead one is what unblocks them.
    result = await refresh_driver_kyc(driver, retire_if_unreachable=True)
    status = result.get("status")
    if status == "no_stripe_account":
        # Driver hasn't started onboarding yet — not an error, just not set up.
        return {"synced": False, "onboarded": False, "payouts_enabled": False, "requirements_due": []}
    if status == "account_not_on_key":
        # The account was retired: it is not reachable on the running Stripe
        # key (test→live rotation). Report the same shape as "never set up"
        # rather than 502ing — the driver's next step is the normal "Set up
        # payouts" flow, which now mints a fresh account. `reonboarding_required`
        # lets the app explain WHY the previously-linked bank disappeared.
        return {
            "synced": True,
            "onboarded": False,
            "details_submitted": False,
            "payouts_enabled": False,
            "requirements_due": [],
            "reonboarding_required": True,
        }
    if status != "ok":
        # stripe_not_configured / stripe_error — surface it (don't silently
        # report "not onboarded") so the app can retry instead of masking it.
        raise HTTPException(status_code=502, detail="Could not sync verification status. Please try again.")

    updates = result.get("updates") or {}
    return {
        "synced": True,
        "onboarded": bool(updates.get("stripe_account_onboarded")),
        "details_submitted": bool(updates.get("stripe_details_submitted")),
        "payouts_enabled": bool(updates.get("stripe_payouts_enabled")),
        "id_number_provided": bool(updates.get("stripe_id_number_provided")),
        "requirements_due": list(updates.get("stripe_requirements_due") or []),
    }


# Driver-app deep link (scheme defined in driver-app/app.config.ts: SCHEME).
_STRIPE_RETURN_DEEP_LINK = "spinr-driver://driver/payout"


def _stripe_bounce_page(deep_link: str, heading: str, body: str) -> str:
    """HTML interstitial that bounces the system browser back into the Spinr
    Driver app. A raw 302 to a custom scheme is unreliable on iOS Safari, so we
    auto-attempt the deep link and also render a manual button as a fallback."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spinr Driver</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f0f10;color:#fff;
       display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;text-align:center}}
  .card{{padding:32px;max-width:360px}}
  h1{{font-size:20px;margin:0 0 8px}} p{{color:#bdbdbd;margin:0 0 24px}}
  a.btn{{display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
         padding:14px 24px;border-radius:12px;font-weight:600}}
</style></head>
<body><div class="card">
  <h1>{heading}</h1><p>{body}</p>
  <a class="btn" href="{deep_link}">Return to Spinr Driver</a>
</div>
<script>setTimeout(function(){{window.location.replace("{deep_link}")}},150);</script>
</body></html>"""


@router.get("/stripe-return", include_in_schema=False)
async def stripe_return() -> HTMLResponse:
    """Stripe redirects the browser here when the driver finishes (or exits)
    hosted onboarding. Bounces back into the app, which re-reads KYC status on
    focus. The authoritative onboarding gate is the account.updated webhook
    (services/stripe_kyc_sync.py) — this endpoint is UX only, never a trust gate."""
    return HTMLResponse(
        _stripe_bounce_page(
            f"{_STRIPE_RETURN_DEEP_LINK}?stripe=return",
            "Verification submitted",
            "You can return to the Spinr Driver app now.",
        )
    )


@router.get("/stripe-refresh", include_in_schema=False)
async def stripe_refresh() -> HTMLResponse:
    """Stripe redirects here when an onboarding link expires or is reopened.
    Sends the driver back into the app, which restarts onboarding on demand."""
    return HTMLResponse(
        _stripe_bounce_page(
            f"{_STRIPE_RETURN_DEEP_LINK}?stripe=refresh",
            "Link expired",
            "Return to the Spinr Driver app and tap Connect again to continue.",
        )
    )


@router.post("/stripe-account-session")
async def stripe_account_session(current_user: dict = Depends(get_current_user)) -> dict:
    """Mint a single-use Stripe AccountSession client secret for the embedded
    account-onboarding component (Option B — fully in-app, no browser redirect).

    The SIN we collected in-app is pre-filled onto the connected account before
    the session is minted (prefill_sin_to_stripe), so the embedded form does not
    ask for it again. Only last-4 + status is mirrored back via the
    account.updated webhook. Called repeatedly by the WebView's
    fetchClientSecret callback, so it must stay cheap and idempotent."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    user = await db_supabase.get_user_by_id(current_user.get("id"))
    if not driver or not user:
        raise HTTPException(status_code=404, detail="Driver/User profile not found")

    # Same hard gate as the hosted-link flow: SIN before Stripe.
    _require_sin_for_onboarding(driver)

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        # Dev/demo: Stripe not configured. 503 lets the WebView surface a clean
        # "not available yet" state instead of a blank embedded component.
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    try:

        def _account_session(acct: str):
            return stripe.AccountSession.create(
                account=acct,
                components={
                    "account_onboarding": {
                        "enabled": True,
                        # Let the driver add their payout bank account inside the
                        # same embedded flow.
                        "features": {"external_account_collection": True},
                    },
                },
                api_key=stripe_secret,
            )

        # Same repair posture as the hosted-link flow: an unstamped account
        # stranded by a key rotation is retired and re-created here rather than
        # 502ing a driver who is actively trying to set up payouts.
        _, session = await with_account_repair(
            driver,
            user,
            stripe_secret,
            _account_session,
            before=lambda acct: prefill_sin_to_stripe(driver, acct, stripe_secret),
        )
        return {"client_secret": session.client_secret}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Stripe AccountSession error", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not start verification. Please try again.") from e


# Plain template (NOT an f-string) so the embedded JS braces stay literal.
# __SPINR_PK__ is substituted with json.dumps(publishable_key) at render time.
_STRIPE_EMBEDDED_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>Spinr Driver — Verification</title>
<style>
  html,body{margin:0;height:100%;background:#0f0f10;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  #msg{color:#bdbdbd;padding:28px;text-align:center;font-size:15px}
  #dbg{color:#6b7280;padding:6px 12px;text-align:center;font-size:11px;
    font-family:ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
  #container{padding:0 4px}
</style></head>
<body>
  <div id="msg">Loading secure verification…</div>
  <div id="container"></div>
  <div id="dbg"></div>
  <script>
    var PK = __SPINR_PK__;
    var mounted = false;
    function post(m){ if(window.ReactNativeWebView){ window.ReactNativeWebView.postMessage(m); } }
    // Surface progress to BOTH the RN host (debug strip) and on-page, so the
    // same URL opened directly in a browser shows exactly where it stalls.
    function stage(s){ post("stage:" + s); var d=document.getElementById("dbg"); if(d){ d.textContent="stage: "+s; } }
    function fail(c){ post("error:" + c); var d=document.getElementById("dbg"); if(d){ d.textContent="error: "+c; } }
    async function fetchClientSecret(){
      stage("fetch-start");
      var token = window.__SPINR_TOKEN || "";
      if(!token){ fail("no-token"); throw new Error("no token"); }
      var res;
      try{
        res = await fetch("/api/v1/drivers/stripe-account-session", {
          method: "POST",
          headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" }
        });
      }catch(e){ fail("fetch-network"); throw e; }  // network/CSP block — previously silent
      if(!res.ok){ fail(String(res.status)); throw new Error("account_session " + res.status); }
      var data = await res.json();
      stage("fetch-ok");
      return data.client_secret;
    }
    function mount(){
      stage("mounting");
      try{
        var instance = StripeConnect.init({
          publishableKey: PK,
          fetchClientSecret: fetchClientSecret,
          appearance: { variables: {
            colorBackground: "#0f0f10", colorText: "#ffffff",
            colorPrimary: "#16a34a", colorSecondaryText: "#bdbdbd" } }
        });
        stage("init-ok");
        var onboarding = instance.create("account-onboarding");
        // Mirror the hosted flow: collect eventually/future-due fields up front
        // so the SIN (individual.id_number) is requested during onboarding.
        onboarding.setCollectionOptions({ fields: "eventually_due", futureRequirements: "include" });
        onboarding.setOnExit(function(){ post("exit"); });
        document.getElementById("msg").style.display = "none";
        document.getElementById("container").appendChild(onboarding);
        mounted = true;
        stage("mounted");
      }catch(e){ fail("init"); }
    }
    if(!PK){ stage("no-pk"); document.getElementById("msg").textContent =
      "Payouts setup is not available yet. Please try again later."; }
    else {
      stage("script-init");
      window.StripeConnect = window.StripeConnect || {};
      // connect.js calls StripeConnect.onLoad once the script finishes loading.
      if(window.StripeConnect && typeof window.StripeConnect.init === "function"){ mount(); }
      else { stage("awaiting-connectjs"); window.StripeConnect.onLoad = mount; }
      // Watchdog: if connect.js never loads (CSP/network block), onLoad never
      // fires and the page would spin forever — surface it instead.
      setTimeout(function(){ if(!mounted){ fail("connectjs-timeout"); } }, 12000);
    }
  </script>
  <script src="https://connect-js.stripe.com/v1.0/connect.js" async onerror="fail('connectjs-load')"></script>
</body></html>"""


def _stripe_embedded_page(publishable_key: str) -> str:
    """HTML shell that mounts Stripe's embedded account-onboarding component.

    Served same-origin with the API so the in-page fetchClientSecret POST to
    /stripe-account-session needs no CORS. The page carries only the publishable
    key (public by design); the driver's bearer token is injected at runtime by
    the WebView via injectedJavaScriptBeforeContentLoaded (window.__SPINR_TOKEN),
    never placed in the URL or server logs. setCollectionOptions mirrors the
    hosted flow so the SIN (eventually/future-due) is collected up front."""
    # The publishable key comes from the admin-writable app_settings table, so
    # treat it as untrusted: plain json.dumps does NOT escape </script>, which
    # would let a tampered value break out of the inline <script> (stored XSS
    # that could read window.__SPINR_TOKEN). Escape the HTML-significant chars
    # to their \uXXXX forms — still a valid JS string literal, no breakout.
    safe_pk = json.dumps(publishable_key)
    for _ch, _esc in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),  # JS line separator — illegal raw in a string literal
        ("\u2029", "\\u2029"),  # JS paragraph separator
    ):
        safe_pk = safe_pk.replace(_ch, _esc)
    return _STRIPE_EMBEDDED_HTML.replace("__SPINR_PK__", safe_pk)


@router.get("/stripe-embedded", include_in_schema=False)
async def stripe_embedded() -> HTMLResponse:
    """Public HTML host for the embedded onboarding WebView. Contains no secret
    (publishable key only); all privileged work goes through the authenticated
    /stripe-account-session endpoint the page calls back into."""
    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    publishable_key = settings.get("stripe_publishable_key", "")
    return HTMLResponse(_stripe_embedded_page(publishable_key))


@router.post("/bank-account")
async def save_bank_account(req: BankAccountCreate, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account_data = req.dict()
    account_data["id"] = str(uuid.uuid4())
    account_data["driver_id"] = driver["id"]
    acc_num = account_data.pop("account_number")

    # Canadian routing number for Stripe is generally 0 + Institution (3) + Transit (5)
    # Ensure zero-padding if needed
    inst = req.institution_number.zfill(3)
    trans = req.transit_number.zfill(5)
    account_data["routing_number"] = f"0{inst}{trans}"

    account_data["account_last4"] = acc_num[-4:] if len(acc_num) >= 4 else acc_num
    account_data["stripe_bank_id"] = None  # Would be populated after calling Stripe's API
    account_data["currency"] = "cad"
    account_data["country"] = "CA"
    account_data["is_verified"] = False
    account_data["created_at"] = datetime.now(timezone.utc).isoformat()

    await db_supabase.delete_many("bank_accounts", {"driver_id": driver["id"]})
    await db_supabase.insert_one("bank_accounts", account_data)

    return {"success": True, "bank_account": serialize_doc(account_data)}


@router.delete("/bank-account")
async def delete_bank_account(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    await db_supabase.delete_many("bank_accounts", {"driver_id": driver["id"]})
    return {"success": True}


_GST_BN_RE = re.compile(r"^\d{9}(RT\d{4})?$")


def _gst_on_file(driver: dict) -> bool:
    """True when the driver has a CRA Business Number on file in a valid format
    (9-digit BN, optionally with the RTxxxx GST/HST program suffix).

    Rideshare drivers must register for GST/HST with the CRA from their first
    fare — the $30k small-supplier exemption does NOT apply to ride-sharing
    (Excise Tax Act "taxi business" definition). So a valid BN is a hard
    precondition for any payout, scheduled or instant."""
    bn = (driver.get("gst_bn") or "").replace(" ", "").upper()
    return bool(_GST_BN_RE.match(bn))


def _require_gst_for_payout(driver: dict) -> None:
    """Block payout until the driver's GST/HST Business Number is on file."""
    if not _gst_on_file(driver):
        raise HTTPException(
            status_code=422,
            detail=(
                "A valid GST/HST Business Number is required before you can be paid. "
                "Rideshare drivers must register for GST/HST with the CRA from their "
                "first fare. Add your 9-digit Business Number in Payouts to continue."
            ),
        )


def _sin_on_file(driver: dict) -> bool:
    """True when the driver's SIN is captured somewhere we can rely on for
    T4A filing: our own Vault-encrypted column (new flow — collected in-app
    before Stripe onboarding), or Stripe's individual.id_number (legacy flow —
    drivers who entered it inside Stripe's hosted form before we started
    collecting it ourselves, mirrored as stripe_id_number_provided)."""
    return bool(driver.get("sin")) or bool(driver.get("stripe_id_number_provided"))


def _require_sin_for_onboarding(driver: dict) -> None:
    """SIN must be on file BEFORE Stripe onboarding starts — enforced, not
    just ordered in the app checklist. The onboarding link pre-fills Stripe
    with our copy (prefill_sin_to_stripe); minting a link without a SIN on
    file would push the question back into Stripe's form and leave us with
    no copy for the T4A. Legacy drivers whose SIN already lives at Stripe
    (stripe_id_number_provided) pass — asking them again would be worse."""
    if not _sin_on_file(driver):
        raise HTTPException(
            status_code=422,
            detail=(
                "Add your SIN before connecting your payout account. It is "
                "needed for your T4A tax slip and is passed to Stripe so you "
                "are only asked once."
            ),
        )


def _require_sin_for_payout(driver: dict) -> None:
    """Block payout until the driver's SIN is on file.

    CRA T4A / platform reporting (Income Tax Act Part XX) requires the SIN,
    so it is a hard precondition for payout — stricter than Stripe, which
    leaves the full SIN "eventually due" until a payout-volume threshold.
    Either our Vault copy or Stripe's (legacy) satisfies the gate; the new
    in-app collection path fills ours before Stripe onboarding even starts."""
    if not _sin_on_file(driver):
        raise HTTPException(
            status_code=422,
            detail=(
                "Your SIN must be on file before you can be paid. Add it in "
                "Payouts — it is needed for your T4A tax slip and is stored "
                "encrypted."
            ),
        )


async def _require_instant_payout_enabled(driver: dict) -> None:
    """Block instant payout when the driver's service area has it disabled.

    Per-service-area kill switch (migration 314). Drivers without a
    service_area_id are allowed through — the kill switch is opt-out per
    market, not a global default-off."""
    sa_id = driver.get("service_area_id")
    if not sa_id:
        return
    sa_rows = await db_supabase.get_rows("service_areas", {"id": sa_id}, limit=1)
    if sa_rows and sa_rows[0].get("instant_payout_enabled") is False:
        raise HTTPException(
            status_code=403,
            detail=(
                "Instant payouts are not available in your service area. "
                "Your earnings are paid out automatically every Sunday."
            ),
        )


@router.post("/payouts")
async def request_payout(
    current_user: dict = Depends(get_current_user),
):
    """Standard cashout is disabled — payouts are now Spinr-controlled and
    run automatically every Sunday for all eligible drivers (>= $10 balance).
    Drivers can still use instant payouts (fee-bearing) for early access."""
    # Old app builds surface this string in a toast that clamps at 140 chars
    # (shared/utils/toastMessage.ts) — keep it under that, and don't advertise
    # instant payout while the driver app has no instant-payout UI to tap.
    raise HTTPException(
        status_code=410,
        detail=(
            "Cash out has been replaced by automatic weekly payouts — your earnings are sent to your bank every Sunday."
        ),
    )


# Kept for reference / rollback — the original handler is fully replaced by
# the auto-payout loop (utils/auto_payout.py). Remove after confirming the
# weekly batch is stable.
_STANDARD_CASHOUT_DISABLED = True


@router.post("/payouts/_legacy", include_in_schema=False)
@idempotent_endpoint(scope="driver_payout")
async def _request_payout_legacy(
    req: PayoutRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if _STANDARD_CASHOUT_DISABLED:
        raise HTTPException(status_code=410, detail="Manual payouts disabled")
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # CRA: rideshare drivers must be GST/HST-registered from their first fare.
    _require_gst_for_payout(driver)
    # CRA T4A: the SIN (held by Stripe) must be on file before any payout.
    _require_sin_for_payout(driver)

    balance = await earnings.get_driver_balance(current_user)
    if req.amount > Decimal(balance.get("payable_balance", "0")):
        raise HTTPException(status_code=400, detail="Insufficient funds")

    stripe_account_id = driver.get("stripe_account_id")
    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )

    if not stripe_account_id and not account:
        raise HTTPException(status_code=400, detail="No bank account linked")

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    stripe_payout_id = None
    payout_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # WS-7 (finding 4): reserve-then-transfer. Insert the payout row with
    # status='reserved' BEFORE the Stripe Transfer so the partial unique
    # index (migration 250) blocks a concurrent request from also reserving.
    # The balance deduction in get_driver_balance already excludes only
    # 'reversed'/'failed', so a 'reserved' row reduces the payable balance
    # for any racing request that gets past the index.
    payout = {
        "id": payout_id,
        "driver_id": driver["id"],
        "amount": req.amount,
        "status": "reserved",
        "stripe_payout_id": None,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_last4") if account else "****",
        "created_at": now_iso,
    }
    try:
        await db_supabase.insert_one("payouts", payout)
    except Exception as reserve_exc:
        _exc_str = str(reserve_exc).lower()
        if "unique" in _exc_str or "duplicate" in _exc_str or "23505" in _exc_str:
            raise HTTPException(
                status_code=409,
                detail="A payout is already in progress. Please wait for it to complete.",
            ) from reserve_exc
        logger.exception("Failed to reserve payout row")
        raise HTTPException(status_code=500, detail="Payout failed. Please try again.") from reserve_exc

    if stripe_secret and stripe_account_id:
        try:
            transfer = await asyncio.to_thread(
                lambda: stripe.Transfer.create(
                    amount=dollars_to_cents(req.amount),
                    currency="cad",
                    destination=stripe_account_id,
                    api_key=stripe_secret,
                    idempotency_key=f"payout-transfer-{payout_id}",
                )
            )
            stripe_payout_id = transfer.id
        except Exception as e:
            logger.exception("Stripe transfer failed for driver payout")
            try:
                await db_supabase.update_one(
                    "payouts",
                    {"id": payout_id},
                    {
                        "status": "failed",
                        "failure_reason": str(e)[:500],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                logger.exception("Failed to mark reserved payout as failed")
            raise HTTPException(
                status_code=500,
                detail="Payout failed. Please contact support.",
            ) from e

    # Terminal write: mark as completed (Stripe transferred) or pending (no Stripe).
    final_status = RideStatus.COMPLETED if stripe_payout_id else "pending"
    try:
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {
                "status": final_status,
                "stripe_payout_id": stripe_payout_id,
                "stripe_transfer_id": stripe_payout_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as terminal_exc:
        # Terminal write failed after Stripe transferred money. Reverse the
        # transfer so the books match — mirrors request_instant_payout.
        if stripe_payout_id:
            logger.exception("Terminal write failed after Stripe transfer; reversing")
            reversal_ok = await _attempt_transfer_reversal(stripe_payout_id, stripe_secret, payout_id)
            new_status = "reversed" if reversal_ok else "stranded"
            try:
                await db_supabase.update_one(
                    "payouts",
                    {"id": payout_id},
                    {"status": new_status, "requires_manual_review": not reversal_ok},
                )
            except Exception:
                logger.exception("Failed to update payout row after reversal")
            raise HTTPException(
                status_code=500,
                detail="Payout failed. Please try again or contact support.",
            ) from terminal_exc
        raise HTTPException(
            status_code=500,
            detail="Payout failed. Please try again.",
        ) from terminal_exc

    payout["status"] = final_status
    payout["stripe_payout_id"] = stripe_payout_id
    return {"success": True, "payout": serialize_doc(payout)}


async def _attempt_transfer_reversal(transfer_id: str, stripe_secret: str, payout_id: str) -> bool:
    """Best-effort compensating reversal of a Stripe Transfer.

    Called when the payout step of an instant payout fails after the
    transfer step has already moved money to the connect account. Uses an
    idempotency key tied to the payout row so a retry of this same payout
    never issues a second reversal. Returns True iff Stripe accepted the
    reversal — the caller writes "reversed" vs "stranded" accordingly.
    """
    try:
        await asyncio.to_thread(
            lambda: stripe.Transfer.create_reversal(
                transfer_id,
                api_key=stripe_secret,
                idempotency_key=f"instant-payout-reversal-{payout_id}",
            )
        )
        return True
    except Exception:
        # logger.exception captures the underlying Stripe error (transfer
        # state, amount mismatch, etc.) without leaking the transfer id
        # back to the client. The payout row will be flagged for manual
        # review so ops can chase it down.
        logger.exception("Transfer reversal failed for stranded instant payout")
        return False


@router.post("/payouts/instant")
@idempotent_endpoint(scope="driver_instant_payout")
async def request_instant_payout(
    req: InstantPayoutRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Same-day cashout via Stripe Instant Payout (Uber-style Instant Pay).

    Money-safety contract:
      Instant payout is two Stripe calls — Transfer (platform → connect),
      then Payout(method=instant). If the second fails after the first
      succeeds, money is stranded in the connect account. To keep the
      books consistent:
        1. Generate the payout_id BEFORE the first Stripe call so every
           Stripe call carries a per-payout idempotency key — a retry on
           the same payout row never double-transfers or double-pays-out.
        2. INSERT the payout row immediately after the transfer succeeds
           with status='transfer_completed' and stripe_transfer_id set;
           a crash between transfer and payout still leaves a recoverable
           DB record.
        3. If the payout step fails, attempt Transfer.create_reversal().
           On reversal success → row status='reversed'. On reversal
           failure → status='stranded' and requires_manual_review=true so
           the ops dashboard surfaces it.

    Fee model is regulator-friendly: shown in the receipt, separate line
    item, never hidden. Standard scheduled payouts remain free (see
    request_payout).
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Per-service-area kill switch: ops can disable instant payouts in
    # specific markets without a code deploy (migration 314).
    await _require_instant_payout_enabled(driver)

    # CRA: rideshare drivers must be GST/HST-registered from their first fare.
    _require_gst_for_payout(driver)
    # CRA T4A: the SIN (held by Stripe) must be on file before any payout.
    _require_sin_for_payout(driver)

    fee = compute_instant_payout_fee(req.amount)
    net_amount = req.amount - fee
    if net_amount <= Decimal("0"):
        # Defence in depth: the request schema's ge=5.00 already prevents
        # this since the floor fee is 0.50, but guard anyway in case fee
        # config changes later.
        raise HTTPException(status_code=400, detail="Fee exceeds payout amount")

    balance = await earnings.get_driver_balance(current_user)
    if req.amount > Decimal(balance.get("payable_balance", "0")):
        raise HTTPException(status_code=400, detail="Insufficient funds")

    stripe_account_id = driver.get("stripe_account_id")
    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )

    # Instant Payout requires a Stripe Connect account with a debit-card
    # external_account on file — Stripe rejects bank-only setups with a
    # generic 400. Surface the eligibility check up-front so the driver
    # sees a clear message instead of a Stripe error.
    if not stripe_account_id:
        raise HTTPException(
            status_code=400,
            detail="Instant payout requires Stripe Connect onboarding. "
            "Please complete onboarding from the Payouts screen.",
        )

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=503, detail="Payouts temporarily unavailable")

    payout_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # WS-7 (finding 4): reserve-then-transfer. Insert the payout row BEFORE
    # any Stripe call so the partial unique index (migration 250) blocks a
    # concurrent instant-payout request from also reserving.
    payout = {
        "id": payout_id,
        "driver_id": driver["id"],
        "amount": req.amount,
        "fee": fee,
        "net_amount": net_amount,
        "payout_type": "instant",
        "status": "reserved",
        "stripe_transfer_id": None,
        "stripe_payout_id": None,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_last4") if account else "****",
        "created_at": now_iso,
    }
    try:
        await db_supabase.insert_one("payouts", payout)
    except Exception as reserve_exc:
        _exc_str = str(reserve_exc).lower()
        if "unique" in _exc_str or "duplicate" in _exc_str or "23505" in _exc_str:
            raise HTTPException(
                status_code=409,
                detail="A payout is already in progress. Please wait for it to complete.",
            ) from reserve_exc
        logger.exception("Failed to reserve instant payout row")
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again.",
        ) from reserve_exc

    # ── Step 1: Transfer platform → connect account ───────────────────
    try:
        transfer = await asyncio.to_thread(
            lambda: stripe.Transfer.create(
                amount=dollars_to_cents(req.amount),
                currency="cad",
                destination=stripe_account_id,
                api_key=stripe_secret,
                idempotency_key=f"instant-payout-transfer-{payout_id}",
            )
        )
        stripe_transfer_id = transfer.id
    except Exception as e:
        logger.exception("Stripe transfer step failed for instant payout")
        try:
            await db_supabase.update_one(
                "payouts",
                {"id": payout_id},
                {
                    "status": "failed",
                    "failure_reason": str(e)[:500],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to mark reserved instant payout as failed")
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from e

    # Update reserved → transfer_completed so a crash between transfer and
    # payout still leaves a recoverable record of the in-flight transfer.
    try:
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {
                "status": "transfer_completed",
                "stripe_transfer_id": stripe_transfer_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as persist_exc:
        logger.exception("Failed to update instant payout to transfer_completed")
        reversal_ok = await _attempt_transfer_reversal(stripe_transfer_id, stripe_secret, payout_id)
        new_status = "reversed" if reversal_ok else "stranded"
        try:
            await db_supabase.update_one(
                "payouts",
                {"id": payout_id},
                {"status": new_status, "requires_manual_review": not reversal_ok},
            )
        except Exception:
            logger.error(
                "STRANDED instant payout — persist failed AND reversal status write failed. payout_id=%s driver_id=%s amount=%s",
                payout_id,
                driver["id"],
                req.amount,
            )
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from persist_exc

    # ── Step 2: Payout on connect account ─────────────────────────────
    try:
        # Stripe deducts its own ~1% fee from the platform side (separate
        # from the fee we charge the driver). Pass stripe_account so the
        # call runs in the connected account's context.
        payout_obj = await asyncio.to_thread(
            lambda: stripe.Payout.create(
                amount=dollars_to_cents(net_amount),
                currency="cad",
                method="instant",
                api_key=stripe_secret,
                stripe_account=stripe_account_id,
                idempotency_key=f"instant-payout-{payout_id}",
            )
        )
        stripe_payout_id = payout_obj.id
    except Exception as payout_exc:
        # Payout failed; reverse the transfer to keep funds on the platform
        # side. The row stays in DB either way — flagged for manual review
        # when reversal also fails so stranded money is visible to ops.
        logger.exception("Stripe payout step failed; attempting transfer reversal")
        reversal_ok = await _attempt_transfer_reversal(stripe_transfer_id, stripe_secret, payout_id)
        new_status = "reversed" if reversal_ok else "stranded"
        try:
            await db_supabase.update_one(
                "payouts",
                {"id": payout_id},
                {
                    "status": new_status,
                    "failure_reason": str(payout_exc)[:500],
                    "requires_manual_review": not reversal_ok,
                },
            )
        except Exception:
            # The row exists with status=transfer_completed; we couldn't
            # update it to reflect the failure. Log loudly — the partial
            # state is still recoverable from Stripe.
            logger.exception("Failed to flag instant payout row after payout failure")
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from payout_exc

    # ── Step 3: Mark row as completed ─────────────────────────────────
    try:
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {"status": RideStatus.COMPLETED, "stripe_payout_id": stripe_payout_id},
        )
    except Exception:
        # Money landed in the driver's bank but we couldn't flip the row
        # to "completed". The row stays as "transfer_completed" — a
        # follow-up reconciliation job will fix the status. Don't unwind
        # the payout (the driver has the money).
        logger.exception(
            "Failed to mark instant payout completed (money already disbursed)",
        )

    payout["status"] = RideStatus.COMPLETED
    payout["stripe_payout_id"] = stripe_payout_id
    return {"success": True, "payout": serialize_doc(payout)}


@router.get("/payouts/instant/quote")
async def get_instant_payout_quote(
    amount: Decimal = Query(..., ge=Decimal("5.00"), le=Decimal("5000.00")),
    current_user: dict = Depends(get_current_user),
):
    """Quote the fee + net for an instant payout before the driver confirms.

    Driver app shows: "Cash out $50.00 now — $0.75 fee, $49.25 to your bank"
    Reading the quote from the server (not computing it client-side) means
    a fee-schedule change rolls out without a mobile release.
    """
    fee = compute_instant_payout_fee(amount)
    net = amount - fee
    return {
        "amount": _money_str(amount),
        "fee": _money_str(fee),
        "net_amount": _money_str(net),
        "payout_type": "instant",
    }


@router.get("/payouts")
async def get_payout_history(
    limit: int = Query(20),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    filters: dict = {"driver_id": driver["id"]}
    # Business decision 2026-08-13 (docs/change-log/2026-08-13-blended-
    # lifetime-earnings.md): previous-app transfers (payout_type=
    # 'stripe_sync') stay in a driver's payout history PERMANENTLY — no more
    # time-limited "transition messaging." Hiding real payout rows after a
    # date would make the driver's own history look like it lost entries,
    # which is the same trust problem A31 fixed for trip counts. This used
    # to filter on utils.legacy_rides.previous_app_history_visible()
    # (PREVIOUS_APP_VISIBLE_UNTIL, 2026-08-31) — that helper still exists
    # and is still correct, it's just no longer called here.
    payouts = await db_supabase.get_rows(
        "payouts",
        filters,
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )
    return {"success": True, "payouts": [serialize_doc(p) for p in payouts]}


# ── Connected-account records (bank payouts + full ledger) ────────────────
#
# `payouts` above records what the PLATFORM sent this driver. The two
# endpoints below serve the other half — what happened INSIDE their Stripe
# connected account — from our own tables (migration 288), populated by
# services/stripe_connect_ledger_service.
#
# Reading from our DB rather than Stripe is the point: the driver's history
# stays available when Stripe is slow or unreachable, survives their account
# being superseded, and costs no API quota per screen view.
#
# NEITHER is an income figure. The same dollar appears as a Transfer in
# `payouts`, as a Payout here, and as two ledger legs — see migration 288's
# header. T4A comes from routes/drivers/tax_exports.py, not from these.

_MAX_LEDGER_PAGE = 100


@router.get("/stripe/bank-payouts")
async def get_bank_payout_history(
    limit: int = Query(20, ge=1, le=_MAX_LEDGER_PAGE),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Money that left the driver's Stripe balance for their bank account.

    This is what a driver actually means by "did I get paid" — `payouts`
    tells them Spinr sent it, this tells them the bank received it, when it
    arrived, and why it failed if it did.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    rows = await db_supabase.get_rows(
        "driver_stripe_payouts",
        {"driver_id": driver["id"]},
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )
    return {
        "success": True,
        "bank_payouts": [
            {
                "id": r.get("id"),
                "amount": _money_str(r.get("amount") or 0),
                "currency": r.get("currency"),
                "status": r.get("status"),
                "method": r.get("method"),
                "arrival_date": r.get("arrival_date"),
                "failure_code": r.get("failure_code"),
                "failure_message": r.get("failure_message"),
                "bank_last4": r.get("bank_last4"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ],
    }


@router.get("/stripe/ledger")
async def get_connect_ledger(
    limit: int = Query(50, ge=1, le=_MAX_LEDGER_PAGE),
    offset: int = Query(0, ge=0),
    entry_type: str = Query(None, max_length=64),
    current_user: dict = Depends(get_current_user),
):
    """Full signed ledger for the driver's Stripe account.

    Amounts are SIGNED in Stripe's convention (credits +, debits -), so the
    client must render them as a statement, never sum them as earnings. A sum
    over a window is the driver's balance CHANGE, not their income.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    filters = {"driver_id": driver["id"]}
    if entry_type:
        filters["type"] = entry_type

    rows = await db_supabase.get_rows(
        "driver_stripe_ledger",
        filters,
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )
    return {
        "success": True,
        "signed_amounts": True,
        "entries": [
            {
                "id": r.get("id"),
                "type": r.get("type"),
                "amount": _money_str(r.get("amount") or 0),
                "fee": _money_str(r.get("fee") or 0),
                "net": _money_str(r.get("net") or 0),
                "currency": r.get("currency"),
                "status": r.get("status"),
                "source": r.get("source"),
                "description": r.get("description"),
                "available_on": r.get("available_on"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ],
    }
