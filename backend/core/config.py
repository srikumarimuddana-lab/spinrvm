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
