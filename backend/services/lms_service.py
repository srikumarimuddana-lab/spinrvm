"""Client for the Spinr driver LMS (training platform) integration API.

The LMS is a separate deployment (spinr-lms) that tracks driver training:
registration, course progress, certificates, quiz attempts, and reminder
communications. It exposes GET /api/integration/driver-training?phone=<number>
authenticated with an x-api-key header. Drivers are matched across the two
systems by phone number (last 10 digits).

Credentials live in the app_settings table (lms_api_base_url / lms_api_key)
so they can be rotated from the admin dashboard without a redeploy, matching
the Stripe/Twilio/Maps pattern. Responses are cached in Redis for a few
minutes keyed on a hash of the phone number (never the raw number).
"""

import hashlib
import json
import logging
import os
import re
from urllib.parse import urlparse

import httpx

try:
    from ..settings_loader import get_app_settings
    from ..utils.redis_client import redis_get, redis_set
except ImportError:  # pragma: no cover - dual import path
    from settings_loader import get_app_settings  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 10.0
_CACHE_TTL_SECONDS = 300  # LMS data changes slowly; 5 min keeps the modal snappy
_DEFAULT_ALLOWED_LMS_HOSTS = frozenset({"training.spinr.ca"})
_LOCAL_DEV_LMS_HOSTS = frozenset({"localhost", "127.0.0.1"})


class LMSNotConfiguredError(Exception):
    """lms_api_base_url / lms_api_key missing from app_settings."""


class LMSUpstreamError(Exception):
    """The LMS API is unreachable or returned an error response."""


def _allowed_lms_hosts() -> frozenset[str]:
    """Return hosts the backend may send the LMS API key to.

    The base URL lives in editable app settings, but the credential must only
    be sent to a deployment host pinned outside app_settings. Operators can
    add staging/blue-green hosts through LMS_ALLOWED_HOSTS in the deployment
    environment; settings admins cannot use the dashboard to repoint the key
    at an attacker-controlled host or an internal SSRF target.
    """
    configured = {
        host.strip().lower()
        for host in os.getenv("LMS_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    return frozenset(configured or _DEFAULT_ALLOWED_LMS_HOSTS)


def _is_local_dev_lms_url(parsed) -> bool:
    return (
        os.getenv("ENV", "development").lower() != "production"
        and parsed.scheme == "http"
        and (parsed.hostname or "").lower() in _LOCAL_DEV_LMS_HOSTS
    )


def _validate_lms_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host or parsed.username or parsed.password:
        raise LMSNotConfiguredError("lms_api_base_url must be an absolute URL without embedded credentials")
    if _is_local_dev_lms_url(parsed):
        return base_url.rstrip("/")
    if parsed.scheme != "https" or host not in _allowed_lms_hosts():
        logger.error("[lms_service] Refusing to send LMS API key to unpinned host")
        raise LMSNotConfiguredError("lms_api_base_url host is not allowlisted")
    return base_url.rstrip("/")


def normalize_phone(phone: str) -> str | None:
    """Reduce a phone number to its last 10 digits for cross-system matching.

    Spinr stores E.164 (+1XXXXXXXXXX); the LMS stores free-form digits from
    Excel uploads. The shared canonical form is the 10-digit national number.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 10:
        return None
    return digits[-10:]


def _cache_key(normalized_phone: str) -> str:
    # Hash the phone so the raw number never lands in Redis keys (PIPEDA).
    digest = hashlib.sha256(normalized_phone.encode()).hexdigest()[:16]
    return f"lms:training:{digest}"


async def _lms_credentials() -> tuple[str, str]:
    settings_row = await get_app_settings() or {}
    raw_base_url = settings_row.get("lms_api_base_url") or ""
    api_key = settings_row.get("lms_api_key") or ""
    if not raw_base_url or not api_key:
        raise LMSNotConfiguredError("lms_api_base_url / lms_api_key not set in app settings")
    return _validate_lms_base_url(raw_base_url), api_key


async def get_training_by_phone(phone: str, force_refresh: bool = False) -> dict:
    """Fetch a driver's LMS training record matched by phone number.

    Returns the LMS payload: {"success": bool, "matched": bool, "data": {...}}.
    Raises ValueError for an unusable phone, LMSNotConfiguredError when the
    integration is not set up, and LMSUpstreamError on transport/API failure.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        raise ValueError("Driver phone number is missing or has fewer than 10 digits")

    cache_key = _cache_key(normalized)
    if not force_refresh:
        cached = await redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass  # corrupt cache entry — fall through to a live fetch

    base_url, api_key = await _lms_credentials()

    # PIPEDA: never log the response body or the stringified exception —
    # the request URL carries the phone number and the LMS error body may
    # echo it (or driver name/email). Status code + exception type only.
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{base_url}/api/integration/driver-training",
                params={"phone": normalized},
                headers={"x-api-key": api_key},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("[lms_service] LMS API returned HTTP %s", e.response.status_code)
        raise LMSUpstreamError(f"LMS API error (HTTP {e.response.status_code})") from e
    except httpx.HTTPError as e:
        logger.error("[lms_service] LMS API request failed: %s", type(e).__name__)
        raise LMSUpstreamError("Failed to reach the LMS API") from e
    except ValueError as e:  # resp.json() — 200 with a non-JSON body
        logger.error("[lms_service] LMS API returned a non-JSON 200 response")
        raise LMSUpstreamError("LMS returned a non-JSON response") from e

    # An application-level failure (success != true) is an outage, not
    # "driver has no training record" — surface it instead of caching it
    # or letting the route report not_found_in_lms.
    if not isinstance(payload, dict) or payload.get("success") is not True:
        logger.error("[lms_service] LMS API returned an unsuccessful payload")
        raise LMSUpstreamError("LMS returned an unsuccessful or malformed response")

    await redis_set(cache_key, json.dumps(payload), ttl=_CACHE_TTL_SECONDS)
    return payload
