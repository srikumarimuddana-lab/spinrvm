"""Self-serve company signup (Spinr for Business, M1.3).

POST /api/portal/companies/signup — the Uber-for-Business onboarding entry:
a company registers ITSELF instead of being staff-created. The caller is
already authenticated with the portal work-email OTP session (D1:
authenticated-first — identity comes from the session, never the payload),
so this endpoint only needs the company form.

Flow: validate → abuse caps → create corporate_account
(status=pending_verification, same staff KYB queue as before) → bootstrap the
creator as the OWNER member → audit log + ops-notify email. The account stays
gated until staff approve KYB.

Mounted under /api/portal (App-Check-exempt prefix; CSRF is enforced via the
csrf_token_company double-submit cookie that exists post-login).

NOTE: no `from __future__ import annotations` here — string annotations break
FastAPI's body-param resolution through slowapi's @limiter.limit wrapper (the
forward ref gets evaluated against slowapi's globals, silently downgrading the
pydantic body to a query param). auth.py avoids it for the same reason.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

try:
    from ..core.config import settings  # type: ignore
    from ..db_supabase import (  # type: ignore
        count_pending_signups_for_user,
        delete_corporate_account,
        insert_corporate_account,
    )
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas.corporate import CorporateSelfServeSignup  # type: ignore
    from ..services.corporate_membership_service import bootstrap_owner  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
    from ..utils.email_provider import send_transactional_email  # type: ignore
    from ..utils.rate_limiter import default_limiter as limiter  # type: ignore
except ImportError:
    from core.config import settings  # type: ignore
    from db_supabase import (  # type: ignore
        count_pending_signups_for_user,
        delete_corporate_account,
        insert_corporate_account,
    )
    from dependencies import get_current_user  # type: ignore
    from schemas.corporate import CorporateSelfServeSignup  # type: ignore
    from services.corporate_membership_service import bootstrap_owner  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from utils.email_provider import send_transactional_email  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["corporate-signup"])

# Abuse guard: a single verified email may hold at most this many companies
# still awaiting KYB review. Approved companies don't count against it.
_MAX_PENDING_SIGNUPS = 3


@router.post("/signup")
@limiter.limit("3/hour")
async def self_serve_company_signup(
    request: Request,
    body: CorporateSelfServeSignup,
    current_user: dict = Depends(get_current_user),
):
    """Register a new company; creator becomes its owner member."""
    user_id = current_user["id"]
    email = (current_user.get("email") or "").strip().lower()
    if not email:
        # Phone-OTP rider sessions have no verified email — the signup
        # identity anchor is the work email, so require the email-OTP session.
        raise HTTPException(
            status_code=400,
            detail="Company signup requires a work-email session. Sign in via the business portal.",
        )

    pending = await count_pending_signups_for_user(user_id)
    if pending >= _MAX_PENDING_SIGNUPS:
        raise HTTPException(
            status_code=429,
            detail="You already have companies awaiting verification. Contact support to proceed.",
        )

    now = datetime.now(timezone.utc).isoformat()
    account_data = {
        "name": body.name,
        "legal_name": body.legal_name,
        "business_number": body.business_number,
        "industry": body.industry,
        "size_tier": body.size_tier.value,
        "contact_name": body.contact_name,
        # Identity fields come from the authenticated session, not the payload.
        "contact_email": email,
        "contact_phone": body.contact_phone,
        "billing_email": str(body.billing_email) if body.billing_email else email,
        "address_line1": body.address_line1,
        "address_line2": body.address_line2,
        "city": body.city,
        "province": body.province,
        "postal_code": body.postal_code,
        # Province doubles as the GST/PST tax region (validated 2-letter code).
        "tax_region": body.province,
        "locale": body.locale.value,
        "status": "pending_verification",
        "signup_user_id": user_id,
        "signup_source": "self_serve",
        "terms_accepted_version": body.terms_version,
        "terms_accepted_at": now,
    }

    try:
        company = await insert_corporate_account(account_data)
    except Exception as e:
        logger.error("self-serve signup: corporate_accounts insert failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Could not create the company. Please try again.") from e
    if not company or not company.get("id"):
        logger.error("self-serve signup: insert returned no row (user %s)", user_id)
        raise HTTPException(status_code=503, detail="Could not create the company. Please try again.")

    company_id = company["id"]

    try:
        member, _ = await bootstrap_owner(company_id=company_id, email=email, user_id=user_id)
    except Exception as e:
        # A company without its owner is unusable and unreachable — roll the
        # row back so a retry starts clean instead of tripping the pending cap.
        logger.error(
            "self-serve signup: owner bootstrap failed for company %s — rolling back",
            company_id,
            exc_info=True,
        )
        try:
            await delete_corporate_account(company_id)
        except Exception:
            logger.error("self-serve signup: rollback delete failed for company %s", company_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not create the company. Please try again.") from e

    await log_admin_action(
        {"id": user_id, "role": "user"},
        action="corporate_self_serve_signup",
        resource="corporate_accounts",
        resource_id=company_id,
        details={
            "signup_source": "self_serve",
            "size_tier": body.size_tier.value,
            "terms_version": body.terms_version,
        },
    )

    # Courtesy heads-up to the ops inbox — best-effort; the staff KYB queue is
    # the durable signal, so a mail failure must not fail the signup.
    if settings.CORPORATE_OPS_EMAIL:
        try:
            await send_transactional_email(
                to=settings.CORPORATE_OPS_EMAIL,
                subject="New Spinr for Business signup awaiting KYB review",
                text=(
                    f"Company '{body.name}' registered via self-serve signup and is in the "
                    f"KYB queue (status: pending_verification).\n\n"
                    f"Review it in the admin dashboard → Corporate Accounts → KYB Queue."
                ),
                log_id=company_id,
                email_type="corporate_signup_ops",
            )
        except Exception:
            logger.error("self-serve signup: ops notification email failed", exc_info=True)

    return {
        "success": True,
        "company": {
            "id": company_id,
            "name": company.get("name"),
            "status": company.get("status", "pending_verification"),
        },
        "member": {"id": member.get("id"), "role": member.get("role", "owner")},
        "message": "Company registered. We'll email you once verification is complete.",
    }
