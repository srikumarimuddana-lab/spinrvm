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
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
    from ...utils.stripe_mode import LIVE, TEST, is_missing_on_key, key_mode, object_mode
except ImportError:
    import db_supabase  # type: ignore
    from db_supabase import run_sync  # type: ignore
    from dependencies import get_admin_user  # type: ignore
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
                    logger.error(
                        "[STRIPE-MODE-AUDIT] inconclusive probe for %s", obj_id, exc_info=True
                    )
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
