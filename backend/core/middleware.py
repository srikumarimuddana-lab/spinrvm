import uuid
from urllib.parse import urlparse

import jwt
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from loguru import logger
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import settings
from utils.log_context import set_request_context
from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

# ── CSRF double-submit constants ─────────────────────────────────────
_CSRF_COOKIE = "csrf_token"
# Per-audience CSRF cookies: the admin dashboard and the company portal are the
# same browser origin, so a single shared cookie name collides when both
# sessions are active (whichever logged in last owns `csrf_token`, breaking the
# other's writes). Each browser surface uses its own cookie name and the
# double-submit header is validated against whichever one it matches. This does
# not weaken the protection: an attacker still cannot read either cookie to
# forge a matching header (SameSite=Strict also blocks cross-site sends).
_CSRF_COOKIE_COMPANY = "csrf_token_company"
_CSRF_COOKIES = (_CSRF_COOKIE, _CSRF_COOKIE_COMPANY)
_CSRF_HEADER = "x-csrf-token"
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# Paths that have no established session yet — no CSRF cookie exists.
# Pre-auth endpoints and server-to-server paths are exempt.
_CSRF_EXEMPT_EXACT = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/auth/send-otp",
        "/api/v1/auth/verify-otp",
        # Company-portal auth namespace mirrors the /api/v1/auth pre-auth
        # exemptions (send-otp is a browser-Origin POST with no csrf cookie yet).
        "/api/portal/auth/send-otp",
        "/api/portal/auth/verify-otp",
        "/api/portal/auth/send-email-otp",
        "/api/portal/auth/verify-email-otp",
        "/api/v1/auth/firebase",
        "/api/admin/auth/login",
        "/api/v1/stripe/webhook",
        # Stripe embedded onboarding: the driver app's WebView posts here from
        # an in-page fetch, which carries an Origin header (so it's not caught
        # by the native-app no-Origin exemption) but no csrf_token cookie. Safe
        # to exempt — the endpoint authenticates via the Bearer JWT, not a
        # cookie session, and CSRF only threatens cookie-based auth.
        "/api/v1/drivers/stripe-account-session",
    }
)
_CSRF_EXEMPT_PREFIXES = ("/ws/",)

# ── Firebase App Check Middleware ─────────────────────────────────────
# Enforcement is tied to ENV: on in production, off in development/staging.
# When off, missing or invalid tokens are logged but requests still go
# through — this lets dev builds (Expo Go, unregistered debug devices)
# reach the API without Firebase Console setup.
#
# IMPORTANT — before flipping ENV=production, complete these manual steps:
#   iOS  : register bundle ID with Apple DeviceCheck in
#          Firebase Console → App Check → Apps
#   Android: register package name with Play Integrity in
#          Firebase Console → App Check → Apps
# Until those steps are done, production devices will get 401s. For local
# dev builds, add a debug token in Firebase Console → App Check → Apps →
# overflow menu → "Manage debug tokens".

# Paths that must never require App Check:
#   - WebSocket connections (no HTTP headers)
#   - OpenAPI docs served by FastAPI
#   - Health / readiness probes
#   - Admin dashboard API (/api/admin/*): the dashboard is a Next.js web
#     app, not a Firebase-registered mobile build, so it cannot attach a
#     X-Firebase-AppCheck header. Admin endpoints are already gated by the
#     admin JWT (get_admin_user dependency, see routes/admin/__init__.py)
#     and the login endpoint is rate-limited (5/min/IP in
#     routes/admin/auth.py), so the attack surface is bounded. Without this
#     exemption /api/admin/auth/login returns 401 "App Check token
#     required" before the login handler runs, and the dashboard at
#     admin-spinr.spinr.ca can never authenticate.
_APP_CHECK_EXEMPT_PREFIXES = (
    "/ws/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/api/admin/",
    # Ride-offer fare banner: the OS (Notifee BigPicture) fetches this image
    # with no App Check header. It is authorised instead by a short-TTL,
    # ride+driver-bound HMAC token in the query string (routes/offer_card.py).
    "/api/v1/offer-cards/",
    # Email logo: mail clients (and Gmail's image proxy) fetch the branded-email
    # header image with no App Check header. Unlike the offer card there is no
    # token, because there is nothing to authorise — it is a single public brand
    # asset with no PII and no per-user dimension (routes/branding.py).
    "/api/v1/branding/",
    # Stripe embedded onboarding: the driver app loads these inside a WebView
    # (Stripe's connect.js page + its same-origin fetchClientSecret POST), which
    # cannot attach the app's X-Firebase-AppCheck header. Alternative auth holds:
    # /stripe-embedded is public and serves only the publishable key (no secret),
    # and /stripe-account-session still requires the driver's JWT Bearer token.
    "/api/v1/drivers/stripe-embedded",
    "/api/v1/drivers/stripe-account-session",
    # Company portal (Spinr for Business) is a browser Next.js app — like the
    # admin dashboard it cannot attach an X-Firebase-AppCheck header. These are
    # its surfaces, gated instead by the rider JWT + corporate membership:
    #   /api/company/*           corporate data (require_company_admin/member)
    #   /api/rider/work-profile  the caller's own memberships (rider JWT)
    #   /api/portal/*            the portal's dedicated auth namespace (OTP /
    #                            refresh / logout). Mobile keeps /api/v1/auth/*
    #                            App-Check-enforced — only the browser portal
    #                            uses /api/portal/auth/*.
    "/api/company/",
    "/api/rider/work-profile",
    "/api/portal/",
    # Low-sensitivity shared reads the portal's booking page needs (public
    # vehicle list; JWT-gated Google Maps proxy). Exempted rather than
    # duplicated under /api/portal — mobile still sends App Check, it is simply
    # no longer required on these two read endpoints.
    "/api/v1/vehicle-types",
    "/api/v1/maps/",
    # Public mobile bootstrap config. It contains publishable/client config only
    # (settings.py filters to Maps/Stripe publishable keys + tracking base URL),
    # so startup must not deadlock behind the App Check configuration it helps
    # the apps discover.
    "/api/v1/settings",
    # OTP login must stay reachable when App Check native modules are unavailable
    # or not yet attested (Expo Go/debug builds, newly registered devices). The
    # handlers keep their own OTP throttling/lockout and do not expose data
    # without possession of the SMS code.
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
)


# ── Forced-upgrade gate (ACTION_ITEMS.md E3) ──────────────────────────
# Old rider/driver binaries in the wild will eventually hit removed or
# changed backend behaviour with no way to prompt the user to update. This
# middleware compares the client-reported X-App-Version against an
# admin-configured floor (app_settings.min_rider_app_version /
# min_driver_app_version) and returns 426 Upgrade Required when the client
# is below it.
#
# Deliberately soft-fails open: a missing/unparseable header, an unknown
# X-App-Platform, or an unset (empty-string) minimum all pass the request
# through unmodified. This is what makes the feature safe to ship dark —
# today's binaries don't send the header yet, so enforcement has zero
# effect until (a) both apps are updated to send it and (b) an admin
# explicitly sets a non-empty minimum.
_FORCED_UPGRADE_PATH_PREFIX = "/api/v1/"
# Pre-auth / bootstrap endpoints a genuinely-too-old client still needs to
# reach in order to *learn* it needs to update (fetch config, see the
# update-required copy) rather than being locked out before it can render
# anything.
_FORCED_UPGRADE_EXEMPT_PREFIXES = (
    "/api/v1/settings",
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
)


def _parse_semver(value: str) -> tuple | None:
    parts = value.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


class ForcedUpgradeMiddleware(BaseHTTPMiddleware):
    """Reject requests from app builds older than the configured minimum."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(_FORCED_UPGRADE_PATH_PREFIX):
            return await call_next(request)
        if any(path.startswith(p) for p in _FORCED_UPGRADE_EXEMPT_PREFIXES):
            return await call_next(request)

        platform = request.headers.get("X-App-Platform")
        client_version_raw = request.headers.get("X-App-Version")
        if platform not in ("rider", "driver") or not client_version_raw:
            # Old binaries that don't send these headers yet — pass through.
            return await call_next(request)

        client_version = _parse_semver(client_version_raw)
        if client_version is None:
            return await call_next(request)

        try:
            from settings_loader import get_app_settings  # type: ignore

            app_settings = await get_app_settings()
        except Exception as exc:
            # Settings lookup failing must never itself lock users out.
            logger.warning("ForcedUpgrade: settings lookup failed, passing through: {}", exc)
            return await call_next(request)

        min_key = "min_rider_app_version" if platform == "rider" else "min_driver_app_version"
        min_version_raw = app_settings.get(min_key, "") or ""
        min_version = _parse_semver(min_version_raw)
        if min_version is None:
            return await call_next(request)

        if client_version < min_version:
            logger.info(
                "ForcedUpgrade: blocking {} v{} (min v{}) on {}",
                platform,
                client_version_raw,
                min_version_raw,
                path,
            )
            return JSONResponse(
                status_code=426,
                content={
                    "detail": "upgrade_required",
                    "min_version": min_version_raw,
                },
            )

        return await call_next(request)


class FirebaseAppCheckMiddleware(BaseHTTPMiddleware):
    """Enforce Firebase App Check on every API request.

    Reads the ``X-Firebase-AppCheck`` header and verifies the token using the
    Firebase Admin SDK. Returns 401 when enforcement is enabled and the token
    is absent or invalid.
    """

    def __init__(self, app, enforcement_enabled: bool = True):
        super().__init__(app)
        self._enforce = enforcement_enabled

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt WebSockets, docs, and health probes.
        if any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES):
            return await call_next(request)

        # Only apply to /api/* routes.
        if not path.startswith("/api"):
            return await call_next(request)

        # B-P2-3: bind request_id explicitly into App Check log lines.
        # RequestIDMiddleware contextualises loguru, but App Check runs
        # *outside* that context (it dispatches before the call_next that
        # establishes the contextualize block — which it then never re-
        # enters when it short-circuits with a 401). Read directly from
        # request.state for correlation in those reject paths.
        request_id = getattr(request.state, "request_id", "-")

        token = request.headers.get("X-Firebase-AppCheck")
        # Prefer the request_id already set by RequestIDMiddleware; fall back
        # to the caller-supplied header so App Check failures are always
        # cross-correlatable even if middleware ordering changes.
        _req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID", "-")

        if not token:
            if self._enforce:
                # DEBUG, not WARNING: a missing App Check token is an expected,
                # high-volume rejection (e.g. a client polling before Firebase
                # App Check has minted a token). The 401 access-log line still
                # captures volume; per CLAUDE.md, expected/degraded paths must
                # not spam WARNING. A genuine attack pattern surfaces via the
                # 401 rate, not this per-request line.
                logger.debug("App Check: missing token for {} req_id={}", path, request_id)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "App Check token required"},
                )
            # Enforcement off — let the request through but log it.
            logger.debug("App Check: token absent (enforcement disabled) for {} req_id={}", path, request_id)
            return await call_next(request)

        # Verify the token with the Firebase Admin SDK.
        try:
            import firebase_admin.app_check as app_check  # noqa: PLC0415

            app_check.verify_token(token)
        except Exception as exc:
            if self._enforce:
                logger.warning("App Check: invalid token for {} req_id={} — {}", path, request_id, exc)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid App Check token"},
                )
            logger.debug("App Check: token verification failed (enforcement disabled) req_id={}: {}", request_id, exc)

        return await call_next(request)


# ── Correlation / Request-ID middleware ──────────────────────────────
# Each request gets a UUID in X-Request-ID (caller may supply their own).
# The ID is echoed back in the response header and bound to the loguru
# context so every log line emitted during that request carries it —
# enabling full end-to-end tracing from mobile client to DB query.


def _extract_user_id(request: Request) -> str | None:
    try:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer ") :]
        payload = jwt.decode(token, options={"verify_signature": False})
        # Spinr JWTs carry the user id in `user_id` (see core auth.create_jwt_token),
        # not the standard `sub` claim — reading `sub` left request.state.user_id
        # (and every log line's user correlation) permanently None. Fall back to
        # `sub` for any token that does use it.
        uid = payload.get("user_id") or payload.get("sub")
        return str(uid) if uid is not None else None
    except Exception:
        return None


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request/response pair."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        user_id = _extract_user_id(request)
        ctx = {"request_id": request_id}
        if user_id is not None:
            ctx["user_id"] = user_id
        # utils/log_context.py's ContextVar-backed request_id, read by
        # utils/audit_logger.py so audit_logs rows can be joined back to
        # the Sentry event / log lines from the same request (both already
        # carry request_id via logger.contextualize below). Task-scoped —
        # asyncio ContextVars propagate through awaits within this request's
        # own task without leaking across concurrent requests.
        set_request_context(request_id, user_id or "")
        with logger.contextualize(**ctx):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # OTel/W3C-compatible alias: clients that look for X-Trace-ID
        # (per OTel HTTP semantic conventions) get the same UUID without
        # us having to introduce a separate trace ID generator yet.
        response.headers["X-Trace-ID"] = request_id
        return response


# ── Security response headers ─────────────────────────────────────────
# Baseline for an API backend. Critical protections: X-Frame-Options
# (clickjacking), X-Content-Type-Options (MIME sniffing), HSTS (TLS
# enforcement), CSP frame-ancestors (clickjacking, modern equivalent).
# CSP default-src='none' is strict-but-safe because the backend serves
# JSON almost exclusively; Swagger UI / ReDoc / openapi.json need a
# relaxed CSP to load external assets from cdn.jsdelivr.net, so those
# paths are exempted.

_BASE_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}

_STRICT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

# Relaxed CSP for FastAPI's built-in docs. Swagger UI + ReDoc pull
# scripts and styles from jsdelivr; they also use inline styles.
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'"
)

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# The driver app's in-WebView Stripe onboarding page (GET .../stripe-embedded)
# loads connect.js, mounts cross-origin Stripe iframes, posts back to our API,
# and captures a camera photo for identity verification. The strict API CSP
# (default-src 'none') + Permissions-Policy camera=() would render a blank page,
# so this single server-rendered path gets a Stripe-scoped CSP and camera
# delegation. Domains follow Stripe's embedded-components CSP guidance. The
# inline <script>/<style> in the page is fully server-controlled (only the
# public publishable key is injected, and it's <script>-escaped at render time),
# so 'unsafe-inline' here is bounded.
_STRIPE_EMBED_PATHS = ("/api/v1/drivers/stripe-embedded",)

_STRIPE_EMBED_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://connect-js.stripe.com https://*.js.stripe.com https://js.stripe.com; "
    "style-src 'self' 'unsafe-inline' https://connect-js.stripe.com https://*.js.stripe.com; "
    "frame-src https://connect-js.stripe.com https://*.js.stripe.com https://js.stripe.com "
    "https://hooks.stripe.com https://m.stripe.network; "
    "connect-src 'self' https://api.stripe.com https://connect-js.stripe.com https://*.js.stripe.com "
    "https://merchant-ui-api.stripe.com https://m.stripe.network; "
    "img-src 'self' data: https://*.stripe.com; "
    "font-src 'self' data: https://*.js.stripe.com; "
    "frame-ancestors 'none'; base-uri 'none'"
)

# Delegate camera to Stripe's identity-capture iframe (and self) ONLY on the
# embedded path; every other path keeps camera=() from the base headers. If
# Stripe serves the capture iframe from another origin, add it here.
_STRIPE_EMBED_PERMISSIONS_POLICY = (
    'camera=(self "https://js.stripe.com" "https://connect-js.stripe.com"), microphone=(), geolocation=(), payment=()'
)


def _apply_security_headers(response: Response, path: str, enable_hsts: bool) -> None:
    """Attach the baseline security headers to a response.

    Uses dict-style assignment (rather than setdefault) so that upstream
    handlers that set weaker values get overridden by our stricter ones.
    """
    for k, v in _BASE_SECURITY_HEADERS.items():
        response.headers[k] = v

    if enable_hsts:
        # 1 year, include subdomains, eligible for preload.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    if any(path.startswith(p) for p in _STRIPE_EMBED_PATHS):
        response.headers["Content-Security-Policy"] = _STRIPE_EMBED_CSP
        response.headers["Permissions-Policy"] = _STRIPE_EMBED_PERMISSIONS_POLICY
        # Stripe's embedded onboarding spawns a cross-origin popup for identity
        # verification (gov ID + selfie) that posts its result back via
        # window.opener. The base COOP=same-origin severs that opener link, so
        # connect.js hangs on "Loading secure verification…" forever and the
        # Stripe component never renders. Stripe's embedded-components guidance
        # requires COOP=unsafe-none on the host page — relax it for this one
        # server-rendered path only; every other response keeps same-origin.
        response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
    elif any(path.startswith(p) for p in _DOCS_PATHS):
        response.headers["Content-Security-Policy"] = _DOCS_CSP
    else:
        response.headers["Content-Security-Policy"] = _STRICT_CSP


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a conservative set of security response headers to every
    response. HSTS only when ENV=production so local dev over HTTP still
    works in browsers that cache HSTS aggressively.
    """

    def __init__(self, app, enable_hsts: bool):
        super().__init__(app)
        self._enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        _apply_security_headers(response, request.url.path, self._enable_hsts)
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for state-changing requests.

    Only enforced when the request carries an Origin header (browser context).
    Native mobile apps and server-to-server calls lack Origin and are implicitly
    exempt — CSRF is impossible without a cookie-based session attack vector.

    Validation: X-CSRF-Token header must equal the csrf_token cookie value.
    Both values are set by the server on login/refresh (see core/csrf.py).
    """

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() in _CSRF_SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if path in _CSRF_EXEMPT_EXACT or any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
            return await call_next(request)

        # Only enforce for browser-originated requests (Origin header present).
        if not request.headers.get("origin"):
            return await call_next(request)

        csrf_header = request.headers.get(_CSRF_HEADER)
        # Collect whichever per-audience CSRF cookies are present (admin uses
        # `csrf_token`, the company portal uses `csrf_token_company`).
        present_cookies = [(request.cookies.get(name)) for name in _CSRF_COOKIES]
        present_cookies = [c for c in present_cookies if c]

        if not present_cookies or not csrf_header:
            # loguru formats with str.format ({}), not %-style — a "%s" here is
            # emitted literally and the arguments are silently dropped.
            logger.warning(
                "CSRF token missing: {} {} header={} cookies={}",
                request.method,
                path,
                "present" if csrf_header else "absent",
                "present" if present_cookies else "absent",
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing"},
            )

        import hmac as _hmac  # noqa: PLC0415

        # Valid if the header matches ANY present CSRF cookie. Constant-time
        # compare against each; the attacker can read none of them.
        header_bytes = csrf_header.encode()
        if not any(_hmac.compare_digest(c.encode(), header_bytes) for c in present_cookies):
            # `cookies` is the count of CSRF cookies presented, not their values
            # — the tokens are session credentials and must never reach a log
            # sink. The count distinguishes "no cookie at all" from "sent the
            # wrong one of two", which is what actually needs diagnosing here.
            logger.warning(
                "CSRF token mismatch: {} {} origin={} cookies={}",
                request.method,
                path,
                request.headers.get("origin"),
                len(present_cookies),
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token invalid"},
            )

        return await call_next(request)


_INSECURE_JWT_DEFAULTS = {
    "your-strong-secret-key",  # core/config.py default
    "spinr-dev-secret-key-NOT-FOR-PRODUCTION",  # previous dependencies.py fallback
    "replace-with-strong-random-secret",  # backend/.env.example placeholder
}
_MIN_JWT_SECRET_LENGTH = 32

# Bootstrap credentials that must be overridden before the dashboard
# is exposed publicly — otherwise anyone who reads the source can log
# in as the super-admin. These are the defaults shipped in
# core/config.py; production deploys must set real values via env vars.
_INSECURE_ADMIN_EMAILS = {"admin@spinr.ca", "admin@example.com"}
_INSECURE_ADMIN_PASSWORDS = {"admin123", "replace-me", "changeme", "password", "Admin12345", "TempPass123!"}

# Supabase service-role keys are signed JWTs (ES256/HS256), ~220 chars,
# always starting with "eyJ" (base64-encoded JSON header). The .env.example
# ships a "replace-with-service-role-key" placeholder; shipping that to
# production would produce silent 401s from every DB call. Reject it and
# anything that clearly isn't a real key. We intentionally do NOT do a
# full JWT parse — that would require trusting Supabase's rotating JWKS
# — we just gate on obvious structural markers.
_SUPABASE_KEY_MIN_LENGTH = 40
_SUPABASE_KEY_PREFIX = "eyJ"

# Placeholder markers in SUPABASE_URL from backend/.env.example. A deploy
# pointing at the example project ref will 404 every query.
_SUPABASE_URL_PLACEHOLDERS = ("your-project-ref", "your-project", "example.supabase.co")


def _validate_production_config():
    """Fail fast on misconfigured production deploys.

    Called at the top of init_middleware so the server never actually
    starts serving requests with a known-insecure configuration. All
    checks only fire when ``ENV=production``; dev/local environments
    get usable defaults.
    """
    if settings.ENV.lower() != "production":
        return

    errors: list[str] = []

    # 1. JWT signing secret
    secret = settings.JWT_SECRET or ""
    if secret in _INSECURE_JWT_DEFAULTS:
        errors.append(
            "JWT_SECRET is set to a well-known default. Generate a strong "
            "secret (python -c 'import secrets; print(secrets.token_urlsafe(64))') "
            "and set JWT_SECRET in the environment."
        )
    elif len(secret) < _MIN_JWT_SECRET_LENGTH:
        errors.append(f"JWT_SECRET is shorter than {_MIN_JWT_SECRET_LENGTH} characters. Use a longer, random secret.")

    # 2. Supabase credentials — the entire backend is Supabase-backed,
    #    so an unset URL or service role key means the server comes up
    #    but every DB call hits a NoneType client. We also reject the
    #    .env.example placeholders so a half-configured deploy can't
    #    reach production (audit P0-S5).
    supabase_url = (settings.SUPABASE_URL or "").strip()
    if not supabase_url:
        errors.append("SUPABASE_URL is not set.")
    elif any(marker in supabase_url for marker in _SUPABASE_URL_PLACEHOLDERS):
        errors.append(
            "SUPABASE_URL looks like the .env.example placeholder "
            f"({supabase_url}). Set it to your real Supabase project URL "
            "(https://<project-ref>.supabase.co)."
        )

    service_key = (settings.SUPABASE_SERVICE_ROLE_KEY or "").strip()
    if not service_key:
        errors.append("SUPABASE_SERVICE_ROLE_KEY is not set.")
    elif not service_key.startswith(_SUPABASE_KEY_PREFIX):
        # Real Supabase service-role keys are JWTs; they always start
        # with "eyJ". A value that doesn't is a placeholder, a typo, or
        # someone pasted the anon key and dropped the header.
        errors.append(
            "SUPABASE_SERVICE_ROLE_KEY does not look like a real Supabase "
            "service-role JWT (expected to start with 'eyJ'). Copy the key "
            "from Supabase → Settings → API and paste it verbatim. NEVER "
            "commit this value — it bypasses RLS."
        )
    elif len(service_key) < _SUPABASE_KEY_MIN_LENGTH:
        errors.append(
            f"SUPABASE_SERVICE_ROLE_KEY is only {len(service_key)} chars; real keys are ~220. "
            "Check that the value was copied in full (a truncated key silently 401s every DB call)."
        )

    # 3. Admin bootstrap credentials — the super-admin login path in
    #    routes/admin/auth.py compares directly against these strings.
    admin_email = (settings.ADMIN_EMAIL or "").lower().strip()
    admin_password = settings.ADMIN_PASSWORD or ""
    if admin_email in _INSECURE_ADMIN_EMAILS:
        errors.append(
            "ADMIN_EMAIL is set to a well-known default. Set a real "
            "admin email in the environment before exposing the dashboard."
        )
    if admin_password in _INSECURE_ADMIN_PASSWORDS:
        errors.append(
            "ADMIN_PASSWORD is set to a well-known default. Set a strong "
            "password in the environment before exposing the dashboard."
        )
    elif len(admin_password) < 20:
        errors.append("ADMIN_PASSWORD is shorter than 20 characters. Use a stronger password.")

    # 4. Rate limiter storage — a multi-machine Fly deploy with
    #    "memory://" slowapi storage means each machine keeps its own
    #    counters, so 5/minute OTP limits become (5 × N_machines)/minute
    #    as LB stickiness shifts. Require a redis:// URL in production.
    redis_url = (settings.RATE_LIMIT_REDIS_URL or "").strip()
    if not redis_url:
        errors.append(
            "RATE_LIMIT_REDIS_URL is not set. Production rate limiting "
            "requires a shared Redis backend (e.g. Upstash / Fly Redis). "
            "Set RATE_LIMIT_REDIS_URL=redis://… or rediss://… before booting."
        )
    elif not (redis_url.startswith("redis://") or redis_url.startswith("rediss://")):
        errors.append(
            "RATE_LIMIT_REDIS_URL must start with redis:// or rediss:// "
            f"(got scheme from: {redis_url.split('://', 1)[0]}://…)."
        )

    # 5. Firebase service account — required for Firebase Auth verify
    #    and for FCM push delivery. Missing means get_current_user can't
    #    verify Firebase-issued tokens and send_push_notification no-ops.
    #    Warn rather than fail because a deploy MIGHT be intentionally
    #    running without Firebase (e.g. SMS-only auth via Twilio + a
    #    local push transport).
    if not settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        logger.warning(
            "FIREBASE_SERVICE_ACCOUNT_JSON is not set — Firebase ID token "
            "verification and FCM push delivery will no-op. Set it if you "
            "want drivers to receive push notifications."
        )

    if errors:
        formatted = "\n  - ".join(errors)
        raise RuntimeError(
            f"Refusing to start: production configuration has {len(errors)} problem(s).\n  - {formatted}"
        )


def init_middleware(app):
    """Initialize all middleware components"""
    # Fail-fast on misconfigured production deploys BEFORE any routes
    # or middleware are attached. See _validate_production_config.
    _validate_production_config()

    is_production = settings.ENV.lower() == "production"

    # CORS Middleware
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Always allow the admin and default apps explicitly regardless of env variables
    always_allowed = [
        "https://spinr.app",
        "https://www.spinr.app",
        "https://spinr-track.app",
        "https://www.spinr-track.app",
        "https://track.spinr.ca",
        "https://admin-spinr.spinr.ca",
        "http://localhost:3000",
        "http://localhost:3001",
    ]
    origins.extend(always_allowed)
    # Remove empty strings and duplicates (preserve order for determinism)
    origins = list(dict.fromkeys(o for o in origins if o))

    wildcard = "*" in origins

    if wildcard and is_production:
        # Fail fast: refuse to start with wide-open CORS in production.
        # Set ALLOWED_ORIGINS in the environment to a comma-separated list.
        raise RuntimeError(
            "CORS is configured with wildcard '*' while ENV=production. "
            "Set ALLOWED_ORIGINS to an explicit comma-separated list of origins."
        )

    # CORS spec forbids credentials with wildcard origin — browsers will drop
    # the Access-Control-Allow-Credentials header if origin is '*'. Disable
    # credentials in that case so dev requests fail loudly rather than silently.
    allow_credentials = not wildcard
    if wildcard:
        logger.warning(
            "CORS: wildcard '*' in ALLOWED_ORIGINS — allow_credentials disabled. This is acceptable for local dev only."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Idempotency-Key",
            "X-Forwarded-For",
            "Accept",
            "Accept-Language",
            "Cache-Control",
            "X-CSRF-Token",
            "X-Request-ID",
            "X-Deadline-Ms",
            "X-App-Version",
            "X-App-Platform",
        ],
    )

    # Security headers — applied after CORS so that every response
    # (including CORS preflight 204s) carries the hardening headers.
    # HSTS is only enabled in production because emitting it over
    # plain-HTTP dev would cause browsers to pin the dev host to HTTPS.
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=is_production)

    # Request ID — outermost layer so X-Request-ID is present on every
    # response, including CORS preflights and error responses.
    app.add_middleware(RequestIDMiddleware)

    # Firebase App Check — verify that requests originate from genuine
    # Spinr builds. Enforced in production; logged-only in dev/staging so
    # unregistered debug devices aren't blocked. See the IMPORTANT comment
    # above FirebaseAppCheckMiddleware for the manual Firebase Console
    # steps required before shipping to production.
    app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=is_production)

    # Forced-upgrade gate — ships dark (see ForcedUpgradeMiddleware docstring);
    # no ENV branch needed, it self-disables whenever the admin-configured
    # minimum is unset.
    app.add_middleware(ForcedUpgradeMiddleware)

    # FIX: Add CORS headers to exception responses (FastAPI bug fix)
    async def cors_exception_handler(request: Request, exc: Exception):
        origin = request.headers.get("origin")

        # Handle standard HTTP exceptions
        if hasattr(exc, "status_code") and hasattr(exc, "detail"):
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        else:
            logger.opt(exception=True).error(f"Unhandled exception: {exc}")
            response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

        # Add CORS headers if origin is allowed
        if origin:
            if origin in origins:
                # Explicit match — safe to allow credentials
                response.headers["Access-Control-Allow-Origin"] = origin
                if allow_credentials:
                    response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, Content-Type, X-Requested-With, "
                    "Idempotency-Key, X-Forwarded-For, Accept, Accept-Language, Cache-Control, "
                    "X-CSRF-Token, X-Request-ID, X-Deadline-Ms"
                )
                response.headers["Vary"] = "Origin"
            elif wildcard:
                # Wildcard (dev only) — credentials already disabled above
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"

        # Error responses must also carry security headers — FastAPI's
        # exception handling can short-circuit before SecurityHeadersMiddleware
        # sees the final response in some edge cases.
        _apply_security_headers(response, request.url.path, enable_hsts=is_production)

        return response

    # NOT registered here. `utils/error_handling.py::register_exception_handlers`
    # (called later, in server.py, after init_middleware) registers its own
    # handlers for HTTPException and the base Exception class. Starlette's
    # exception-handler lookup always picks the most specific class in the
    # MRO regardless of registration order, and for two handlers on the
    # *same* exact class the one registered last wins — so had this been
    # registered, it would never run: HTTPException instances go to
    # error_handling.http_exception_handler (more specific), and everything
    # else goes to error_handling.general_exception_handler (registered
    # later for the same Exception class, silently overwriting this one in
    # Starlette's handler dict). general_exception_handler already logs the
    # full traceback (`traceback.format_exc()`) and adds the same CORS
    # headers this function computes below, via `_cors_headers_for` in
    # error_handling.py — so removing this dead registration doesn't lose
    # any behavior. cors_exception_handler is kept only because
    # tests/test_p1_cors.py exercises its logic directly (via its own
    # closure, not through the app), not because anything live calls it.

    # Relative-redirect middleware — when FastAPI issues a 307 trailing-slash
    # redirect the Location header contains an absolute backend URL
    # (e.g. http://127.0.0.1:8400/api/admin/foo/). If the Next.js rewrite proxy
    # forwards that to the browser, the browser follows it directly to the
    # backend, bypassing the proxy and triggering CORS + auth-header loss.
    # Stripping the scheme+host makes Location relative so the browser's
    # follow-up request still goes through Next.js.
    class RelativeRedirectMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get("location", "")
                if location.startswith("http"):
                    parsed = urlparse(location)
                    relative = parsed.path
                    if parsed.query:
                        relative += f"?{parsed.query}"
                    response.headers["location"] = relative
            return response

    app.add_middleware(RelativeRedirectMiddleware)

    # Deadline propagation — clients send `X-Deadline-Ms: <epoch-ms>`
    # (derived from their local axios / fetch timeout). The backend
    # stashes the deadline on a contextvar so any code running inside
    # the request coroutine can consult it — most importantly
    # db_supabase.run_sync, which checks remaining budget before each
    # retry sleep. If the client has already given up, we skip the
    # retry and fail fast instead of burning thread-pool workers on a
    # doomed request.
    #
    # Absent header = no deadline enforced (current behaviour).
    from utils.deadline import set_request_deadline

    class DeadlineMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            deadline_header = request.headers.get("x-deadline-ms")
            token = None
            if deadline_header:
                try:
                    deadline_epoch_ms = int(deadline_header)
                    import time as _t

                    now_epoch_ms = int(_t.time() * 1000)
                    remaining_ms = deadline_epoch_ms - now_epoch_ms
                    # Convert the client's epoch deadline into a
                    # monotonic-clock one so comparisons inside the
                    # request (which may run for many seconds) aren't
                    # affected by system clock jumps.
                    monotonic_deadline = _t.monotonic() + (remaining_ms / 1000.0)
                    request.state.deadline_monotonic = monotonic_deadline
                    token = set_request_deadline(monotonic_deadline)
                except (ValueError, TypeError):
                    # Malformed header — ignore silently. Not worth a 400.
                    pass
            try:
                return await call_next(request)
            finally:
                if token is not None:
                    set_request_deadline(None, reset_token=token)

    app.add_middleware(DeadlineMiddleware)

    # CSRF double-submit cookie protection — validates X-CSRF-Token header
    # against the csrf_token cookie on every state-changing browser request.
    # Mobile apps and server-to-server calls (no Origin header) bypass this.
    app.add_middleware(CSRFMiddleware)

    # Rate Limiting Middleware
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    logger.info(
        f"Middleware initialized: CORS, CSRF, Security Headers (HSTS={'on' if is_production else 'off'}), "
        f"App Check enforcement={'on' if is_production else 'off'}, Rate Limiting"
    )
