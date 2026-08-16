#!/usr/bin/env python3
"""Attach the rider's email to Stripe customers created without one.

Rider Stripe customers used to be created with ``metadata.user_id`` only — no
email, no name — so an email search in the Stripe dashboard returned nothing
for a rider, and every support, dispute, or refund lookup needed a Supabase
round-trip to translate an address into a ``cus_…`` first. Customer creation
now carries the email (``routes/payments.py::_customer_identity_fields``), but
that only fixes customers minted from here on. Every customer created before
it is still emailless. This script repairs those.

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
backend keeps it), NOT from the environment — so this script always addresses
whichever Stripe account the running backend is pointed at. That matters here:
run it against the wrong key and every customer looks missing.

Safety properties:
  * Dry run is the DEFAULT. Nothing is written without --apply.
  * ONLY ``email`` is written, and only on the Stripe side. No Supabase column
    is modified, no customer is created, and no ``payment_method`` is touched.
  * Idempotent: a customer whose email already matches is skipped, so re-running
    converges and changes nothing on the second pass.
  * A customer the running key cannot see (``resource_missing`` — the residue of
    a test→live rotation) is COUNTED AND REPORTED, never repaired here.
    Re-provisioning rewrites ``users.stripe_customer_id`` and clears the saved
    default card; that belongs in a live payment path with a rider present, not
    in a bulk directory job.
  * A user row with no email, or no ``stripe_customer_id`` yet, is counted and
    reported rather than silently skipped — an unexplained gap is how a partial
    run gets mistaken for a complete one.
  * One customer's failure never aborts the rest; failures are listed and the
    script exits non-zero.
  * Reads are paged. An unbounded PostgREST select silently caps at db-max-rows
    (1000), which would leave later riders unbackfilled while reporting success.

PIPEDA note: this transfers rider email addresses to Stripe (a US processor) in
bulk. That is the deliberate decision this script implements — see the Change
Impact log at docs/change-log/2026-08-16-stripe-customer-email-mapping.md. The
script never LOGS an email address; it reports user ids and ``cus_…`` ids only.

Rollback: clear the field again with the same selection, i.e.
``stripe.Customer.modify(cus_id, email="")`` for the ids this run reports. No
Spinr-side state changes, so there is nothing to restore in the database.
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

PAGE_SIZE = 500


async def main() -> int:  # noqa: C901 - a linear report/apply loop reads better whole
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the emails to Stripe (default: dry run)")
    parser.add_argument("--user-id", action="append", dest="user_ids", help="restrict to a user (repeatable)")
    parser.add_argument("--email", action="append", dest="emails", help="restrict to an email (repeatable)")
    parser.add_argument("--limit", type=int, default=None, help="cap the customers considered (default: all)")
    args = parser.parse_args()

    import stripe

    try:
        import db_supabase
        from settings_loader import get_app_settings
    except ImportError:  # pragma: no cover - CLI convenience
        from backend import db_supabase  # type: ignore
        from backend.settings_loader import get_app_settings  # type: ignore

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.error("stripe_secret_key is not set in app_settings — nothing to address. Aborting.")
        return 1
    # Say which account we are about to touch. Running a bulk PII transfer
    # against the wrong mode is the mistake worth one extra line of output.
    logger.info("Stripe key mode: %s", "LIVE" if "_live_" in stripe_secret else "TEST")

    selector: dict = {}
    if args.user_ids:
        selector["id"] = {"$in": args.user_ids}
    if args.emails:
        selector["email"] = {"$in": [e.strip().lower() for e in args.emails]}

    # Page explicitly rather than trusting an unbounded select.
    users: list[dict] = []
    offset = 0
    while True:
        page = await db_supabase.get_rows(
            "users",
            selector,
            limit=PAGE_SIZE,
            offset=offset,
            columns="id,email,stripe_customer_id",
        )
        if not page:
            break
        users.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if args.limit and len(users) >= args.limit:
            break

    has_more = bool(args.limit and len(users) > args.limit)
    if args.limit:
        users = users[: args.limit]

    updated = 0
    already = 0
    no_customer = 0
    no_email = 0
    missing_on_key: list[str] = []
    failed: list[str] = []

    for user in users:
        user_id = user.get("id")
        email = (user.get("email") or "").strip()
        customer_id = user.get("stripe_customer_id")

        if not customer_id:
            # Nothing minted yet — creation will carry the email itself.
            no_customer += 1
            continue
        if not email:
            no_email += 1
            logger.info("user=%s customer=%s has no email on file — nothing to attach", user_id, customer_id)
            continue

        try:
            customer = await asyncio.to_thread(
                lambda cid=customer_id: stripe.Customer.retrieve(cid, api_key=stripe_secret)
            )
            current = (getattr(customer, "email", None) or "").strip()
            if current.lower() == email.lower():
                already += 1
                continue

            if args.apply:
                await asyncio.to_thread(
                    lambda cid=customer_id, em=email: stripe.Customer.modify(cid, email=em, api_key=stripe_secret)
                )
            # Never log the address itself — only whether one was present.
            logger.info(
                "%s user=%s customer=%s (had_email=%s)",
                "UPDATED" if args.apply else "WOULD UPDATE",
                user_id,
                customer_id,
                bool(current),
            )
            updated += 1
        except Exception as e:  # noqa: BLE001 - classified immediately below
            code = getattr(e, "code", None) or getattr(getattr(e, "error", None), "code", None)
            if code == "resource_missing":
                # Stranded by a key-mode rotation. Reported, deliberately not repaired.
                missing_on_key.append(f"{user_id}:{customer_id}")
                continue
            logger.error("user=%s customer=%s failed: %s", user_id, customer_id, e, exc_info=True)
            failed.append(f"{user_id}:{customer_id}")

    logger.info(
        "%s: %d updated, %d already correct, %d without a Stripe customer, "
        "%d without an email, %d unreachable on this key, %d failed",
        "APPLIED" if args.apply else "DRY RUN",
        updated,
        already,
        no_customer,
        no_email,
        len(missing_on_key),
        len(failed),
    )
    if missing_on_key:
        logger.warning(
            "unreachable on this key (test->live residue; repaired lazily by the cards screen, NOT by this script): %s",
            json.dumps(missing_on_key),
        )
    if has_more:
        logger.warning("more users remain beyond --limit; re-run to continue")
    if failed:
        logger.error("failed: %s", json.dumps(failed))
        return 1
    if not args.apply and updated:
        logger.info("re-run with --apply to write these %d update(s)", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
