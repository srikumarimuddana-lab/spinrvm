"""Stripe payout-history sync — rebuild ``payouts`` from Stripe Transfer truth.

The legacy migration imported drivers and mapped their Stripe Connect accounts
(``stripe_mapping_import_service``), but the old app's payout history was NOT
imported — the operator does not trust the legacy export. Stripe itself is the
authoritative record of money actually sent to each driver: every driver payout
on this platform is a Stripe ``Transfer`` with ``destination=acct_…`` (standard
payouts and step 1 of instant payouts; nothing else creates Transfers — see
routes/drivers/payouts.py).

This service lists those Transfers per driver and materializes the MISSING ones
as ``payouts`` rows (``payout_type='stripe_sync'``, ``status='completed'``):

- Transfers already tracked by an app-written payout row are skipped, matched
  on ``stripe_transfer_id`` OR ``stripe_payout_id`` (the standard payout path
  historically wrote the transfer id into ``stripe_payout_id`` too). The app's
  reserve-then-transfer ordering guarantees new-app transfers always have a
  row, so an untracked transfer is legacy-app money by construction.
- Fully-reversed transfers are skipped (the money came back); a partial
  reversal syncs the NET amount with a warning.
- Row ids are deterministic (``stripe-sync-{transfer_id}``) so re-runs and
  concurrent commits converge instead of duplicating.

The synced rows are tax/history records, NOT balance events: payable_balance
derives from new-app completed rides, which do not contain the legacy earnings
these transfers paid out. ``get_driver_balance`` therefore EXCLUDES
``payout_type='stripe_sync'`` from its money-out deduction
(routes/drivers/earnings.py) — without that exclusion every migrated driver's
balance would go negative and block withdrawals. T4A generation (the annual
job and the driver endpoint) ADDS these rows so the slip reports legacy-era
income paid through Stripe.

Validate/commit contract mirrors the other importers: ``build_plan`` never
writes; ``commit_plan`` refuses while errors exist. Reports carry only
``driver_id`` / ``acct_…`` / ``tr_…`` identifiers — never names, phones, or
bank details (PIPEDA).

Known limitation: transfers are listed against the driver's CURRENT
``stripe_account_id``. A driver migrated onto a brand-new account (Stripe
support scenario B in docs/runbooks/stripe-legacy-migration.md) has their old
account's transfer history left behind; the validate report surfaces a
``no_transfers`` warning for such drivers so the operator can follow up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    from ..supabase_client import supabase
    from .driver_import_service import _select_in
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from services.driver_import_service import _select_in
    from supabase_client import supabase  # type: ignore  # noqa: F401

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover
    import db_supabase  # type: ignore

try:
    from ..utils.money import cents_to_dollars, to_decimal
except ImportError:  # pragma: no cover
    from utils.money import cents_to_dollars, to_decimal  # type: ignore

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "stripe_transfer_sync"
PAYOUT_TYPE = "stripe_sync"
PAYOUT_LABEL = "Synced from Stripe transfer history"

# Stay far below Stripe's rate limit; one list stream per driver at a time.
MAX_STRIPE_CONCURRENCY = 4

ZERO = Decimal("0")


def payout_id_for(transfer_id: str) -> str:
    """Deterministic payouts.id for a synced transfer — re-runs converge."""
    return f"stripe-sync-{transfer_id}"


@dataclass
class SyncReportItem:
    row_ref: str  # driver_id or tr_… — never raw PII
    field: str
    message: str


@dataclass
class StripePayoutSyncPlan:
    batch: str
    payouts_to_insert: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[SyncReportItem] = field(default_factory=list)
    errors: list[SyncReportItem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _epoch(iso_or_year: Any) -> int | None:
    """Best-effort conversion of a datetime to epoch seconds (UTC)."""
    if iso_or_year is None:
        return None
    if isinstance(iso_or_year, datetime):
        return int(iso_or_year.timestamp())
    return int(iso_or_year)


def _transfer_created_iso(created_epoch: int) -> str:
    return datetime.fromtimestamp(int(created_epoch), tz=timezone.utc).isoformat()


def _fetch_sync_targets(driver_ids: list[str] | None) -> list[dict[str, Any]]:
    """Drivers that have a Stripe Connect account mapped (sync candidates)."""
    query = supabase.table("drivers").select("id,stripe_account_id")
    if driver_ids:
        rows: list[dict[str, Any]] = []
        for i in range(0, len(driver_ids), 200):
            batch = driver_ids[i : i + 200]
            rows.extend(supabase.table("drivers").select("id,stripe_account_id").in_("id", batch).execute().data or [])
    else:
        rows = query.execute().data or []
    return [d for d in rows if d.get("stripe_account_id")]


def _prefetch_tracked_transfer_ids(transfer_ids: list[str]) -> set[str]:
    """Transfer ids already represented by ANY payouts row.

    Matches three ways so no app-written or previously-synced row is ever
    duplicated: stripe_transfer_id (instant + modern standard payouts),
    stripe_payout_id (older standard payouts stored the transfer id there),
    and the deterministic synced-row id (previous runs of this sync).
    """
    tracked: set[str] = set()
    if not transfer_ids:
        return tracked
    for r in _select_in("payouts", "stripe_transfer_id", "stripe_transfer_id", transfer_ids):
        if r.get("stripe_transfer_id"):
            tracked.add(r["stripe_transfer_id"])
    for r in _select_in("payouts", "stripe_payout_id", "stripe_payout_id", transfer_ids):
        if r.get("stripe_payout_id"):
            tracked.add(r["stripe_payout_id"])
    wanted_ids = [payout_id_for(t) for t in transfer_ids]
    for r in _select_in("payouts", "id", "id", wanted_ids):
        rid = r.get("id") or ""
        if rid.startswith("stripe-sync-"):
            tracked.add(rid[len("stripe-sync-") :])
    return tracked


async def _list_transfers_for_account(
    account_id: str,
    stripe_secret: str,
    created_gte: int | None,
    created_lte: int | None,
) -> list[dict[str, Any]]:
    """List ALL transfers destined for one connected account, off the loop."""
    import stripe

    params: dict[str, Any] = {"destination": account_id, "limit": 100, "api_key": stripe_secret}
    created: dict[str, int] = {}
    if created_gte is not None:
        created["gte"] = created_gte
    if created_lte is not None:
        created["lte"] = created_lte
    if created:
        params["created"] = created

    def _list() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in stripe.Transfer.list(**params).auto_paging_iter():
            out.append(
                {
                    "id": t["id"],
                    "amount": int(t.get("amount") or 0),
                    "amount_reversed": int(t.get("amount_reversed") or 0),
                    "currency": (t.get("currency") or "").lower(),
                    "created": int(t.get("created") or 0),
                }
            )
        return out

    return await asyncio.to_thread(_list)


async def build_plan(
    stripe_secret: str,
    batch: str,
    *,
    driver_ids: list[str] | None = None,
    created_gte: int | None = None,
    created_lte: int | None = None,
    concurrency: int = MAX_STRIPE_CONCURRENCY,
) -> StripePayoutSyncPlan:
    """Full dry-run plan: never writes. Lists Stripe transfers per mapped
    driver, drops everything already tracked in ``payouts``, and stages the
    remainder as synced rows. Per-driver Stripe failures become plan errors so
    a partial listing can never silently under-report a driver's history."""
    plan = StripePayoutSyncPlan(batch=batch)
    if not stripe_secret:
        plan.errors.append(SyncReportItem("*", "stripe_not_configured", "stripe_secret_key is not set in app settings"))
        return plan

    import stripe

    drivers = await asyncio.to_thread(_fetch_sync_targets, driver_ids)
    now_iso = datetime.now(timezone.utc).isoformat()

    sem = asyncio.Semaphore(concurrency)
    transfers_by_driver: dict[str, list[dict[str, Any]]] = {}

    async def one(driver: dict[str, Any]) -> None:
        acct = driver["stripe_account_id"]
        async with sem:
            try:
                transfers_by_driver[driver["id"]] = await _list_transfers_for_account(
                    acct, stripe_secret, created_gte, created_lte
                )
            except stripe.error.StripeError:
                # A driver we cannot list must BLOCK commit — a silent skip
                # would under-report their T4A income for the synced era.
                logger.error("[STRIPE-SYNC] transfer list failed for %s", acct, exc_info=True)
                plan.errors.append(
                    SyncReportItem(driver["id"], "stripe_list_failed", f"could not list transfers for {acct}; re-run")
                )

    await asyncio.gather(*(one(d) for d in drivers))

    all_transfer_ids = sorted({t["id"] for ts in transfers_by_driver.values() for t in ts})
    tracked = await asyncio.to_thread(_prefetch_tracked_transfer_ids, all_transfer_ids)

    transfers_seen = 0
    already_tracked = 0
    fully_reversed = 0
    partial_reversals = 0
    drivers_with_transfers = 0
    sum_planned = ZERO
    sum_by_year: dict[str, float] = {}

    for driver_id in sorted(transfers_by_driver):
        transfers = transfers_by_driver[driver_id]
        if transfers:
            drivers_with_transfers += 1
        else:
            plan.warnings.append(
                SyncReportItem(driver_id, "no_transfers", "mapped account has no transfer history in the window")
            )
        for t in transfers:
            transfers_seen += 1
            if t["id"] in tracked:
                already_tracked += 1
                continue
            if t["currency"] != "cad":
                # Should be unreachable on this CA-only platform; refuse to
                # fold foreign-currency money into CAD T4A totals silently.
                plan.errors.append(
                    SyncReportItem(t["id"], "currency", f"transfer currency is {t['currency']}, expected cad")
                )
                continue
            net_cents = t["amount"] - t["amount_reversed"]
            if net_cents <= 0:
                fully_reversed += 1
                continue
            if t["amount_reversed"] > 0:
                partial_reversals += 1
                plan.warnings.append(
                    SyncReportItem(t["id"], "partial_reversal", "transfer partially reversed; net amount synced")
                )
            amount = cents_to_dollars(net_cents)
            created_iso = _transfer_created_iso(t["created"])
            sum_planned += amount
            year_key = created_iso[:4]
            sum_by_year[year_key] = float(to_decimal(sum_by_year.get(year_key, 0)) + amount)
            plan.payouts_to_insert.append(
                {
                    "id": payout_id_for(t["id"]),
                    "driver_id": driver_id,
                    # float only at the serialization boundary (matches the
                    # booking importer); all arithmetic above stays Decimal.
                    "amount": float(amount),
                    # 'completed' is inert to the payout retry loop, the Stripe
                    # reconciler, and the migration-250 reservation guard.
                    # get_driver_balance excludes it via payout_type (see module
                    # docstring) so it never distorts payable_balance.
                    "status": "completed",
                    "payout_type": PAYOUT_TYPE,
                    "stripe_transfer_id": t["id"],
                    "bank_name": PAYOUT_LABEL,
                    "created_at": created_iso,
                    "processed_at": created_iso,
                    "updated_at": now_iso,
                }
            )

    plan.stats = {
        "batch": batch,
        "drivers_scanned": len(drivers),
        "drivers_with_transfers": drivers_with_transfers,
        "transfers_seen": transfers_seen,
        "already_tracked": already_tracked,
        "fully_reversed_skipped": fully_reversed,
        "partial_reversals": partial_reversals,
        "payouts_planned": len(plan.payouts_to_insert),
        "sum_planned": float(sum_planned),
        "sum_by_year": dict(sorted(sum_by_year.items())),
    }
    return plan


def commit_plan(plan: StripePayoutSyncPlan) -> dict[str, Any]:
    """Apply a clean plan. Sync (call via ``asyncio.to_thread`` from routes).

    Deterministic ids make this idempotent: a row that landed in a previous
    (or concurrent) run raises 23505 on its chunk, which falls back to
    row-by-row inserts that skip duplicates — never a second row for the same
    transfer, never a partial batch lost.
    """
    if plan.errors:
        raise RuntimeError("plan has errors; refusing to commit")

    inserted = 0
    skipped_existing = 0
    for i in range(0, len(plan.payouts_to_insert), 200):
        chunk = plan.payouts_to_insert[i : i + 200]
        try:
            supabase.table("payouts").insert(chunk).execute()
            inserted += len(chunk)
        except Exception as e:
            if not _is_unique_violation(e):
                raise
            for row in chunk:
                try:
                    supabase.table("payouts").insert(row).execute()
                    inserted += 1
                except Exception as row_e:
                    if _is_unique_violation(row_e):
                        skipped_existing += 1
                        continue
                    raise
    if skipped_existing:
        logger.info(
            "[STRIPE-SYNC] batch=%s: %d row(s) already present (concurrent/previous run), skipped",
            plan.batch,
            skipped_existing,
        )
    return {"inserted": inserted, "skipped_existing": skipped_existing}


def _is_unique_violation(exc: Exception) -> bool:
    """Postgres 23505 via PostgREST/supabase-py."""
    return getattr(exc, "code", None) == "23505" or "duplicate key value" in str(exc)


async def driver_synced_earnings_for_year(driver_id: str, year: int) -> Decimal:
    """Sum of synced legacy payouts for one driver in a calendar year (UTC).

    T4A input: legacy-era income the driver was PAID through Stripe, attributed
    to the year of the transfer (CRA reports T4A on amounts paid). Only
    ``payout_type='stripe_sync'`` rows count — app-native payouts are cash-outs
    of ride earnings that T4A already reports from ``rides.driver_earnings``,
    and ``legacy_import`` offset rows pair with imported rides for the same
    reason; adding either would double-count.
    """
    rows = await db_supabase.get_rows(
        "payouts",
        {
            "driver_id": driver_id,
            "payout_type": PAYOUT_TYPE,
            "created_at": {
                "$gte": f"{year}-01-01T00:00:00+00:00",
                "$lt": f"{year + 1}-01-01T00:00:00+00:00",
            },
        },
        limit=10000,
    )
    return sum((to_decimal(r.get("amount") or 0) for r in rows), ZERO)
