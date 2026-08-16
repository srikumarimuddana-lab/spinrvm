"""Attach the rider's email to Stripe customers created without one.

Shared by the admin "Backfill Stripe customer emails" button
(``routes/admin/stripe_mode_audit.py``) and the CLI
(``scripts/backfill_stripe_customer_emails.py``) so the two can never drift —
the button and the script perform exactly the same repair with exactly the
same safety properties.

**Why this exists.** Rider Stripe customers used to be created with
``metadata.user_id`` only — no email — so an email search in the Stripe
dashboard returned nothing for a rider, and every support, dispute, refund, or
chargeback lookup needed a Supabase round-trip to translate an address into a
``cus_…`` first. Customer creation now carries the email
(``routes/payments.py::_customer_identity_fields``), but that only fixes
customers minted from here on. Every customer created before it is still
emailless. This backfills those.

Safety properties (identical on both entry points):

* **Dry run is the default.** ``apply=False`` retrieves every customer and
  reports the exact diff, writing nothing.
* **Only ``email`` is written, and only on the Stripe side.** No Supabase
  column is modified, no customer is created, and no ``payment_method`` is
  touched.
* **Idempotent.** A customer whose email already matches is reported as
  unchanged, so a second pass writes nothing.
* **A customer the running key cannot see** (``resource_missing`` — the residue
  of a test→live rotation) is COUNTED AND REPORTED, never repaired here.
  Re-provisioning rewrites ``users.stripe_customer_id`` *and clears
  ``default_payment_method``*; that belongs in a live payment path with the
  rider present (``routes/payments.py::with_customer_repair``), not in a bulk
  directory job run by an admin who is not in the room.
* **Nothing is silently dropped.** A rider with no email, or with no customer
  yet, is counted and reported — an unexplained gap is how a partial run gets
  mistaken for a complete one.
* **One customer's failure never aborts the rest**; failures are listed.
* **Reads are paged** past the PostgREST db-max-rows cap (1000), which would
  otherwise leave later riders unbackfilled while reporting success.
* **Bounded per invocation**, so the HTTP entry point cannot run unboundedly
  long. ``has_more`` reports that a further run is needed rather than
  truncating silently (CLAUDE.md: no silent caps).

PIPEDA: this transfers rider email addresses to Stripe (a US processor) in
bulk — the deliberate decision recorded in
``docs/change-log/2026-08-16-stripe-customer-email-mapping.md``. Neither the
logs nor the returned payload contain an email address: results carry
``user_id`` / ``cus_…`` and a ``had_email`` boolean only, matching the
IDs-only rule the rest of the Stripe admin tooling follows.

Rollback for an applied run: clear the field again for the reported ids —
``stripe.Customer.modify(cus_id, email="")``. Every touched
``user_id``/``customer_id`` pair is in ``changes``, so the affected set is
recoverable from the run output without scanning Stripe. No Spinr-side state
changes, so there is nothing to restore in the database.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import stripe

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.stripe_mode import is_missing_on_key, key_mode
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.stripe_mode import is_missing_on_key, key_mode  # type: ignore

logger = logging.getLogger(__name__)

# PostgREST caps an unbounded select at db-max-rows without signalling it.
PAGE_SIZE = 500

# Bound on one invocation so the HTTP entry point cannot run unboundedly long.
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000

# Stay well inside Stripe's rate limit. Each row costs a retrieve (+ a modify
# when applying), so this is the one knob that decides how hard a full sweep
# leans on the API.
MAX_CONCURRENCY = 8


@dataclass
class EmailChange:
    """One customer whose Stripe email does not match the rider's."""

    user_id: str
    customer_id: str
    # Whether the Stripe customer already carried SOME address (a correction)
    # versus none at all (the backfill case). The address itself is never
    # included — see the module docstring's PIPEDA note.
    had_email: bool = False


@dataclass
class BackfillResult:
    applied: bool = False
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    has_more: bool = False
    # Riders with nothing to do, kept apart so a run's arithmetic is legible
    # rather than hidden inside one "skipped" bucket.
    no_customer: int = 0
    no_email: int = 0
    changes: list[EmailChange] = field(default_factory=list)
    # "user_id:cus_…" for customers this key cannot see. Reported, not repaired.
    missing_on_key: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def _fetch_users(user_ids: Optional[list[str]], emails: Optional[list[str]], cap: int) -> tuple[list[dict], bool]:
    """Page the users table under the selection, up to ``cap`` + 1 rows.

    Reads one row beyond the cap so ``has_more`` reflects reality instead of
    being inferred from a full page.
    """
    selector: dict = {}
    if user_ids:
        selector["id"] = {"$in": user_ids}
    if emails:
        selector["email"] = {"$in": [e.strip().lower() for e in emails if e and e.strip()]}

    rows: list[dict] = []
    offset = 0
    while len(rows) <= cap:
        page = await db_supabase.get_rows(
            "users",
            selector,
            limit=PAGE_SIZE,
            offset=offset,
            columns="id,email,stripe_customer_id",
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    has_more = len(rows) > cap
    return rows[:cap], has_more


async def backfill_stripe_customer_emails(
    *,
    user_ids: Optional[list[str]] = None,
    emails: Optional[list[str]] = None,
    limit: int = DEFAULT_LIMIT,
    apply: bool = False,
) -> BackfillResult:
    """Attach each rider's email to their existing Stripe customer.

    Returns a :class:`BackfillResult`. With ``apply=False`` (the default) the
    diff is fully computed — every customer is retrieved from Stripe — and
    nothing is written, which is what the dashboard previews before asking the
    operator to confirm.

    Raises only for a setup problem the caller must see (Stripe unconfigured).
    Per-customer failures are collected into the result, never raised.
    """
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        # Not a per-row failure — there is no account to address at all. The
        # caller turns this into a 503 rather than reporting an empty success.
        raise RuntimeError("stripe_secret_key is not configured")

    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    users, has_more = await _fetch_users(user_ids, emails, cap)

    result = BackfillResult(applied=bool(apply), has_more=has_more)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    lock = asyncio.Lock()

    async def _one(user: dict) -> None:
        user_id = str(user.get("id") or "")
        email = (user.get("email") or "").strip()
        customer_id = user.get("stripe_customer_id")

        if not customer_id:
            # Nothing minted yet — creation will carry the email itself.
            async with lock:
                result.no_customer += 1
            return
        if not email:
            # Never blank out an address already on the Stripe customer just
            # because our row has none.
            async with lock:
                result.no_email += 1
            return

        async with semaphore:
            try:
                customer = await asyncio.to_thread(lambda: stripe.Customer.retrieve(customer_id, api_key=stripe_secret))
                current = (getattr(customer, "email", None) or "").strip()
                async with lock:
                    result.scanned += 1
                if current.lower() == email.lower():
                    async with lock:
                        result.unchanged += 1
                    return

                if apply:
                    await asyncio.to_thread(
                        lambda: stripe.Customer.modify(customer_id, email=email, api_key=stripe_secret)
                    )
                async with lock:
                    result.updated += 1
                    result.changes.append(
                        EmailChange(user_id=user_id, customer_id=str(customer_id), had_email=bool(current))
                    )
            except Exception as e:  # noqa: BLE001 - classified immediately below
                if is_missing_on_key(e, customer_id):
                    # Stranded by a key-mode rotation. Reported, deliberately
                    # not repaired here (see the module docstring).
                    async with lock:
                        result.missing_on_key.append(f"{user_id}:{customer_id}")
                    return
                # A payment-adjacent failure must surface with its cause, not
                # be softened to a warning (CLAUDE.md).
                logger.error(
                    "Stripe customer email backfill failed for one rider: %s",
                    e,
                    exc_info=True,
                    extra={"user_id": user_id, "stripe_customer_id": customer_id, "domain": "payments"},
                )
                async with lock:
                    result.failed.append(f"{user_id}:{customer_id}")

    await asyncio.gather(*(_one(u) for u in users))

    logger.info(
        "Stripe customer email backfill finished",
        extra={
            "domain": "payments",
            "applied": result.applied,
            "mode": key_mode(stripe_secret),
            "scanned": result.scanned,
            "updated": result.updated,
            "unchanged": result.unchanged,
            "no_customer": result.no_customer,
            "no_email": result.no_email,
            "missing_on_key": len(result.missing_on_key),
            "failed": len(result.failed),
        },
    )
    return result
