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
* **A rider mid-deletion is never transferred.** ``deleted_at IS NULL`` is
  filtered in SQL and ``status = 'pending_deletion'`` is skipped per row. A
  rider inside the window between requesting deletion and the retention purge
  still has an address on their row; shipping it to a US processor would
  partially undo the request.
* **Nothing is silently dropped.** A rider with no email, or mid-deletion, is
  counted and reported — an unexplained gap is how a partial run gets mistaken
  for a complete one.
* **One customer's failure never aborts the rest**; failures are listed.
* **Keyset paging, resumable.** Rows are ordered by ``id`` and each run
  resumes from ``next_cursor``, so "more remain — run again" actually makes
  progress. An earlier version restarted at ``offset = 0`` with no ordering:
  the second run re-read the same first page, found everything already
  correct, and reported "nothing to sync" while most of the fleet had never
  been touched. Safe to page this way because the sweep writes nothing to
  Supabase, so the ordering it walks is stable across calls.
* **Bounded per invocation**, so the HTTP entry point cannot run unboundedly
  long. ``has_more`` + ``next_cursor`` report that a further run is needed
  rather than truncating silently (CLAUDE.md: no silent caps).
* **The Stripe key mode is reported before the sweep**, not after, so an
  operator learns they are pointed at LIVE while they can still stop.

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
import re
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


# A rider who has asked to be forgotten must not have their address shipped to
# a US processor by a bulk job. `deleted_at` is filtered in SQL; `status` is
# checked per row (below) rather than with a `$ne`, because `!= 'x'` is NULL —
# and therefore false — for a row whose status is NULL, which would silently
# drop those riders from the sweep. Explicit beats clever on an exclusion.
DELETION_STATUSES = frozenset({"pending_deletion"})

# Stripe error messages quote the value they rejected, and here that value is
# an email address — so a raw `str(exc)` in a log line would defeat the
# no-addresses-in-logs rule this module claims to follow.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _redact_emails(text: str) -> str:
    return _EMAIL_RE.sub("[email-redacted]", text or "")


@dataclass
class BackfillResult:
    applied: bool = False
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    has_more: bool = False
    # Pass back to the next call to continue where this one stopped. None when
    # the sweep reached the end of the selection.
    next_cursor: Optional[str] = None
    # Which Stripe account this ran against. Surfaced so an operator is told
    # LIVE vs TEST *before* confirming a bulk PII transfer, not after.
    key_mode: str = ""
    # Riders with nothing to do, kept apart so a run's arithmetic is legible
    # rather than hidden inside one "skipped" bucket.
    no_customer: int = 0
    no_email: int = 0
    # Riders mid-deletion whose row still carries an address. Counted, never sent.
    skipped_deleted: int = 0
    changes: list[EmailChange] = field(default_factory=list)
    # "user_id:cus_…" for customers this key cannot see. Reported, not repaired.
    missing_on_key: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _selector(user_ids: Optional[list[str]], emails: Optional[list[str]], cursor: Optional[str]) -> dict:
    """PostgREST filter for one page of the sweep.

    Two exclusions are pushed into SQL rather than filtered in Python:

    * ``deleted_at IS NULL`` — a rider inside the window between "delete my
      account" and the retention purge still has an address on their row.
      Sending it to Stripe would partially undo a deletion request.
    * ``stripe_customer_id IS NOT NULL`` — riders with no customer yet have
      nothing to repair (creation carries the email itself). Without this they
      consume the per-run budget and starve the riders who DO need work.
    """
    selector: dict = {
        "deleted_at": None,
        "stripe_customer_id": {"$notnull": True},
    }
    # `$in` and `$gt` are both applied by _apply_filters (every operator in the
    # dict is), so a scoped re-run can still be resumed by cursor.
    id_pred: dict = {}
    if user_ids:
        id_pred["$in"] = user_ids
    if cursor:
        id_pred["$gt"] = cursor
    if id_pred:
        selector["id"] = id_pred
    if emails:
        selector["email"] = {"$in": [e.strip().lower() for e in emails if e and e.strip()]}
    return selector


async def _fetch_users(
    user_ids: Optional[list[str]],
    emails: Optional[list[str]],
    cap: int,
    cursor: Optional[str],
) -> tuple[list[dict], bool, Optional[str]]:
    """Keyset-page the users table, returning at most ``cap`` rows.

    **Keyset, not offset.** The previous version restarted at ``offset = 0`` on
    every invocation and ordered by nothing at all, which made the advertised
    "more remain — run again" a lie: the second run re-read the same first
    page, found every customer already correct, and reported "nothing to sync"
    while the rest of the fleet had never been touched. Ordering by ``id`` and
    resuming from the last one seen makes each run pick up where the last
    stopped, and makes paging *within* a run well-defined too (offset paging
    without ORDER BY may repeat or skip rows between pages).

    Reads one row beyond the cap so ``has_more`` reflects reality rather than
    being inferred from a full page.
    """
    rows: list[dict] = []
    page_cursor = cursor
    while len(rows) <= cap:
        page = await db_supabase.get_rows(
            "users",
            _selector(user_ids, emails, page_cursor),
            order="id",
            limit=PAGE_SIZE,
            columns="id,email,status,stripe_customer_id",
        )
        if not page:
            break
        rows.extend(page)
        page_cursor = page[-1].get("id")
        if len(page) < PAGE_SIZE or not page_cursor:
            break

    has_more = len(rows) > cap
    kept = rows[:cap]
    # The cursor is the last row we actually PROCESS, so the next run resumes
    # immediately after it — never the last row merely read.
    next_cursor = str(kept[-1].get("id")) if kept and has_more else None
    return kept, has_more, next_cursor


async def backfill_stripe_customer_emails(
    *,
    user_ids: Optional[list[str]] = None,
    emails: Optional[list[str]] = None,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    apply: bool = False,
) -> BackfillResult:
    """Attach each rider's email to their existing Stripe customer.

    Returns a :class:`BackfillResult`. With ``apply=False`` (the default) the
    diff is fully computed — every customer is retrieved from Stripe — and
    nothing is written, which is what the dashboard previews before asking the
    operator to confirm.

    One call handles at most ``limit`` riders. When ``has_more`` is set, pass
    ``next_cursor`` back to continue; the sweep writes nothing to Supabase, so
    the ordering it pages over is stable across calls.

    Raises only for a setup problem the caller must see (Stripe unconfigured).
    Per-customer failures are collected into the result, never raised.
    """
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        # Not a per-row failure — there is no account to address at all. The
        # caller turns this into a 503 rather than reporting an empty success.
        raise RuntimeError("stripe_secret_key is not configured")

    mode = key_mode(stripe_secret)
    # Announced BEFORE the sweep, not in the completion line: which Stripe
    # account a bulk PII transfer is about to address is the one thing an
    # operator needs while they can still stop it.
    logger.info(
        "Stripe customer email backfill starting",
        extra={"domain": "payments", "key_mode": mode, "apply": bool(apply), "resumed": bool(cursor)},
    )

    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    users, has_more, next_cursor = await _fetch_users(user_ids, emails, cap, cursor)

    result = BackfillResult(applied=bool(apply), has_more=has_more, next_cursor=next_cursor, key_mode=mode)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    lock = asyncio.Lock()

    async def _one(user: dict) -> None:
        user_id = str(user.get("id") or "")
        email = (user.get("email") or "").strip()
        customer_id = user.get("stripe_customer_id")

        if str(user.get("status") or "").lower() in DELETION_STATUSES:
            # Mid-deletion: the row still has an address only because the
            # retention purge has not run yet. Never ship it onward.
            async with lock:
                result.skipped_deleted += 1
            return
        if not customer_id:
            # The query already excludes these; this is a belt-and-braces guard
            # so a filter regression shows up as a non-zero count rather than a
            # crash or a wasted Stripe call. It should always be 0.
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
                # be softened to a warning (CLAUDE.md) — but the cause is a
                # Stripe message that can quote the value it rejected, and the
                # value here IS an email address. Redact before logging;
                # exc_info is omitted for the same reason.
                logger.error(
                    "Stripe customer email backfill failed for one rider: %s",
                    _redact_emails(str(e)),
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
            "key_mode": mode,
            "scanned": result.scanned,
            "updated": result.updated,
            "unchanged": result.unchanged,
            "no_customer": result.no_customer,
            "no_email": result.no_email,
            "skipped_deleted": result.skipped_deleted,
            "missing_on_key": len(result.missing_on_key),
            "failed": len(result.failed),
            "has_more": result.has_more,
        },
    )
    return result
