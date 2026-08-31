# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (local commit, not yet pushed) |
| Related issue or gap ID | ACTION_ITEMS.md A34 — "rider-referral velocity/identity-cross-check gap" |

## 1. Issue / gap identified

`utils/referral_payout.py` had zero awareness of whether a referral's
REFEREE was a legacy-imported account (from the pre-Spinr old-app
migration) rather than a genuine new signup. A legacy-imported rider or
driver could apply a referral code post-import and be paid the "new user"
referral bonus (rider: $5 referrer + $5 referee on one ride; driver: $10
referrer once the referee reaches `REFERRAL_RIDES_REQUIRED`) it was never
eligible for.

## 2. Root cause

`referral_payout.py`'s qualification check (`_process_one`) only looks at
`referral_applied_at` (window/deadline) and completed-ride counts
(`grand_total > 0` for riders, per the already-shipped N2 fix). It never
checked provenance. Both apply-referral-code endpoints
(`routes/users.py::apply_rider_referral`, `routes/drivers/referrals.py
::apply_referral_code`) also let a legacy-imported account set
`referral_code_used` with no such check — "Apply a referral code (at
signup or later)" is explicitly allowed at any time. This is the same
shape as the already-fixed `rider_import_service.py` `created_at=now()`
bug (PR #4132, `routes/promotions.py::_is_legacy_imported_rider`) that let
old-app customers pass as new signups for `new_user_days` promos — that
fix was never mirrored onto the referral-payout path.

## 3. Fix / remediation

Added a legacy-import guard directly in the payout loop
(`utils/referral_payout.py`), the single choke point money actually moves
through, rather than only gating the two apply-endpoints (defense in
depth — it also closes the door on any `referral_code_used` rows a
legacy account may already have set before this fix, e.g. via an admin
action or existing test/staging data):

- New `_is_legacy_referral_referee(kind, referee, driver_row=None)`:
  rider referees are blocked when `users.legacy_import_metadata
  .rider_csv_import` is set (same marker `promotions.py` checks); driver
  referees are blocked when the referee's OWN `drivers.legacy_import_metadata
  .source` is `legacy_saskatoon_driver_import` or `legacy_mongo_driver_import`
  (driver provenance lives on the `drivers` row, not `users`).
- `_process_one` calls this guard and returns early (no
  `referral_payouts` row opened, no credit) for a legacy-imported referee
  — the exact same "return, no claim" shape already used for an
  unresolved/self-referral (`if not referrer_user_id or referrer_user_id
  == referee_id: return`), so no schema change was needed and the
  UNIQUE(referee_user_id) claim semantics are untouched.
- `_tick`'s users query and `_prefetch_chunk`'s drivers query now also
  select `legacy_import_metadata` so the guard has the data it needs on
  both the batched and per-referee (`ctx=None`) code paths.
- The referrer's OWN legacy status is deliberately NOT checked — a
  legacy-imported account referring someone else is fine; only the
  referee side ("is this genuinely a new user?") is the fraud surface,
  matching `promotions.py`'s `new_user_days` precedent.

## 4. Risk & impact on existing functionality

- **Blast radius grep performed**: `grep -rln "referral_payouts" --include=*.py backend | grep -v tests/` →
  `scripts/_requeue_failed_referrals.py`, `core/lifespan.py` (starts the
  loop), `utils/referral_terms.py`, `utils/referral_payout.py` (this
  file), `routes/admin/drivers.py` (admin re-credit via
  `recredit_failed_claim`). None of these read/write anything this diff
  changes: no column was added, no status value was added, no change to
  `recredit_failed_claim`, the velocity cap, the stale-claim sweep, or the
  claim/paid/failed/expired state machine.
- Isolated to `utils/referral_payout.py` (+ two new query columns) and one
  new test file — single-surface, backend-only.
- Could regress: a legitimate rider/driver referral where the referee's
  `legacy_import_metadata` is set for an unrelated reason (e.g.
  `stripe_mapping_import_service` also writes into the same
  `users.legacy_import_metadata` JSON column). Guarded against by keying
  specifically on the `rider_csv_import` sub-key (not "is the column
  non-null"), mirroring `promotions.py`'s own precedent and covered by
  `test_rider_metadata_present_but_no_rider_csv_import_key_is_not_blocked`.
- No interaction with the ride state machine or any Stripe flow. Money
  path affected: wallet credit (rider) / `driver_bonuses` insert (driver)
  — both are now correctly *not* triggered for a legacy referee, which is
  the intended behavior change, not a regression.
- Existing organic (non-legacy) referrals are unaffected — pinned by
  `test_organic_rider_referee_still_qualifies_normally` /
  `test_organic_driver_referee_still_qualifies_normally`, and the
  pre-existing `test_referral_payout_*` suites (47 tests total across the
  module) all still pass unmodified.

## 5. User-experience effect

- Rider/driver-facing: a legacy-imported account that applies a referral
  code no longer receives the referee's first-ride bonus, and their
  referrer no longer receives the referrer bonus for that account. No UI
  copy changes — the apply-code screens still say "success" (the code IS
  applied, `referral_code_used`/`referred_by` are still set as before);
  only the *payout* silently never fires for that referee, the same way a
  $0-reward area or an expired-window referral already silently never
  pays today. No new user-facing error message was added; this was a
  deliberate minimal-diff choice (see "What was NOT verified" below) —
  flagged for product/support awareness rather than adding new copy on my
  own judgment.
- Not visible mid-session to anyone already using the app — this only
  affects a background loop's future payout decisions, not any live ride
  or wallet balance a user is currently watching.
- No admin-dashboard change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/referral_payout.py` | Added `_is_legacy_referral_referee` + `_DRIVER_LEGACY_SOURCE(S)` constants (imported from `services/driver_import_service`); called from `_process_one` for both rider and driver kinds; added `legacy_import_metadata` to the `_tick` users query and the `_prefetch_chunk` drivers query | Close the A34 legacy-import referee gap at the payout choke point |
| `backend/tests/test_referral_payout_legacy_guard.py` | New test file: pure-function coverage of `_is_legacy_referral_referee` (8 cases) + `_process_one` end-to-end for legacy vs. organic rider/driver referees (4 cases) | Regression coverage for the fix, accept + reject cases per task |

## 7. Before / after

```python
# Before (utils/referral_payout.py::_process_one, rider branch)
    if not referrer_user_id or referrer_user_id == referee_id:
        return  # unresolved or self — nothing to pay

    # Never pay retroactively for rides that predate the referral. ...
    applied_at = referee.get("referral_applied_at")
```

```python
# After
    if not referrer_user_id or referrer_user_id == referee_id:
        return  # unresolved or self — nothing to pay

    # A34: a legacy-imported REFEREE never qualifies for a referral bonus, no
    # matter how recent referral_applied_at is or how many rides they take.
    if is_rider and _is_legacy_referral_referee("rider", referee):
        return

    applied_at = referee.get("referral_applied_at")
```

(Driver branch gets the equivalent check once `ref_as_driver` is resolved,
since driver provenance lives on the `drivers` row.)

## 8. Rollback plan

No feature flag was added — the fix is a pure early-return with no schema
change, no new `app_settings` key, and no destructive write. Rollback is a
plain `git revert` of the two changed files (`referral_payout.py` +
its test file); no live-data remediation is needed because this change
only *prevents* future credits from a narrow, newly-recognized-ineligible
population — it never mutated any existing `referral_payouts`,
`wallets`, `wallet_transactions`, or `driver_bonuses` row. (No prior
payouts to a legacy referee were found or touched — see "What was NOT
verified.")

## 9. Verification performed

- [x] Automated tests run (unit, mocked `db_supabase` via the existing
  `mock_supabase_client`-style module-level `db_supabase` patch pattern
  this file's other test suites already use — no real DB): `python -m
  pytest tests/test_referral_payout_legacy_guard.py
  tests/test_referral_payout_batching.py
  tests/test_referral_payout_deadline.py
  tests/test_referral_payout_fraud_guards.py
  tests/test_referral_payout_scan_filters.py
  tests/test_referral_payout_zero_reward.py -q --no-cov` → **47 passed**,
  0 failed.
- [ ] Manual repro steps followed in staging — not done (no live/staging
  Supabase access in this session; see below).
- [x] Blast-radius grep performed — see §4.
- [x] Reviewed against relevant `CLAUDE.md` conventions: Decimal-only
  money (unchanged — no money-math touched, only a qualification gate),
  dual-import pattern (new `services.driver_import_service` import
  follows the `try: from ..x import ... / except ImportError: from x
  import ...` convention used everywhere else in this file), "do not
  silently swallow errors" (N/A — no error path added; the guard is a
  business-rule check, not error handling), background-loop replay
  safety (unaffected — no change to the leader lock or the claim
  mechanism).
- [ ] Feature-flagged — not flagged. Justification: this narrows
  eligibility for money that was never correctly owed in the first place
  (closing a gap, not changing a working flow's behavior for anyone it
  was ever *supposed* to pay), the affected population (legacy-imported
  accounts applying a referral code) is a narrow edge case, not the
  common path, and per CLAUDE.md gate 3 a flag is expected for
  "user-visible and non-trivial" changes — this one is not user-visible
  (no new copy, no new error) and is a straightforward eligibility-rule
  fix, closer in shape to the already-unflagged N2 zero-fare-ride /
  velocity-cap fraud guards in this same file than to a UX change.
- [x] `ruff check` / `ruff format --check` on the touched files: clean.

## What was NOT verified

- **No live/staging Supabase access this session** — could not query
  production `referral_payouts` joined against `users.legacy_import_metadata`
  /`drivers.legacy_import_metadata` to confirm whether this gap has
  actually been exploited (any already-paid rows where the referee is
  legacy-imported). That live-data check is exactly what the original A34
  note said this session couldn't do, and remains true after this fix —
  **the fix prevents future exploitation; it does not retroactively
  detect or reclaim any past one.** Recommended follow-up (not done
  here): a read-only query joining `referral_payouts` (status='paid') →
  `users.legacy_import_metadata->'rider_csv_import'` /
  `drivers.legacy_import_metadata->>'source'` to quantify historical
  exposure, if any.
- Did not add a user-facing message explaining *why* a legacy account's
  referral silently never pays (currently indistinguishable from a $0-area
  or an expired-window referral from the UI's perspective) — a product/UX
  decision, not made unilaterally here.
- Did not gate the two apply-referral-code endpoints themselves
  (`routes/users.py`, `routes/drivers/referrals.py`) — left `referral_code_used`
  settable by a legacy account (consistent with how `promotions.py`'s
  parallel fix also only gates at redemption/qualification time, not at
  a separate "can this account even see the field" layer). If product
  wants the apply endpoint itself to reject legacy accounts outright
  (a different, stricter UX), that's a follow-up decision, not folded in
  here to keep the diff minimal per CLAUDE.md's "surgical changes" rule.
- No `npm run build` / frontend build applies — this is a backend-only
  Python change with no rider-app/driver-app/admin-dashboard touch.
