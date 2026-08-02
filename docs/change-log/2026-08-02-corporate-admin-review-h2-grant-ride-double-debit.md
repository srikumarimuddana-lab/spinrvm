# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #2 |

## 1. Issue / gap identified

A grant-funded corporate ride debited the company's master wallet twice —
once when the top-up request was granted, again when the ride the grant was
meant to cover was actually taken.

## 2. Root cause

`corporate_allowance_apply_delta` (the RPC backing every corporate
allowance/wallet mutation) mapped `allowance_grant` to `master -p_amount,
used -p_amount` — real money left the master wallet at grant time. Later,
when the member rode on that raised allowance room, `ride_debit` (fixed in
migration 248) correctly mapped to `master -p_amount, used +p_amount` —
debiting master again for the same ride. A member granted $50 who then
takes a $50 ride costs the company $100 for one $50 ride. This was a known,
explicitly deferred gap: migration 248's own "NOTE ON GRANTS" comment names
it directly and names the intended fix ("make grant a pure limit raise").

## 3. Fix / remediation

New migration `277_corporate_allowance_grant_no_master_debit.sql`
(`CREATE OR REPLACE` of the same function, following the exact precedent
migration 248 itself established for versioning this function) changes
`allowance_grant`'s master delta from `-p_amount` to `0`. A grant now raises
the member's spending room (`used -p_amount`, unchanged) without moving any
real money — money only leaves the master wallet once, at `ride_debit`,
when the member actually spends. This matches how a credit-limit increase
works: raising a limit isn't itself a cash outflow, only a purchase against
it is.

`allowance_rollback` (undo a prior grant: `master +p_amount, used +p_amount`)
was deliberately left unchanged — grepped for any live call site and found
none (not wired to any route today), so a mismatch between grant's new
zero-master-delta and rollback's non-zero master delta has no current
practical effect; noted as a named follow-up in the migration's own comment
rather than silently left unexplained.

## 4. Risk & impact on existing functionality

- **Blast radius: one SQL function, `corporate_allowance_apply_delta`,
  specifically its `allowance_grant` branch.** No Python code changes —
  `services/corporate_allowance_service.py::apply_grant` and its two
  callers (`routes/corporate_rider.py::submit_request`'s auto-approve path,
  `routes/corporate_company.py::decide_allowance_request`'s admin-approval
  path) are unchanged; they still call the RPC with `p_type="allowance_grant"`
  the same way. The delta semantics change is entirely inside the SQL
  function body.
- **`ride_debit`, `ride_debit_reversal`, `allowance_reset`, and
  `allowance_rollback` branches are byte-for-byte unchanged** from migration
  248 — only the `allowance_grant` branch's `v_master_delta` assignment
  differs. Confirmed via the existing `test_allowance_rpc_sign_contract.py`
  sign-contract test, updated to read the new migration file and assert the
  corrected delta.
- **`services/test_corporate_allowance_service.py`'s `apply_grant` tests**
  assert on the Python-level RPC call parameters (`p_type ==
  "allowance_grant"`), not on the SQL function's internal delta computation
  — unaffected by this change, confirmed by running them.
- No interaction with C1/C2/H1 beyond sharing the same underlying wallet
  transaction table — this is an independent fix to a different branch of
  the same RPC.

## 5. User-experience effect

**Corporate riders/admins using grant-funded top-ups**: no visible UI
change — a grant still appears to raise the member's available allowance by
the same amount as before. The only change is internal: the company's
master wallet balance no longer drops at the moment a grant is approved,
only when the member actually rides. A finance manager reviewing wallet
transaction history will see the `allowance_grant` line item's master-scope
amount is now `0` going forward (the member-scope line item, which tracks
their own usage counter, is unchanged) — this is the intended correction,
not a regression.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/277_corporate_allowance_grant_no_master_debit.sql` (new) | `CREATE OR REPLACE` of `corporate_allowance_apply_delta`; `allowance_grant`'s master delta changed from `-p_amount` to `0` | Stop double-charging the master wallet for grant-funded rides |
| `backend/tests/test_allowance_rpc_sign_contract.py` | Points at the new migration file instead of 248; updated ground-truth table; split the old combined assertion into a dedicated `test_grant_is_a_pure_limit_raise_no_master_debit` | Lock in the corrected delta the same way the file already locks in every other branch |

## 7. Before / after

```sql
-- Before (migration 248)
IF p_type = 'allowance_grant' THEN
    v_master_delta := -p_amount;
    v_used_delta   := -p_amount;
```

```sql
-- After (migration 277)
IF p_type = 'allowance_grant' THEN
    v_master_delta := 0;
    v_used_delta   := -p_amount;
```

## 8. Rollback plan

Documented directly in the migration's own top comment: re-apply migration
248's function body verbatim via `CREATE OR REPLACE`, which restores
`allowance_grant`'s master delta to `-p_amount`. No schema/DDL change is
made by either migration beyond the function body, so rollback is a pure
function replace with no data migration and no downtime. A `git revert` of
the migration file plus a manual re-run of 248's `CREATE OR REPLACE`
against the database achieves the same result without needing a new
migration file, since this function is explicitly versioned by
`CREATE OR REPLACE` rather than an `ALTER`/down-migration pair (same
established pattern migration 248 used).

Note per CLAUDE.md: since this changes a money-moving function, a rollback
here does not retroactively correct any grant-funded rides settled between
this migration's deploy and a hypothetical revert — same posture as
migration 248's own backfill section, which treats correcting already-moved
money as a separate finance-signed-off action, not something a code
rollback performs.

## 9. Verification performed

- [x] Automated tests: `test_allowance_rpc_sign_contract.py` (5 tests, 1
      updated + 1 new), `test_corporate_allowance_service.py` (10 tests,
      unaffected), `test_corporate_rider_routes.py` (23 tests, unaffected),
      `test_allowance_cap_fallback.py` (3 tests, unaffected) — 41 passed
      total, via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on the touched test file — clean.
- [x] Migration-numbering check: `277` does not collide with any existing
      prefix (checked against the full duplicate-prefix list already
      present in the repo's migration history).
- [x] Migration follows the established `CREATE OR REPLACE`-versioning
      convention this exact function already uses (migration 248's own
      "migration-override-ok" precedent), with the same rollback-plan,
      backfill-query, and "SAFE TO RE-RUN" comment structure.
- [ ] Manual repro in staging / actual RPC execution — not performed, no
      live Postgres instance available in this environment; the SQL was
      read and structurally verified (parses correctly as the same
      function shape, same branches, only one delta assignment changed) but
      not executed.
- [x] Blast-radius grep performed (see §4): every caller of `apply_grant`,
      every test referencing `allowance_grant`.
- [x] Dry-run scenario: a member's allowance has `amount=0, used=0`. An
      admin grants them $50 (master wallet balance: $500 → before this fix,
      $450; after this fix, still $500 — the grant only lowers `used` to
      -50, raising their effective room to $50). The member takes a $50
      ride. Before this fix: `ride_debit` debits master another $50 →
      master ends at $400 for one $50 ride actually taken. After this fix:
      `ride_debit` debits master the first and only time → master ends at
      $450, correctly charged once for the $50 ride.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — documented in the migration
      file itself, matching the established pattern for this function
- [x] Blast radius is stated, not assumed — one function, one branch, every
      Python-level caller and test confirmed unaffected
- [x] No silent behavior change to a working flow — the member-facing
      allowance-room math is identical; only the master-wallet-charging
      timing changes, which is the fix's entire purpose, and is explicitly
      the "deliberately not changed here" item migration 248 flagged as a
      known follow-up

## What was NOT verified

Not executed against a live/staging Postgres instance — the RPC body was
read and structurally verified against the established pattern, and the
Python-level sign-contract test parses and asserts on the SQL text directly
(the same technique already used for migration 248's fix), but the actual
`FOR UPDATE`/locking/floor-check runtime behavior of the modified function
was not exercised against a real database in this session. Did not
retroactively correct any already-double-debited historical
`corporate_wallet_transactions` rows — per the migration's own backfill
section, that is a finance-signed-off billing correction, out of scope for
this code fix. A reporting query to find historically-affected companies is
included in the migration's top comment for whoever picks that up.
