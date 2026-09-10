from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: str = "development"
    PORT: int = 8585
    LOG_LEVEL: str = "info"
    HERMES_SECRET: str = ""

    # Spinr backend
    SPINR_BACKEND_URL: str = "https://api-spinr.spinr.ca"
    SPINR_API_KEY: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ALLOWED_USERS: str = ""
    HERMES_PUBLIC_URL: str = ""

    # Zoho shared OAuth
    ZOHO_CLIENT_ID: str = ""
    ZOHO_CLIENT_SECRET: str = ""
    ZOHO_REFRESH_TOKEN: str = ""
    ZOHO_ACCOUNTS_URL: str = "https://accounts.zoho.com"

    # Zoho Cliq
    ZOHO_CLIQ_WEBHOOK_TOKEN: str = ""
    ZOHO_CLIQ_BOT_API_URL: str = ""

    # Zoho Desk
    ZOHO_DESK_ORG_ID: str = ""
    ZOHO_DESK_API_URL: str = "https://desk.zoho.com/api/v1"

    @property
    def allowed_telegram_users(self) -> set[int]:
        if not self.TELEGRAM_ALLOWED_USERS:
            return set()
        return {int(uid.strip()) for uid in self.TELEGRAM_ALLOWED_USERS.split(",") if uid.strip()}

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


settings = Settings()
