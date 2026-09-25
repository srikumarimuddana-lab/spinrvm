"""Per-admin daily cap + single-action alert for admin money actions.

ROADMAP N23 / finding ADMIN-OPS-001: admins could credit/debit wallets
(routes/admin/wallet.py) and issue dispute refunds (routes/disputes.py) with
no amount limit and no alert. This is the interim control until a real
second-approver queue exists.

In-app disputes (and the refunds admins issued through them) were disabled
2026-09-25 (see docs/change-log/2026-09-25-disable-in-app-disputes.md);
``routes/disputes.py``'s resolve endpoint is now a 410 stub that writes
nothing. The ``dispute_resolved``/``dispute_refund`` handling below is dead
going forward and kept only so the daily-cap sum still counts any
historical ``dispute_resolved`` audit_logs rows written before that date.

Call :func:`enforce_admin_money_action_cap` immediately before the money moves.

- ``admin_money_daily_cap_per_admin`` (settings, migration 479): if set and
  ``abs(amount)`` plus what this admin already moved today (UTC) exceeds it,
  raise 403 and nothing moves.
- ``admin_money_alert_threshold``: if set and ``abs(amount)`` is at or above
  it, the action is still allowed (subject to the cap) but raises a warning
  log, an ``audit_logs`` row and a Sentry event tagged ``domain=admin``.

Both NULL (the shipped default) = no DB query for the daily sum (the
settings themselves are still read, from settings_loader's 60s cache), and
behaviour is unchanged.

"Already moved today" is summed from ``audit_logs``, which every covered
endpoint already writes with ``actor_id`` = the admin: ``wallet_credit`` /
``wallet_debit`` rows carry ``details.amount``; historical ``dispute_resolved``
rows (none written since in-app disputes were disabled 2026-09-25) carry
``details.refund_amount`` and count only when ``details.refund_issued`` is
True (a Stripe refund was actually created — not a rejected dispute, not one
resolved while admin_dispute_refunds_enabled was off, not manual_required).
No new table. Super-admins are NOT exempt — the point is to
bound what one compromised admin session can move, and a super-admin session
is the most valuable one to compromise.

Known limit: check-then-act, not a lock. Two concurrent actions by the same
admin can both pass the check; admin_wallet_limit rate-limits the wallet side.
"""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from fastapi import HTTPException

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_PAGE = 1000

# admin_money_threshold_alert / admin_money_cap_blocked rows (written below)
# are deliberately NOT counted: they record attempts, not money moved.
_COUNTED_ACTIONS = ["wallet_credit", "wallet_debit", "dispute_resolved"]

_UNAVAILABLE = "Admin money-action limits could not be checked, so nothing was moved. Retry shortly."


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _original(e: Exception) -> Any:
    """DatabaseError's str() is only "Database operation failed"; log the cause."""
    details = getattr(e, "details", None)
    return details.get("original", e) if isinstance(details, dict) else e


def _setting_amount(settings: Dict[str, Any], key: str) -> Optional[Decimal]:
    raw = settings.get(key)
    if raw in (None, ""):
        return None
    return _round(_d(raw))


def _row_amount(row: Dict[str, Any]) -> Decimal:
    """Absolute money moved by one audit_logs row (0 if it moved none)."""
    details = row.get("details") or {}
    if row.get("action") == "dispute_resolved":
        # Historical rows only (routes/disputes.py's resolve endpoint is a
        # 410 stub since 2026-09-25 and writes none of these anymore).
        # Only rows where it actually issued a refund count.
        if details.get("refund_issued") is not True:
            return Decimal("0")
        raw = details.get("refund_amount")
    else:
        raw = details.get("amount")
    if raw in (None, ""):
        return Decimal("0")
    return abs(_d(raw))


async def _moved_today(admin_id: str) -> Decimal:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = Decimal("0")
    offset = 0
    while True:
        rows = await db_supabase.get_rows(
            "audit_logs",
            {
                "actor_id": admin_id,
                "action": {"$in": _COUNTED_ACTIONS},
                "created_at": {"$gte": day_start.isoformat()},
            },
            order="created_at",
            limit=_PAGE,
            offset=offset,
            columns="action,details",
        )
        total += sum((_row_amount(r) for r in rows), Decimal("0"))
        if len(rows) < _PAGE:
            return _round(total)
        offset += _PAGE


def _sentry_threshold_alert(context: Dict[str, str]) -> None:
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "Admin money action at or above alert threshold",
            level="warning",
            tags={"domain": "admin", "surface": "backend", "spinr_alert": "admin_money_threshold"},
            contexts={"admin_money": context},
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never block the action
        logger.debug("admin money threshold: Sentry unavailable: %s", sentry_err)


async def enforce_admin_money_action_cap(
    admin: Dict[str, Any],
    amount: Any,
    *,
    action: str,
    resource: str,
    resource_id: str,
) -> None:
    """Raise 403 if this action would take the admin over today's cap, 503 if
    the check itself cannot run; alert (but allow) at/above the threshold.

    ``action`` is a short label for logs (``wallet_credit``, ``wallet_debit``;
    ``dispute_refund`` is a retired label, no longer passed by any caller
    since in-app dispute refunds were disabled 2026-09-25). Logs carry IDs
    and amounts only — no PII.
    """
    admin_id = admin["id"]
    this_amount = abs(_round(_d(amount)))

    try:
        settings = await get_app_settings()
        cap = _setting_amount(settings, "admin_money_daily_cap_per_admin")
        threshold = _setting_amount(settings, "admin_money_alert_threshold")
    except Exception as e:
        logger.error(
            "admin money cap: settings read failed admin_id=%s action=%s resource_id=%s: %r",
            admin_id,
            action,
            resource_id,
            _original(e),
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from e

    if cap is not None:
        try:
            moved_today = await _moved_today(admin_id)
        except Exception as e:
            logger.error(
                "admin money cap: daily-total query failed admin_id=%s action=%s resource_id=%s: %r",
                admin_id,
                action,
                resource_id,
                _original(e),
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from e

        if moved_today + this_amount > cap:
            logger.warning(
                "admin money cap: blocked admin_id=%s action=%s resource_id=%s amount=%s moved_today=%s cap=%s",
                admin_id,
                action,
                resource_id,
                this_amount,
                moved_today,
                cap,
            )
            await log_admin_action(
                admin,
                "admin_money_cap_blocked",
                resource,
                resource_id,
                {
                    "money_action": action,
                    "amount": str(this_amount),
                    "moved_today": str(moved_today),
                    "cap": str(cap),
                },
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Daily admin money-action cap of ${cap} reached: ${moved_today} already moved "
                    f"today (UTC) and this action is ${this_amount}. Nothing was moved. Ask another "
                    "admin to process it, or wait until 00:00 UTC."
                ),
            )

    if threshold is not None and this_amount >= threshold:
        logger.warning(
            "admin money alert: action at/above threshold admin_id=%s action=%s resource_id=%s amount=%s threshold=%s",
            admin_id,
            action,
            resource_id,
            this_amount,
            threshold,
        )
        await log_admin_action(
            admin,
            "admin_money_threshold_alert",
            resource,
            resource_id,
            {"money_action": action, "amount": str(this_amount), "threshold": str(threshold)},
        )
        _sentry_threshold_alert(
            {
                "admin_id": str(admin_id),
                "money_action": action,
                "resource_id": str(resource_id),
                "amount": str(this_amount),
                "threshold": str(threshold),
            }
        )
