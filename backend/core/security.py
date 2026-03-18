import firebase_admin
from firebase_admin import credentials as firebase_credentials
import json
from .config import settings

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
                pass
    except Exception as e:
        print(f"Firebase initialization failed: {e}")