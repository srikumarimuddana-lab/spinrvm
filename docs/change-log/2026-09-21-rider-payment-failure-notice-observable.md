# Change Impact & Risk Log — a lost "your payment failed" push is no longer silent

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's request |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review finding **E2** (payment-failure push swallowed at `debug`) |

## 1. Issue / gap identified

Three places push the rider a "Payment failed" notification after marking a ride
`payment_status='failed'`. All three swallowed a raised exception at `logger.debug`:

```python
except Exception as _push_err:
    logger.debug(f"Payment failure push to rider failed: {_push_err}")
```

`debug` is gated off in production, so a rider who was **never told** their card failed was
indistinguishable from one who ignored the notice. There was no log, no metric, and no way to
answer "how often does this happen?"

The review named two sites (`settle_card`'s declined and generic-failure branches). The third
— `utils/payment_retry.py`, the push on the final exhausted retry — was found while verifying
this fix's own justification, and is the **most** consequential: retries are finished, so nothing
follows it.

## 2. Root cause

Two mistakes, one in the original code and one in my first draft of the fix.

The original: push delivery was treated as pure best-effort infrastructure, so the handler was
written to be maximally quiet. That is right about the *mechanism* and wrong about the *message* —
this is the one notification that tells a rider their card failed and their ride is unpaid.

Mine: the first draft logged at `warning`, justified in a comment with "payment_retry re-tries and
re-notifies." **That is false.** `payment_retry.py` pushes the rider only when
`new_count >= MAX_RETRIES`, and only from its `except` branch; its normal decline path calls
`_alert_admins_payment_exhausted` — which alerts *admins*, not the rider. There is no prompt
compensating notice. `spinr-money-auditor` caught the overclaim; the level is now ERROR.

## 3. Fix / remediation

| File | Site | Before | After |
|---|---|---|---|
| `services/payment_service.py` | `settle_card`, declined | `logger.debug` | `logger.opt(exception=True).error` + metric `{reason: card_declined}` |
| `services/payment_service.py` | `settle_card`, generic failure | `logger.debug` | same, `{reason: payment_error}` |
| `utils/payment_retry.py` | final exhausted retry | `logger.debug` | `logger.error(..., exc_info=True)` + metric `{reason: retry_exhausted}` |

Counter: `spinr_payment_rider_notice_failed_total{reason}`. Singular `payment_` matches this
module's own convention (`spinr_payment_settlement_total`, `spinr_payment_settings_read_failed_total`);
the plural `spinr_payments_*` spelling belongs to `routes/payments.py`.

**ERROR, not WARNING**, for two reasons. CLAUDE.md's `warning + metric` row is for
*degraded-but-recovered*, and as established in §2 nothing recovers this — the rider's only
remaining signal is being blocked at their next booking attempt. And ERROR is the established
precedent for this exact notification: `routes/webhooks.py:1151` already logs the same lost
"Payment Failed" push at ERROR. Leaving mine at WARNING would have made two paths with identical
user-visible content disagree on severity.

**Logger spelling differs by module, deliberately.** `payment_service.py` is loguru, so it needs
`.opt(exception=True)` and `{}` placeholders. `payment_retry.py` is stdlib
(`logger = logging.getLogger(__name__)`), so `exc_info=True` and `%s` are correct there and
`.opt()` would not exist. Both are what `tests/test_loguru_call_conventions.py` requires.

## 4. Risk & impact on existing functionality

**Settlement behaviour is unchanged.** In all three sites the `payment_status='failed'` DB write
happens *before* the push and is unconditional; every edit is inside a pre-existing
`except Exception` block, and the `PaymentResult(...)` returned below is byte-identical. No Stripe
call, no ledger write, no `PaymentResult` field, no retry-count arithmetic is touched.

**The one way this could have broken settlement — and how it was caught.** `payment_service.py`
imports metrics *per function*, not at module scope. My first draft called `_metric_inc` without a
local import, which would have raised `NameError` **inside an exception handler** — masking the
original push error and changing what `settle_card` returns. `ruff` caught it as F821 before commit;
both sites now carry their own dual-import, matching `settle_corporate` (`payment_service.py:1008`).
The new `TestRiderNoticeFailureIsObservable` tests exercise the raising path specifically, which is
the only way that class of bug surfaces.

**Blast radius:** grepped for other readers of the new counter — none, it is new. The three edited
handlers have no callers of their own (they are inline `except` blocks). `payment_retry.py` gains
one import; verified `ruff --fix` placed it in both dual-import branches in sorted order.

**Sentry volume:** these fire only when a push *raises*, on a ride that has already failed payment.
Unlike a per-request path, this is bounded by the rate of failed settlements — low, and if it ever
is not, that is itself the thing you want paged about.

**No migration, no schema, no settings row, no feature flag, no API change.**

## 5. User-experience effect

None, for riders, drivers, corporate admins or internal admins. No copy changed, no notification
added or removed, no timing altered. A rider whose push fails experiences exactly what they did
before. The change is visible only in Sentry and `/metrics`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | 2 × `debug` → `error` + metric, each with a local metrics dual-import | E2 |
| `backend/utils/payment_retry.py` | 1 × `debug` → `error` + metric; metrics added to both dual-import branches | the third site, found while checking this fix's own rationale |
| `backend/tests/test_settle_card_capture.py` | new `TestRiderNoticeFailureIsObservable`, 3 tests | pins the counter, the unchanged result, and the surviving DB write |
| `docs/change-log/2026-09-21-rider-payment-failure-notice-observable.md` | this file | |

## 7. Before / after

**Before** — invisible in production, since `debug` is gated off:

```python
except Exception as _push_err:
    logger.debug(f"Payment failure push to rider failed: {_push_err}")
```

**After** — an ERROR with a traceback, and a counter:

```python
except Exception as _push_err:
    logger.opt(exception=True).error(
        "[PAYMENT] rider payment-failure push failed for ride {}: {}", ride_id, _push_err
    )
    _metric_inc("spinr_payment_rider_notice_failed_total", {"reason": "card_declined"})
```

**Concrete scenario.** A rider's card is declined at trip end. FCM is briefly unreachable, so the
push raises. *Before:* the ride sits at `payment_status='failed'`, the rider is silently blocked
from their next booking with no idea why, and nothing anywhere records that they were never told.
*After:* the same rider is still blocked — the fix does not deliver the push — but there is a
Sentry event and `spinr_payment_rider_notice_failed_total{reason="card_declined"}` increments, so
support can see the notice was lost and the new admin unpaid-rides screen has context for chasing
it.

## 8. Rollback plan

`git revert` + redeploy. Nothing is persisted, migrated or written to any table by this diff, and
behaviour is unchanged, so a revert cannot leave anything half-applied — the log lines drop back to
`debug` and the counter stops being emitted. Nothing consumes it yet.

## 9. Verification performed

- `ruff check`, `ruff format --check`, `python3 -m py_compile` clean on all three changed files.
  `ruff` is what caught the `NameError`-in-an-exception-handler bug described in §4.
- Confirmed logger type per module before writing each call (loguru vs stdlib), rather than
  assuming one convention across both.
- Confirmed `{}` placeholder count matches positional-arg count at both loguru sites (2 and 2).
- Confirmed `ride_id` and `logger` are in scope in all three blocks (both `settle_card` blocks are
  in the function body, `ride_id` is its own parameter).
- Confirmed the metric-name spelling against this module's existing counters by grep, rather than
  picking one of the repo's two inconsistent spellings at random.
- Verified `patches[5]` is really `send_push_notification` in `_common_patches`, cross-checked
  against the pre-existing `TestFreshChargeFailurePushTargetApp` in the same file, which already
  relies on that index.
- **Read `payment_retry.py`'s actual notify path** instead of trusting my own comment about it —
  which is how the overclaim in §2 and the third bug site were found.
- `spinr-money-auditor` run against the real diff before commit (CLAUDE.md gate 10): verdict
  **SAFE TO MERGE**, no blockers. Its findings — the WARNING/ERROR inconsistency with
  `webhooks.py:1151`, the overclaiming comment, and the third silent-swallow site — are all fixed
  above rather than deferred.

## 10. What was NOT verified — read this before merging

- **No test was executed.** `pytest` is not installed here and PyPI is blocked by policy, so CI is
  the first thing that will run any of it. Static checks and a reviewer pass are all that stand
  behind this.
- **The `payment_retry.py` site has no direct test.** Reaching it requires driving the retry loop
  to `new_count >= MAX_RETRIES` *through its exception branch* with a raising push — several layers
  of mocking deep, and no existing test in `test_payment_retry.py` /
  `test_payment_retry_coverage.py` exercises that branch to extend. It is a three-line change of
  the same shape as the two that *are* tested, but it is untested, and a test that cannot be run is
  not worth writing blind. Flagged for whoever can execute the suite.
- Only the declined branch is covered by the "DB write survives a raising push" assertion; the
  generic-failure branch is structurally identical but not separately asserted. Coverage gap, not a
  suspected bug.
- Nothing was exercised against real Stripe, a real FCM failure, or a real Supabase. The push
  failure is simulated with `AsyncMock(side_effect=RuntimeError(...))`.
- That these ERRORs reach Sentry with the right tags is inferred from `server.py`'s
  `event_level="ERROR"` bridge, not observed. Note these calls do **not** bind a `domain` tag —
  consistent with the surrounding code in both files, but the same gap flagged in
  `2026-09-21-admin-idle-touch-observability.md` §11. A `domain="payments"` sweep across these
  modules is worth doing as its own change.
- Backend-only: no `admin-dashboard` build, none of the 6 merge-blocking visual baselines touched.
