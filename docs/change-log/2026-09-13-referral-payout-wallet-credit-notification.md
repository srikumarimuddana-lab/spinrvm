# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this commit) |
| Related issue or gap ID | `ACTION_ITEMS.md` R31 follow-up — "Deliberately left open" note under the wallet-notification sweep |

## 1. Issue / gap identified

A rider's wallet is credited when a referral reward/bonus is earned
(`utils/referral_payout.py::_credit`), but nothing ever told the rider it
happened — no push, no in-app signal. Every sibling money-credit path
(top-up, admin wallet credit/debit, promo, loyalty tier-up) already got this
exact fix in a prior pass (R31/R33/R34); this one was explicitly noted as
left open, needing its own file + tests.

## 2. Root cause

Never built — this notification path simply didn't exist when
`referral_payout.py` was written. Not a regression.

## 3. Fix / remediation

Added one best-effort push notification inside `_credit()`'s rider-wallet
branch, firing immediately after the wallet credit + ledger write succeed
(both call sites in the module — the initial payout and the failed-claim
re-credit retry — route through this one function, so one change covers
both). Mirrors `routes/admin/wallet.py`'s `admin_credit_wallet`/
`admin_debit_wallet` pattern exactly: wrapped in its own try/except so a
notification failure can never surface as a failed credit, `logger.warning`
on failure (not error — this is the established convention for a
degraded-but-recovered side effect, not a DB/auth/payment failure itself).

**Caught and fixed before commit** (per this repo's CLAUDE.md item #10 —
run a `spinr-*` reviewer against the diff before committing on a
live-tested surface): the first draft hardcoded `target_app="rider"`,
reasoning that `kind` (the referral-code type: rider-referral vs
driver-referral program) guaranteed the recipient was a rider.
`spinr-money-auditor` found this false — `routes/users.py`'s
`apply_rider_referral` resolves the referrer from the shared `users` table
with **no role filter**, so a driver account can legitimately end up as
`referrer_user_id` on a `kind="rider"` payout, in which case a hardcoded
`target_app="rider"` push would silently no-op (wrong FCM token column).
Verified this claim directly against `routes/users.py` before accepting it.
Fixed to resolve `target_app` dynamically via `db_supabase.get_user_by_id`,
mirroring `routes/admin/wallet.py`'s `_wallet_target_app` helper — the
existing, already-reviewed solution to this exact ambiguity.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `_credit()`'s rider-wallet branch.** The
  `kind == "driver"` branch (driver_bonuses payable row) returns before this
  new code and is unaffected — confirmed by
  `test_driver_referral_credit_never_sends_rider_push`.
- **Money-path code itself is untouched.** The wallet increment, ledger
  write, and reversal-on-failure logic above the new code is unchanged; the
  notification sits strictly after that block has already committed.
- One new DB read added per rider-referral credit (`get_user_by_id`, which
  has a 30s Redis read-through cache per `repositories/auth_repo.py`) — not
  on any SLA-tracked path (dispatch, fare calc, WS fan-out); referral
  payouts run via the periodic `referral_payout_loop`, not a request path.
- Grepped for every other caller of `_credit()`: only the two call sites
  inside `referral_payout.py` itself (initial payout, failed-claim
  re-credit) — no other file calls it.
- No other consumer of `send_push_notification` or `get_user_by_id` is
  touched; both are pre-existing, already-used helpers.

## 5. User-experience effect

- **Rider-facing.** A rider who earns a referral reward or bonus now gets a
  push notification ("Referral reward earned!" / "Referral bonus earned!")
  they didn't get before. Not visible mid-session in any disruptive way —
  it's an informational push tied to an asynchronous background payout, not
  a change to any existing screen's behavior.
- No driver-facing change for the `kind == "driver"` path (unaffected,
  returns before the new code).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/referral_payout.py` | Added `send_push_notification` import (dual try/except pattern) + a best-effort push in `_credit()`'s rider-wallet branch, with `target_app` resolved from the recipient's actual role | Close the R31 follow-up gap; avoid the misrouted-push bug the money-auditor review caught |
| `backend/tests/test_referral_payout_credit.py` | 6 new tests: push sent with correct args, title differs reward-vs-bonus, driver-account-referrer routes to the driver app (regression test for the caught bug), push failure doesn't fail the credit, user-lookup failure doesn't fail the credit, driver-kind path never pushes | Cover the new behavior and the specific bug found in review |
| `docs/change-log/2026-09-13-referral-payout-wallet-credit-notification.md` | New Change Impact Log entry (this file) | Required for a live-tested (money) surface per `CLAUDE.md` |
| `ACTION_ITEMS.md` | Marked the R31 follow-up note closed with detail | Keep the backlog doc accurate — same discipline applied to the `location_batch_ack` stale-note correction earlier this session |

## 7. Before / after

Purely additive to `_credit()` — no existing statement changed, only new
code appended after the existing try/except block:

```python
# Before: function ends here after a successful credit — no notification.
    except Exception:
        ...
        raise

# After:
    except Exception:
        ...
        raise

    _title = "Referral reward earned!" if txn_type == "referral_reward" else "Referral bonus earned!"
    try:
        _recipient = await db_supabase.get_user_by_id(user_id)
        _target_app = "driver" if _recipient and _recipient.get("role") == "driver" else "rider"
        await send_push_notification(
            user_id, _title, f"${_f(amount)} was added to your wallet.",
            data={"type": "referral_payout", "amount": _f(amount), "referral_payout_id": reference_id},
            target_app=_target_app,
        )
    except Exception as e:
        logger.warning(f"referral_payout: push failed user_id={user_id} reference_id={reference_id}: {e}", exc_info=True)
```

## 8. Rollback plan

`git revert` is fully sufficient: this is a pure addition with no schema
change and no mutation of the money-moving code above it. Reverting removes
the notification only — the wallet credit, ledger entry, and referral
payout mechanics are entirely unaffected either way, so there's no data to
clean up.

## 9. Verification performed

- [x] `ruff check` / `ruff format --check` on both changed files — clean
- [x] `pytest tests/test_referral_payout_credit.py` — 9/9 passed (3
      pre-existing unaffected, 6 new)
- [x] Full `referral_payout` test suite re-run (8 files, 59 tests) — all
      passed, no regressions
- [x] Blast-radius grep: only two call sites of `_credit()`, both inside
      `referral_payout.py`
- [x] Adversarial review performed before commit: dispatched
      `spinr-money-auditor` against the actual diff — found and this PR
      fixed the `target_app` hardcode bug described above; verified that
      specific claim independently against `routes/users.py` rather than
      accepting it on the agent's word
- [x] PIPEDA: log line and notification body/data use only `user_id` /
      `reference_id` (internal IDs) and a dollar amount — no name, phone,
      email, GPS, or address

## 10. What was NOT verified

- Not run against a real Supabase project or real FCM/Expo push delivery —
  mocked `db_supabase`/`send_push_notification` only, per this repo's
  background-loop unit-test convention.
- Copy ("Referral reward earned!" / "Referral bonus earned!") was not
  reviewed by anyone for tone/localization — written to match the existing
  `admin_credit_wallet` notification's plain, factual style, not customer-
  support-reviewed.
