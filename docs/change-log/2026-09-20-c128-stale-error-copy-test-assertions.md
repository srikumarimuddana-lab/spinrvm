# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code |
| Surface(s) | backend (tests only — no application code changed) |
| Domain (Sentry tag) | payments, rides, auth, admin |
| PR / commit link | (see this branch's PR) |
| Related issue or gap ID | ACTION_ITEMS.md C128 |

## 1. Issue / gap identified

`backend-test` was red on `main`'s own tip: 19 tests failed with
`19 failed, 15058 passed, 6 skipped, 1 xfailed`. Found while babysitting an
unrelated CI/deploy-workflow PR (#5580) and confirmed identical on `main`'s
own tip (commit `abece4fd6`, run 35536280732) before touching anything —
this PR's diff never caused it.

## 2. Root cause

`docs/audit/2026-09-18-user-facing-message-audit.md` rewrote several
`HTTPException`/`SpinrException` `detail`/`message` strings from internal,
integrator-facing text into rider/admin-facing friendly copy — an
intentional, already-reviewed product decision. That audit's own doc
discloses "No tests were run" (PyPI/npm were blocked in that session's
sandbox), so the 19 tests asserting the old internal substrings (`tokenize`,
`object`, `discount_type`, `too_short`, `complexity`, `too_common`, raw
status values like `in_progress`) were never updated and started failing
the moment the new copy shipped. Nothing tracked this as a follow-up in
`ACTION_ITEMS.md`, so the gate sat red with no open item explaining why.

## 3. Fix / remediation

Updated each of the 19 assertions to match the current, already-shipped
copy — verifying the *specific* branch under test still fires, not just
"some 4xx/409 happened". No application code changed.

One improvement beyond a literal copy-match: `test_e2e_cancellation.py`'s
two `test_cancel_illegal_state_raises_409` cases now assert on the
structured `SpinrException.details["current_status"]` field (already
carried by that exception, just previously unused by the test) instead of
string-matching the friendly message — so a future copy change to that
message won't break this test again.

## 4. Risk & impact on existing functionality

- **Blast radius: test-file only, isolated.** No `backend/routes/`,
  `backend/utils/`, or migration file touched — only the five test files
  listed below. Grepped each touched assertion's source route
  (`backend/routes/payments.py`, `backend/routes/promotions.py`,
  `backend/routes/rides/_shared.py`, `backend/utils/password_policy.py`)
  to confirm the new assertions match the code that is actually live today
  (not a hoped-for future state).
- No table, background loop, or wallet/ride-state write is touched.
- Two pre-existing (not introduced by this fix) narrowing losses, both a
  property of the already-shipped copy change: `test_rejects_non_object_body`
  in `test_payments_pci_guard.py` can no longer distinguish "unparseable
  JSON" from "parsed but not an object" (both branches in `add_card` now
  share one message); the three `TestPasswordPolicy` complexity sub-tests
  (`missing_uppercase`/`missing_digit`/`missing_symbol`) already shared one
  generic message before this fix and still do after. Noted inline in each
  test so a future reader doesn't mistake it for new information.

## 5. User-experience effect

None. No application code changed — the messages users already see were
not altered by this fix; only the tests that verify them were brought back
in sync with copy that shipped 2026-09-18.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_e2e_cancellation.py` | 2 assertions switched from raw-status substring match to `SpinrException.details["current_status"]` | Old assertion checked for the raw status string (e.g. `"in_progress"`) inside the now-friendly 409 message, which no longer contains it |
| `backend/tests/test_payments_pci_guard.py` | 2 assertions updated to match current `add_card` copy | Old assertions checked for `"tokenize"`/`"object"`, both dropped by the 2026-09-18 copy rewrite |
| `backend/tests/test_promotions_coverage.py` | 1 assertion updated to match current `admin_create_promo_code` copy | Old assertion checked for the raw field name `"discount_type"`, dropped by the rewrite |
| `backend/tests/test_routes_promotions_coverage.py` | 1 assertion updated (same as above) | Same route, same rewritten copy, duplicate test coverage |
| `backend/tests/test_utils_extended.py` | 5 assertions (`TestPasswordPolicy`) updated to match current `validate_admin_password` copy | Old assertions checked for code prefixes `too_short`/`complexity`/`too_common`, all dropped by the rewrite |

## 7. Before / after

```python
# Before (backend/tests/test_e2e_cancellation.py)
assert exc_info.value.status_code == 409
text = getattr(exc_info.value, "detail", None) or getattr(exc_info.value, "message", "")
assert status in str(text)
```

```python
# After
assert exc_info.value.status_code == 409
assert isinstance(exc_info.value, SpinrException)
assert exc_info.value.details.get("current_status") == status
```

```python
# Before (backend/tests/test_payments_pci_guard.py)
assert "tokenize" in exc_info.value.detail.lower()
```

```python
# After
assert "secure card form" in exc_info.value.detail.lower()
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this is a test-only change
with no data, migration, or feature-flag surface. No live data was written
by this change; nothing to remediate beyond the code revert itself.

## 9. Verification performed

- [x] Automated tests run: the exact 19 previously-failing tests, directly
  by name — `23 passed` (19 fixed + 4 already-green siblings in the same
  classes).
- [x] Full mocked-DB suite (`pytest --ignore=tests/rls
  --ignore=tests/direct_pool --ignore=tests/test_schemathesis_fuzz.py`,
  matching `backend-test`'s own exclusions) run to confirm no other
  regression — see PR for the result captured after this doc was written.
- [x] Blast-radius grep performed: searched each touched route/util source
  file for the message text asserted on, confirming the new assertions
  match currently-live code, not a hoped-for future state.
- [x] Reviewed against CLAUDE.md's CI-red-handling convention (gate 2):
  confirmed the failure reproduces identically on `main`'s own tip before
  touching anything, so this is filed as its own change rather than folded
  into the unrelated PR where it was discovered.
- [ ] Manual repro steps followed in staging — N/A, test-only change.
- [ ] Feature-flagged — N/A, test-only change, nothing user-visible.

## 10. What was NOT verified

- Whether any non-test caller (e.g. a rider/driver-app screen
  string-matching a `detail`/`message` field client-side instead of the
  dedicated `message_key`) depends on any of the old substrings this audit
  already changed. Out of scope for a test-only fix; flagging for a future
  session auditing rider/driver-app error handling.
- Not run against the RLS or direct-pool test tiers — untouched by this
  diff (no migration or SQL-adjacent code changed).
- Not run in this PR's own CI at the time this doc was written (filed
  alongside the fix, before its `backend-test` run completes).
