import os
from typing import Optional

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
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    USE_SUPABASE: bool = True  # Supabase is now the default database

    # Firebase settings
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = None

    # Security settings — no defaults; app refuses to start if unset in production
    JWT_SECRET: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Rider/driver access-token TTL in days. Default is 30 days to match
    # the pre-refresh-token behaviour so mobile clients that haven't
    # shipped refresh-flow support yet keep working; operators should
    # drop this to 1-7 days once the mobile rollout lands. The audit
    # finding P0-S3 is addressed by the token_version + refresh_tokens
    # revocation primitives, not by shortening TTL — so the default
    # here is about deployment compatibility, not security posture.
    ACCESS_TOKEN_TTL_DAYS: int = 30
    # Admin-console access-token TTL in hours. Previously ∞ (no exp
    # claim), which is unacceptable — anyone who captured an admin
    # token had permanent access. Cap at 12h so at worst the attacker
    # has until the next business-day login.
    ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 12
    # Refresh-token TTL in days. 30 lines up with a reasonable "remember
    # this device" window; anything longer turns refresh tokens into
    # de-facto permanent credentials.
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
    OTP_MAX_FAILURES: int = 5                  # attempts before lockout
    OTP_FAILURE_WINDOW_SECONDS: int = 3600     # sliding window (1 hr)
    OTP_LOCKOUT_DURATION_SECONDS: int = 86400  # lockout duration (24 hr)

    # Fare cache TTL (PERF-001)
    FARE_CACHE_TTL_SECONDS: int = 300          # 5-minute cache per lat/lng grid cell

    # File storage
    STORAGE_BUCKET: str = "driver-documents"

    # Environment
    ENV: str = "development"

    # Observability — optional; Sentry only initialises when this is set
    sentry_dsn: Optional[str] = None

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """Refuse to start in production with known-weak placeholder values."""
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
        return self

    @property
    def SECRET_KEY(self) -> str:
        return self.JWT_SECRET

    @property
    def debug(self) -> bool:
        return self.ENV.lower() == "development"


settings = Settings()
