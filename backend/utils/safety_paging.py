"""Best-effort on-call paging for rider/driver SOS (ACTION_ITEMS.md B15(b)).

Today a successful SOS produces an admin WS broadcast, a safety
distribution-list email, and a ``logger.critical()`` line
(``trigger_emergency`` in ``backend/routes/rides/safety.py`` +
``notify_safety_team`` in ``backend/features.py``) — none of which reaches an
on-call person who isn't actively watching the admin dashboard or a log
stream. This module adds a fourth, purely additive channel: one webhook POST
to a paging provider.

Two rules govern everything here, mirroring ``utils/meta_capi.py``:

1. **A paging failure must never affect the SOS response.** The rider/driver
   already has (or hasn't) an incident_id by the time this runs. Every
   public function here swallows its own transport errors and reports them
   via ``logger.error``; it never raises. Callers should still wrap the call
   in their own try/except (matching every other best-effort SOS side
   effect in ``trigger_emergency``) — belt and suspenders, not a substitute.
2. **Defaults to disabled.** No real PagerDuty/Opsgenie account exists yet
   to configure this against. With ``sos_paging_webhook_url`` unset (the
   shipped default), ``page_on_call`` logs at debug and returns False —
   identical in spirit to how Meta CAPI and the LMS integration ship dark
   until an admin pastes real credentials into ``app_settings``.

Config lives in the ``app_settings`` DB row (see ``settings_loader``), not
``.env``, matching how Stripe/Twilio/Google Maps/Meta credentials are
already handled in this codebase (CLAUDE.md "Settings in DB").

PIPEDA: the payload carries only IDs (incident_id, ride_id,
reported_by_user_id) and a geohashed area (``utils.pii.geohash``) — never a
full name, email, phone number, or exact address. Mirrors the redaction
already applied to Sentry/log emission sites (``utils/pii.py``,
``utils/audit_logger.py``) and to ``features.notify_safety_team``'s own
CRITICAL log line, which likewise omits the raw incident description.

Payload defaults to PagerDuty Events API v2 shape
(``{"routing_key", "event_action", "payload": {...}}``). Swapping to
Opsgenie (or another provider whose webhook accepts/adapts that same shape)
is a config change to ``sos_paging_webhook_url`` — not a rewrite of this
module. A provider needing a genuinely different payload shape would need a
small follow-up change here; that is out of scope for this dark-shipped
first cut.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

import httpx

try:
    from ..settings_loader import get_app_settings
    from .pii import geohash
except ImportError:  # pragma: no cover - exercised by `python -m` vs top-level
    from settings_loader import get_app_settings  # type: ignore
    from utils.pii import geohash  # type: ignore

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 5.0


class SosPagingConfig:
    """Resolved paging config for one call. Empty ``webhook_url`` ⇒ disabled."""

    __slots__ = ("webhook_url", "routing_key")

    def __init__(self, webhook_url: str, routing_key: str) -> None:
        self.webhook_url = webhook_url
        self.routing_key = routing_key

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)


async def get_config() -> SosPagingConfig:
    """Read SOS paging credentials out of the app_settings row.

    A settings-read failure is logged and treated as "paging disabled"
    rather than raised — mirrors ``utils/meta_capi.py::get_config``, since
    the caller of ``page_on_call`` is already inside a best-effort branch
    with no useful way to react to this failing.
    """
    try:
        settings = await get_app_settings()
    except Exception:
        logger.error("safety_paging: failed to load app_settings — treating paging as disabled", exc_info=True)
        return SosPagingConfig("", "")

    return SosPagingConfig(
        webhook_url=str(settings.get("sos_paging_webhook_url") or "").strip(),
        routing_key=str(settings.get("sos_paging_routing_key") or "").strip(),
    )


def _build_payload(incident: Dict[str, Any], config: SosPagingConfig) -> Dict[str, Any]:
    incident_id = incident.get("id")
    ride_id = incident.get("ride_id")
    role = incident.get("role") or "rider"
    category = incident.get("category") or "unknown"
    reported_by = incident.get("reported_by_user_id")
    area = geohash(incident.get("latitude"), incident.get("longitude"))

    return {
        "routing_key": config.routing_key,
        "event_action": "trigger",
        # De-dup on the incident, not the request — a client retry after a
        # transient DB error mints a *new* incident_id (see
        # trigger_emergency's try/except), so this never collapses two
        # genuinely distinct SOS presses into one page.
        "dedup_key": f"spinr-sos-{incident_id}",
        "payload": {
            "summary": f"Spinr SOS — {role} — ride {ride_id or '(none)'}",
            "source": "spinr-backend",
            "severity": "critical",
            "component": "safety",
            "group": "sos",
            "class": category,
            "custom_details": {
                "incident_id": incident_id,
                "ride_id": ride_id,
                "role": role,
                "category": category,
                "reported_by_user_id": reported_by,
                # Geohashed to a ~5km cell (utils.pii.geohash) — never raw
                # lat/lng. "?" when no coordinates were supplied.
                "area_geohash": area,
            },
        },
    }


async def page_on_call(incident: Dict[str, Any]) -> bool:
    """POST one page for a safety incident. Returns True on a 2xx.

    Never raises. A False return means "logged and metered, carry on" —
    identical contract to ``meta_capi.send_meta_event``. Single attempt, no
    retry: paging is itself a best-effort notification layered on top of the
    already-persisted ``safety_incidents`` row and the WS/email/log channels
    in ``notify_safety_team``, so a slow retry loop here would only delay
    the caller's own (already best-effort, already-non-blocking) SOS
    handling for no real benefit — the incident is not lost if this fails,
    just unpaged.
    """
    config = await get_config()
    if not config.configured:
        logger.debug(
            "safety_paging: sos_paging_webhook_url not configured — skipping page for incident %s",
            incident.get("id"),
        )
        return False

    incident_id = incident.get("id")
    payload = _build_payload(incident, config)

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(config.webhook_url, json=payload)
        duration_ms = (time.monotonic() - started) * 1000.0
        if response.is_success:
            logger.info(
                "safety_paging: page sent incident_id=%s status=%d duration_ms=%.0f",
                incident_id,
                response.status_code,
                duration_ms,
            )
            return True
        logger.error(
            "safety_paging: page failed incident_id=%s status=%d body=%s",
            incident_id,
            response.status_code,
            response.text[:300],
        )
        return False
    except Exception as exc:  # httpx transport errors, DNS, TLS, timeouts
        logger.error(
            "safety_paging: page request failed incident_id=%s error=%s: %s",
            incident_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return False
