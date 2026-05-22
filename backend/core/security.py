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
            except Exception:  # noqa: S110
                pass
    except Exception:
        logger.error("Firebase initialization failed — all FCM pushes will be silently dropped", exc_info=True)
