"""Admin diagnostic for Stripe identities stranded by a key-mode rotation.

``stripe_secret_key`` lives in ``app_settings`` so it can be rotated without a
redeploy. Rotating it ACROSS modes (``sk_test_…`` → ``sk_live_…``) invalidates
every stored Stripe ID at once — Stripe scopes object IDs per mode — and the
symptom is diffuse: riders' cards stop loading, drivers' payout setup
dead-ends, corporate auto-topup errors. Nothing in the product tells an
operator how many accounts are in that state or which ones.

This module answers exactly that, and nothing else:

- ``GET  /api/admin/stripe/mode-audit`` — DB-only. Current key mode plus, per
  table, how many rows carry a Stripe identity stamped live / test /
  unverified. Cheap enough to poll while a repair rolls through.
- ``POST /api/admin/stripe/mode-audit/probe`` — asks Stripe about a bounded
  sample of *unverified* rows and reports which resolve. Stamps the ones that
  do (that is the only write, and it is idempotent), so the population of
  unknowns shrinks as it runs.

The probe never re-provisions or retires anything. Repair is deliberately left
to the rider's own card screen (``routes/payments.py::with_customer_repair``)
and the driver's "Set up payouts" flow (``routes/drivers/payouts.py``), where
the affected user is present and the consequences are visible to them. An
admin sweep that silently replaced identities would move money-path state for
people who are not in the room.

Reports carry IDs only — never phones, emails, or names — per CLAUDE.md's
PIPEDA rules. Stripe object IDs are operational identifiers, not PII.

Access: **super_admin**, matching stripe_import.py. Reading which drivers have
an unreachable payout destination is payout-adjacent, and the probe spends
Stripe API quota.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...db_supabase import run_sync
    from ...dependencies import get_admin_user
    from ...services import stripe_customer_email_backfill as email_backfill_svc
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
    from ...utils.stripe_mode import LIVE, TEST, is_missing_on_key, key_mode, object_mode
except ImportError:
    import db_supabase  # type: ignore
    from db_supabase import run_sync  # type: ignore
    from dependencies import get_admin_user  # type: ignore
    from services import stripe_customer_email_backfill as email_backfill_svc  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from utils.stripe_mode import LIVE, TEST, is_missing_on_key, key_mode, object_mode  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

# Stay well inside Stripe's rate limit; the probe is a diagnostic, not a sweep.
MAX_PROBE_CONCURRENCY = 8
MAX_PROBE_LIMIT = 200

# table → (id column, mode column, Stripe retrieve callable)
_KINDS: Dict[str, Dict[str, Any]] = {
    "riders": {
        "table": "users",
        "id_column": "stripe_customer_id",
        "mode_column": "stripe_customer_id_mode",
        "retrieve": lambda oid, key: stripe.Customer.retrieve(oid, api_key=key),
    },
    "drivers": {
        "table": "drivers",
        "id_column": "stripe_account_id",
        "mode_column": "stripe_account_id_mode",
        "retrieve": lambda oid, key: stripe.Account.retrieve(oid, api_key=key),
    },
    "corporate": {
        "table": "corporate_accounts",
        "id_column": "stripe_customer_id",
        "mode_column": "stripe_customer_id_mode",
        "retrieve": lambda oid, key: stripe.Customer.retrieve(oid, api_key=key),
    },
}


def _require_super_admin(admin: dict) -> None:
    if (admin or {}).get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")


def _count(table: str, id_column: str, mode_column: str, mode: Optional[str]) -> int:
    """Count rows holding a Stripe identity whose stamp is `mode`.

    ``mode=None`` counts the unstamped rows. Uses PostgREST's exact count with
    ``limit(1)`` so only the count crosses the wire, not the rows — this runs
    over the whole users table. Blocking; call inside ``run_sync``.
    """
    q = db_supabase.supabase.table(table).select("id", count="exact").not_.is_(id_column, "null")
    q = q.is_(mode_column, "null") if mode is None else q.eq(mode_column, mode)
    return int(getattr(q.limit(1).execute(), "count", None) or 0)


@router.get("/stripe/mode-audit")
async def stripe_mode_audit(admin: dict = Depends(get_admin_user)) -> Dict[str, Any]:
    """DB-only summary of Stripe-identity mode drift. Makes no Stripe calls.

    `unverified` is the population that predates migration 286 — their mode was
    never recorded and cannot be inferred from the ID, so only a probe (or the
    user's next real interaction) can classify them.
    """
    _require_super_admin(admin)

    settings = await get_app_settings()
    current = key_mode(settings.get("stripe_secret_key", ""))

    kinds: Dict[str, Any] = {}
    for kind, spec in _KINDS.items():
        counts = await run_sync(
            lambda s=spec: {
                "live": _count(s["table"], s["id_column"], s["mode_column"], LIVE),
                "test": _count(s["table"], s["id_column"], s["mode_column"], TEST),
                "unverified": _count(s["table"], s["id_column"], s["mode_column"], None),
            }
        )
        # Rows stamped with the OTHER mode are known-stranded: they are repaired
        # on the affected user's next interaction, no probe needed.
        stranded = counts["test"] if current == LIVE else counts["live"] if current == TEST else 0
        kinds[kind] = {**counts, "table": spec["table"], "known_stranded": stranded}

    return {
        "current_key_mode": current,
        "kinds": kinds,
        "note": (
            "Identities stamped with a mode other than current_key_mode are unreachable and are "
            "repaired automatically on the user's next interaction. 'unverified' rows predate mode "
            "tracking — run the probe to classify them."
        ),
    }


class ProbeRequest(BaseModel):
    kind: Literal["riders", "drivers", "corporate"]
    limit: int = Field(25, ge=1, le=MAX_PROBE_LIMIT)
    # Stamping is the whole point (it shrinks the unverified population and
    # lets the hot paths short-circuit), but it is still a write, so it is
    # opt-out rather than implicit.
    stamp: bool = True


@router.post("/stripe/mode-audit/probe")
async def probe_stripe_identities(
    body: ProbeRequest,
    admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Ask Stripe about a bounded sample of unverified identities.

    For each row: retrieve the object on the running key. Resolvable → stamp
    its true mode from the object's own ``livemode``. Not resolvable → report
    it as stranded. Ambiguous failures (auth, rate limit, connection) are
    reported separately and never treated as evidence about the object — see
    utils/stripe_mode.py for why that line matters.

    Read-mostly by design: this never re-provisions or retires. It tells you
    the size and shape of the problem.
    """
    _require_super_admin(admin)

    spec = _KINDS[body.kind]
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    current = key_mode(stripe_secret)

    def _select():
        return (
            db_supabase.supabase.table(spec["table"])
            .select(f"id,{spec['id_column']}")
            .not_.is_(spec["id_column"], "null")
            .is_(spec["mode_column"], "null")
            .limit(body.limit)
            .execute()
        )

    rows = (await run_sync(_select)).data or []
    if not rows:
        return {
            "kind": body.kind,
            "current_key_mode": current,
            "probed": 0,
            "resolvable": 0,
            "stranded": 0,
            "inconclusive": 0,
            "stranded_ids": [],
        }

    sem = asyncio.Semaphore(MAX_PROBE_CONCURRENCY)
    resolvable: List[str] = []
    stranded: List[str] = []
    inconclusive: List[str] = []

    async def _probe(row: Dict[str, Any]) -> None:
        obj_id = row[spec["id_column"]]
        async with sem:
            try:
                obj = await asyncio.to_thread(spec["retrieve"], obj_id, stripe_secret)
            except Exception as e:
                if is_missing_on_key(e, obj_id):
                    stranded.append(obj_id)
                else:
                    # Our key or the network — NOT evidence about this object.
                    logger.error("[STRIPE-MODE-AUDIT] inconclusive probe for %s", obj_id, exc_info=True)
                    inconclusive.append(obj_id)
                return
            resolvable.append(obj_id)
            observed = object_mode(obj)
            if body.stamp and observed:
                # Filtered on the id so a row re-provisioned mid-probe is not
                # stamped with a mode belonging to an identity it no longer has.
                await db_supabase.update_one(
                    spec["table"],
                    {"id": row["id"], spec["id_column"]: obj_id},
                    {spec["mode_column"]: observed},
                )

    await asyncio.gather(*(_probe(r) for r in rows))

    await log_admin_action(
        admin,
        "stripe_mode_probe",
        spec["table"],
        # audit_logs.entity_id is NOT NULL (migration 06) and log_admin_action
        # swallows its own failures — passing None here meant every probe ran
        # unaudited while still returning 200. This action is table-wide rather
        # than row-scoped, so the kind names what was swept.
        f"kind:{body.kind}",
        {
            "kind": body.kind,
            "probed": len(rows),
            "resolvable": len(resolvable),
            "stranded": len(stranded),
            "inconclusive": len(inconclusive),
            "stamped": body.stamp,
        },
    )

    return {
        "kind": body.kind,
        "current_key_mode": current,
        "probed": len(rows),
        "resolvable": len(resolvable),
        "stranded": len(stranded),
        "inconclusive": len(inconclusive),
        # Stripe IDs only — no names, emails, or phones (PIPEDA).
        "stranded_ids": sorted(stranded)[:MAX_PROBE_LIMIT],
        "note": (
            "Stranded identities are replaced automatically on the user's next interaction "
            "(riders: payment screen; drivers: Set up payouts). Cards and Connect accounts from "
            "the previous mode cannot be recovered — Stripe has no test→live copy path."
        ),
    }


class BackfillEmailsRequest(BaseModel):
    """Scope for the Stripe customer email backfill. Empty body = every rider."""

    model_config = {"extra": "forbid"}

    # Omit for every rider; pass ids/addresses to rehearse on a few first.
    user_ids: Optional[List[str]] = Field(None, max_length=500)
    emails: Optional[List[str]] = Field(None, max_length=500)
    # Bounded so one request cannot run unboundedly long; the response says
    # whether more riders remain rather than truncating silently.
    limit: int = Field(email_backfill_svc.DEFAULT_LIMIT, ge=1, le=email_backfill_svc.MAX_LIMIT)
    # Resume token from a previous response's next_cursor. Without it a second
    # call re-reads the first page and reports "nothing to sync" while the rest
    # of the fleet goes untouched.
    cursor: Optional[str] = Field(None, max_length=128)
    # Dry run by DEFAULT: this transfers rider PII to a US processor in bulk,
    # so writing must be asked for explicitly.
    apply: bool = False


@router.post("/stripe/customer-emails/backfill")
async def admin_backfill_stripe_customer_emails(
    body: BackfillEmailsRequest,
    admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Attach riders' emails to Stripe customers created without one.

    Rider customers minted before the email-mapping change carry only
    ``metadata.user_id``, so searching Stripe by a rider's address finds
    nothing and every support / dispute / refund lookup needs a Supabase
    round-trip first. This repairs those customers in place.

    ``apply=false`` (the default) retrieves every customer and returns the
    exact diff without writing — that is what the dashboard previews before
    asking to confirm.

    Unlike the probe above this DOES write to Stripe, but only the ``email``
    field: no customer is created, no identity is re-provisioned, and no
    ``payment_method`` is touched. A customer this key cannot see is reported,
    never repaired — that stays with the rider's own card screen, where the
    person whose default card would be cleared is present.

    super_admin only: a bulk PII transfer to a US processor, the same bar as
    the rest of this module.
    """
    _require_super_admin(admin)

    try:
        result = await email_backfill_svc.backfill_stripe_customer_emails(
            user_ids=body.user_ids,
            emails=body.emails,
            limit=body.limit,
            cursor=body.cursor,
            apply=body.apply,
        )
    except RuntimeError as e:
        # Stripe unconfigured — a setup problem, not an empty success.
        raise HTTPException(status_code=503, detail="Stripe is not configured.") from e

    await log_admin_action(
        admin,
        "stripe_customer_email_backfill",
        "users",
        (body.user_ids[0] if body.user_ids and len(body.user_ids) == 1 else "*"),
        {
            "applied": result.applied,
            "scanned": result.scanned,
            "updated": result.updated,
            "unchanged": result.unchanged,
            "no_email": result.no_email,
            "skipped_deleted": result.skipped_deleted,
            "missing_on_key": len(result.missing_on_key),
            "throttled": len(result.throttled),
            "failed": len(result.failed),
            "key_mode": result.key_mode,
        },
    )

    # A write run that could not reach some customers must not read as success.
    # `updated` counts successes only — a failed row never reaches the counter —
    # so it is reported as-is rather than having the failures subtracted twice.
    #
    # Throttling is deliberately NOT a 502. A 429 from Stripe means "come back
    # later", not "this broke": raising here threw away the counts (they only
    # survived as prose in `detail`) and aborted the client's batch loop, so a
    # rate-limited sweep looked like an outage. Throttled riders come back in
    # `throttled` on a 200 and the client reports the run as incomplete.
    if result.applied and result.failed:
        raise HTTPException(
            status_code=502,
            detail=(
                f"{result.updated} customer(s) updated, "
                f"{len(result.failed)} could not be written. Re-run — it is safe to repeat."
            ),
        )

    return {
        "applied": result.applied,
        "scanned": result.scanned,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "no_email": result.no_email,
        "skipped_deleted": result.skipped_deleted,
        "has_more": result.has_more,
        "next_cursor": result.next_cursor,
        # True when riders in this selection still need work — more pages,
        # throttled rows, or failures. The client must not report success on it.
        "incomplete": result.incomplete,
        "throttled": result.throttled,
        # Which Stripe account this addressed, so the operator confirming a
        # bulk PII transfer is told LIVE vs TEST rather than inferring it.
        "key_mode": result.key_mode,
        # IDs only — never the email addresses themselves (PIPEDA).
        "changes": [
            {"user_id": c.user_id, "customer_id": c.customer_id, "had_email": c.had_email} for c in result.changes
        ],
        "missing_on_key": result.missing_on_key,
        "failed": result.failed,
        "note": (
            "Customers unreachable on this key are stranded by a test→live rotation. They are "
            "repaired on the rider's next visit to their own payment screen, not by this tool."
        ),
    }
