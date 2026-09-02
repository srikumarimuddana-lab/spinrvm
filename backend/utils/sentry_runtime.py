"""Shared Sentry process init for the API and the dedicated worker."""

from __future__ import annotations

from typing import Any

from loguru import logger

try:
    from ..core.config import settings
    from .sentry_scrub import pipeda_sentry_options, tags_from_log_extra
except ImportError:
    from core.config import settings  # type: ignore
    from utils.sentry_scrub import pipeda_sentry_options, tags_from_log_extra  # type: ignore


def init_backend_sentry(*, process_name: str = "spinr backend") -> None:
    """Initialize Sentry with PIPEDA scrub options, or log loudly if unset.

    ``sentry_sdk.init`` lives here so both ``server.py`` and ``worker.py``
    share the same PIPEDA controls. Do not re-set include_local_variables /
    send_default_pii / before_send / before_breadcrumb at the call site.
    """
    sentry_dsn = settings.sentry_dsn if hasattr(settings, "sentry_dsn") and settings.sentry_dsn else None

    if sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        _StarletteMiddleware = None
        try:
            from sentry_sdk.integrations.starlette import StarletteMiddleware as _StarletteMiddleware
        except Exception as exc:  # noqa: BLE001
            logger.debug("Sentry Starlette integration unavailable: {}", exc)

        integrations = [
            FastApiIntegration(transaction_style="url"),
            LoggingIntegration(event_level="ERROR", level="WARNING"),
        ]
        if _StarletteMiddleware is not None:
            integrations.append(_StarletteMiddleware())

        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=integrations,
            traces_sample_rate=0.1,
            profiles_sample_rate=0.1,
            environment=settings.ENV if hasattr(settings, "ENV") else "production",
            **pipeda_sentry_options(),
        )

        def _loguru_sentry_sink(message: Any) -> None:
            record = message.record
            tags = tags_from_log_extra(record.get("extra") or {})
            scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
            with scope_cm() as scope:
                for key, val in tags.items():
                    scope.set_tag(key, val)
                exc_info = record["exception"]
                if exc_info is not None and exc_info.value is not None:
                    sentry_sdk.capture_exception(exc_info.value)
                else:
                    sentry_sdk.capture_message(record["message"], level="error")

        logger.add(_loguru_sentry_sink, level="ERROR")
        logger.info("Sentry SDK initialized for error monitoring process={}", process_name)
        if getattr(settings, "ENV", "development") == "production":
            sentry_sdk.capture_message(
                f"{process_name} started — Sentry pipeline verified",
                level="info",
            )
        return

    if getattr(settings, "ENV", "development") == "production":
        logger.error(
            "SENTRY_DSN is not set in production — {} errors are NOT being "
            "reported to Sentry. Deploy the SENTRY_DSN secret to restore error tracking.",
            process_name,
        )
