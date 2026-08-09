"""Connected-account ledger sync — bank payouts and balance transactions.

``stripe_payout_sync_service`` captures what the PLATFORM sent each driver
(Stripe ``Transfer``, platform → connected account) and materializes it into
``payouts``. This service captures the other half, which lives on the
connected account itself:

- ``stripe.Payout``            → ``driver_stripe_payouts``  (account → bank)
- ``stripe.BalanceTransaction``→ ``driver_stripe_ledger``   (full signed ledger)

Both are read with ``stripe_account=acct_…`` — they are Connect-scoped objects
that do not exist on the platform account, which is why nothing in the app had
them before.

**These are not income records.** One dollar earned appears in ``payouts``
(platform sent it), then in ``driver_stripe_payouts`` (the account sent it to
the bank), then twice more in ``driver_stripe_ledger`` (one row per leg, plus
fees). Summing them together over-reports a driver's income to the CRA. T4A
stays computed from completed rides + ``payouts.payout_type='stripe_sync'``
(``routes/drivers/tax_exports.py``) and payable balance from rides
(``routes/drivers/earnings.py``); this service deliberately touches neither.
Migration 288's header carries the same warning at the schema level.

Idempotency: the Stripe object id is the primary key, so a re-run upserts the
same rows. Payouts are upserted with UPDATE (their status moves
``pending → in_transit → paid|failed``); balance transactions are immutable
once created, so a conflict there is a no-op refresh of ``synced_at``.

Accounts: current AND superseded (migration 286), via
``stripe_payout_sync_service._driver_accounts`` — a driver retired by a
key-mode change keeps only the superseded value until they re-onboard, and
their bank payouts are exactly the history worth preserving. An unreachable
*superseded* account is a warning; an unreachable *current* account is an
error, matching the transfer sync's asymmetry so a wrong key cannot produce a
"successful" sync of nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import stripe

try:
    from ..supabase_client import supabase
    from ..utils.money import cents_to_dollars
    from ..utils.stripe_config import stripe_object_to_dict
    from ..utils.stripe_mode import is_missing_on_key
    from .stripe_payout_sync_service import _driver_accounts, _fetch_sync_targets
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from services.stripe_payout_sync_service import _driver_accounts, _fetch_sync_targets  # type: ignore
    from supabase_client import supabase  # type: ignore
    from utils.money import cents_to_dollars  # type: ignore
    from utils.stripe_config import stripe_object_to_dict  # type: ignore
    from utils.stripe_mode import is_missing_on_key  # type: ignore

logger = logging.getLogger(__name__)

PAYOUTS_TABLE = "driver_stripe_payouts"
LEDGER_TABLE = "driver_stripe_ledger"

# One list stream per account at a time; well under Stripe's rate limit.
MAX_STRIPE_CONCURRENCY = 4

# Supabase rejects very large single payloads; a busy driver-year can be
# thousands of balance transactions.
_UPSERT_CHUNK = 200


@dataclass
class LedgerSyncReportItem:
    row_ref: str  # driver_id / acct_… / po_… — never raw PII
    field: str
    message: str


@dataclass
class ConnectLedgerSyncResult:
    payouts_upserted: int = 0
    ledger_upserted: int = 0
    drivers_synced: int = 0
    accounts_read: int = 0
    warnings: list[LedgerSyncReportItem] = field(default_factory=list)
    errors: list[LedgerSyncReportItem] = field(default_factory=list)


def _iso(epoch: Any) -> str | None:
    if epoch in (None, ""):
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def _created_window(created_gte: int | None, created_lte: int | None) -> dict[str, Any]:
    created: dict[str, int] = {}
    if created_gte is not None:
        created["gte"] = created_gte
    if created_lte is not None:
        created["lte"] = created_lte
    return {"created": created} if created else {}


def _payout_row(p: dict[str, Any], driver_id: str, acct: str, now_iso: str) -> dict[str, Any]:
    """Stripe Payout → driver_stripe_payouts row. Money via Decimal, never float."""
    destination = p.get("destination")
    # `destination` is an id string unless the caller expanded it. Only read a
    # last4 when Stripe actually handed us the object — never guess.
    bank_last4 = destination.get("last4") if isinstance(destination, dict) else None
    return {
        "id": p["id"],
        "driver_id": driver_id,
        "stripe_account_id": acct,
        "amount": str(cents_to_dollars(p.get("amount") or 0)),
        "currency": (p.get("currency") or "cad").lower(),
        "status": p.get("status") or "unknown",
        "method": p.get("method"),
        "arrival_date": _iso(p.get("arrival_date")),
        "failure_code": p.get("failure_code"),
        "failure_message": p.get("failure_message"),
        "bank_last4": bank_last4,
        "created_at": _iso(p.get("created")),
        "synced_at": now_iso,
    }


def _ledger_row(t: dict[str, Any], driver_id: str, acct: str, now_iso: str) -> dict[str, Any]:
    """Stripe BalanceTransaction → driver_stripe_ledger row.

    `amount` and `net` stay SIGNED (Stripe's convention). That is deliberate:
    a signed column cannot be mistaken for an income total, and summing a
    period nets to the account's balance change.
    """
    source = t.get("source")
    return {
        "id": t["id"],
        "driver_id": driver_id,
        "stripe_account_id": acct,
        "type": t.get("type") or "unknown",
        "amount": str(cents_to_dollars(t.get("amount") or 0)),
        "fee": str(cents_to_dollars(t.get("fee") or 0)),
        "net": str(cents_to_dollars(t.get("net") or 0)),
        "currency": (t.get("currency") or "cad").lower(),
        "status": t.get("status"),
        "source": source.get("id") if isinstance(source, dict) else source,
        "description": t.get("description"),
        "available_on": _iso(t.get("available_on")),
        "created_at": _iso(t.get("created")),
        "synced_at": now_iso,
    }


def _list_connect(
    resource: Any,
    acct: str,
    stripe_secret: str,
    window: dict[str, Any],
    expand: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List every page of a Connect-scoped resource. Blocking; call in a thread."""
    params: dict[str, Any] = {"limit": 100, "api_key": stripe_secret, "stripe_account": acct, **window}
    if expand:
        params["expand"] = expand
    out: list[dict[str, Any]] = []
    for obj in resource.list(**params).auto_paging_iter():
        # stripe_object_to_dict, never dict(obj) — see utils/stripe_config:
        # paged StripeObjects are not Mappings on all SDK builds and dict()
        # falls back to integer indexing (KeyError: 0 in production).
        out.append(stripe_object_to_dict(obj))
    return out


# Stripe returns `destination` as a bare `ba_…` id unless asked to expand it.
# Without this, `_payout_row` can never find a last4 and the column — plus the
# driver-facing `bank_last4` field — is always null, which defeats the point of
# showing which bank account a payout went to.
_PAYOUT_EXPAND = ["data.destination"]


async def _upsert(table: str, rows: list[dict[str, Any]]) -> int:
    """Upsert on the Stripe id, UPDATING existing rows.

    Not ``insert_many_ignore_conflicts``: that passes ``ignore_duplicates=True``,
    which would freeze a payout at its first-seen status. A payout moves
    ``pending → in_transit → paid|failed`` after we first see it, so the row has
    to be refreshed on every sync.
    """
    if not rows or not supabase:
        return 0
    written = 0
    for i in range(0, len(rows), _UPSERT_CHUNK):
        chunk = rows[i : i + _UPSERT_CHUNK]

        def _fn(c=chunk):
            return supabase.table(table).upsert(c, on_conflict="id").execute()

        await asyncio.to_thread(_fn)
        written += len(chunk)
    return written


async def sync_connect_ledger(
    stripe_secret: str,
    *,
    driver_ids: list[str] | None = None,
    created_gte: int | None = None,
    created_lte: int | None = None,
    concurrency: int = MAX_STRIPE_CONCURRENCY,
) -> ConnectLedgerSyncResult:
    """Pull bank payouts + balance transactions for the selected drivers.

    Unlike the transfer sync there is no separate validate/commit: every write
    is an idempotent upsert keyed on the Stripe id, so a re-run converges and a
    partial run is simply resumed. Nothing here is an income record, so there
    is no T4A total to get wrong by writing early.
    """
    result = ConnectLedgerSyncResult()
    if not stripe_secret:
        result.errors.append(LedgerSyncReportItem("*", "stripe_not_configured", "stripe_secret_key is not set"))
        return result

    drivers = await asyncio.to_thread(_fetch_sync_targets, driver_ids)
    window = _created_window(created_gte, created_lte)
    now_iso = datetime.now(timezone.utc).isoformat()
    sem = asyncio.Semaphore(concurrency)

    async def one(driver: dict[str, Any]) -> None:
        driver_id = driver["id"]
        current = (driver.get("stripe_account_id") or "").strip()
        payout_rows: list[dict[str, Any]] = []
        ledger_rows: list[dict[str, Any]] = []
        read_any = False

        async with sem:
            for acct in _driver_accounts(driver):
                try:
                    payouts = await asyncio.to_thread(
                        _list_connect, stripe.Payout, acct, stripe_secret, window, _PAYOUT_EXPAND
                    )
                    txns = await asyncio.to_thread(
                        _list_connect, stripe.BalanceTransaction, acct, stripe_secret, window
                    )
                except stripe.error.StripeError as e:
                    unreachable = isinstance(e, stripe.error.PermissionError) or is_missing_on_key(e, acct)
                    # Same asymmetry as the transfer sync: a SUPERSEDED account
                    # we cannot reach is expected residue of a cutover, but an
                    # unreachable CURRENT account means the key or the platform
                    # is wrong and must not read as "nothing to sync".
                    if unreachable and acct != current:
                        logger.info("[CONNECT-LEDGER] superseded account %s not on this key; skipping", acct)
                        result.warnings.append(
                            LedgerSyncReportItem(
                                driver_id, "account_not_accessible", f"{acct} is not on this Stripe key"
                            )
                        )
                        continue
                    logger.error("[CONNECT-LEDGER] list failed for %s", acct, exc_info=True)
                    result.errors.append(
                        LedgerSyncReportItem(driver_id, "stripe_list_failed", f"could not read {acct}; re-run")
                    )
                    continue

                read_any = True
                result.accounts_read += 1
                payout_rows.extend(_payout_row(p, driver_id, acct, now_iso) for p in payouts)
                ledger_rows.extend(_ledger_row(t, driver_id, acct, now_iso) for t in txns)

        if not read_any:
            return

        # Await FIRST, then increment. `result.x += await _upsert(...)` reads
        # the attribute, suspends at the await, and writes back a value derived
        # from the stale read — so concurrent drivers clobber each other's
        # totals and the reported counts are near-meaningless (50 concurrent
        # increments can land as 1). The counts are this endpoint's entire
        # output, so that is the whole result being wrong, not a cosmetic slip.
        try:
            written_payouts = await _upsert(PAYOUTS_TABLE, payout_rows)
            written_ledger = await _upsert(LEDGER_TABLE, ledger_rows)
        except Exception:
            # A DB failure for one driver must not discard the warnings and
            # errors already collected for everyone else.
            logger.error("[CONNECT-LEDGER] upsert failed for driver %s", driver_id, exc_info=True)
            result.errors.append(
                LedgerSyncReportItem(driver_id, "db_write_failed", "could not persist synced rows; re-run")
            )
            return

        result.drivers_synced += 1
        result.payouts_upserted += written_payouts
        result.ledger_upserted += written_ledger

    # return_exceptions so one unexpected failure cannot abort the gather and
    # leave the remaining coroutines running uncancelled after we unwind.
    outcomes = await asyncio.gather(*(one(d) for d in drivers), return_exceptions=True)
    for driver, outcome in zip(drivers, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.error("[CONNECT-LEDGER] driver %s failed", driver["id"], exc_info=outcome)
            result.errors.append(LedgerSyncReportItem(driver["id"], "sync_failed", "unexpected failure; re-run"))
    logger.info(
        "[CONNECT-LEDGER] synced drivers=%s accounts=%s payouts=%s ledger=%s errors=%s",
        result.drivers_synced,
        result.accounts_read,
        result.payouts_upserted,
        result.ledger_upserted,
        len(result.errors),
    )
    return result
