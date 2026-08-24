"""Legacy wallet import — parse, validate, and commit previous-app wallet balances.

Imports prepaid rider/driver wallet credits from the previous (MongoDB-backed)
app's ``wallets`` collection into Spinr's ``wallets``/``wallet_transactions``
tables. Per the migration audit (docs/audit/2026-08-19-full-mongodb-export-
collection-inventory.md), this collection is small (13 rows in the reference
export) but carries real, owed money: ~$900 of rider wallet credit and ~$60 of
driver referral-wallet credit, all under legacy ``type`` values ``from_bank``
(rider top-up), ``from_driver_refer`` / ``for_owner_refer`` (driver referral
reward), and legacy ``status`` values ``add`` / ``deduct``.

**Column-name disclaimer (read before running against a real export):** the
column names below (``_id``, ``customer_id``, ``driver_id``, ``amount``,
``type``, ``status``, ``created_at``) are inferred from this same export's
sibling collections' consistent naming convention (``bookings.csv``,
``customers.csv``, ``driverearnings.csv`` all use exactly these names for the
equivalent fields) -- they have NOT been confirmed against a real
``wallets.csv`` header row, since no prior audit session captured one.
``validate_required_columns`` fails loudly (as plan errors, refusing commit)
rather than silently defaulting if any expected column is absent, so a
mismatch surfaces immediately on the first dry-run instead of corrupting a
balance. Confirm the real header row before the first real run.

Money safety, mirroring corporate_wallet_apply_delta / wallet_apply_delta's
own design (migrations 196/249): every credit/debit goes through the
row-locked ``wallet_apply_delta`` RPC, never a plain balance UPDATE. That RPC
is itself idempotent -- it dedups on ``(wallet_id, reference_id, type)``
inside the row lock -- so this importer does not need its own separate
"already imported" prefetch (unlike booking_import_service.py/
driver_import_service.py, which write plain rows with no DB-side dedup of
their own). ``reference_id`` is deterministic (``legacy-wallet-<old_id>``),
so re-running commit_plan() against the same plan, or re-building and
re-committing after a partial failure, is always safe: an already-applied
row comes back ``deduped=True`` with the original transaction untouched, and
the balance is never mutated twice for the same legacy entry.

A ``deduct`` entry that would take a wallet below its $0 floor is refused
loudly by the RPC (``wallet_below_floor``) rather than silently clamped --
consistent with the admin debit path's behavior, since a legacy migration
should never guess at a partial charge.

Follows the same validate-then-commit contract as booking_import_service.py,
driver_import_service.py, and rider_import_service.py. Reports carry only row
numbers + legacy wallet-entry ids -- never rider/driver names, phones, or
raw amounts beyond what's needed to reconcile the batch total.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase  # type: ignore

try:
    from ..utils.money import to_decimal
except ImportError:
    from utils.money import to_decimal  # type: ignore

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "legacy_mongo_wallet_import"

ZERO = Decimal("0")

REQUIRED_WALLET_COLUMNS = {
    "_id",
    "customer_id",
    "driver_id",
    "amount",
    "type",
    "status",
}

# Legacy `type` -> the live wallet_transactions.type this maps onto (must be
# one of the values wallet_transactions_type_check allows -- migration 199).
# `for_owner_refer` reads as the referring party's own reward for referring
# someone else, same live bucket as a driver's own `from_driver_refer`
# credit -- both are a referral reward landing in *this* row's owner's
# wallet, not a payment for a ride. A legacy `type` outside this map is
# never guessed at; see validate/build_plan below.
LEGACY_TYPE_TO_TXN_TYPE = {
    "from_bank": "top_up",
    "from_driver_refer": "referral_reward",
    "for_owner_refer": "referral_reward",
}

LEGACY_STATUS_TO_SIGN = {
    "add": 1,
    "deduct": -1,
}


@dataclass
class ImportReportItem:
    row_num: int
    old_id: str
    field: str
    message: str


@dataclass
class WalletImportPlan:
    # Each entry: {user_id, type_, delta (Decimal), reference_id, description,
    # metadata}. Built in CSV row order; commit_plan() applies them in the
    # same order, one wallet_apply_delta RPC call per entry.
    deltas_to_apply: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ImportReportItem] = field(default_factory=list)
    errors: list[ImportReportItem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def normalize_phone(phone: str) -> str:
    """Legacy 10-digit NANP number -> E.164, matching users.phone storage."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return (phone or "").strip()


def parse_money(value: str) -> Decimal:
    """Parse a legacy money field to a 2-dp Decimal (blank -> 0)."""
    raw = (value or "").strip()
    if not raw:
        return ZERO
    return to_decimal(raw)


def _index_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["_id"]: r for r in rows if r.get("_id")}


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def validate_required_columns(rows: list[dict[str, str]], plan: WalletImportPlan) -> None:
    if not rows:
        plan.errors.append(ImportReportItem(0, "<file>", "wallets_csv", "wallets CSV is empty"))
        return
    missing = REQUIRED_WALLET_COLUMNS - set(rows[0].keys())
    for col in sorted(missing):
        plan.errors.append(ImportReportItem(0, "<file>", col, "wallets CSV is missing required column"))


def build_plan(
    wallet_rows: list[dict[str, str]],
    customers: list[dict[str, str]],
    drivers: list[dict[str, str]],
    *,
    batch: str,
) -> WalletImportPlan:
    plan = WalletImportPlan()
    now_iso = datetime.now(timezone.utc).isoformat()

    validate_required_columns(wallet_rows, plan)
    if plan.errors:
        # Column-shape errors make every row-level field lookup below
        # meaningless (KeyError-prone, not just wrong) -- refuse to guess at
        # rows against a CSV shape that doesn't match what this importer
        # expects. Matches driver_import_service.validate_required_columns'
        # own refuse-early contract.
        plan.stats = {"rows_read": len(wallet_rows), "target_rows": 0}
        return plan

    customers_by_id = _index_by_id(customers)
    drivers_by_id = _index_by_id(drivers)

    # Prefetch phones for every row's owning party (rider or driver, never
    # both -- see the loop below) so users/drivers are matched with two
    # bulk queries instead of one per row.
    legacy_phones: set[str] = set()
    for r in wallet_rows:
        cust_id = (r.get("customer_id") or "").strip()
        drv_id = (r.get("driver_id") or "").strip()
        if cust_id:
            phone = (customers_by_id.get(cust_id) or {}).get("phone", "")
            if phone:
                legacy_phones.add(normalize_phone(phone))
        if drv_id:
            phone = (drivers_by_id.get(drv_id) or {}).get("phone", "")
            if phone:
                legacy_phones.add(normalize_phone(phone))

    users_by_phone: dict[str, dict[str, Any]] = {}
    driver_users_by_phone: dict[str, dict[str, Any]] = {}
    if legacy_phones:
        phones = sorted(legacy_phones)
        for u in _select_in("users", "id,phone", "phone", phones):
            if u.get("phone") and u["phone"] not in users_by_phone:
                users_by_phone[u["phone"]] = u
        # drivers.user_id, not drivers.id: wallets.user_id references the
        # shared users table (both riders and drivers wallet off the same
        # column -- see routes/wallet.py's module docstring), so a driver
        # row's OWN wallet is keyed by their linked users.id, not their
        # drivers.id.
        for d in _select_in("drivers", "id,user_id,phone", "phone", phones):
            if d.get("phone") and d.get("user_id") and d["phone"] not in driver_users_by_phone:
                driver_users_by_phone[d["phone"]] = d

    seen_old_ids: set[str] = set()
    skipped_missing_id = 0
    skipped_duplicate_id = 0
    skipped_zero_amount = 0
    skipped_unmatched = 0
    rider_rows = 0
    driver_rows = 0
    sum_add = ZERO
    sum_deduct = ZERO

    for idx, r in enumerate(wallet_rows, start=1):
        old_id = (r.get("_id") or "").strip()
        if not old_id:
            plan.errors.append(ImportReportItem(idx, "<none>", "_id", "wallet entry is missing its legacy _id"))
            skipped_missing_id += 1
            continue
        if old_id in seen_old_ids:
            plan.errors.append(ImportReportItem(idx, old_id, "_id", "duplicate legacy wallet entry _id in CSV"))
            skipped_duplicate_id += 1
            continue
        seen_old_ids.add(old_id)

        cust_id = (r.get("customer_id") or "").strip()
        drv_id = (r.get("driver_id") or "").strip()

        user_id: str | None = None
        if cust_id:
            cust = customers_by_id.get(cust_id)
            if cust:
                user_row = users_by_phone.get(normalize_phone(cust.get("phone", "")))
                if user_row:
                    user_id = user_row["id"]
        elif drv_id:
            drv = drivers_by_id.get(drv_id)
            if drv:
                driver_row = driver_users_by_phone.get(normalize_phone(drv.get("phone", "")))
                if driver_row:
                    user_id = driver_row["user_id"]

        if not user_id:
            # Never fabricate an owner: a wallet entry whose rider/driver
            # hasn't been matched to an existing Spinr account (not yet
            # imported, or phone mismatch) is skipped and reported --
            # re-running after that account is imported will pick it up
            # (this importer does its own phone lookup fresh every build_plan
            # call, unlike a one-time prefetch keyed to a specific batch).
            plan.warnings.append(
                ImportReportItem(idx, old_id, "customer_id/driver_id", "no matching rider/driver account found")
            )
            skipped_unmatched += 1
            continue

        amount = parse_money(r.get("amount", ""))
        if amount <= ZERO:
            plan.warnings.append(ImportReportItem(idx, old_id, "amount", "amount is zero or unparseable, skipped"))
            skipped_zero_amount += 1
            continue

        legacy_type = (r.get("type") or "").strip()
        txn_type = LEGACY_TYPE_TO_TXN_TYPE.get(legacy_type)
        if txn_type is None:
            plan.errors.append(
                ImportReportItem(idx, old_id, "type", f"unrecognized legacy wallet type {legacy_type!r}")
            )
            continue

        legacy_status = (r.get("status") or "").strip()
        sign = LEGACY_STATUS_TO_SIGN.get(legacy_status)
        if sign is None:
            plan.errors.append(
                ImportReportItem(idx, old_id, "status", f"unrecognized legacy wallet status {legacy_status!r}")
            )
            continue

        delta = amount * sign
        if sign > 0:
            sum_add += amount
        else:
            sum_deduct += amount
        if cust_id:
            rider_rows += 1
        else:
            driver_rows += 1

        plan.deltas_to_apply.append(
            {
                "user_id": user_id,
                "type_": txn_type,
                "delta": delta,
                "reference_id": f"legacy-wallet-{old_id}",
                "description": f"Legacy wallet balance ({legacy_type}, imported)",
                "metadata": {
                    "source": IMPORT_SOURCE,
                    "batch": batch,
                    "old_wallet_entry_id": old_id,
                    "old_legacy_type": legacy_type,
                    "old_legacy_status": legacy_status,
                    "imported_at": now_iso,
                },
            }
        )

    plan.stats = {
        "rows_read": len(wallet_rows),
        "skipped_missing_id": skipped_missing_id,
        "skipped_duplicate_id": skipped_duplicate_id,
        "skipped_unmatched": skipped_unmatched,
        "skipped_zero_amount": skipped_zero_amount,
        "target_rows": len(plan.deltas_to_apply),
        "rider_rows": rider_rows,
        "driver_rows": driver_rows,
        "sum_add": float(sum_add),
        "sum_deduct": float(sum_deduct),
        "sum_net": float(sum_add - sum_deduct),
    }
    return plan


def _get_or_create_wallet_id(user_id: str) -> str:
    """Return the user's wallet id, creating an inactive-free wallet row if
    none exists yet. Mirrors routes/wallet.py's get_or_create_wallet (sync
    version -- commit_plan runs inside asyncio.to_thread, same as every
    other importer's commit_plan)."""
    import uuid as _uuid

    rows = supabase.table("wallets").select("id").eq("user_id", user_id).limit(1).execute().data or []
    if rows:
        return rows[0]["id"]

    wallet_id = str(_uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    supabase.table("wallets").insert(
        {
            "id": wallet_id,
            "user_id": user_id,
            "balance": "0.00",
            "currency": "CAD",
            "is_active": True,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    ).execute()
    return wallet_id


def commit_plan(plan: WalletImportPlan) -> list[dict[str, Any]]:
    """Apply every planned delta via the row-locked wallet_apply_delta RPC.

    Returns one result dict per planned entry, in plan order:
    ``{"reference_id", "status": "applied"|"deduped"|"failed", ...}``. Unlike
    booking_import_service.commit_plan (which lets any DB error abort the
    whole batch), each row here is independent -- a failure applying one
    legacy wallet entry (e.g. "wallet_below_floor" on a deduct larger than
    the current balance) is logged loudly (CLAUDE.md: never swallow a
    money-path error) and recorded in the return value, but does not block
    the other rows, since wallet_apply_delta's own row lock + dedup already
    makes each call fully atomic and independent.
    """
    if plan.errors:
        raise RuntimeError("refusing to commit with validation errors")

    results: list[dict[str, Any]] = []
    for entry in plan.deltas_to_apply:
        ref = entry["reference_id"]
        try:
            wallet_id = _get_or_create_wallet_id(entry["user_id"])
            res = (
                supabase.rpc(
                    "wallet_apply_delta",
                    {
                        "p_wallet_id": wallet_id,
                        "p_user_id": entry["user_id"],
                        "p_type": entry["type_"],
                        "p_delta": str(entry["delta"]),
                        "p_reference_id": ref,
                        "p_description": entry["description"],
                        "p_metadata": entry["metadata"],
                    },
                )
                .execute()
                .data
                or []
            )
            if not res:
                raise RuntimeError("wallet_apply_delta returned no row")
            row = res[0]
            results.append(
                {
                    "reference_id": ref,
                    "status": "deduped" if row.get("deduped") else "applied",
                    "transaction_id": row.get("transaction_id"),
                    "balance_after": row.get("balance_after"),
                    "applied_delta": row.get("applied_delta"),
                }
            )
        except Exception:
            logger.error(
                "wallet_import: wallet_apply_delta FAILED for legacy entry %s (user_id=%s, type=%s, delta=%s)",
                ref,
                entry["user_id"],
                entry["type_"],
                entry["delta"],
                exc_info=True,
            )
            results.append({"reference_id": ref, "status": "failed"})
    return results


def print_report(plan: WalletImportPlan, results: list[dict[str, Any]] | None, *, dry_run: bool) -> None:
    """Reports counts only -- never rider/driver PII, never raw phone or customer ids."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    s = plan.stats
    print(f"\n=== Legacy wallet import ({mode}) ===")
    print(f"  rows read                : {s.get('rows_read', 0)}")
    print(f"  skipped (missing id)     : {s.get('skipped_missing_id', 0)}")
    print(f"  skipped (duplicate id)   : {s.get('skipped_duplicate_id', 0)}")
    print(f"  skipped (no account)     : {s.get('skipped_unmatched', 0)}")
    print(f"  skipped (zero amount)    : {s.get('skipped_zero_amount', 0)}")
    print(f"  deltas planned           : {s.get('target_rows', 0)}")
    print(f"    rider-owned            : {s.get('rider_rows', 0)}")
    print(f"    driver-owned           : {s.get('driver_rows', 0)}")
    print(f"  sum credited (add)       : ${s.get('sum_add', 0):,.2f}")
    print(f"  sum debited (deduct)     : ${s.get('sum_deduct', 0):,.2f}")
    print(f"  net                      : ${s.get('sum_net', 0):,.2f}")

    if results is not None:
        applied = sum(1 for r in results if r["status"] == "applied")
        deduped = sum(1 for r in results if r["status"] == "deduped")
        failed = sum(1 for r in results if r["status"] == "failed")
        print("\n  --- commit results ---")
        print(f"  applied                  : {applied}")
        print(f"  deduped (already ran)    : {deduped}")
        print(f"  failed                   : {failed}")
        if failed:
            print("  Failed reference_ids:")
            for r in results:
                if r["status"] == "failed":
                    print(f"    {r['reference_id']}")

    if plan.warnings:
        print(f"\n  --- warnings ({len(plan.warnings)}) ---")
        for w in plan.warnings[:50]:
            print(f"    row {w.row_num} [{w.old_id}] {w.field}: {w.message}")
        if len(plan.warnings) > 50:
            print(f"    … and {len(plan.warnings) - 50} more")

    if plan.errors:
        print(f"\n  --- ERRORS ({len(plan.errors)}) ---")
        for e in plan.errors[:50]:
            print(f"    row {e.row_num} [{e.old_id}] {e.field}: {e.message}")
        if len(plan.errors) > 50:
            print(f"    … and {len(plan.errors) - 50} more")
        print("\n  Refusing to commit until every error above is resolved.")
    print()
