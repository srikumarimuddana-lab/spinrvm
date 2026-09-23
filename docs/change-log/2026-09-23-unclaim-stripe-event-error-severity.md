# Change Impact & Risk Log — `unclaim_stripe_event` silent-failure severity

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code session (proactive edge-case/error-handling sweep, on behalf of ittalenthire.ca@gmail.com) |
| Surface(s) | backend (payments) |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch, `fix/unclaim-stripe-event-error-severity`) |
| Related issue or gap ID | ACTION_ITEMS.md C135 (new) |

## 1. Issue / gap identified

`unclaim_stripe_event()` releases a Stripe-idempotency claim row so a webhook retry can
reprocess an event after a transient failure. If the underlying DB delete itself fails, the
function returns `False` — its own docstring says the caller must escalate, since the claim
row stays held and Stripe's retry will be deduped away, silently losing the event until a
manual admin replay. In practice, that failure was logged at `warning`, and 7 of 8 call sites
in `routes/webhooks.py` don't check the return value before raising the `HTTPException` that
tells Stripe to retry — so a real DB error here produced no signal beyond a log line nobody
was watching.

## 2. Root cause

Found via a proactive repo-wide sweep for the "silently swallow a DB/auth/payment error"
anti-pattern that CLAUDE.md documents as recurring (independently re-found in 10 separate
audits, 2026-07-24 to 2026-09-05). Not introduced by a recent change — pre-existing.

## 3. Fix / remediation

Changed `unclaim_stripe_event`'s exception handler from `logger.warning(...)` to
`logger.opt(exception=True).error(...)` (loguru's correct traceback-preserving call, per
CLAUDE.md's loguru conventions — not stdlib's `exc_info=`). Verified via `spinr-money-auditor`
review that this newly routes the failure into Sentry through the existing
`_loguru_sentry_sink` (registered at `level="ERROR"` in `utils/sentry_runtime.py`), which it
did not before.

**This is an interim mitigation, not the full fix** — see ACTION_ITEMS.md C135 for the
remaining work (the 7 unchecked call sites in `webhooks.py` should check the return value and
escalate explicitly with ride/event context, mirroring the one compliant call site at line 855).
Filed rather than silently expanded in scope, per CLAUDE.md's minimal-fix discipline.

## 4. Risk & impact on existing functionality

- **Blast radius: single function, log-severity only.** Grepped every call site of
  `unclaim_stripe_event` across the repo (10 files: `routes/webhooks.py`,
  `routes/admin/stripe_events.py`, `db_supabase.py` re-export, and 6 test files). Behavior
  (return value, control flow) is byte-for-byte unchanged — only the log level and the
  addition of exception-context capture changed.
- No other reader/writer of `stripe_events` or the claim/unclaim RPC path was touched.
- Because this only raises log severity on an already-existing failure path, it cannot
  introduce a new failure mode; worst case is more Sentry noise if this path fires often
  (it shouldn't — it only fires on a DB error during an already-rare unclaim).

## 5. User-experience effect

None visible to riders/drivers/admins. This is an internal observability fix only — it does
not change what happens to a Stripe event on failure (still returns `False`, still requires
manual replay), only whether that failure becomes visible via Sentry.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/wallet_repo.py` | `unclaim_stripe_event`'s exception handler: `logger.warning` → `logger.opt(exception=True).error`, plus a comment explaining why | Route a real Stripe-idempotency DB failure into Sentry instead of a log line nobody watches |
| `backend/tests/test_wallet_repo.py` | `test_unclaim_stripe_event_returns_false_on_db_error` updated to assert `logger.opt(exception=True)` + `.error(...)` instead of `logger.warning(...)`; docstring updated | Keep the test asserting the actual (now-fixed) behavior |
| `ACTION_ITEMS.md` | New C135 entry tracking the remaining real fix (7 unchecked call sites in `webhooks.py`) | Per `spinr-money-auditor`'s explicit instruction not to let this diff be mistaken for having closed the full gap |

## 7. Before / after

```python
# Before
try:
    await run_sync(_fn)
    return True
except Exception as e:  # noqa: BLE001
    logger.warning(f"Failed to unclaim stripe event {event_id}: {e}")
    return False

# After
try:
    await run_sync(_fn)
    return True
except Exception as e:  # noqa: BLE001
    # error, not warning: most callers (routes/webhooks.py) don't check this
    # return value before raising a 5xx that tells Stripe to retry — if this
    # delete itself failed, the claim row stays held and that retry will be
    # deduped away, silently losing the event until a manual admin replay.
    logger.opt(exception=True).error(f"Failed to unclaim stripe event {event_id}: {e}")
    return False
```

## 8. Rollback plan

`git revert` is a complete rollback — no data migration, no config/flag, no Stripe/wallet/ride-state
interaction. Reverting returns to the prior (already-broken, not newly-broken) `warning`-level logging.

## 9. Verification performed

- [x] Automated tests run — `pytest backend/tests/test_wallet_repo.py -k unclaim_stripe_event`
  (3 passed) and `pytest backend/tests/test_loguru_call_conventions.py` (8 passed, confirming
  the new `.opt(exception=True).error(...)` call satisfies this repo's static loguru-convention
  scanner)
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access
  in this environment
- [x] Blast-radius grep performed — every `unclaim_stripe_event` call site across 10 files
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — payments/Stripe idempotency surface;
  dispatched `spinr-money-auditor` before commit (verdict: SHIP WITH SMALL CHANGE — add the
  ACTION_ITEMS follow-up, done)
- [x] Feature-flagged if user-visible and non-trivial — not applicable; no user-visible change,
  pure observability fix on an internal failure path

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in —
  not applicable, no user-facing behavior changed

## What was NOT verified

- Not tested against a real Supabase/Stripe integration — verified via the existing mocked
  unit test only.
- Whether this Sentry event actually pages anyone (vs. just appearing in the Sentry issue
  stream) depends on alert-rule/on-call configuration outside this diff's scope — not verified,
  flagged by `spinr-money-auditor` as a separate question.
- The 7 unchecked call sites in `routes/webhooks.py` are explicitly NOT fixed by this change —
  tracked as ACTION_ITEMS.md C135, not silently left unaddressed.
