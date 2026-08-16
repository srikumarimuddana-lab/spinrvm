#!/usr/bin/env python3
"""Attach the rider's email to Stripe customers created without one.

Thin CLI over ``services/stripe_customer_email_backfill.py``. The admin
dashboard's "Backfill Stripe customer emails" button calls that same service,
so the button and this script can never apply a different repair — the safety
properties documented there hold identically on both.

    # 1. See what would change. Reads only — no writes. (default)
    python backend/scripts/backfill_stripe_customer_emails.py

    # 2. Write the emails onto the Stripe customers.
    python backend/scripts/backfill_stripe_customer_emails.py --apply

    # Narrow the scope while verifying:
    python backend/scripts/backfill_stripe_customer_emails.py --email someone@example.com
    python backend/scripts/backfill_stripe_customer_emails.py --user-id <uuid> --apply
    python backend/scripts/backfill_stripe_customer_emails.py --limit 50 --apply

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

The Stripe secret key is read from the ``app_settings`` table (where the
backend keeps it), NOT from the environment — so this always addresses
whichever Stripe account the running backend is pointed at. That matters here:
run it against the wrong key and every customer looks missing.

PIPEDA: this transfers rider email addresses to Stripe (a US processor) in
bulk — the deliberate decision recorded in
docs/change-log/2026-08-16-stripe-customer-email-mapping.md. Neither this
script nor the service ever logs an email address; output is user ids and
``cus_…`` ids only.

Rollback for an applied run: clear the field again for the reported ids,
``stripe.Customer.modify(cus_id, email="")``. Every touched pair is printed,
so the affected set is recoverable from this output without scanning Stripe.
No Spinr-side state changes, so there is nothing to restore in the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_stripe_customer_emails")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the emails to Stripe (default: dry run)")
    parser.add_argument("--user-id", action="append", dest="user_ids", help="restrict to a user (repeatable)")
    parser.add_argument("--email", action="append", dest="emails", help="restrict to an email (repeatable)")
    parser.add_argument("--limit", type=int, default=None, help="cap the riders considered per run")
    args = parser.parse_args()

    # The admin dashboard button calls this same service, so the CLI and the UI
    # can never apply a different repair.
    try:
        from services import stripe_customer_email_backfill as svc
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import stripe_customer_email_backfill as svc  # type: ignore

    try:
        result = await svc.backfill_stripe_customer_emails(
            user_ids=args.user_ids,
            emails=args.emails,
            limit=args.limit or svc.DEFAULT_LIMIT,
            apply=args.apply,
        )
    except RuntimeError as e:
        logger.error("%s — nothing to address. Aborting.", e)
        return 1

    for c in result.changes:
        # Never the address itself — only whether one was already present.
        logger.info(
            "%s user=%s customer=%s (had_email=%s)",
            "UPDATED" if result.applied else "WOULD UPDATE",
            c.user_id,
            c.customer_id,
            c.had_email,
        )

    logger.info(
        "%s: %d updated, %d already correct, %d without a Stripe customer, "
        "%d without an email, %d unreachable on this key, %d failed",
        "APPLIED" if result.applied else "DRY RUN",
        result.updated,
        result.unchanged,
        result.no_customer,
        result.no_email,
        len(result.missing_on_key),
        len(result.failed),
    )
    if result.missing_on_key:
        logger.warning(
            "unreachable on this key (test->live residue; repaired on the rider's next "
            "visit to their own payment screen, NOT by this script): %s",
            json.dumps(result.missing_on_key),
        )
    if result.has_more:
        logger.warning("more riders remain beyond this batch; re-run to continue")
    if result.failed:
        logger.error("failed: %s", json.dumps(result.failed))
        return 1
    if not result.applied and result.updated:
        logger.info("re-run with --apply to write these %d update(s)", result.updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
