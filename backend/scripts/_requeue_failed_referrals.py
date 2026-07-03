"""One-off ops: inspect (and optionally re-credit) stranded 'failed' referral claims.

Talks to Supabase via the REST API using backend/.env creds (service role) — the
same path the backend uses, so the direct-Postgres IPv6 issue does not apply.

  python scripts/_requeue_failed_referrals.py            # READ-ONLY: show failed claims + referee wallet state
  python scripts/_requeue_failed_referrals.py --apply     # credit each via the vetted recredit_failed_claim

NOT committed; delete after use.
"""

import asyncio
import sys

import db_supabase as db
from utils.referral_payout import ReferralClaimNotFound, recredit_failed_claim


async def wallet_state(user_id: str):
    """READ-ONLY: (balance, count of referral_bonus txns) or (None, 0) if no wallet."""
    ws = await db.get_rows("wallets", {"user_id": user_id}, columns="id,balance", limit=1)
    if not ws:
        return None, 0
    txns = await db.get_rows(
        "wallet_transactions",
        {"user_id": user_id, "type": "referral_bonus"},
        columns="amount,created_at",
        limit=10,
    )
    return ws[0].get("balance"), len(txns or [])


async def main(apply: bool) -> None:
    rows = await db.get_rows(
        "referral_payouts",
        {"status": "failed"},
        columns=(
            "id,referee_user_id,referrer_user_id,kind,referrer_reward,referee_reward,"
            "referrer_credited_at,referee_credited_at,status,created_at"
        ),
        limit=1000,
    )
    print(f"FAILED CLAIMS: {len(rows or [])}  (mode={'APPLY' if apply else 'READ-ONLY'})")
    for r in rows or []:
        print("-" * 70)
        print(f"  id={r.get('id')}  kind={r.get('kind')}  status={r.get('status')}  created_at={r.get('created_at')}")
        print(
            f"  referrer={r.get('referrer_user_id')}  credited_at={r.get('referrer_credited_at')}  "
            f"reward={r.get('referrer_reward')}"
        )
        print(
            f"  referee ={r.get('referee_user_id')}  credited_at={r.get('referee_credited_at')}  "
            f"reward={r.get('referee_reward')}"
        )
        rid = r.get("referee_user_id")
        if rid:
            bal, n = await wallet_state(rid)
            print(f"  referee wallet: balance={bal}  referral_bonus_txns={n}")
        if apply and rid:
            try:
                out = await recredit_failed_claim(rid)
                bal, n = await wallet_state(rid)
                print(f"  >>> RECREDITED credited={out['credited']}  -> referee wallet: balance={bal} txns={n}")
            except ReferralClaimNotFound:
                print("  >>> SKIP: no failed claim / already taken")
            except Exception as e:  # noqa: BLE001 — one-off ops script, surface everything
                print(f"  >>> ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
