"""Dual-run cutover monitoring signals (audit finding A34 / P3.1).

During the dual-run window (old vendor app + new Spinr app both live until
the old app's decommission), nothing in this codebase would surface a
cross-system collision — an imported driver going online here while still
active on the old platform, or a payout landing on a Stripe Connect account
the old app may also pay into. These helpers add the three cheapest
observable signals identified by the 2026-08-15 cutover audit
(``docs/audit/2026-08-15-dual-run-cutover/P1-financial-identity-reconciliation.md``,
§P3.1):

1. ``audit_logs`` row ``legacy_driver_first_go_online`` — once per imported
   driver, the first time they actually flip online in the new app.
2. Counter ``spinr_drivers_go_online_total{is_legacy_import}`` — every
   actual offline→online flip, labeled so an imported-driver activation
   burst is dashboardable during launch week.
3. Counter ``spinr_payments_legacy_driver_payout_total`` — every settled
   Stripe transfer to a legacy-imported driver.

Feature flag: ``dual_run_monitoring_enabled`` in the ``app_settings`` DB row
(default **enabled** when unset). Rollback without redeploy = set it false in
the admin dashboard. The flag exists purely as a kill switch; these helpers
are observation-only.

Contract: like ``utils/audit_logger``, failures here are logged but never
re-raised — a monitoring write must not break go-online or a payout that
already settled. That is a deliberate, documented exception to the
"don't soften errors" rule: the underlying money/state mutation has already
succeeded by the time these run, and re-raising would fail the request the
driver just completed.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .audit_logger import log_user_action
    from .metrics import inc as _metric_inc
except ImportError:  # pragma: no cover - top-level run layout
    import db_supabase
    from settings_loader import get_app_settings
    from utils.audit_logger import log_user_action
    from utils.metrics import inc as _metric_inc

logger = logging.getLogger(__name__)

FLAG_KEY = "dual_run_monitoring_enabled"
FIRST_GO_ONLINE_KEY = "first_go_online_at"


def is_legacy_driver(driver: Dict[str, Any]) -> bool:
    """True when the driver row was created by a legacy import.

    ``legacy_import_metadata`` is ``NOT NULL DEFAULT '{}'::jsonb`` — an empty
    dict means organic signup, anything else means imported (see
    ``utils/legacy_rides.py`` for the same convention on rides).
    """
    return bool(driver.get("legacy_import_metadata") or None)


async def _enabled() -> bool:
    try:
        settings = await get_app_settings()
        return bool(settings.get(FLAG_KEY, True))
    except Exception:  # settings read must never block monitoring's callers
        logger.exception("dual_run_monitor: app_settings lookup failed; treating as enabled")
        return True


async def record_go_online_flip(driver: Dict[str, Any], user: Dict[str, Any]) -> None:
    """Call on an actual offline→online flip (``status_flipped and is_online``).

    Emits the labeled go-online counter for every driver, and — once per
    imported driver — the ``legacy_driver_first_go_online`` audit row plus a
    ``first_go_online_at`` stamp inside ``legacy_import_metadata`` so the
    audit row fires exactly once.
    """
    try:
        if not await _enabled():
            return
        legacy = is_legacy_driver(driver)
        _metric_inc(
            "spinr_drivers_go_online_total",
            {"is_legacy_import": "true" if legacy else "false"},
        )
        if not legacy:
            return
        meta = dict(driver.get("legacy_import_metadata") or {})
        if meta.get(FIRST_GO_ONLINE_KEY):
            return
        meta[FIRST_GO_ONLINE_KEY] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one(
            "drivers",
            {"id": driver["id"]},
            {"$set": {"legacy_import_metadata": meta}},
        )
        await log_user_action(
            user,
            "legacy_driver_first_go_online",
            "drivers",
            str(driver["id"]),
            details={
                "source": meta.get("source"),
                "batch": meta.get("batch"),
                "first_go_online_at": meta[FIRST_GO_ONLINE_KEY],
            },
        )
        logger.info("[DUAL-RUN] legacy-imported driver first go-online driver_id=%s", driver["id"])
    except Exception:
        logger.exception("dual_run_monitor: go-online signal failed (monitoring only, not re-raised)")


async def record_legacy_payout(driver: Dict[str, Any], payout_id: str, amount: Any) -> None:
    """Call only once the money can no longer be reversed away from the driver
    (standard payout: after the terminal write following ``Transfer.create``;
    instant payout: after the Step 2 ``Payout.create`` succeeds — counting at
    Step 1 would overcount transfers later reversed by a failed Step 2).

    Counts payouts to legacy-imported drivers — the population whose Stripe
    Connect accounts may also be paid by the old platform during dual-run.
    """
    try:
        if not is_legacy_driver(driver) or not await _enabled():
            return
        _metric_inc("spinr_payments_legacy_driver_payout_total")
        logger.info(
            "[DUAL-RUN] payout settled to legacy-imported driver driver_id=%s payout_id=%s amount=%s",
            driver.get("id"),
            payout_id,
            amount,
        )
    except Exception:
        logger.exception("dual_run_monitor: payout signal failed (monitoring only, not re-raised)")
