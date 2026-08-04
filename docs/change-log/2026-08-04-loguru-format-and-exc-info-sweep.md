# Change Impact & Risk Log — loguru %-style formatting and `exc_info` sweep

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-04 |
| Author | Claude Code (session with @srikumarimuddana) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, corporate, drivers, auth, dispatch (observability across all) |
| PR / commit link | PR #3433 — commits `81d490c`, `50aed13`, `b369e33`, `11bb95c`, + this one |
| Related issue or gap ID | discovered from the `CSRF token mismatch: %s %s origin=%s` production line |

## 1. Issue / gap identified

Three defects in how this backend calls loguru, none of which raises, lints, or
fails a test — the log line just quietly loses information:

1. **55 call sites used `%s`-style placeholders.** loguru formats with `str.format`; `%s` is
   not a format field, so the placeholders were emitted verbatim and every argument silently
   discarded. Surfaced in production as `CSRF token mismatch: %s %s origin=%s`.
2. **112 call sites passed `exc_info=`.** loguru has no such parameter. It was accepted as a
   `str.format` keyword and ignored, so **no traceback was ever captured on any of them**.
3. **The stderr sink ran with loguru's default `diagnose=True`**, which annotates traceback
   frames with the *values* of locals and call arguments — a PIPEDA exposure that blocked
   fixing (2).

Concrete cost of (1) and (2) together, from `services/payment_service.py`:

```
[PAYMENT] Stripe charge %s confirmed but ride %s DB update failed —
ride stuck in 'processing'; financial_events written for recovery. err=%s
```

An operator is told a charge succeeded while the ride update failed — with no charge id, no
ride id, and no stack. Same for `Stripe event %s is STUCK … Manual reconciliation required`.

## 2. Root cause

The backend uses `from loguru import logger` in ~40 modules but was written against stdlib
`logging` conventions. Both defects are silent by construction:

- `"… %s".format(ride_id)` returns the string unchanged and raises nothing.
- an unknown keyword is just another `str.format` kwarg, ignored when the message has no
  matching field.

Nothing in the toolchain covers this: ruff has no rule for it, and no test asserted on log
text. It accumulated over the life of the codebase.

## 3. Fix / remediation

| Commit | Scope |
|---|---|
| `81d490c` | `server.py` stderr sink → `backtrace=True, diagnose=False`; `tests/test_loguru_sink_frame_vars.py` |
| `50aed13` | payments — 25 format sites, 13 `exc_info` (`payment_service.py`, `wallet_repo.py`) |
| `b369e33` | corporate — 14 format sites, 10 `exc_info` (member offboarding, suspension) |
| `11bb95c` | documents + misc — 12 format sites, 20 `exc_info` (7 files) |
| this commit | remaining 69 `exc_info` across 9 files; `tests/test_loguru_call_conventions.py` |

Ordering was deliberate: the sink hardening had to land first, because converting
`exc_info=True` to `logger.opt(exception=True)` is what actually starts emitting tracebacks,
and with `diagnose=True` those tracebacks would have carried rider PII out of settlement-code
frame locals.

Conversions applied: `%s`/`%d` → `{}`, `%r` → `{!r}`, `%.2f` → `{:.2f}`, `%.5f` → `{:.5f}`;
`exc_info=True` → `logger.opt(exception=True)`; `exc_info=<exception>` →
`logger.opt(exception=<exception>)` (`utils/rate_limiter.py`); `exc_info=False` → dropped, since
no traceback is already loguru's default (`utils/surge_engine.py`).

## 4. Risk & impact on existing functionality

**Blast radius: wide but shallow — 20 files, log statements only. No control flow, no
conditionals, no return values, no schema, no state.**

Every edit is confined to a `logger.*(...)` call expression. Nothing else in any of these
files was touched. The specific risks that do exist:

- **Formatting can now raise where it previously could not.** `str.format` is actually invoked
  now, so a malformed field would `IndexError`/`KeyError` *inside* the logging call. Mitigated
  by construction: the transformer refused to touch any message containing a literal `{`/`}`,
  and verified placeholder count == argument count at every site (both are hard failures in the
  script, not warnings). The repo-wide test now pins the invariant.
  Note this risk ran the *other* way before: `logger.error(f"Receipt email error: {e}",
  exc_info=True)` passed a kwarg to `str.format` on an f-string built from exception text, so an
  exception whose message contained a brace would have raised inside the log call. That is gone.
- **Log volume increases.** 112 error paths now emit a stack where they emitted one line.
  Concentrated in `core/lifespan.py` (35 sites — the background loops), which is where a
  recurring failure would multiply. Accepted: these are all `logger.error`/`critical` paths that
  are supposed to be rare, and a loop failing repeatedly with no stack is the situation this is
  meant to fix.
- **Sentry event shape changes for those 112 sites.** The `_loguru_sentry_sink` bridge in
  `server.py` branches on `record["exception"]`: `capture_exception` when present, else
  `capture_message`. These sites previously always took the `capture_message` branch. They will
  now arrive as exceptions with stack traces — better grouping, but *different* grouping, so
  existing Sentry issue fingerprints for these errors will not match and may re-alert as new
  issues. No alert rule references them by fingerprint (checked: alerting is on the
  `spinr_*` Prometheus metrics named in CLAUDE.md, not Sentry fingerprints).
- **`utils/log_guard.py` still applies** — it patches the logger, so it covers every sink and is
  unaffected by these call-site changes. Its per-call-site redaction warnings should now *drop*,
  since the one path it was actively redacting (`features.py` safety email) is fixed at source.

Not touched: ride state machine, dispatch logic, money arithmetic, wallet deltas, migrations,
RLS, API responses, WebSocket payloads. No `Decimal` arithmetic was altered — `{:.2f}` on a
`Decimal` is display formatting inside a log message, downstream of every money calculation.

## 5. User-experience effect

**Nobody — rider, driver, corporate admin or internal admin.** Backend log output only. Nothing
reaches an API response, a screen, or a notification. Not visible mid-session to anyone.

The beneficiary is whoever is on call: a stuck Stripe event or a failed allowance compensation
now names its ride and carries a stack.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/server.py` | stderr sink: `backtrace=True, diagnose=False` | loguru default renders frame-local *values* into tracebacks (PIPEDA) |
| `backend/services/payment_service.py` | 23 format, 13 `exc_info` | settlement/refund/capture errors logged without ids or stacks |
| `backend/repositories/wallet_repo.py` | 2 format | stuck-Stripe-event CRITICAL logged without the event id |
| `backend/services/corporate_member_offboarding_service.py` | 7 format, 5 `exc_info` | bulk ride-cancel failures logged without the ride |
| `backend/services/corporate_suspension_service.py` | 7 format, 5 `exc_info` | same |
| `backend/documents.py` | 4 format, 2 rewritten by hand | would have started logging document filename and signed URL |
| `backend/features.py` | 2 format, 11 `exc_info`, 1 PII fix | airport-fee + push/safety paths; safety email address removed |
| `backend/core/lifespan.py` | 35 `exc_info` | 18 background loops had no tracebacks on failure |
| `backend/utils/ws_pubsub.py` | 10 `exc_info` | WS fan-out failures |
| `backend/utils/push_retry.py` | 8 `exc_info` | push retry loop |
| `backend/utils/refresh_tokens.py` | 5 `exc_info` | includes the token-reuse-detection path |
| `backend/utils/surge_engine.py` | 4 `exc_info` (1 `False` dropped) | surge area updates |
| `backend/core/middleware.py` | 2 format (earlier commit), 1 `exc_info` | the original CSRF line |
| `backend/dependencies/__init__.py`, `repositories/ride_repo.py`, `repositories/driver_repo.py`, `routes/websocket.py`, `routes/lost_and_found.py`, `services/cancellation_service.py`, `utils/idempotency.py`, `utils/rate_limiter.py` | 1–4 sites each | remainder |
| `backend/tests/test_loguru_sink_frame_vars.py` | new | pins `diagnose=False` with a non-vacuity guard |
| `backend/tests/test_loguru_call_conventions.py` | new | pins both conventions repo-wide |
| `backend/tests/test_error_handling_guards.py` | widened one assertion | source-grepped the literal `"logger.error"`, which no longer matches `logger.opt(...).error` though the level is unchanged |

## 7. Before / after

```python
# Before — emits "%s" literally, drops both ids, captures no traceback
logger.error(
    "[PAYMENT] Stripe charge %s confirmed but ride %s DB update failed — "
    "ride stuck in 'processing'; financial_events written for recovery. err=%s",
    outcome.payment_intent_id, ride_id, db_err,
    exc_info=True,
)
```

```python
# After
logger.opt(exception=True).error(
    "[PAYMENT] Stripe charge {} confirmed but ride {} DB update failed — "
    "ride stuck in 'processing'; financial_events written for recovery. err={}",
    outcome.payment_intent_id, ride_id, db_err,
)
```

```python
# documents.py — NOT a mechanical conversion; the argument had to go
# Before (never actually emitted, because %s dropped it)
logger.info("[upload] create_signed_url returned type=%s repr=%r",
            type(signed_res).__name__, signed_res)
# After — repr(signed_res) is the signed URL: an hour-long credential
# for a driver's identity document
logger.info("[upload] create_signed_url returned type={}", type(signed_res).__name__)
```

## 8. Rollback plan

`git revert` is sufficient and complete. Every change is a log statement or a sink option —
stateless, no persisted data, no external side effect, no migration. Reverting restores the
previous (information-losing) behavior exactly.

The one deployment-ordering constraint: `81d490c` (`diagnose=False`) must not be reverted while
the `opt(exception=True)` commits remain, or tracebacks would start rendering frame-local values
again. Revert the whole set, or none of it.

No feature flag: log-statement changes are not user-visible behavior and flagging them would
mean shipping a knob whose "off" position is the defect.

## 9. Verification performed

- [x] **Full backend suite: 9847 passed, 12 failed, 8 skipped, 1 xfailed.** All 12 failures
      confirmed **pre-existing** by stashing this branch's changes and re-running them against
      the unmodified tree — `test_favorites_coverage` (4), `test_routes_favorites_coverage` (4),
      `test_admin_sentry` (2), `test_auth_remaining_endpoints` (1),
      `test_routes_webhooks_coverage::TestTwilioInboundSignatureVerification` (1). None are
      touched by this work and none are logging-related. Run twice to confirm stability.
- [x] Targeted suites per group as each landed: payments `-k "payment or wallet or fare or
      stripe or settle"` 1378 passed; corporate `-k "corporate or suspension or offboarding"`
      860 passed; misc group 843 passed.
- [x] `ruff check` + `ruff format` clean on all 20 modified files.
- [x] **loguru's actual behavior confirmed empirically, not assumed** — that `%s` is emitted
      verbatim, that `exc_info=True` attaches no traceback, that `opt(exception=True)` does, and
      that `diagnose=True` renders argument values while `diagnose=False` does not.
- [x] **Transformer output independently re-verified.** A byte-offset bug was found mid-sweep
      (Python's AST `col_offset` is a UTF-8 *byte* offset, and these messages are full of
      em-dashes); it produced a syntax error on `utils/refresh_tokens.py`. All three
      already-committed groups were then re-derived from their pre-change versions with the
      corrected transformer and compared: **ruff-formatted output identical for every
      non-hand-edited file**, and the only message-string differences anywhere were the three
      intentional hand edits. The bug never reached a commit.
- [x] Regex false-positive found by its own test and fixed: the placeholder pattern accepted the
      printf space flag, so the prose `"150% of base"` matched as `% o` (octal). Re-checked every
      transformed file — no string anywhere differs between the loose and tight patterns, so no
      message was corrupted by it. The tight pattern and the false-positive case are both pinned
      by tests.
- [x] Argument-level PIPEDA review at every site, not a blanket sweep — three sites needed their
      arguments changed rather than interpolated (see §7 and commit `11bb95c`).
- [x] Reviewed against CLAUDE.md — "do not silently swallow errors", the observability
      log/metric/Sentry split, and the "what can NEVER appear in logs" list.
- [x] **The two new log-assertion tests were rewritten after proving flaky.** Their first form
      captured through an additive loguru sink (`logger.add(...)` / `remove(id)`). That passed
      alone and in several subsets, failed in two full-suite runs, then passed in a third with no
      code change — because the suite mutates loguru's global state from several directions:
      `test_log_guard.py`'s fixture calls a bare `logger.remove()` (drops every handler) plus
      `logger.configure(patcher=...)`, and `test_p3_loop_jitter_metrics.py`,
      `test_p3_ws_broadcast.py` and `test_ws_health.py` each run
      `sys.modules["loguru"].logger = MagicMock()` at import time, permanently. Both tests now
      avoid that global state: the CSRF one monkeypatches `core.middleware.logger` with a
      recorder and applies loguru's own `str.format` rule to the captured call (which is exactly
      the defect under test); the sink one takes exclusive ownership of the handler set for the
      duration of each test. Neither shares state with another module now.

## 10. What was NOT verified

- **No log output was inspected from a running backend.** Correctness rests on the loguru
  behavior experiments above plus static analysis; the server was not started and no real
  request was traced end to end. The conversions are uniform and test-covered, but a staging
  soak would be the real confirmation — particularly for log *volume* from `core/lifespan.py`'s
  background loops, which is the one effect that scales with production traffic rather than with
  the diff.
- **The Sentry re-grouping is reasoned, not observed.** Those 112 sites moving from
  `capture_message` to `capture_exception` should improve grouping, but whether existing Sentry
  issues split or re-alert was not tested against a real DSN — this session cannot authenticate
  to the Sentry MCP server. Expect some previously-silenced issues to resurface as new.
- **`{:.2f}` on `Decimal` was verified in isolation** (`'{:.2f}'.format(Decimal('5.005'))` →
  `5.00`) but not through a real settlement path with a live fare.
- **No test asserts the *content* of any specific production log line** beyond the two CSRF
  lines. The new repo-wide test pins the calling convention, which is what actually failed here;
  it cannot tell you a message says something useful.
- **The 12 pre-existing suite failures were confirmed pre-existing, not diagnosed or fixed.**
  They are outside this work's scope, but they mean the suite is not green on `main` either —
  worth a separate look, particularly `TestTwilioInboundSignatureVerification::
  test_invalid_signature_returns_403`, which is a webhook signature-verification test.
- **The global-loguru-state landmine is worked around, not removed.** Three test modules still
  permanently replace `loguru.logger` with a `MagicMock` at import time, which will silently
  hollow out any future test that captures log output. The two tests added here are immune; the
  next one written the obvious way will not be. Fixing those modules to scope their stubs is a
  standing gap, not closed here.
