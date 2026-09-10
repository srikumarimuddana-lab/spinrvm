import json
import logging

import firebase_admin
from firebase_admin import credentials as firebase_credentials

from .config import settings

logger = logging.getLogger(__name__)


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
    except Exception:
        logger.error("Firebase initialization failed — all FCM pushes will be silently dropped", exc_info=True)
