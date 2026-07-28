"""Meta (Facebook) Conversions API transport.

A thin HTTPS wrapper over ``POST https://graph.facebook.com/v21.0/{DATASET_ID}/events``.
Deliberately not the ``facebook_business`` SDK: that package pulls a large
dependency tree into a pinned requirements file for what is ultimately one
JSON POST, and its sync-only client would need a thread-pool hop anyway.

Two rules govern everything in this module:

1. **A Meta failure must never break a Spinr flow.** Conversion tracking is
   marketing telemetry. A signup, a ride completion, or a payment settlement
   is never allowed to fail, stall, or roll back because Meta returned a 500.
   Every public function here swallows its own transport errors and reports
   them via ``logger.error`` + a metric. This is a deliberate, documented
   exception to the repo-wide "never swallow errors" rule (CLAUDE.md), which
   exists to protect DB/auth/payment/dispatch correctness — none of which this
   module participates in. The error is still surfaced loudly in logs and
   metrics; it just doesn't propagate into the caller's request.

2. **Raw PII never leaves this process.** Meta's Advanced Matching expects
   SHA-256 hex digests of *normalized* values. The mobile SDKs hash for you;
   on this server-side path we hash ourselves. ``_hash`` is the only way user
   data enters a payload, and it always normalizes first.

Config lives in the ``app_settings`` DB row (see settings_loader), not .env,
so dataset ids and the access token rotate without a redeploy — matching how
Stripe/Twilio/Maps credentials are already handled in this codebase.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

try:
    from ..settings_loader import get_app_settings
except ImportError:  # pragma: no cover - exercised by `python -m` vs top-level
    from settings_loader import get_app_settings  # type: ignore

try:
    from .metrics import inc as _metric_inc
    from .metrics import observe as _metric_observe
except ImportError:  # pragma: no cover
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = "https://graph.facebook.com"

# Meta's endpoint is not on any Spinr latency SLA — these sends are always
# backgrounded off the request path — but an unbounded timeout would leak
# asyncio tasks if Graph hangs.
_REQUEST_TIMEOUT_SECONDS = 10.0

# Transport-level retries only (network error / 5xx / 429). A 4xx other than
# 429 is a payload or auth problem: retrying it just burns quota and fills the
# log with the same error, so we fail fast and surface it.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5


# ── PII normalization + hashing ─────────────────────────────────────────────


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Lowercase + strip. Returns None for anything that isn't an address.

    Meta's spec is lowercase, whitespace-trimmed, no other transformation —
    notably Gmail dot-stripping and plus-addressing are NOT applied, because
    Meta hashes the address the advertiser's own users typed on their side too.
    Normalizing further would reduce matches, not improve them.
    """
    if not email:
        return None
    cleaned = email.strip().lower()
    # A bare "@" check, not full RFC validation: the goal is to avoid hashing
    # obvious junk (empty strings, "null", a phone number pasted in the email
    # column) into a digest that can never match anything on Meta's side.
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        return None
    return cleaned


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """E.164 digits only — no '+', no spaces, no punctuation.

    Spinr stores phones already E.164-normalized (auth.py runs validate_phone
    before persisting), so this is mostly a defensive strip. Meta wants the
    country code included but the leading '+' removed: '+13065551234' becomes
    '13065551234'.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    # Shorter than this cannot be a country-code-prefixed number; hashing it
    # produces a digest guaranteed never to match.
    if len(digits) < 8:
        return None
    return digits


def hash_email(email: Optional[str]) -> Optional[str]:
    normalized = normalize_email(email)
    return _sha256(normalized) if normalized else None


def hash_phone(phone: Optional[str]) -> Optional[str]:
    normalized = normalize_phone(phone)
    return _sha256(normalized) if normalized else None


def hash_external_id(user_id: Optional[str]) -> Optional[str]:
    """Hash the Spinr user id used as Meta's ``external_id``.

    Meta accepts external_id either raw or hashed, but requires that you pick
    one and stay consistent — a raw id on one event and a hashed id on another
    are two different users as far as matching is concerned. We always hash, so
    a Spinr user id never appears in Meta's systems in a form that could be
    joined back against a support ticket or a log line.
    """
    if not user_id:
        return None
    cleaned = str(user_id).strip().lower()
    return _sha256(cleaned) if cleaned else None


def build_user_data(
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    user_id: Optional[str] = None,
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    client_ip: Optional[str] = None,
    client_user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble Meta's ``user_data`` block with every PII field hashed.

    Keys are omitted entirely when the source value is missing — Meta treats a
    present-but-empty field as a failed match signal, which drags Event Match
    Quality *down* rather than leaving it neutral.

    fbp/fbc are Meta's own browser/click identifiers and are NOT hashed (they
    are already opaque Meta-issued tokens, and hashing them makes them
    unmatchable). They're only populated if the app forwarded them.
    """
    data: Dict[str, Any] = {}

    # Meta expects these three as arrays — it supports multiple values per user.
    if (hashed := hash_email(email)) is not None:
        data["em"] = [hashed]
    if (hashed := hash_phone(phone)) is not None:
        data["ph"] = [hashed]
    if (hashed := hash_external_id(user_id)) is not None:
        data["external_id"] = [hashed]

    if fbp:
        data["fbp"] = fbp
    if fbc:
        data["fbc"] = fbc
    if client_ip:
        data["client_ip_address"] = client_ip
    if client_user_agent:
        data["client_user_agent"] = client_user_agent

    return data


# ── Config ──────────────────────────────────────────────────────────────────


class MetaCapiConfig:
    """Resolved Meta config for one send. Empty ``access_token`` ⇒ disabled."""

    __slots__ = ("access_token", "rider_dataset_id", "driver_dataset_id", "test_event_code")

    def __init__(
        self,
        access_token: str,
        rider_dataset_id: str,
        driver_dataset_id: str,
        test_event_code: str,
    ) -> None:
        self.access_token = access_token
        self.rider_dataset_id = rider_dataset_id
        self.driver_dataset_id = driver_dataset_id
        self.test_event_code = test_event_code

    def dataset_for(self, app: str) -> str:
        return self.driver_dataset_id if app == "driver" else self.rider_dataset_id


async def get_config() -> MetaCapiConfig:
    """Read Meta credentials out of the app_settings row.

    A settings-read failure is logged and treated as "tracking disabled"
    rather than raised: this is called from inside already-backgrounded send
    tasks, and there is no caller left to handle an exception usefully.
    """
    try:
        settings = await get_app_settings()
    except Exception:
        logger.error("meta_capi: failed to load app_settings — treating tracking as disabled", exc_info=True)
        return MetaCapiConfig("", "", "", "")

    return MetaCapiConfig(
        access_token=str(settings.get("meta_capi_access_token") or "").strip(),
        rider_dataset_id=str(settings.get("meta_rider_dataset_id") or "").strip(),
        driver_dataset_id=str(settings.get("meta_driver_dataset_id") or "").strip(),
        test_event_code=str(settings.get("meta_test_event_code") or "").strip(),
    )


# ── Transport ───────────────────────────────────────────────────────────────


def _should_retry(status_code: Optional[int]) -> bool:
    """Network error (None), 429, or any 5xx is worth another attempt."""
    if status_code is None:
        return True
    return status_code == 429 or status_code >= 500


async def send_meta_event(
    *,
    dataset_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    user_data: Dict[str, Any],
    custom_data: Optional[Dict[str, Any]] = None,
    action_source: str = "app",
    access_token: Optional[str] = None,
    test_event_code: Optional[str] = None,
) -> bool:
    """POST one event to a Meta dataset. Returns True on a 2xx.

    ``event_id`` is the de-duplication key. When the same logical event is also
    fired by the mobile SDK, both sides MUST carry this identical value or Meta
    counts the conversion twice. See META_EVENTS.md for which side mints it.

    Never raises. A False return means "logged and metered, carry on".
    """
    if not dataset_id or not access_token:
        # Not an error: this is the state before the Meta credentials are
        # pasted into app_settings, and every call site is expected to be live
        # in that state. debug, not warning — otherwise every signup and every
        # ride completion logs a line until config lands.
        logger.debug("meta_capi: dataset/token not configured — skipping %s", event_name)
        return False

    payload: Dict[str, Any] = {
        "data": [
            {
                "event_name": event_name,
                "event_time": event_time,
                "event_id": event_id,
                "action_source": action_source,
                "user_data": user_data,
                **({"custom_data": custom_data} if custom_data else {}),
            }
        ]
    }
    # Env-gated: routes events to the Events Manager "Test Events" tab instead
    # of live reporting, so the integration can be verified before it counts.
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{dataset_id}/events"
    started = time.monotonic()

    last_status: Optional[int] = None
    last_error: str = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    json=payload,
                    params={"access_token": access_token},
                )
            last_status = response.status_code
            if response.is_success:
                _metric_inc("spinr_meta_capi_events_total", {"event": event_name, "outcome": "success"})
                _metric_observe("spinr_meta_capi_send_duration_ms", (time.monotonic() - started) * 1000.0)
                logger.info(
                    "meta_capi: sent %s event_id=%s attempt=%d",
                    event_name,
                    event_id,
                    attempt,
                    extra={"meta_event": event_name, "meta_event_id": event_id},
                )
                return True

            # Truncate: Graph error bodies can be long, and the useful part
            # (code/message) is always at the front.
            last_error = response.text[:500]
            if not _should_retry(last_status):
                break
        except Exception as exc:  # httpx transport errors, DNS, TLS, timeouts
            last_status = None
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    _metric_inc("spinr_meta_capi_events_total", {"event": event_name, "outcome": "failed"})
    logger.error(
        "meta_capi: failed to send %s event_id=%s after %d attempt(s) status=%s error=%s",
        event_name,
        event_id,
        _MAX_ATTEMPTS,
        last_status,
        last_error,
        extra={"meta_event": event_name, "meta_event_id": event_id},
    )
    return False


async def send_events(
    *,
    dataset_id: str,
    events: List[Dict[str, Any]],
    access_token: Optional[str] = None,
    test_event_code: Optional[str] = None,
) -> bool:
    """Send several events to one dataset in a single request.

    Only used where two events genuinely fire from the same moment (Purchase +
    FirstRide on a first paid ride) — one round trip instead of two, and both
    land with identical event_time so Events Manager doesn't show them a
    second apart.
    """
    if not events:
        return True
    if not dataset_id or not access_token:
        logger.debug("meta_capi: dataset/token not configured — skipping %d event(s)", len(events))
        return False

    payload: Dict[str, Any] = {"data": events}
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{dataset_id}/events"
    names = ",".join(str(e.get("event_name")) for e in events)
    started = time.monotonic()

    last_status: Optional[int] = None
    last_error: str = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload, params={"access_token": access_token})
            last_status = response.status_code
            if response.is_success:
                for event in events:
                    _metric_inc(
                        "spinr_meta_capi_events_total",
                        {"event": str(event.get("event_name")), "outcome": "success"},
                    )
                _metric_observe("spinr_meta_capi_send_duration_ms", (time.monotonic() - started) * 1000.0)
                logger.info("meta_capi: sent batch [%s] attempt=%d", names, attempt)
                return True
            last_error = response.text[:500]
            if not _should_retry(last_status):
                break
        except Exception as exc:
            last_status = None
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    for event in events:
        _metric_inc(
            "spinr_meta_capi_events_total",
            {"event": str(event.get("event_name")), "outcome": "failed"},
        )
    logger.error(
        "meta_capi: failed to send batch [%s] after %d attempt(s) status=%s error=%s",
        names,
        _MAX_ATTEMPTS,
        last_status,
        last_error,
    )
    return False
