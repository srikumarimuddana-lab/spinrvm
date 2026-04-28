import os
from typing import Optional

import bcrypt
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Firebase settings
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    FIREBASE_DRIVER_APP_ID: str = ""
    FIREBASE_RIDER_APP_ID: str = ""

    # Security settings — no defaults; app refuses to start if unset in production
    JWT_SECRET: str
    ALGORITHM: str = "HS256"
    # Rider/driver access-token TTL in minutes. Short-lived for security (P0-S3).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # Legacy days TTL — preserved for mobile clients that haven't adopted rotation yet.
    ACCESS_TOKEN_TTL_DAYS: int = 30
    # Admin-console access-token TTL in hours. 1h forces frequent rotation via
    # the refresh token flow; reduces the blast radius of a captured token.
    ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1
    # Refresh-token TTL in days (30 days "remember this device").
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

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
    # Redis configuration
    # Generic base URL (e.g. redis://localhost:6379/0). If specialized URLs below
    # are unset, they fall back to this.
    REDIS_URL: str = ""

    # Distributed rate limiter storage (audit P0-B3). Falls back to REDIS_URL.
    RATE_LIMIT_REDIS_URL: str = ""

    # WebSocket pub/sub backend (audit P0-B3). Falls back to REDIS_URL.
    WS_REDIS_URL: str = ""

    # OTP brute-force lockout (SEC-008)
    OTP_MAX_FAILURES: int = 5  # attempts before lockout
    OTP_FAILURE_WINDOW_SECONDS: int = 3600  # sliding window (1 hr)
    OTP_LOCKOUT_DURATION_SECONDS: int = 86400  # lockout duration (24 hr)

    # Fare cache TTL (PERF-001)
    FARE_CACHE_TTL_SECONDS: int = 300  # 5-minute cache per lat/lng grid cell

    # File storage
    STORAGE_BUCKET: str = "driver-documents"

    # Environment
    ENV: str = "development"

    # Observability — optional; Sentry only initialises when this is set
    sentry_dsn: Optional[str] = None

    @model_validator(mode="after")
    def _hash_admin_password(self) -> "Settings":
        """Hash ADMIN_PASSWORD with bcrypt at startup (A-P3-1).

        The plaintext env var is read once here and the hash is stored in
        `admin_password_hash`. All login code compares against the hash so
        a leaked Settings object never exposes the plaintext.
        """
        if self.ADMIN_PASSWORD and not self.admin_password_hash:
            self.admin_password_hash = bcrypt.hashpw(
                self.ADMIN_PASSWORD.encode(), bcrypt.gensalt(rounds=12)
            ).decode()
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
        return self

    @property
    def SECRET_KEY(self) -> str:
        return self.JWT_SECRET

    @property
    def debug(self) -> bool:
        return self.ENV.lower() == "development"


settings = Settings()
