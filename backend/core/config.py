import logging
import os
from decimal import Decimal
from typing import Optional

import bcrypt
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Reviewer OTP codes must match the verify-otp schema constraint exactly
# (VerifyOTPRequest.code is `^\d{4}$`). A code that doesn't satisfy this can be
# issued by send-otp but never accepted by verify-otp, silently breaking the
# reviewer login. Validate against the same rule before allow-listing.
_REVIEW_OTP_LEN = 4


def _is_valid_review_otp(otp: str) -> bool:
    return otp.isascii() and otp.isdigit() and len(otp) == _REVIEW_OTP_LEN


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application settings
    APP_NAME: str = "Spinr API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Database settings
    SUPABASE_URL: str = ""
    # IMPORTANT: Rotate this key before deploying — see docs/key-rotation.md
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    USE_SUPABASE: bool = True  # Supabase is now the default database
    # PIPEDA 22-2: must be "ca-central-1" in production; checked at startup.
    SUPABASE_REGION: str = ""

    # Firebase settings
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    FIREBASE_DRIVER_APP_ID: str = ""
    FIREBASE_RIDER_APP_ID: str = ""

    # Security settings — no defaults; app refuses to start if unset in production
    JWT_SECRET: str
    ALGORITHM: str = "HS256"
    # Rider/driver access-token TTL in minutes. Short-lived for security (P0-S3).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # Admin-console access-token TTL in hours. 1h forces frequent rotation via
    # the refresh token flow; reduces the blast radius of a captured token.
    ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1
    # Require TOTP MFA for every admin_staff login. Staff without MFA get an
    # enrollment-scoped token at login instead of a session and must finish
    # enrolling before the dashboard issues real tokens. The admin-001 env
    # break-glass account is exempt (it has no admin_staff row to hold a
    # secret); its password strength is enforced at startup instead.
    # Set false only in local dev where repeated logins make TOTP painful.
    ADMIN_MFA_ENFORCED: bool = True
    # Refresh-token TTL in days (30 days "remember this device").
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ──── Cookie Settings ────
    # HTTP-only cookie flags for secure token storage (P3 HttpOnly migration)
    COOKIE_SECURE: bool = True  # HTTPS only in production
    COOKIE_HTTPONLY: bool = True  # JavaScript cannot read
    COOKIE_SAMESITE: str = "Strict"  # Prevent CSRF token leaks
    COOKIE_DOMAIN: str = ""  # Set to ".spinr.ca" in production env vars for cross-subdomain support
    COOKIE_PATH: str = "/"  # Available to all routes

    # CORS settings
    # Comma-separated list of origins. Defaults to localhost dev ports so a
    # fresh deploy is not wide-open. Override in .env for staging/prod.
    # Set to "*" ONLY for local development — wildcard is rejected in production
    # (see core/middleware.init_middleware).
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8081,http://localhost:19006"

    # Admin credentials — no defaults; app refuses to start if unset in production
    ADMIN_EMAIL: str = "admin@spinr.ca"
    ADMIN_PASSWORD: str
    # Bcrypt hash of ADMIN_PASSWORD — computed once at startup so the plaintext
    # is never compared directly in hot-path code (A-P3-1).
    admin_password_hash: str = ""

    # Break-glass emergency access token.  Store the SHA-256 hex digest here,
    # not the raw token.  When unset, the /admin/auth/break-glass endpoint is
    # disabled entirely.  Generate with:
    #   python3 -c "import hashlib, secrets; t=secrets.token_hex(32); print('token:', t, '\nhash:', hashlib.sha256(t.encode()).hexdigest())"
    BREAK_GLASS_TOKEN_HASH: str = ""

    # Rate limiting
    RATE_LIMIT: str = "10/minute"

    # ── Google Maps Platform daily-spend circuit breaker ────────────────────
    # When estimated USD spend for the current UTC day exceeds this threshold
    # the Maps proxy responds 503 instead of forwarding to Google. Defence in
    # depth on top of GCP-side budget alerts. Default is conservative; raise
    # via env var once steady-state usage is known.
    MAPS_DAILY_BUDGET_USD: float = 5.0
    # Redis configuration
    # Generic base URL (e.g. redis://localhost:6379/0). If specialized URLs below
    # are unset, they fall back to this.
    REDIS_URL: str = ""

    # Distributed rate limiter storage (audit P0-B3). Falls back to REDIS_URL.
    RATE_LIMIT_REDIS_URL: str = ""

    # WebSocket pub/sub backend (audit P0-B3). Falls back to REDIS_URL.
    WS_REDIS_URL: str = ""

    # Self-hosted OSRM (map-matching) base URL for billable road-distance.
    # No trailing slash, no path — e.g. "http://osrm.railway.internal:5000"
    # (Railway private networking) or "https://osrm-xxxx.up.railway.app".
    # When set, the trip-end road-snap prefers OSRM /match over Google Roads;
    # empty disables OSRM and falls back to Google (and then haversine).
    # A DB override `osrm_url` in app_settings, if present, takes precedence.
    OSRM_URL: str = ""

    # Public OSRM used for the LIGHT live-routing calls only (live-route line,
    # ETA, single-point road snap) when no self-hosted OSRM is configured.
    # Without this fallback an unset OSRM_URL silently disabled the live route
    # line in both apps (the planned booking-time polyline never updated).
    # The project-osrm demo server has no SLA and a fair-use policy — fine for
    # a Saskatchewan-scale pilot at ~1 call/20s per active ride, but self-host
    # OSRM (a Saskatchewan extract is tiny) before scaling. Set to "" to
    # disable the fallback entirely. The heavy billing path (/match over full
    # GPS traces) intentionally still requires an explicit OSRM_URL.
    OSRM_FALLBACK_URL: str = "https://router.project-osrm.org"

    # OTP brute-force lockout (SEC-008)
    OTP_MAX_FAILURES: int = 5  # attempts before lockout
    OTP_FAILURE_WINDOW_SECONDS: int = 3600  # sliding window (1 hr)
    OTP_LOCKOUT_DURATION_SECONDS: int = 86400  # lockout duration (24 hr)

    # Fare cache TTL (PERF-001)
    FARE_CACHE_TTL_SECONDS: int = 300  # 5-minute cache per lat/lng grid cell

    # Card pre-authorization buffer (PAY pre-auth). At booking we place a manual-
    # capture hold of (estimated_fare + this buffer) on the rider's card/Apple Pay
    # so a post-trip tip can be captured on the SAME PaymentIntent (one Stripe fee)
    # and a dead card is caught BEFORE dispatch. Tunable via env without redeploy;
    # money value so it is a Decimal, never a float. Applies to card source only.
    RIDE_AUTH_BUFFER_CAD: Decimal = Decimal("10.00")

    # Public tracking page base URL — used when generating shareable trip links.
    # Customer links are clean: "{TRACKING_BASE_URL}/{token}". The tracking
    # domain rewrites /{token} → /track/{token} server-side. Set to the
    # dedicated tracking subdomain in production (admin panel is NOT served here).
    TRACKING_BASE_URL: str = "https://track.spinr.ca"

    # Public origin of this API itself, used to build absolute URLs the mobile
    # OS fetches directly (e.g. the ride-offer fare-banner image in the driver
    # notification). Must be the externally-routable host (Cloudflare CNAME),
    # NOT an internal Fly/Railway address. Override per environment via env.
    PUBLIC_API_BASE_URL: str = "https://api-spinr.spinr.ca"

    # File storage
    STORAGE_BUCKET: str = "driver-documents"

    # Environment
    ENV: str = "development"

    # App Store / Google Play reviewer login accounts. Comma-separated
    # "phone:otp" pairs — E.164 phone and a 4-digit numeric OTP (must match
    # OTP_LENGTH). For these numbers, /auth/send-otp stores the fixed code and
    # never sends an SMS — even in production — so store reviewers can sign in
    # without receiving a text (they get the code from App Store Connect notes).
    # Leave EMPTY except while an app review is in flight; clear it once the
    # build is approved. Use of these accounts is audit-logged. Kept as an env
    # secret (not in app_settings) so it can't be edited from the admin
    # dashboard — a compromised admin cannot add an auth-bypass number.
    #   Example: "+13065550100:4821,+13065550101:4821"
    REVIEW_LOGIN_ACCOUNTS: str = ""

    # Observability — optional; Sentry only initialises when this is set
    sentry_dsn: Optional[str] = None

    # Operational alerting — Slack-compatible incoming webhook URL.
    # When set, the loop watchdog posts a message here whenever a background
    # loop goes stale.  Leave unset in development; required in production.
    ALERT_WEBHOOK_URL: Optional[str] = None

    # Corporate ops inbox — notified on self-serve company signups (new KYB
    # queue entries). Optional: when unset the signup still succeeds and the
    # staff KYB queue remains the source of truth; only the courtesy email is
    # skipped.
    CORPORATE_OPS_EMAIL: Optional[str] = None

    @model_validator(mode="after")
    def _hash_admin_password(self) -> "Settings":
        """Hash ADMIN_PASSWORD with bcrypt at startup (A-P3-1).

        The plaintext env var is read once here and the hash is stored in
        `admin_password_hash`. All login code compares against the hash so
        a leaked Settings object never exposes the plaintext.
        """
        if self.ADMIN_PASSWORD and not self.admin_password_hash:
            self.admin_password_hash = bcrypt.hashpw(self.ADMIN_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
        return self

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """Refuse to start in production with weak placeholder values, short
        secrets, or missing Firebase audience identifiers.

        - JWT_SECRET: ≥32 chars (B-P1-2 / CLAUDE.md). HS256 with a short shared
          secret is brute-forceable in seconds on a modern GPU.
        - FIREBASE_DRIVER_APP_ID / FIREBASE_RIDER_APP_ID: required so the manual
          audience check (B-P1-1 / DV-10) cannot be silently skipped.
        """
        if self.ENV.lower() == "production":
            weak = {
                "JWT_SECRET": ("your-strong-secret-key",),
                "ADMIN_PASSWORD": ("admin123", "password", "changeme"),
            }
            for field, bad_values in weak.items():
                value = getattr(self, field, None)
                if value in bad_values:
                    msg = (
                        f"{field} is set to a known-weak placeholder value. "
                        "Set a strong secret in your environment before running in production."
                    )
                    raise ValueError(msg)

            jwt_secret = self.JWT_SECRET or ""
            if len(jwt_secret) < 32:
                raise ValueError(
                    f"JWT_SECRET must be at least 32 characters in production "
                    f"(got {len(jwt_secret)}). HS256 with a short shared secret "
                    "is brute-forceable. Generate one with: "
                    "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
                )

            for field in ("FIREBASE_DRIVER_APP_ID", "FIREBASE_RIDER_APP_ID"):
                if not getattr(self, field, ""):
                    raise ValueError(
                        f"{field} must be set in production. The Firebase ID-token "
                        "audience check is gated on this value; an unset env var "
                        "would silently allow cross-app token reuse (DV-10)."
                    )

            for field in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
                if not getattr(self, field, ""):
                    raise ValueError(
                        f"{field} must be set in production. An empty value causes "
                        "the Supabase client to initialise successfully but fail on "
                        "every database call, producing a misleading 500 at runtime "
                        "rather than a clean startup error."
                    )

            # PIPEDA data residency: primary storage must be in a Canadian
            # region (ca-central-1 or equivalent). An unset or non-Canadian
            # region in production is a hard compliance boundary — fail fast
            # at startup rather than silently storing PII offshore. Relaxing
            # this requires legal sign-off (CLAUDE.md "Data residency").
            region = (self.SUPABASE_REGION or "").strip().lower()
            if not region:
                raise ValueError(
                    "SUPABASE_REGION must be set in production for PIPEDA data "
                    "residency. Set it to the Supabase project's Canadian region "
                    "(e.g. ca-central-1). Changing regions is a compliance event."
                )
            if not region.startswith("ca-"):
                raise ValueError(
                    f"SUPABASE_REGION='{self.SUPABASE_REGION}' is not a Canadian "
                    "region. PIPEDA requires primary storage in Canada "
                    "(ca-central-1 or equivalent). Do not relax this without "
                    "legal sign-off."
                )
        return self

    @property
    def SECRET_KEY(self) -> str:
        return self.JWT_SECRET

    @property
    def debug(self) -> bool:
        return self.ENV.lower() == "development"

    def review_login_map(self) -> dict[str, str]:
        """Parse REVIEW_LOGIN_ACCOUNTS into {phone: fixed_otp}.

        Only entries whose OTP satisfies the verify-otp constraint (exactly 4
        numeric digits) are included — a malformed code is skipped rather than
        allow-listed, so send-otp never issues a code that verify-otp can't
        accept. Returns an empty dict when unset, which disables the
        reviewer-login path entirely. Malformed entries are surfaced once at
        startup by ``_validate_review_accounts``.
        """
        out: dict[str, str] = {}
        raw = (self.REVIEW_LOGIN_ACCOUNTS or "").strip()
        if not raw:
            return out
        for pair in raw.split(","):
            phone, sep, otp = pair.strip().partition(":")
            if not sep:
                continue
            phone, otp = phone.strip(), otp.strip()
            if phone and _is_valid_review_otp(otp):
                out[phone] = otp
        return out

    @model_validator(mode="after")
    def _validate_review_accounts(self) -> "Settings":
        """Surface a misconfigured REVIEW_LOGIN_ACCOUNTS at startup.

        We do not raise — a typo in this optional reviewer secret must not take
        down the API — but we log loudly (ERROR → Sentry via the loguru bridge)
        so the operator catches it at deploy time instead of discovering it when
        a store reviewer can't log in. Each malformed entry names the offending
        phone's last 4 digits only (never the full number or the code).
        """
        raw = (self.REVIEW_LOGIN_ACCOUNTS or "").strip()
        if not raw:
            return self
        valid = 0
        for pair in raw.split(","):
            entry = pair.strip()
            if not entry:
                continue
            phone, sep, otp = entry.partition(":")
            phone, otp = phone.strip(), otp.strip()
            if not sep or not phone:
                logger.error("REVIEW_LOGIN_ACCOUNTS: malformed entry (expected 'phone:otp') — skipped")
            elif not _is_valid_review_otp(otp):
                logger.error(
                    "REVIEW_LOGIN_ACCOUNTS: OTP for ...%s is not %d digits — entry skipped, "
                    "reviewer login will NOT work for this number",
                    phone[-4:],
                    _REVIEW_OTP_LEN,
                )
            else:
                valid += 1
        if valid:
            logger.info("REVIEW_LOGIN_ACCOUNTS: %d reviewer login account(s) active", valid)
        return self


settings = Settings()
