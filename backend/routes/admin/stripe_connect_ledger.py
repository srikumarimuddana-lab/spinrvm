"""Admin endpoint for the connected-account ledger sync.

Pulls each driver's Stripe ``Payout`` (connected account → their bank) and
``BalanceTransaction`` (the full signed ledger: fees, refunds, payout
failures, adjustments) into ``driver_stripe_payouts`` / ``driver_stripe_ledger``
so the driver app can serve them from our own database.

Complements ``stripe_payout_sync.py``, which syncs the other leg — the
platform's Transfers *to* each driver — into ``payouts``.

No validate/commit split, deliberately, unlike every other importer here.
Those exist because their writes feed a T4A total, so a wrong number must
never land. **These tables are display/reconciliation only and are never
summed as income** (see migration 288's header), and every write is an
idempotent upsert keyed on the Stripe object id. A re-run converges and a
partial run resumes, so a dry run would add ceremony without protecting
anything.

Super-admin only, matching the payout sync: it reads a driver's full financial
history from Stripe and spends API quota against every connected account.

Reports carry only ``driver_id`` / ``acct_…`` identifiers — never names,
phones, or bank details (PIPEDA).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ...dependencies import get_admin_user
    from ...services import stripe_connect_ledger_service as ledger_svc
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from dependencies import get_admin_user  # type: ignore
    from services import stripe_connect_ledger_service as ledger_svc  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if (admin or {}).get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Connect ledger sync requires super_admin")


class ConnectLedgerSyncRequest(BaseModel):
    """Sync scope. Omit everything for all mapped drivers, full history."""

    model_config = {"extra": "forbid"}

    # min_length=1: an empty list must be a 422, not a silent full-fleet sync.
    # `_fetch_sync_targets` treats a falsy list as "all drivers", so
    # `{"driver_ids": []}` would otherwise read every connected account's full
    # history — the opposite of what an empty selection means, and there is no
    # dry-run step here to catch it.
    driver_ids: Optional[list[str]] = Field(None, min_length=1, max_length=500)
    # Calendar-year convenience: `year=2025` bounds the Stripe listing to that
    # year, which is how an operator reconciles one tax year at a time.
    year: Optional[int] = Field(None, ge=2015, le=2100)


def _year_window(year: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if year is None:
        return None, None
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp()) - 1


def _serialize(items: list) -> list[dict[str, str]]:
    return [{"row_ref": i.row_ref, "field": i.field, "message": i.message} for i in items]


@router.post("/stripe/connect-ledger/sync")
async def sync_connect_ledger(
    body: ConnectLedgerSyncRequest,
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Pull bank payouts + balance transactions for the selected drivers."""
    _require_super_admin(admin)

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")

    created_gte, created_lte = _year_window(body.year)
    try:
        result = await ledger_svc.sync_connect_ledger(
            stripe_secret,
            driver_ids=body.driver_ids,
            created_gte=created_gte,
            created_lte=created_lte,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Per-driver failures are already collected into result.errors; reaching
        # here means the sync itself could not run (DB unreachable, Stripe
        # client misconfigured). Surface 502 so the operator retries, rather
        # than a bare 500 — matching the sibling payout-sync endpoint and
        # CLAUDE.md's "never hand back a half-valid response" rule.
        logger.error("[CONNECT-LEDGER] sync failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not sync from Stripe. Please try again.") from e

    await log_admin_action(
        admin,
        "stripe_connect_ledger_sync",
        "driver_stripe_ledger",
        # entity_id is NOT NULL (migration 06) and log_admin_action swallows
        # its own failures — a None here would silently go unaudited. This
        # action is fleet-wide unless scoped, so describe the scope instead.
        f"year:{body.year or 'all'}",
        {
            "driver_ids": len(body.driver_ids or []) or "all",
            "year": body.year,
            "drivers_synced": result.drivers_synced,
            "payouts_upserted": result.payouts_upserted,
            "ledger_upserted": result.ledger_upserted,
            "errors": len(result.errors),
        },
    )

    return {
        "drivers_synced": result.drivers_synced,
        "accounts_read": result.accounts_read,
        "payouts_upserted": result.payouts_upserted,
        "ledger_upserted": result.ledger_upserted,
        "warnings": _serialize(result.warnings),
        "errors": _serialize(result.errors),
        "note": (
            "These records are for display and reconciliation only — they are NEVER summed "
            "into T4A income or payable balance. The same dollar appears as a Transfer in "
            "`payouts`, a Payout here, and two ledger legs."
        ),
    }
