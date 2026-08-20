# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (session), on behalf of @vikas |
| Surface(s) | backend (test-only — `routes/payments.py` reads, no writes) |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/spinr-app-all-surfaces-de596c`, commit adding `backend/tests/test_payments_coverage_gap_closure.py` |
| Related issue or gap ID | ranked blocker #30 escalation, `docs/audit/2026-08-19-decision-writeups.md` item #4; follow-up to `docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md` |

## 1. Issue / gap identified

`routes/payments.py` measured 86.13% test coverage against its CLAUDE.md-documented 90% target,
flagged as an explicit escalation (not silently patched) when the `money-path-coverage-floor-gate`
CI job was introduced earlier the same day.

## 2. Root cause

Several correctness/idempotency/ownership branches in `confirm_payment`, `create_payment_intent`,
`add_card`, `set_default_card`, and `delete_card` had never been exercised by a test — not because
they were untestable, but because no test happened to hit them (the module's existing test suite,
`test_coverage_payments.py`, focused on happy paths and the most common error branches). A dedicated
research pass (see the decision-writeups doc) confirmed zero uncovered lines touched money
arithmetic — the gap was entirely in idempotency guards, ownership checks, and Stripe error-handler
branch specificity (e.g. `StripeError` vs. the broader `Exception` catch-all below it).

## 3. Fix / remediation

Added 14 targeted unit tests in a new file, `backend/tests/test_payments_coverage_gap_closure.py`,
each documented with why the branch matters (see the file's own docstring and per-test docstrings).
No production code changed. Coverage measured before/after via `pytest --cov=routes.payments`:
86.13% → 90.16% (broad `-k payment/fare/crypto/otp` run) / 92% (narrower payments-only run),
crossing the 90% target on both.

Also updated `backend/scripts/check_money_path_coverage_floor.py`'s `FLOOR_MANIFEST` entry for
`routes/payments.py` from `(80.0, "measured 86.1%... BELOW 90% target")` to
`(85.0, "measured 90.16%... target 90% met")` — following the file's own documented convention
(floor = measured − 5, rounded down to the nearest 5) rather than leaving a stale floor that no
longer reflects reality once the gap closed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** This is a pure test addition plus a coverage-floor-manifest number
  update — no route handler, no Stripe call shape, no DB write path in `routes/payments.py` itself
  was touched.
- **Who else reads `check_money_path_coverage_floor.py`'s `FLOOR_MANIFEST`:** only
  `ci-guardrails.yml`'s `money-path-coverage-floor-gate` job (via `main_for` in
  `_coverage_floor_lib.py`) and this script's own unit tests
  (`backend/tests/test_coverage_floor_gates.py`) — grepped and confirmed those tests use synthetic
  manifests, not the real `FLOOR_MANIFEST` values, so raising the floor doesn't touch them.
- **Could this regress anything?** The floor going from 80%→85% means a future PR that drops
  `payments.py` coverage into the 80-85% band, which used to pass, will now fail CI. This is the
  gate working as intended (following measured reality upward, per the script's own "do not lower a
  floor to make a failing PR pass" rule, applied in the opposite, safe direction) — not a regression,
  but worth naming: any in-flight PR that happened to be relying on the old 80% floor with coverage
  in the 80-85% range would now need to either add tests or the floor change would need
  re-evaluation. No such PR is known to exist at this time.
- No interaction with background loops, the ride state machine, or wallet/allowance deltas — this
  is test scaffolding plus a CI-gate threshold number.

## 5. User-experience effect

None. Backend-only, test-only change. Not visible to riders, drivers, corporate admins, or internal
admins in any running system — it only changes what a future PR's CI run will accept.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_payments_coverage_gap_closure.py` | New file, 14 tests | Closes the genuine coverage gaps in `routes/payments.py` |
| `backend/scripts/check_money_path_coverage_floor.py` | `FLOOR_MANIFEST["routes/payments.py"]` floor 80.0→85.0, provenance string updated; module docstring updated to describe the closure | Keeps the gate's floor honest once coverage crossed the target, per the script's own convention |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | Ranked blocker #30 and the corresponding decision-log row marked resolved/closed | Session convention: mark compliance/audit items resolved when fixed |
| `ACTION_ITEMS.md` | A-series entry's escalation note updated to reflect closure | Same reason |
| `docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md` | Added a forward-pointer note (not a rewrite) to this doc | Preserves the original doc as an accurate historical record of the gate's introduction |

## 7. Before / after

```python
# Before — FLOOR_MANIFEST, backend/scripts/check_money_path_coverage_floor.py
"routes/payments.py": (
    80.0,
    "measured 86.1% (2026-08-19, -k payment/fare/crypto/otp run); "
    "BELOW 90% target -- see change-log follow-up, floor = measured - 5 rounded to nearest 5",
),
```

```python
# After
"routes/payments.py": (
    85.0,
    "measured 90.16% (2026-08-19, -k payment/fare/crypto/otp run, after "
    "test_payments_coverage_gap_closure.py closed the prior 86.1% gap); "
    "target 90% met",
),
```

## 8. Rollback plan

`git-revert-safe`. Pure test-file addition plus a floor-number/docstring update in a CI script — no
migration, no data mutation, no live-data anything to unwind. A `git revert` of the commit fully
restores the prior state (86.1% coverage remains true of the underlying code either way; only the
test count and the documented floor change).

## 9. Verification performed

- [x] Automated tests run — the 14 new tests: `pytest backend/tests/test_payments_coverage_gap_closure.py -v --no-cov` → 14 passed
- [x] Ran alongside the full existing payments test surface (`test_coverage_payments.py`,
      `test_payments_coverage_gap_closure.py`, `test_payments_stripe_error_specificity.py`,
      `test_payments_pci_guard.py`, `test_e4_d10_payment_3ds_quests.py`,
      `test_stripe_customer_email_mapping.py`) — 113 passed (before this fix's own file: 99, +14 new)
- [x] `ruff check` on both changed files — clean
- [x] Coverage re-measured via real `pytest --cov=routes.payments --cov-report=term-missing`, not
      estimated: 86.13% → 90.16% (broad `-k` run, matches the gate's own manifest measurement
      convention) / 92% (narrower payments-only run)
- [x] `check_money_path_coverage_floor.py`'s own unit tests (`test_coverage_floor_gates.py`, 20
      tests, synthetic manifests) re-run — unaffected, still pass
- [x] `docker`/live-Supabase not applicable — no DB schema or RLS surface touched
- [ ] Full backend suite (`pytest backend/tests -q --no-cov`) — run separately as part of this
      wave's integration; see the commit that references this doc for the result
- [ ] Real production build (`npm run build`) — not applicable, no frontend surface touched

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated — test file + one manifest number)
- [x] No silent behavior change to an already-shipped flow — this changes zero runtime behavior;
      the CI-gate floor number is the only "behavior" that moves, and it's stated explicitly above
