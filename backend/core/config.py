import os
from typing import Optional

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

    # Security settings
    JWT_SECRET: str = "your-strong-secret-key"  # Default for development only
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS settings
    # Comma-separated list of origins. Defaults to localhost dev ports so a
    # fresh deploy is not wide-open. Override in .env for staging/prod.
    # Set to "*" ONLY for local development — wildcard is rejected in production
    # (see core/middleware.init_middleware).
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8081,http://localhost:19006"

    # Admin credentials
    ADMIN_EMAIL: str = "admin@spinr.ca"
    ADMIN_PASSWORD: str = "admin123"

    # Rate limiting
    RATE_LIMIT: str = "10/minute"
    # Storage backend for the distributed rate limiter. When empty the
    # limiter uses slowapi's in-process "memory://" backend, which is fine
    # for local dev but wrong in production: each Fly machine / worker
    # keeps its own counters, so a 5/minute limit effectively becomes
    # (5 × N_machines)/minute and an attacker can sidestep OTP / login
    # limits by riding LB stickiness. Production deploys must set this
    # to a redis:// (or rediss://) URL backed by Upstash / Fly Redis;
    # _validate_production_config() enforces this at boot.
    RATE_LIMIT_REDIS_URL: str = ""

    # File storage
    STORAGE_BUCKET: str = "driver-documents"

    # Environment
    ENV: str = "development"

    @property
    def SECRET_KEY(self) -> str:
        return self.JWT_SECRET

    @property
    def debug(self) -> bool:
        return self.ENV.lower() == "development"


settings = Settings()
