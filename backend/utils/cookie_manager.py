import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Response

try:
    from ..core.config import settings
except ImportError:
    from core.config import settings


class CookieManager:
    """
    Centralized HTTP-only cookie management for auth tokens.
    All Set-Cookie headers must go through this class for consistency.
    """

    @staticmethod
    def set_auth_cookie(
        response: Response,
        token: str,
        ttl_minutes: int = 15
    ) -> None:
        """
        Set HTTP-only authentication token cookie.

        Args:
            response: FastAPI Response object
            token: JWT access token
            ttl_minutes: Time-to-live in minutes (default 15 for rider/driver, 60 for admin)
        """
        response.set_cookie(
            key="auth_token",
            value=token,
            max_age=ttl_minutes * 60,
            expires=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
            httponly=True,
            secure=settings.is_production_like,
            samesite="Strict",
            domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
            path="/"
        )

    @staticmethod
    def set_refresh_cookie(
        response: Response,
        token: str,
        ttl_days: int = 30
    ) -> None:
        """
        Set HTTP-only refresh token cookie.
        Longer TTL; used only for token rotation.

        Args:
            response: FastAPI Response object
            token: JWT refresh token
            ttl_days: Time-to-live in days (default 30)
        """
        response.set_cookie(
            key="refresh_token",
            value=token,
            max_age=ttl_days * 86400,
            expires=datetime.now(timezone.utc) + timedelta(days=ttl_days),
            httponly=True,
            secure=settings.is_production_like,
            samesite="Strict",
            domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
            path="/"
        )

    @staticmethod
    def clear_auth_cookie(response: Response) -> None:
        """Clear authentication token cookie."""
        response.delete_cookie(
            key="auth_token",
            domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
            path="/"
        )

    @staticmethod
    def clear_refresh_cookie(response: Response) -> None:
        """Clear refresh token cookie."""
        response.delete_cookie(
            key="refresh_token",
            domain=settings.COOKIE_DOMAIN if settings.COOKIE_DOMAIN else None,
            path="/"
        )

    @staticmethod
    def clear_all_cookies(response: Response) -> None:
        """Clear all authentication cookies (logout)."""
        CookieManager.clear_auth_cookie(response)
        CookieManager.clear_refresh_cookie(response)
