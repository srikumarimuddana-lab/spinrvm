import json
import logging

import firebase_admin
from firebase_admin import credentials as firebase_credentials

from .config import settings

logger = logging.getLogger(__name__)


def _report_firebase_init_failure() -> None:
    """C97 recommendation #2 (Sentry half): give the loud `logger.error(...)`
    calls below an explicitly-tagged Sentry event.

    This module uses stdlib `logging`, which `server.py`'s `LoggingIntegration
    (event_level="ERROR", ...)` already auto-captures — but that auto-capture
    does not gain a `domain` tag: `tags_from_log_extra` (the helper that lifts
    `domain`/`surface`/... into Sentry tags) is only wired to the loguru bridge
    (`server.py`'s loguru sink / `utils/sentry_runtime.py`), not to stdlib
    `logging`'s `extra=`. Without an explicit capture here these events would
    arrive taggable only by `surface` (stamped globally by
    `utils.sentry_scrub.scrub_event`), not by `domain`, unlike every other
    Sentry capture site in this codebase. `env` is not passed explicitly —
    it's already the global `environment=` set on `sentry_sdk.init(...)`
    in server.py, not a per-event tag.

    Same lazy-import-inside-try/except shape as `utils/driver_statement_pdf.py`'s
    capture site: telemetry must never be the reason Firebase init fails to
    complete, so any error reporting this fails is swallowed after a debug log.
    """
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_exception(tags={"domain": "drivers", "surface": "backend"})
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break startup
        logger.debug(f"Firebase init failure: Sentry capture unavailable: {sentry_err}")


def init_firebase():
    """Initialize Firebase Admin SDK"""
    try:
        if settings.FIREBASE_SERVICE_ACCOUNT_JSON:
            sa_info = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
            cred = firebase_credentials.Certificate(sa_info)
            try:
                firebase_admin.initialize_app(cred)
            except ValueError:
                # App already initialized
                pass
        else:
            try:
                firebase_admin.initialize_app()
            except Exception:
                # C97: this branch used to be `except Exception: pass  # noqa: S110`
                # — completely silent, no log at all. On a non-GCP host (Fly/Railway,
                # both non-GCP) with FIREBASE_SERVICE_ACCOUNT_JSON unset, Application
                # Default Credentials reliably fail to resolve, and the app booted
                # looking fully healthy with FCM permanently non-functional — the SDK
                # only ever failed later, per-push, as a scattered log line. Logging
                # loudly here (matching the outer handler's own message) is the
                # minimum fix; see docs/audit/2026-09-10-driver-app-notification-
                # delivery-audit.md for the full investigation.
                logger.error(
                    "Firebase initialization failed (no FIREBASE_SERVICE_ACCOUNT_JSON "
                    "set, and Application Default Credentials are also unavailable) — "
                    "all FCM pushes will be silently dropped",
                    exc_info=True,
                )
                _report_firebase_init_failure()
    except Exception:
        logger.error("Firebase initialization failed — all FCM pushes will be silently dropped", exc_info=True)
        _report_firebase_init_failure()
