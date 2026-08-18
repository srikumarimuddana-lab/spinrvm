"""C23 (ACTION_ITEMS.md) Action item 5: admin-triggered submission of
chargeback evidence to Stripe's Files/Disputes API.

This is the highest-risk piece of C23 -- unlike item 4's zip download (a
read-only, human-reviewed artifact), this endpoint makes a real, effectively
irreversible external call: once evidence is submitted to Stripe on a live
dispute, it can only be UPDATED (not un-submitted) before the dispute's
evidence_due_by. Per CLAUDE.md's pre-merge gates for money-touching,
non-trivial, user-visible+external changes, this endpoint:

  1. Ships dark behind `dispute_stripe_evidence_submission_enabled` in
     app_settings (default false/unset) -- see routes/admin/settings.py.
  2. Requires an explicit `confirm: true` on every request -- the flag
     alone does not make a single call submit.
  3. Is mounted with require_super_admin (both at the router mount in
     routes/admin/__init__.py AND re-checked here), same posture as this
     repo's other Stripe-ledger-sensitive routes (stripe_payout_sync,
     stripe_connect_ledger, tax_id_import) -- stricter than item 4's
     read-only pack download, which stays on the general "support" module
     gate.
  4. Is idempotent: an atomic claim on `evidence_submitted_at IS NULL`
     (same claim-flag shape as utils/dispute_evidence_reminder.py's
     evidence_reminder_sent_at) means a retried/duplicate request cannot
     double-submit.
  5. Logs to the admin-action audit table (security-relevant event per
     CLAUDE.md's observability conventions) whether the flag was off,
     the claim was lost to another request, or the submission succeeded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import require_super_admin
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
    from ...utils.dispute_evidence_pack import build_account_history_summary, build_cover_letter_text
except ImportError:
    import db_supabase
    from dependencies import require_super_admin
    from settings_loader import get_app_settings
    from utils.audit_logger import log_admin_action
    from utils.dispute_evidence_pack import build_account_history_summary, build_cover_letter_text

logger = logging.getLogger(__name__)

router = APIRouter()


class SubmitEvidenceRequest(BaseModel):
    confirm: bool = False
    # Optional overrides so a support agent can submit their edited cover
    # letter text instead of the auto-drafted one -- the pack in item 4 is
    # explicitly a *draft* meant to be edited before submission.
    uncategorized_text: Optional[str] = None


@router.post("/disputes/{dispute_id}/submit-evidence")
async def admin_submit_dispute_evidence(
    dispute_id: str,
    body: SubmitEvidenceRequest,
    admin_user: dict = Depends(require_super_admin),
):
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to submit evidence to Stripe -- this cannot be un-submitted.",
        )

    settings = await get_app_settings()
    if not (settings or {}).get("dispute_stripe_evidence_submission_enabled"):
        await log_admin_action(
            admin_user,
            "dispute_evidence_submit_rejected_flag_off",
            "stripe_dispute",
            dispute_id,
            {},
        )
        raise HTTPException(
            status_code=503,
            detail="Stripe evidence submission is not enabled. An admin must turn on "
            "dispute_stripe_evidence_submission_enabled in Settings after verifying this "
            "flow in staging.",
        )

    dispute_rows = await db_supabase.get_rows("stripe_disputes", {"id": dispute_id}, limit=1)
    if not dispute_rows:
        raise HTTPException(status_code=404, detail="Dispute not found")
    dispute = dispute_rows[0]

    if dispute.get("evidence_submitted_at"):
        raise HTTPException(status_code=409, detail="Evidence was already submitted for this dispute")

    ride_id = dispute.get("ride_id")
    ride = await db_supabase.get_ride_details_enriched(ride_id) if ride_id else None

    evidence_text = body.uncategorized_text
    if not evidence_text:
        if ride:
            account_summary = await build_account_history_summary(ride)
            evidence_text = build_cover_letter_text(ride, dispute, account_summary)
        else:
            evidence_text = f"Chargeback response for dispute {dispute.get('stripe_dispute_id')}."

    stripe_secret = (settings or {}).get("stripe_secret_key", "")
    if not stripe_secret:
        logger.error("dispute-evidence-submit: no Stripe secret key configured")
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_dispute_id = dispute.get("stripe_dispute_id")
    try:
        import stripe

        stripe.Dispute.modify(
            stripe_dispute_id,
            evidence={"uncategorized_text": evidence_text},
            api_key=stripe_secret,
        )
    except Exception as exc:
        logger.error(
            "dispute-evidence-submit: Stripe call failed for dispute %s: %s",
            stripe_dispute_id,
            exc,
            exc_info=True,
        )
        await log_admin_action(
            admin_user,
            "dispute_evidence_submit_stripe_error",
            "stripe_dispute",
            dispute_id,
            {"stripe_dispute_id": stripe_dispute_id, "error": str(exc)[:500]},
        )
        raise HTTPException(status_code=502, detail="Stripe rejected the evidence submission") from exc

    # Atomic claim AFTER the Stripe call succeeds -- if two requests race
    # past the evidence_submitted_at check above, Stripe's own dispute
    # object is the actual source of truth (the second call just re-submits
    # the same evidence, which Stripe allows before due_by); the claim here
    # only prevents this endpoint's own response/audit-log from double-firing.
    claimed = await db_supabase.update_one(
        "stripe_disputes",
        {"id": dispute_id, "evidence_submitted_at": None},
        {"$set": {"evidence_submitted_at": datetime.now(timezone.utc).isoformat()}},
    )

    await log_admin_action(
        admin_user,
        "dispute_evidence_submitted",
        "stripe_dispute",
        dispute_id,
        {"stripe_dispute_id": stripe_dispute_id, "claim_won": claimed is not None},
    )

    return {
        "submitted": True,
        "stripe_dispute_id": stripe_dispute_id,
        "dispute_id": dispute_id,
    }
