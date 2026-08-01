# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (branch: `claude/fix-mark-stripe-event-processed`) |
| Related issue or gap ID | Bug flagged (not fixed) by PR #3098 (`repositories/wallet_repo.py` coverage pass); `ACTION_ITEMS.md` C10 (new, filed alongside this fix) |

## 1. Issue / gap identified

`repositories/wallet_repo.py::mark_stripe_event_processed` caught any DB-write exception and logged it at `logger.warning(...)`, then unconditionally returned `None` — the caller (the Stripe webhook handler) has no way to know the "processed_at" stamp failed to write, and the failure never rose above a log line nobody was watching.

## 2. Root cause

The function's own docstring argued this was an acceptable trade-off: "the reconciliation job can still distinguish processed vs. stuck events... Stripe will not retry since we returned 2xx." That claim does not hold up — verified via `grep -rn "processed_at" backend/ --include="*.py"` and reading `backend/utils/stripe_reconcile.py` in full: **no reconciliation job in this codebase queries `stripe_events` for `processed_at IS NULL` rows.** The only code that ever reads `processed_at` back is a reactive check inside `claim_stripe_event` that logs `logger.critical(...STUCK...)` — but it only fires if Stripe retries the *same* `event_id`, which won't happen for an event that already received a 2xx response. So a DB-write failure here was not just under-logged, it was **structurally unrecoverable and silent** — the exact scenario `CLAUDE.md`'s "Do not silently swallow errors" section calls out for payment errors ("Never `logger.warning(...)` and continue on a DB/auth/payment error").

## 3. Fix / remediation

- `logger.warning(...)` → `logger.error(...)`, with `extra={"domain": "payments", "event_id": event_id}` and the full exception repr in the message. `backend/server.py` bridges every `logger.error` call to Sentry (`_loguru_sentry_sink`, `level="ERROR"`), promoting the `extra={}` context into Sentry tags — so this failure now shows up as a tagged, alertable Sentry event instead of a log line.
- Docstring rewritten to drop the disproven "reconciliation job" claim and instead state plainly that no such job exists yet, pointing at the new `ACTION_ITEMS.md` C10 entry.
- Return type intentionally **not** changed (still `-> None`). There is no retry/replay lever a caller could pull today — Stripe already got its 2xx, and building an actual replay mechanism is a separate, larger piece of work (the missing reconciliation sweep, tracked as C10), not something to bundle into a logging fix.
- Filed `ACTION_ITEMS.md` C10 to track the actual missing-reconciliation-job gap this surfaced (it also affects a second, unrelated code path — unhandled Stripe event types in `routes/webhooks.py` — that makes the same false claim about a "nightly reconciliation job").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every caller of `mark_stripe_event_processed` (`backend/routes/webhooks.py`, 4 call sites, none check the return value) and every test that patches it (`test_webhooks_main.py`, `test_corporate_webhook.py`, `test_corporate_e2e_wallet.py`, `test_webhook_stripe_v15.py`, `test_spinr_pass_subscription.py` — all patch it as `AsyncMock()` with no assertion on its return value or on `logger` calls). None of these are affected by a log-level/message change; the function's control flow and return value are unchanged.
- No interaction with the ride state machine. No new DB write, no schema change, no new background loop.
- Does not touch `unclaim_stripe_event` (a similar but not identical sibling — it already returns `bool` so its caller *can* detect failure, making its `logger.warning` a lesser instance of the same pattern). Left alone, out of scope for this fix, not re-flagged since PR #3098 already only flagged `mark_stripe_event_processed` specifically.
- Sentry volume: this only fires on a genuine DB-write failure on an already-claimed Stripe event, which should be rare. Not expected to add noise.

## 5. User-experience effect

`none` — backend-only, no rider/driver/corporate-admin-facing change. Internal-admin-facing indirectly (an on-call engineer would now see a Sentry alert they previously would not have), which is the entire point of the fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/wallet_repo.py` | `mark_stripe_event_processed`: `logger.warning` → `logger.error` with `extra={}` domain tag; docstring corrected | Stop silently swallowing a payment-adjacent DB error; make it Sentry-visible |
| `backend/tests/test_wallet_repo.py` | Updated the test pinning this behavior (`mock_logger.warning` → `mock_logger.error`, plus asserts on the `extra` tags); updated the section-header comment that documented the old bug as intentionally-unfixed | Keep the regression test honest about the new, fixed behavior |
| `ACTION_ITEMS.md` | New `C10` entry | Track the real gap this surfaced: no reconciliation job exists for stuck `stripe_events` rows, despite two separate code comments claiming one does |
| `docs/change-log/2026-08-01-fix-mark-stripe-event-processed-swallow.md` | New file | This log |

## 7. Before / after

```python
# Before
    try:
        await run_sync(_fn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to stamp processed_at on stripe event {event_id}: {e}")
```

```python
# After
    try:
        await run_sync(_fn)
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Failed to stamp processed_at on stripe event {event_id}: {e!r}. "
            "This event will remain stuck at processed_at=NULL indefinitely -- "
            "Stripe already got a 2xx and will not retry, and no automated "
            "reconciliation currently scans for this state (ACTION_ITEMS.md C10). "
            "Manual DB check required if this fires.",
            extra={"domain": "payments", "event_id": event_id},
        )
```

## 8. Rollback plan

`git-revert-safe` — a plain `git revert` restores the old logging behavior exactly. No data, migration, or Stripe-side state is touched by this change; it only affects what happens in a log/Sentry event *after* a DB write has already failed.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_wallet_repo.py -q` → 67 passed (real venv: `/tmp/spinr-venv`, Python 3.11.15).
- [x] Caller/regression tests — `pytest tests/test_webhooks_main.py tests/test_corporate_webhook.py tests/test_corporate_e2e_wallet.py tests/test_webhook_stripe_v15.py tests/test_orphan_refund.py tests/test_spinr_pass_subscription.py -q` → 116 passed.
- [x] Full backend suite re-run: `pytest tests/ -q` → **6782 passed, 8 skipped, 1 xfailed, 0 failed** in 290.11s.
- [ ] Manual repro against real Supabase/Stripe — not applicable, this only changes log-level/message on an already-mocked-in-tests failure path.
- [x] Blast-radius grep performed — see §4.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — directly implements the "Do not silently swallow errors" section's payment-error rule; matches the existing `logger.error` + best-effort `sentry_sdk` pattern established in `backend/utils/refresh_tokens.py` for the loguru→Sentry bridge (used the simpler `extra={}`-only path since this doesn't need the extra structured-tags-for-PagerDuty treatment that refresh-token-reuse detection does).
- [ ] Feature-flagged — not applicable; this is a pure observability fix, nothing to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — isolated, every caller/test-patcher enumerated in §4
- [x] No silent behavior change to an already-shipped flow — the *point* of this change is to stop a silent failure from being silent; the externally-observable behavior (webhook still returns 2xx, no retry, function still returns `None`) is unchanged

## What was NOT verified

- Not tested against a real Sentry DSN — the loguru→Sentry bridge itself (`backend/server.py`'s `_loguru_sentry_sink`) is pre-existing, unmodified code; this change only relies on it firing for `level="ERROR"`, which is a one-line, already-covered assumption, not independently re-verified end-to-end against a live Sentry project in this pass.
- The underlying reconciliation gap (C10) is **not** fixed by this PR — only the visibility of one of its two symptom paths is. The unhandled-Stripe-event-type path (`routes/webhooks.py` ~line 1578) still leaves rows silently unreconciled; not touched here, tracked separately.
