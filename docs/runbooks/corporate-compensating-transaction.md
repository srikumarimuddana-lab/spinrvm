# Corporate wallet/allowance compensating transaction

What to do when a `corporate_wallet_apply_delta` or `corporate_allowance_apply_delta`
call moved the wrong amount, hit the wrong wallet/member, or was applied twice —
money has already moved through `corporate_wallets.balance` and/or
`corporate_member_allowances.used`, and `wallet_transactions`/
`corporate_wallet_transactions` rows already exist.

**This is not a `git revert`.** Reverting the application code (or, worse,
`DROP FUNCTION`ing the RPC) does nothing to the ledger rows or balances the bad
call already wrote — those are separate, durable state in Supabase. The only way
to undo a bad money delta is another money delta, through the same locked RPC,
computed from what actually happened since the bad write.

See also: `backend/services/corporate_wallet_service.py`,
`backend/services/corporate_allowance_service.py`,
`backend/migrations/214_corporate_actor_user_id_text.sql` (current
`corporate_wallet_apply_delta` body), `backend/migrations/261_corporate_allowance_actor_user_id_text.sql`
(current `corporate_allowance_apply_delta` body), `backend/migrations/258_corporate_allowance_cap_in_rpc.sql`
(the ride_debit ceiling guard).

## When this applies

- A wallet top-up, adjustment, refund, or ride debit was applied with the wrong
  `p_delta`, wrong `p_wallet_id`, or duplicate `stripe_payment_intent_id` /
  `reference_id` that the RPC's idempotency check didn't catch (e.g. two
  genuinely different Stripe events that should have been deduped by a
  higher-level bug, not the RPC).
- An allowance `allowance_grant` / `allowance_reset` / `ride_debit` /
  `ride_debit_reversal` was applied against the wrong `corporate_member_allowances`
  row, or with the wrong amount (bad fare split, bug in `settle_corporate`).
- A manual admin adjustment (`corporate_wallet.py manual_adjust`) was entered
  with a typo'd amount or wrong sign.

## Step 0 — do not touch the balance directly

Never `UPDATE corporate_wallets SET balance = ...` or
`UPDATE corporate_member_allowances SET used = ...` by hand, even to "just fix
the number." That bypasses the row lock, the ledger insert, and the floor/cap
checks — it produces a balance with no corresponding `corporate_wallet_transactions`
row, which breaks every reconciliation query below and makes the *next* bad-delta
investigation impossible. All corrections go through `corporate_wallet_apply_delta`
/ `corporate_allowance_apply_delta`, same as any other money movement.

## Step 1 — detect and scope the bad application

Every application of either RPC leaves exactly one (`corporate_wallet_apply_delta`)
or two (`corporate_allowance_apply_delta`: one `master`-scope row, one
`member:<id>`-scope row) rows in `corporate_wallet_transactions`. Start there —
never trust a support ticket's stated amount, confirm it in the ledger.

```sql
-- Find the suspect transaction(s) by wallet, time window, and/or reference.
SELECT id, wallet_id, scope, type, amount, balance_after, ride_id, member_id,
       stripe_payment_intent_id, actor_user_id, notes, created_at
FROM corporate_wallet_transactions
WHERE wallet_id = :wallet_id
  AND created_at BETWEEN :window_start AND :window_end
ORDER BY created_at ASC;
```

Cross-check against:
- **Application logs** — the route/service that called `_apply()` /
  `corporate_wallet_service.apply_*()` / `corporate_allowance_service.apply_*()`
  logs at `info` on success per Observability Conventions (state transition);
  search by `wallet_id`/`ride_id`, domain tag `corporate`.
- **Stripe** (for topups/refunds) — `stripe_payment_intent_id` on the ledger row
  must match a real, correctly-amounted Stripe object. A mismatch (wrong PI, or
  a PI that was itself later refunded/disputed) is a strong signal the delta was
  the wrong number, not just badly timed.
- **`corporate_member_allowances.used` / `corporate_wallets.balance` right now**
  vs. what the ledger sums to (Step 4's reconciliation query) — if they already
  disagree, something outside these two RPCs wrote to the row (see Step 0) and
  the fix needs to account for that drift too.

Write down, before doing anything else:
1. The exact bad transaction id(s) (`corporate_wallet_transactions.id`).
2. The delta that *should* have been applied (could be zero, i.e. the whole
   transaction should not have happened).
3. Every transaction on the same wallet/allowance that happened **after** the
   bad one, up to now — Step 2 needs these.

## Step 2 — compute the compensating delta (not a blind reversal)

**Do not just negate the bad amount and stop there.** Both RPCs are
lock-then-mutate against the *current* balance/usage counter, not against the
value at the time of the bad write. If any legitimate delta happened on the same
wallet or allowance between the bad write and now (a real top-up, a real ride
debit, another admin adjustment), simply applying `-bad_amount` will land the
balance at the wrong number — it corrects the bad transaction's effect but
ignores everything that happened after it, which is exactly wrong if intervening
activity itself depended on the post-bad-write balance (e.g. a ride was
correctly clamped/declined because the wallet looked lower than it should have).

The correct approach is target-balance reasoning, not delta-negation:

1. Compute what `corporate_wallets.balance` (or `corporate_member_allowances.used`)
   **should be right now**, as if the bad transaction had never happened but
   every subsequent legitimate transaction still had:
   ```sql
   -- Sum every transaction on this wallet EXCEPT the bad one(s), in order,
   -- starting from the balance immediately before the earliest bad txn.
   SELECT balance_after
   FROM corporate_wallet_transactions
   WHERE wallet_id = :wallet_id
     AND created_at < :bad_txn_created_at
   ORDER BY created_at DESC
   LIMIT 1;
   -- => v_balance_before_bad

   SELECT COALESCE(SUM(amount), 0) AS legit_deltas_since
   FROM corporate_wallet_transactions
   WHERE wallet_id = :wallet_id
     AND created_at >= :bad_txn_created_at
     AND id NOT IN (:bad_txn_id, ...);
   -- => v_legit_deltas_since

   -- target_balance = v_balance_before_bad + v_legit_deltas_since
   ```
2. Compare `target_balance` to the wallet's actual current `balance`. The
   compensating delta is `target_balance - current_balance` — apply exactly
   that, not `-bad_amount`. In the common case (nothing else touched the wallet
   between the bad write and the fix), these two are numerically identical;
   they diverge whenever intervening activity happened, which is exactly the
   case a blind reversal gets wrong.
3. For allowances, do the same target-usage computation against
   `corporate_member_allowances.used`, and remember `corporate_allowance_apply_delta`
   moves **two** balances per call (`corporate_wallets.balance` and
   `corporate_member_allowances.used`) — both need the target computation, and
   they are not independent: an `allowance_grant`/`ride_debit` moves both in the
   same call, so a compensating `allowance_rollback` (or a manual
   `apply_adjustment` on the master side plus a separate allowance correction,
   if the two have since diverged for unrelated reasons) must be sized against
   each target independently, not assumed to be symmetric.
4. If the bad transaction crossed a **floor or cap boundary** (e.g. a ride debit
   that should have been capped by `allowance_cap_exceeded` and routed to the
   master wallet instead, per migration 258's fallback), the compensation is not
   purely arithmetic — reason about which path (member allowance vs. master
   wallet) the money *should* have taken and correct both sides to match that,
   not just net the dollar amount to zero.

If the compensating delta computation is not straightforward (multiple bad
transactions interleaved with legitimate ones, or the target-balance math
disagrees with common sense), stop and get a second person to check the SQL
before Step 3 — this is exactly the kind of case CLAUDE.md's "escalate, don't
silently ship" rule covers, and it applies to data fixes as much as to code.

## Step 3 — apply the correction through the RPC

Always go through the same function, never a raw `UPDATE`. This preserves the
row lock, the ledger insert (so the correction itself is auditable and shows up
in the next reconciliation), and the floor/cap checks (so a bad correction can't
silently push a wallet negative past its floor either).

```sql
-- Wallet correction (master scope; use the RPC, not psql UPDATE):
SELECT * FROM corporate_wallet_apply_delta(
    p_wallet_id      => :wallet_id,
    p_scope          => 'master',
    p_type           => 'adjustment',
    p_delta          => :compensating_delta,   -- signed, from Step 2
    p_actor_user_id  => :admin_user_id,         -- the human doing the fix, not the original actor
    p_notes          => 'compensating txn for bad ' || :bad_txn_id || ' — <one-line reason>',
    p_floor          => :floor_if_applicable
);
```

Or from application code, prefer `corporate_wallet_service.apply_adjustment()` /
`corporate_allowance_service.apply_rollback()` (or `apply_grant`/`apply_reset` as
appropriate for the allowance case) over a hand-written `supabase.rpc()` call —
they already handle the `Decimal` → RPC-string conversion correctly (money
arithmetic rule) and won't accidentally hand the RPC a `float`.

Notes:
- `p_notes` must reference the bad transaction id and a one-line reason — this
  is the only place a future auditor sees *why* an off-schedule adjustment
  landed on the wallet ledger.
- Use a fresh, distinct `stripe_payment_intent_id`/reference where the RPC's
  idempotency key applies, so the correction itself can't collide with — or be
  deduped against — the original bad transaction.
- If the correction would drive the wallet below its configured floor, that's
  the RPC telling you the target-balance math in Step 2 is wrong (or the
  business actually needs to authorize a floor exception) — don't work around
  it by omitting `p_floor`.

## Step 4 — verify with a reconciliation query

After applying the correction, confirm the ledger now sums to the live balance
and that the sequence of `balance_after` values is self-consistent (each row's
`balance_after` equals the previous row's `balance_after` plus its own `amount`
— any gap means something wrote to the balance outside these RPCs).

```sql
-- 1. Ledger sum vs. live balance (must match to the cent).
WITH wallet_start AS (
    SELECT 0::numeric(12,2) AS opening_balance   -- wallets are created at 0; adjust if not
)
SELECT
    w.id,
    w.balance AS live_balance,
    ws.opening_balance + COALESCE(SUM(t.amount), 0) AS ledger_computed_balance,
    w.balance - (ws.opening_balance + COALESCE(SUM(t.amount), 0)) AS drift
FROM corporate_wallets w
CROSS JOIN wallet_start ws
LEFT JOIN corporate_wallet_transactions t
    ON t.wallet_id = w.id AND t.scope = 'master'
WHERE w.id = :wallet_id
GROUP BY w.id, w.balance, ws.opening_balance;
-- drift must be 0.00

-- 2. Row-by-row balance_after continuity (catches any raw-UPDATE bypass, past or future).
SELECT id, created_at, amount, balance_after,
       balance_after - amount AS implied_prior_balance,
       LAG(balance_after) OVER (ORDER BY created_at) AS actual_prior_balance
FROM corporate_wallet_transactions
WHERE wallet_id = :wallet_id AND scope = 'master'
ORDER BY created_at;
-- implied_prior_balance must equal actual_prior_balance on every row after the first

-- 3. Allowance-side check (used vs. ledger sum for that member).
SELECT
    a.id AS allowance_id,
    a.used AS live_used,
    COALESCE(SUM(t.amount), 0) AS ledger_computed_used
FROM corporate_member_allowances a
LEFT JOIN corporate_wallet_transactions t
    ON t.member_id = a.member_id
   AND t.scope = 'member:' || a.member_id::text
WHERE a.id = :allowance_id
GROUP BY a.id, a.used;
-- live_used must equal ledger_computed_used
```

If drift is non-zero anywhere, do not consider the fix done — go back to Step 1;
there is either another bad transaction not yet accounted for, or a raw `UPDATE`
somewhere outside these two RPCs (grep the codebase for direct
`.update({"balance"` / `.update({"used"` calls against these tables — there
should be none outside `db_supabase.py`'s RPC wrappers).

## Step 5 — close the loop

- Record the incident (bad transaction id, compensating transaction id, root
  cause, who applied the fix) — treat it as a P1/P2 incident writeup per
  whatever the team's current incident process is, not just a Slack message.
  If the bad delta was PII- or payment-adjacent at any point (e.g. touched a
  Stripe object), the breach-protocol triage in `docs/runbooks/data-breach.md`
  may also apply — check, don't assume it doesn't.
- If the root cause was a bug in the calling code (not a fat-fingered manual
  adjustment), file it and fix the bug before the same class of error recurs —
  a compensating transaction fixes the data, not the code path that produced it.
- If the bad delta was large enough or old enough to have affected a rider- or
  company-facing statement/invoice already sent, loop in whoever owns billing
  comms before the correction is considered closed — the ledger being right is
  necessary but not sufficient if a human already saw the wrong number.

## Known gap — not covered by this runbook

KYB (know-your-business) document Storage bucket RLS/access scoping was flagged
in the 2026-07-28 corporate lifecycle audit as "not confirmed." This runbook
does not touch document storage and has no bearing on that gap — see
`ACTION_ITEMS.md` B12 for tracking; it needs its own security-focused pass
before it can be marked verified.
