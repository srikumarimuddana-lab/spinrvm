#!/usr/bin/env python3
"""
Seed a test corporate company + owner + members for local manual e2e testing (DEV-ONLY).

Creates, directly via Supabase, everything the real self-serve signup +
staff-KYB-approval + owner-bootstrap flow would produce — but skips the
email-OTP round trip and the KYB review queue, so a developer can log
straight into the corporate portal (`/company-login`) with a known email
and no inbox/staff step required.

This is NOT a migration and does not run in CI or production deploys. It
talks to whatever SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are set in the
environment (or backend/.env) when it runs — do not point it at a
production project.

Usage:
    python backend/scripts/seed_corporate_test_data.py
    python backend/scripts/seed_corporate_test_data.py --company-name "Acme Test Co" \\
        --owner-email owner@acmetest.dev --topup 500 --members 2
    python backend/scripts/seed_corporate_test_data.py --cleanup --company-name "Acme Test Co"

Idempotent: re-running with the same --company-name reuses the existing
company (looked up by name) instead of creating a duplicate, and
bootstrap_owner is itself idempotent for the same owner user.

After seeding, log in at http://localhost:3000/company-login with the
printed owner email (dev email provider — check console/log output or
whatever local email catcher is configured for the actual OTP code, or use
the `"1234"` dev bypass code per CLAUDE.md's OTP convention when ENV != production).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed_corporate_test_data")


def load_dotenv() -> None:
    """Load local .env variables if not already set in environment."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


load_dotenv()

try:
    from backend import db_supabase
    from backend.services.corporate_membership_service import bootstrap_owner
    from backend.services.corporate_wallet_service import apply_topup
except ImportError:
    import db_supabase
    from services.corporate_membership_service import bootstrap_owner
    from services.corporate_wallet_service import apply_topup


def _synthetic_phone_for_email(email: str) -> str:
    import hashlib

    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
    return f"email:{digest}"


async def _find_company_by_name(name: str):
    rows = await db_supabase.get_rows("corporate_accounts", {"name": name}, limit=1)
    return rows[0] if rows else None


async def _find_user_by_email(email: str):
    rows = await db_supabase.get_rows("users", {"email": email.strip().lower()}, limit=1)
    return rows[0] if rows else None


async def _ensure_user(email: str) -> dict:
    existing = await _find_user_by_email(email)
    if existing:
        logger.info("Reusing existing users row for %s (id=%s)", email, existing["id"])
        return existing
    payload = {
        "id": str(uuid.uuid4()),
        "phone": _synthetic_phone_for_email(email),
        "email": email.strip().lower(),
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_complete": True,
        "token_version": 0,
    }
    created = await db_supabase.create_user(payload)
    logger.info("Created users row for %s (id=%s)", email, created["id"])
    return created


async def _cleanup(company_name: str) -> None:
    company = await _find_company_by_name(company_name)
    if not company:
        logger.info("No company named %r found — nothing to clean up.", company_name)
        return
    company_id = company["id"]
    if not db_supabase.supabase:
        raise RuntimeError("Supabase client not configured")

    def _fn():
        wallet_rows = db_supabase.supabase.table("corporate_wallets").select("id").eq("company_id", company_id).execute().data
        if wallet_rows:
            db_supabase.supabase.table("corporate_wallet_transactions").delete().eq(
                "wallet_id", wallet_rows[0]["id"]
            ).execute()
        db_supabase.supabase.table("corporate_wallets").delete().eq("company_id", company_id).execute()
        db_supabase.supabase.table("corporate_members").delete().eq("company_id", company_id).execute()
        db_supabase.supabase.table("corporate_accounts").delete().eq("id", company_id).execute()

    await db_supabase.run_sync(_fn)
    logger.info("Deleted company %r (id=%s) and its wallet/members.", company_name, company_id)


async def _seed(company_name: str, owner_email: str, topup: Decimal, member_count: int) -> None:
    owner_user = await _ensure_user(owner_email)

    company = await _find_company_by_name(company_name)
    if company:
        logger.info("Reusing existing company %r (id=%s)", company_name, company["id"])
    else:
        company = await db_supabase.insert_corporate_account(
            {
                "name": company_name,
                "status": "active",
                "size_tier": "smb",
                "contact_name": "Dev Seed Owner",
                "contact_email": owner_user["email"],
                "billing_email": owner_user["email"],
                "signup_source": "staff",
                "signup_user_id": owner_user["id"],
            }
        )
        logger.info("Created corporate_accounts row %r (id=%s, status=active)", company_name, company["id"])

    member, _invite_url = await bootstrap_owner(
        company_id=company["id"], email=owner_user["email"], user_id=owner_user["id"]
    )
    logger.info("Owner membership: user_id=%s role=%s status=%s", member["user_id"], member["role"], member["status"])

    wallet = await db_supabase.ensure_corporate_wallet(company_id=company["id"])
    logger.info("Wallet id=%s balance=%s", wallet["id"], wallet["balance"])

    if topup > 0:
        txn = await apply_topup(
            wallet_id=wallet["id"],
            amount=topup,
            stripe_payment_intent_id=f"seed-{uuid.uuid4()}",
            actor_user_id=owner_user["id"],
            notes="dev seed top-up",
        )
        logger.info("Applied top-up of %s, new balance=%s", topup, txn.get("balance_after"))

    seeded_members = [(owner_user["email"], "owner")]
    for i in range(1, member_count + 1):
        member_email = f"seed-member{i}-{company['id'][:8]}@example.dev"
        member_user = await _ensure_user(member_email)
        m, _ = await bootstrap_owner(company_id=company["id"], email=member_email, user_id=member_user["id"])
        # bootstrap_owner always grants role='owner' for the first call per user;
        # demote extra seeded members to 'member' directly since they should not be owners.
        if m.get("role") == "owner":

            def _demote(member_id=m["id"]):
                return (
                    db_supabase.supabase.table("corporate_members")
                    .update({"role": "member"})
                    .eq("id", member_id)
                    .execute()
                )

            await db_supabase.run_sync(_demote)
        seeded_members.append((member_email, "member"))
        logger.info("Seeded member %s (role=member)", member_email)

    print("\n=== Corporate test data seeded ===")
    print(f"Company:      {company_name}  (id={company['id']}, status=active)")
    print(f"Owner login:  {owner_user['email']}  (go to http://localhost:3000/company-login)")
    print("  OTP code:   dev bypass \"1234\" works when ENV != production (see CLAUDE.md OTP convention)")
    for email, role in seeded_members[1:]:
        print(f"Member:       {email}  (role={role})")
    print(f"Wallet balance: {topup if topup > 0 else wallet['balance']}")
    print("===================================\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company-name", default="Dev Seed Corp")
    parser.add_argument("--owner-email", default="owner@devseedcorp.example.dev")
    parser.add_argument("--topup", type=Decimal, default=Decimal("500.00"))
    parser.add_argument("--members", type=int, default=1, help="number of additional (non-owner) members to seed")
    parser.add_argument("--cleanup", action="store_true", help="delete the named company and its wallet/members instead of seeding")
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — refusing to run against an unknown project.")
        sys.exit(1)

    if os.environ.get("ENV", "development").lower() == "production":
        logger.error(
            "ENV=production — refusing to seed fake corporate data into a production project. "
            "This script is for local/dev use only."
        )
        sys.exit(1)

    if args.cleanup:
        await _cleanup(args.company_name)
    else:
        await _seed(args.company_name, args.owner_email, args.topup, args.members)


if __name__ == "__main__":
    asyncio.run(main())
