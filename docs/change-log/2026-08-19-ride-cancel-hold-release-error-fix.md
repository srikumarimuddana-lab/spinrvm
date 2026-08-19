# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | local worktree commit (not pushed — see commit SHAs in task report) |
| Related issue or gap ID | Audit finding N13, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` (same-pattern instance of baseline ranked blocker #13) |

## 1. Issue / gap identified

In `backend/routes/drivers/ride_cancel.py` (driver-initiated ride cancel), after Stripe successfully releases the rider's booking-time pre-auth hold, the follow-up DB write that records `auth_status = "released"` on the `rides` row was wrapped in a bare `except Exception: logger.warning(...)` that swallowed the failure and continued — no exception detail, no error-level signal, nothing a human or alert would ever see.

## 2. Root cause

The write-failure path was written defensively ("don't let a bookkeeping write break the cancel") but went further than intended: it downgraded to `warning` and dropped the exception object entirely, instead of following CLAUDE.md's "Do not silently swallow errors" convention (`logger.error(...)` with the full underlying exception, including `e.details["original"]` for a `DatabaseError`, since `str(e)` alone collapses to `"Database operation failed"`). This is a copy/adjacent-pattern instance of the same class of finding already flagged elsewhere in the fleet audit (baseline blocker #13) — a payment-adjacent DB write failure logged too quietly to be actionable.

## 3. Fix / remediation

Changed the `except Exception: logger.warning(...)` block to `except Exception as _mark_exc: logger.error(...)`, with:
- the full exception object interpolated into the message,
- `exc_info=True` so the traceback is captured,
- `_mark_exc.details.get("original")` appended when the exception carries a `details` dict (the `DatabaseError` shape from `backend/utils/error_handling.py`, matching the existing convention already used in `backend/routes/drivers/tax_exports.py`'s DSAR-export handler and `backend/routes/webhooks.py`'s SES-suppression handler),
- the `payment_intent_id` included in the log line (id only, no PII, per the PIPEDA logging rules).

No behavior change to control flow: the ride cancellation itself still returns `{"success": True}` on this path — the Stripe-side release already succeeded (logged at `info` a few lines above); only the *bookkeeping write* about that release failed, and that write failing does not undo the money-side effect. Blocking or retrying the ride cancel on this specific write failing would be wrong: the rider's card is already unblocked in Stripe by this point, and failing the cancel response back to the driver would make it look like their cancel didn't go through when it did.

## 4. Risk & impact on existing functionality

- **`orphaned_hold_reconciler`** (`backend/utils/orphaned_hold_reconciler.py`) is the direct downstream consumer of this signal's *absence of corruption*, not of the log line itself. It scans `rides` where `status` is terminal (`cancelled`) and `auth_status` is still in `OPEN_AUTH_STATES = ("authorized", "fare_only")`. Because the failing write in `ride_cancel.py` is caught *before* `auth_status` is mutated, a failed write leaves `auth_status` at its prior open value rather than corrupting it to a false `"released"` — so the reconciler's next 15-minute tick still finds the ride and retries `release_open_hold` (safe: the Stripe cancel call carries an idempotency key `ride-cancelauth-{ride}-{pi}`, so a second cancel against an already-canceled PaymentIntent is a no-op). This fix does not change that self-heal mechanism at all — it only makes the failure loud enough that a human/alert can notice a *pattern* of these writes failing (e.g. a degraded DB) instead of relying purely on the 15-minute reconciler as the only signal. No change to the reconciler's code, query, or claim logic was needed or made.
- **`db_supabase.update_ride`** (the function whose exception is now caught differently) is a generic, widely-shared helper — 51 non-test call sites across the backend (rides, dispatch, corporate, safety, admin, etc.). This fix does **not** touch `update_ride` itself, its signature, or its exception type; it only changes what one specific caller (`ride_cancel.py`'s driver-cancel hold-release path) does with an exception it already caught. Blast radius of the actual code change: isolated to this one `try/except` block in `backend/routes/drivers/ride_cancel.py`.
- The sibling rider-cancel path (`backend/routes/rides/cancellation.py`) has a structurally different pattern — it folds `auth_status` into the main attribution `update_ride` call rather than a separate follow-up write — and was not touched by this fix (out of scope per the task; noted for a possible future N13-pattern sweep).
- The unrelated `_deps._metric_inc("spinr_rides_state_transition_total", {"to_status": "cancelled"})` call earlier in the same function (added in PR #4243) was left untouched, as instructed.

## 5. User-experience effect

None. The driver still receives `{"success": True}` and the rider still sees the ride cancelled exactly as before — this is a pure observability change (log level + detail) with no change to the HTTP response, WebSocket events, or ride state machine.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_cancel.py` | `auth_status="released"` write-failure handler changed from `except Exception: logger.warning(...)` (no exception detail) to `except Exception as _mark_exc: logger.error(...)` with the exception, `DatabaseError.details["original"]` when present, `exc_info=True`, and the `payment_intent_id` | Surface a payment-adjacent DB write failure loudly per CLAUDE.md, instead of silently swallowing it at warning level |
| `backend/tests/test_c2_driver_cancel_atomic.py` | Added `test_driver_cancel_logs_hold_release_db_error_loudly` | Regression test asserting the failure is now logged at ERROR with the underlying detail, and that the ride cancel still succeeds |

## 7. Before / after

```python
# Before
                try:
                    await db_supabase.update_ride(ride_id, {"auth_status": "released"})
                except Exception:
                    logger.warning("[CANCEL] auth_status=released write failed ride_id=%s", ride_id)
```

```python
# After
                try:
                    await db_supabase.update_ride(ride_id, {"auth_status": "released"})
                except Exception as _mark_exc:
                    _mark_original = (
                        _mark_exc.details.get("original")
                        if hasattr(_mark_exc, "details") and isinstance(_mark_exc.details, dict)
                        else None
                    )
                    logger.error(
                        "[CANCEL] auth_status=released write failed ride_id=%s pi=%s: %s%s",
                        ride_id,
                        _booking_pi,
                        _mark_exc,
                        f" — {_mark_original}" if _mark_original else "",
                        exc_info=True,
                    )
```

**Concrete scenario**: Stripe's `PaymentIntent.cancel` call in `_deps.cancel_authorization` succeeds — the rider's ~7-day hold is actually freed on the card issuer's side — but the follow-up `db_supabase.update_ride(ride_id, {"auth_status": "released"})` fails (e.g. Supabase connection reset, `DatabaseError(details={"original": "connection reset by peer"})`).
- **Before**: `logger.warning("[CANCEL] auth_status=released write failed ride_id=ride-1")` — no exception object, no `DatabaseError.details["original"]`, no traceback. The ride cancel returns success, the hold is genuinely released, but there is no trace in the logs of *why* the bookkeeping write failed, and a `warning` is easy to miss/filter out of alerting.
- **After**: `logger.error(...)` with the exception, `"connection reset by peer"` detail, and full traceback (`exc_info=True`) — actionable and alertable, while the ride cancel still returns success and `orphaned_hold_reconciler` still self-heals `auth_status` within 15 minutes as before.

## 8. Rollback plan

Single-line, additive-only change (log level + message content, no schema, no control-flow, no new dependency). Revert via `git revert <commit-sha>` — this is safe here (unlike a data-mutating change) because nothing was applied to live data; the change only affects what gets written to application logs. No feature flag or migration is needed for a logging-only change.

## 9. Verification performed

- [x] Automated tests run: `/tmp/spinr-venv/bin/pytest backend/tests/test_c2_driver_cancel_atomic.py backend/tests/test_preauth_release_on_cancel.py backend/tests/test_ride_cancellation_branches.py -q` — **40 passed** (0 failed), including the new `test_driver_cancel_logs_hold_release_db_error_loudly`. (The suite's global `--cov-fail-under=60` gate reports "fail" against this 3-file subset because it measures coverage of the *entire* backend, not these files — that is expected and unrelated to this change; all individual tests passed.)
- [x] `ruff check` run on both modified files (`backend/routes/drivers/ride_cancel.py`, `backend/tests/test_c2_driver_cancel_atomic.py`) — all checks passed.
- [x] Blast-radius grep performed: `grep -rn "db_supabase.update_ride(" backend` (51 non-test call sites, all elsewhere, none touched by this change) and `grep -rln orphaned_hold_reconciler backend` to find and read the reconciler's actual trust assumption (documented in section 4).
- [x] Reviewed against CLAUDE.md conventions: "Do not silently swallow errors" (Critical Conventions), Observability Conventions log-level table, and the existing `DatabaseError.details["original"]` precedent in `routes/drivers/tax_exports.py` / `routes/webhooks.py`.
- [ ] Manual repro steps followed in staging — **not performed** (no staging Supabase/Stripe access from this session; verified via mocked `DatabaseError` in the pytest suite instead).
- N/A Feature-flagged — not user-visible, logging-only change; a flag would add complexity with no benefit.

**This is a Python/`backend` change only** — no `admin-dashboard`/`rider-app`/`driver-app` build was run, none was needed (no frontend files touched).

## 10. What was NOT verified

- Not tested against a live/staging Supabase instance — only against the `mock_supabase_client`/patched-`AsyncMock` unit-test harness (`DatabaseError` raised synthetically with a scripted `details["original"]`, not a real Supabase connection failure).
- Not verified end-to-end against a real Stripe test-mode `PaymentIntent` — `cancel_authorization` is mocked to return `True` in the new test; the real Stripe cancel path is exercised by the pre-existing `test_preauth_release_on_cancel.py` suite, not by this change.
- No visual regression tooling exists in this repo for backend log output; the log-format change (level, added detail) was verified by asserting on `caplog` records in the new test, not by inspecting a running log aggregator (e.g. Sentry/Datadog) — there is no Sentry wiring in `ride_cancel.py` or its siblings to add a capture to, and none was added, since the reconciler's 15-minute self-heal means this is a "degraded-but-recovered" signal (warning-tier per the Observability Conventions table) rather than a user-visible payment failure requiring Sentry; `logger.error` was still used per the more specific, explicit "Do not silently swallow errors" rule for DB/payment writes, which overrides the general degraded-but-recovered guidance for this class of write.
- Did not sweep the sibling rider-cancel path (`routes/rides/cancellation.py`) or the search-timeout auto-cancel path (`routes/rides/matching.py`) for the same N13 pattern — out of scope per the task's explicit scoping to `ride_cancel.py`'s driver-cancel hold-release write; flagging here in case a follow-up audit sweep is warranted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, logging-only change)
- [x] Blast radius is stated, not assumed (isolated to one `try/except` block; `update_ride` itself and its 51 other call sites untouched; `orphaned_hold_reconciler`'s self-heal mechanism explicitly named and confirmed unaffected)
- [x] No silent behavior change to an already-shipped flow — HTTP response, ride state machine, and WebSocket events are unchanged; only the log level/content of a caught internal exception changed
