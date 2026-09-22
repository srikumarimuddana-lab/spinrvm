#!/usr/bin/env python3
"""Refund cancelled rides whose booking hold was captured and never given back.

The one-off counterpart to the 2026-09-21 cancellation fix
(``docs/change-log/2026-09-21-cancellation-refund-already-captured-hold.md``).
That fix added a refund branch to ``routes/rides/cancellation.py`` for a ride
whose hold is already ``auth_status='captured'`` at cancel time — but it only
helps rides cancelled **after** it deploys. Rides already sitting in that state
keep the rider's money with nothing in the system that will ever return it.

There is deliberately **no background loop** for this. The population is a
closed historical backlog, not an ongoing one (the route fix closes the source),
and every action here moves real money out of the platform account, so it is a
human-triggered script with a read-only default — per CLAUDE.md's
"escalate, don't silently ship" gate.

    # 1. Size the backlog. Reads only — no writes, no refunds. Stripe is read
    #    (PaymentIntent + refund list) but never mutated.
    python backend/scripts/reconcile_cancelled_captured_refunds.py

    # 2. Refund one specific ride, after reviewing what step 1 printed.
    python backend/scripts/reconcile_cancelled_captured_refunds.py \
        --apply --ride-id e1b453ee-3c3e-44e6-a74f-80257c30a041

    # 3. Refund the whole backlog (bounded by --batch, default 200).
    python backend/scripts/reconcile_cancelled_captured_refunds.py --apply

Environment — the same variables the backend reads, so a backend ``.env`` is
enough:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Stripe keys are NOT read from the environment; they live in ``app_settings``
(CLAUDE.md, "Settings in DB") and are resolved per ride. **Pointing this at the
production database therefore means moving production money** — there is no
separate Stripe switch to forget.

Safety properties
-----------------
  * ``status='cancelled'`` only — never a completed ride whose fare is owed to
    the driver. A completed ride's captured hold is correct, not orphaned.
  * ``auth_status='captured'`` only — a still-live hold is the orphaned-hold
    reconciler's job (``reconcile_orphaned_holds.py``), and refunding one here
    would fight that loop.
  * Refunds only the EXCESS over the cancellation fee actually recorded on the
    ride (``cancellation_fee_admin + cancellation_fee_driver``) — a legitimately
    charged late-cancel fee is kept, not handed back.
  * Idempotent three ways over: skips any ride whose ``refund_amount`` is
    already set; skips any PaymentIntent that Stripe says already carries a
    refund (this is what makes a re-run safe past the 24h Stripe
    idempotency-key window); and reuses ``refund_excess_capture``'s own
    per-(ride, amount) idempotency key within it. The middle guard is a read
    taken moments before the refund, not an atomic one — a refund issued by
    hand in the Stripe dashboard inside that window would only be caught by
    Stripe's own over-refund rejection. Don't hand-refund a ride while this is
    running.
  * Never withholds a cancellation fee twice, and never hands a collected one
    back: the fee is treated as already paid (so not deducted from the capture
    again) only when it was charged on a DIFFERENT, successfully paid
    PaymentIntent. A fee captured out of the booking hold itself stamps that
    same PI, and an attempted-but-failed fee charge stamps one too — see
    ``_fee_still_owed_from_capture``.
  * Never refunds on an unknown Stripe state — a failed read is reported as
    ``unknown`` and skipped, not treated as "nothing refunded yet".
  * A failure on one ride never stops the rest.
  * Ledger + ride-row bookkeeping mirrors the live cancel path exactly (same
    ``record_refund_event`` dedupe-key shape, same ``refund_amount`` /
    ``payment_status`` writes), so a row this script fixes is indistinguishable
    from one the route handled itself.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Import as the backend package does, so the dual-import modules resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("reconcile_cancelled_captured_refunds")

# Outcome keys, in the order they are reported.
OUTCOMES = (
    "would_refund",
    "refunded",
    "not_needed",
    "skipped_db_refund",
    "already_refunded_on_stripe",
    "pending_refund_on_stripe",
    "captured_nothing",
    "unknown",
    "failed",
    "ledger_failed",
)


def _recorded_fee(ride: Dict[str, Any]) -> Decimal:
    """Cancellation fee recorded on the ride, as Decimal (never float)."""
    from utils.money import to_decimal

    return to_decimal(ride.get("cancellation_fee_admin") or 0) + to_decimal(ride.get("cancellation_fee_driver") or 0)


def _fee_still_owed_from_capture(ride: Dict[str, Any]) -> Decimal:
    """How much of the recorded fee this captured hold still has to cover.

    **Zero only when the fee was successfully collected on a DIFFERENT
    PaymentIntent.** That is the normal shape for the historical population this
    script targets: before the 2026-09-21 fix there was no
    ``auth_status == "captured"`` branch at all, so ``fee_taken_from_hold``
    stayed 0 and ``cancellation.py``'s fresh-charge fallback billed the fee on a
    SECOND PaymentIntent. Subtracting that fee from the captured booking hold as
    well would withhold it twice — a $30 captured fare with a $5 fee charged
    separately would refund only $25, leaving the rider $35 out of pocket on a
    ride they owed $5 for.

    Both halves of that condition are load-bearing:

    * **Different PI.** On the partial-capture path the fee comes out of the
      booking hold itself, and ``cancellation.py`` stamps
      ``cancel_fee_payment_intent_id`` with *that same booking PI*
      (``capture_cancellation_fee`` returns ``intent.id``);
      ``routes/drivers/ride_cancel.py`` does the same for no-shows. Treating a
      truthy column alone as "collected elsewhere" would hand the fee back on
      every fee-bearing cancel — after the driver was already paid their share.
    * **Actually paid.** ``cancel_fee_payment_intent_id`` is written on any real
      charge attempt, including ``requires_action``/``requires_payment_method``
      outcomes that leave ``payment_status='failed'``. An unpaid fee is still
      owed, so it stays deducted from the capture.
    """
    fee_pi = ride.get("cancel_fee_payment_intent_id")
    collected_elsewhere = (
        bool(fee_pi) and fee_pi != ride.get("payment_intent_id") and (ride.get("payment_status") or "") == "paid"
    )
    if collected_elsewhere:
        return Decimal("0")
    return _recorded_fee(ride)


async def find_candidates(
    *, limit: int, offset: int = 0, ride_ids: Optional[List[str]] = None
) -> tuple[List[Dict[str, Any]], int]:
    """Cancelled rides with a captured hold and no refund recorded against them.

    Returns ``(candidates, rows_scanned)``. ``rows_scanned`` is the pre-filter
    row count, which is what tells the operator whether the ``--batch`` ceiling
    was hit: this script never changes ``status``/``auth_status``, so rides it has
    already refunded keep matching the query forever. Judging "is there more?"
    on the post-filter count would report an empty backlog while later pages of
    it were never looked at. Ordering is by ``id`` — unique, so ``--offset`` walks the
    backlog deterministically. A non-unique key like ``created_at`` leaves ties
    in unspecified order, which can silently skip a row between pages.

    ``refund_amount`` is filtered in Python rather than in the query: the column
    is nullable, and "null OR 0" is exactly the kind of predicate CLAUDE.md's
    query-filter rules say to keep out of the OR builder.
    """
    import db_supabase as db

    filters: Dict[str, Any] = {"status": "cancelled", "auth_status": "captured"}
    if ride_ids:
        filters["id"] = {"$in": list(ride_ids)}

    rows = await db.get_rows("rides", filters, order="id", limit=limit, offset=offset) or []
    out = []
    for r in rows:
        if not r.get("payment_intent_id"):
            continue
        if to_cents(r.get("refund_amount")) > 0:
            continue
        out.append(r)
    return out, len(rows)


def to_cents(value: Any) -> int:
    from utils.money import dollars_to_cents, to_decimal

    return dollars_to_cents(to_decimal(value or 0))


async def reconcile_one(ride: Dict[str, Any], *, apply_changes: bool) -> str:
    """Handle one ride. Returns an outcome key from ``OUTCOMES``. Never raises."""
    import db_supabase as db
    from services.payment_service import record_refund_event
    from utils.money import cents_to_dollars
    from utils.stripe_charge import read_capture_state, refund_excess_capture

    ride_id = ride["id"]
    pi = ride["payment_intent_id"]
    # What this hold still owes (0 if the fee was billed on its own PI) vs what
    # the rider was charged in total — the first drives the refund maths, the
    # second decides whether the ride ends up "refunded" or "partially_refunded".
    fee_owed = _fee_still_owed_from_capture(ride)
    fee_cents = to_cents(fee_owed)
    fee_retained = _recorded_fee(ride) > 0

    state = await read_capture_state(ride_id=ride_id, payment_intent_id=pi)
    if state is None:
        # Unknown, not zero. Refunding blind here could double-refund a rider
        # whose money already came back.
        logger.error("ride=%s could not read Stripe capture state — skipping", ride_id)
        return "unknown"

    captured_cents = state["captured_cents"]
    if state.get("pending_refund_cents", 0) > 0:
        logger.warning(
            "ride=%s has %s cents in pending/action-required Stripe refunds — no new refund created",
            ride_id,
            state["pending_refund_cents"],
        )
        return "pending_refund_on_stripe"

    if state["refunded_cents"] > 0:
        # Stripe already gave money back but the ride row still says otherwise.
        # Deliberately NOT repaired here: writing refund_amount for a refund this
        # script did not issue, with no matching ledger event, would paper over a
        # real bookkeeping gap. Report it for a human instead.
        logger.error(
            "ride=%s has %s cents refunded on Stripe but refund_amount is unset — needs manual reconciliation",
            ride_id,
            state["refunded_cents"],
        )
        return "already_refunded_on_stripe"

    if captured_cents <= 0:
        # auth_status says captured but Stripe received nothing. Either the
        # auth_status stamp is wrong or this is not the PI that holds the money —
        # both are payment-path anomalies, not a quiet no-op. The live cancel path
        # logs the same condition (cancellation.py's "nothing was received" warning).
        logger.error(
            "ride=%s auth_status is 'captured' but Stripe received nothing on pi=%s — needs investigation",
            ride_id,
            pi,
        )
        return "captured_nothing"

    refund_cents = captured_cents - fee_cents
    if refund_cents <= 0:
        return "not_needed"

    if not apply_changes:
        logger.info(
            "ride=%s WOULD refund %s cents (captured=%s fee_owed=%s)",
            ride_id,
            refund_cents,
            captured_cents,
            fee_cents,
        )
        return "would_refund"

    outcome = await refund_excess_capture(ride_id=ride_id, payment_intent_id=pi, fee_owed=fee_owed)
    if outcome.status != "refunded":
        if outcome.status == "not_needed":
            # The helper's own fresh read of amount_received disagrees with the
            # one above (separate round-trips). Nothing is owed — not a failure,
            # and reporting it as one would send an operator after a rider who
            # is owed nothing.
            logger.info("ride=%s Stripe re-read says no refund is due — nothing to do", ride_id)
            return "not_needed"
        if outcome.status == "unconfigured":
            logger.error("ride=%s Stripe is not configured for this ride — skipping", ride_id)
            return "unknown"
        logger.error(
            "ride=%s refund did not go through (status=%s) — rider is still owed %s cents",
            ride_id,
            outcome.status,
            refund_cents,
        )
        return "failed"

    refunded_cents = to_cents(outcome.charged_amount)
    # Same dedupe-key shape the live cancel path and routes/webhooks.py use, so
    # this books once even if the route later handles the same money movement.
    # Wrapped: the money has already left the platform account by this point, so
    # a raise here must not be reported as "rider is still owed" — it is the
    # opposite, a refunded ride with no ledger row.
    try:
        ledger_id = await record_refund_event(
            ride_id=ride_id,
            user_id=ride.get("rider_id") or "",
            refund_cents=refunded_cents,
            payment_intent_id=outcome.payment_intent_id,
            ride=ride,
            dedupe_key=f"stripe_refund|{outcome.payment_intent_id}|{refunded_cents}",
        )
    except Exception as e:
        logger.exception(
            "ride=%s refunded %s cents on Stripe but the ledger write raised — money moved: %s",
            ride_id,
            refunded_cents,
            e,
        )
        ledger_id = None

    # Compare-and-swap on the state this script selected for: if the live cancel
    # path (or another pass) moved the row in the meantime, leave it alone rather
    # than overwrite its bookkeeping.
    try:
        claimed = await db.update_one(
            "rides",
            {"id": ride_id, "status": "cancelled", "auth_status": "captured"},
            {
                "refund_amount": str(cents_to_dollars(refunded_cents)),
                # Keyed on the fee the rider actually ended up paying by ANY
                # route, not just what this hold covered — a fee collected on its
                # own PI still means they were only partially refunded overall.
                # cancel_fee_payment_intent_id keeps recording which PI took it.
                "payment_status": "refunded" if not fee_retained else "partially_refunded",
            },
        )
    except Exception as e:
        logger.exception(
            "ride=%s refunded %s cents on Stripe but the ride-row write failed — money moved, "
            "refund_amount still unset: %s",
            ride_id,
            refunded_cents,
            e,
        )
        return "ledger_failed"

    if claimed is None:
        # Zero rows matched: the row moved out of cancelled/captured between the
        # candidate read and this write. The Stripe refund is real either way, so
        # this is a bookkeeping gap for a human, not something to retry blindly.
        logger.error(
            "ride=%s refunded %s cents on Stripe but the ride row no longer matched "
            "status=cancelled/auth_status=captured — refund_amount not written, needs reconciliation",
            ride_id,
            refunded_cents,
        )
        return "ledger_failed"

    if ledger_id is None:
        logger.error(
            "ride=%s refunded %s cents on Stripe but the ledger write failed — needs reconciliation",
            ride_id,
            refunded_cents,
        )
        return "ledger_failed"

    logger.info("ride=%s refunded %s cents (fee kept=%s)", ride_id, refunded_cents, fee_cents)
    return "refunded"


async def _main(*, apply_changes: bool, batch: int, offset: int, ride_ids: Optional[List[str]]) -> int:
    candidates, rows_scanned = await find_candidates(limit=batch, offset=offset, ride_ids=ride_ids)
    totals = dict.fromkeys(OUTCOMES, 0)

    # An explicitly requested ride that does not qualify is worth saying out
    # loud: on a money path the operator is usually chasing one named rider, and
    # "does not qualify" is meaningfully different from "nothing to do".
    if ride_ids:
        missing = sorted(set(ride_ids) - {r["id"] for r in candidates})
        for ride_id in missing:
            logger.warning(
                "ride=%s was requested but does not qualify "
                "(not cancelled, hold not captured, no payment_intent_id, or refund_amount already set)",
                ride_id,
            )

    for ride in candidates:
        try:
            totals[await reconcile_one(ride, apply_changes=apply_changes)] += 1
        except Exception as e:  # pragma: no cover — one bad row must not stop the rest
            logger.exception("ride=%s reconcile raised: %s", ride.get("id"), e)
            totals["failed"] += 1

    print()
    print(f"  rows scanned        : {rows_scanned} (offset {offset})")
    print(f"  candidates examined : {len(candidates)}")
    for key in OUTCOMES:
        if totals[key]:
            print(f"  {key:<26}: {totals[key]}")
    if rows_scanned == batch:
        # Rides this script already refunded still match the query, so a full
        # page means "look further", not "this page was all work".
        print(
            f"  NOTE: scanned a full page ({batch}); more rides may qualify beyond it. "
            f"Re-run with --offset {offset + batch} (or a larger --batch)."
        )
    if not apply_changes:
        print("  DRY RUN — no Stripe refund was issued and nothing was written.")
        print("  Review the ride ids above, then re-run with --apply (optionally --ride-id) to refund.")
    print()

    # Non-zero whenever money is unaccounted for — a rider still owed, a state we
    # could not read, or a refund whose bookkeeping did not land.
    return (
        1
        if (
            totals["failed"]
            or totals["unknown"]
            or totals["ledger_failed"]
            or totals["already_refunded_on_stripe"]
            or totals["captured_nothing"]
        )
        else 0
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually issue the Stripe refunds (default is a read-only dry run)",
    )
    ap.add_argument(
        "--ride-id",
        dest="ride_ids",
        action="append",
        help="restrict to this ride id (repeatable) — the safe way to refund a reviewed row one at a time",
    )
    ap.add_argument("--batch", type=int, default=200, help="max rides scanned in one run (default 200)")
    ap.add_argument(
        "--offset", type=int, default=0, help="skip this many rows — page through a backlog bigger than --batch"
    )
    args = ap.parse_args()

    # A long id list is rendered into the PostgREST request line and, past roughly
    # 150 UUIDs, comes back as an opaque non-JSON Bad Request (see
    # repositories/_base.py). Fail with something an operator can act on instead.
    if args.ride_ids and len(args.ride_ids) > 100:
        print(
            f"--ride-id was given {len(args.ride_ids)} ids; pass at most 100 per run "
            "(a longer list hits an opaque PostgREST request-size failure).",
            file=sys.stderr,
        )
        return 2

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.\n"
            "This script reads the rides table and, with --apply, issues real Stripe\n"
            "refunds against whichever project those point at.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(_main(apply_changes=args.apply, batch=args.batch, offset=args.offset, ride_ids=args.ride_ids))


if __name__ == "__main__":
    raise SystemExit(main())
