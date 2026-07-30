# Change Impact & Risk — Sentry egressing stack-frame locals (T1c)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (Sentry egress) · **Risk:** medium — narrow code change, wide data-egress consequence
**Related:** `docs/change-log/2026-07-30-base-pii-logging.md` (T1), `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md`

---

## Issue / gap identified

`sentry_sdk.init()` in `backend/server.py` did not set `include_local_variables`, which
**defaults to `True`** in sentry-sdk 2.59.0. Every `capture_exception` therefore shipped the
local variables of every frame in the traceback to Sentry. For a booking handler that is
`pickup_lat`, `pickup_lng`, rider phone, email and name — all on CLAUDE.md's categorical
never-log list.

This is the second half of the launch-gate "Ship PII log/Sentry redaction" item. T1 closed the
stdout/stderr sink; this closes the telemetry sink.

## Root cause

`utils/sentry_scrub.py` was written as the PII control for Sentry and covers `event["message"]`,
`logentry.message`, `exception.values[].value`, and deep-scrubs `extra` / `contexts` / `request`.
It never covered `exception.values[].stacktrace.frames[].vars`, and nothing set the SDK option
that would have made frame vars moot. Two independent reasons the gap persisted:

1. **The frame-var path is unreachable from the existing deep-scrub.** Frame vars sit at
   `exception → values[] → stacktrace → frames[] → vars → key`, which is already past
   `_MAX_SCRUB_DEPTH = 6` before reaching a single value. Widening the deep-scrub to include
   `exception` would have silently no-opped.
2. **Value-pattern scrubbing cannot contain frame vars, and the reason is not obvious.**
   Verified empirically against a real SDK round-trip: Sentry serializes values *before*
   `before_send` runs, so `pickup_lat = 52.1332` arrives as the string `'52.1332'` under one key
   and `'-106.67'` under another. Every coordinate pattern in `ai/pii.py` requires lat and lng
   **adjacent in one string** (`lat=… lng=…` or `lat,lng`), so neither half matches. Names are
   not regex-matchable at all — `ai/pii.py` says so in its own module docstring.

Measured, not assumed:

| Input as it reaches `before_send` | `scrub_pii` result |
|---|---|
| `'52.1332'` | `'52.1332'` — **unchanged** |
| `-106.67` | `-106.67` — **unchanged** |
| `lat=52.1332 lng=-106.67` | `[COORDS]` ✓ |
| `'+13065551234'` | `'[PHONE]'` ✓ |
| `'Jane Doe'` | `'Jane Doe'` — **unchanged** |

So the obvious fix — "route frame vars through `scrub_pii`" — would have caught the phone and
email while **still shipping raw GPS and the rider's name**, producing a green test suite and a
false sense of containment.

## Fix / remediation

Three layers, in order of what actually does the work:

1. **`include_local_variables=False`** — the containment. Frame vars are unbounded (whole
   request payloads, DB rows, user objects) and cannot be reliably pattern-scrubbed, so they are
   switched off at source.
2. **`_scrub_frames()` with key-name redaction** — defense-in-depth for a re-enabled option or a
   non-`capture_exception` path. Walks the exact frame-var path (not the depth-bounded
   deep-scrub) and redacts on the **key** as well as the value, because that is the only thing
   that catches split coordinates and names.
3. **Key-name redaction extended to `_scrub_deep`** — so `event["request"]["data"]` is covered
   too. Verified against the SDK: request **bodies are not gated by `send_default_pii`** (only
   cookies and sensitive headers are), and `max_request_body_size` defaults to `"medium"`, so a
   booking POST body reaches `before_send` with its coordinates already split across keys.

**Key-name redaction is deliberately a denylist**, which is the opposite of T1's choice, and the
reason is specific: for frame vars and JSON bodies we control none of the values and the key is
the only reliable signal about what a value means. `_KEY_ALLOWLIST` protects the IDs that make an
event triageable (`ride_id`, `driver_id`, `request_id`, …) from over-redaction, and there is a
test asserting each one survives.

**Also: the PIPEDA options are now assertable.** They moved out of the inline `init()` call into
`sentry_scrub.pipeda_sentry_options()`. See "Verification" for why this was not cosmetic.

## Risk & impact on existing functionality

**Blast radius — every Sentry event from the backend.** Narrow code change, wide data
consequence. Specifically:

- **Debuggability is reduced, deliberately.** Frame locals are genuinely useful and we are giving
  them up. Mitigated by what is kept: the exception type, message, and the **full traceback with
  frame/function/lineno** all still egress — there is a test asserting exactly that, because a
  fix that stripped the diagnostic payload along with the PII would be a bad trade.
- **Over-redaction risk in `_scrub_deep`.** This function is shared with `scrub_breadcrumb` and
  the `extra`/`contexts`/`request` paths, so key-name redaction now applies to breadcrumb data
  and structured log context too. A key like `driver_name` in an `extra={...}` will now come
  through as `[REDACTED]`. That is the intended behaviour, but it is a behaviour change to
  existing events, not only to frame vars. Enumerated consumers of `_scrub_deep`: `scrub_event`
  (`extra`, `contexts`, `request`), `scrub_breadcrumb` (`data`), and now `_scrub_frames`.
- **`tags_from_log_extra` is untouched**, so tag-based triage (`domain`, `ride_id`, `surface`, …)
  is unaffected — those go through a separate allowlist that was already ID-only.
- **No runtime behaviour outside Sentry.** No API response, DB write, ride state, or money path
  is touched.

**Fixed a pre-existing test-suite landmine as part of this.** `tests/test_admin_security.py:25`
installed a permanent `sys.modules["sentry_sdk"] = MagicMock()` at import time, never restored.
Because pytest imports all test modules during collection, every test module collected
alphabetically after `test_admin_security` received the mock. This made the new frame-var tests
**pass in isolation and fail in the full suite** — `sentry_sdk.Client()` was returning a
MagicMock that captured nothing. `sentry_sdk` is a real installed dependency and never needed
stubbing to import, so it came out of that list. `loguru`, `logging_utils` and `firebase_admin`
were left alone (unverified whether they are equally vestigial — see "not verified").

## User experience effect

**None.** No rider-, driver-, corporate-admin-, or internal-admin-facing change. Nothing is
visible mid-session to anyone using the app. The only affected audience is engineers reading
Sentry, who will see redacted locals and no frame vars.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/server.py` | `sentry_sdk.init` now spreads `pipeda_sentry_options()`; inline PII options removed | Stop shipping frame locals; make the controls unit-assertable |
| `backend/utils/sentry_scrub.py` | Added `pipeda_sentry_options()`, `_scrub_frames()`, `_SENSITIVE_KEY_RE`, `_KEY_ALLOWLIST`, `_key_is_sensitive()`; `_scrub_deep` now redacts on key name; `scrub_event` calls `_scrub_frames` | Defense-in-depth for frame vars and JSON bodies whose values patterns cannot match |
| `backend/tests/test_sentry_frame_vars.py` | New — 30 tests | Pin all three layers; mutation-verified |
| `backend/tests/test_admin_security.py` | Removed `sentry_sdk` from the permanent `sys.modules` stub list; documented the blast radius of the remaining stubs | Its session-wide mock silently voided the new P0 tests |
| `docs/change-log/2026-07-30-sentry-frame-var-pii.md` | New — this file | Required by CLAUDE.md |

## Before / after

```python
# BEFORE — server.py; include_local_variables defaults to True, so every
# capture_exception ships every frame's locals
sentry_sdk.init(
    dsn=sentry_dsn,
    integrations=integrations,
    traces_sample_rate=0.1,
    profiles_sample_rate=0.1,
    environment=...,
    send_default_pii=False,
    before_send=scrub_event,
    before_breadcrumb=scrub_breadcrumb,
)

# AFTER — the PIPEDA controls are data, and therefore testable
sentry_sdk.init(
    dsn=sentry_dsn,
    integrations=integrations,
    traces_sample_rate=0.1,
    profiles_sample_rate=0.1,
    environment=...,
    **pipeda_sentry_options(),   # send_default_pii=False, include_local_variables=False,
)                                # before_send, before_breadcrumb
```

What a booking-handler exception used to egress, verified through a real SDK round-trip:

```
- vars: {'pickup_lat': '52.1332', 'pickup_lng': '-106.67',
-        'rider_phone': "'+13065551234'", 'rider_email': "'jane.doe@example.com'",
-        'rider_name': "'Jane Doe'"}
+ (no vars key — frames carry function/lineno/filename only)
```

And with locals force-enabled, the defense-in-depth layer:

```
- vars: {'pickup_lat': '52.1332', 'rider_name': "'Jane Doe'", 'ride_id': "'ride_abc123'"}
+ vars: {'pickup_lat': '[REDACTED]', 'rider_name': '[REDACTED]', 'ride_id': "'ride_abc123'"}
```

## Rollback plan

`git revert` is a genuine rollback — the change alters Sentry egress and exception-detail text
only. No migration, no data written, no state altered, nothing applied to live data to unwind.

Reverting **restores the leak**, so the correct response to "we needed those locals" is not a
revert. It is either adding the specific key to `_KEY_ALLOWLIST` (if the value is genuinely not
PII) or logging the needed field explicitly through the redacting path. No feature flag: the
change is strictly safer than the status quo, so shipping it dark would only prolong the leak.

## Verification performed

- **New tests:** 30 pass (`pytest tests/test_sentry_frame_vars.py`). Existing
  `test_sentry_scrub.py` (9) still passes untouched.
- **Real SDK round-trip, not hand-built dicts.** The core tests drive an actual exception through
  `sentry_sdk.Client` + a fake transport and inspect what would have left the process. This is
  what surfaced the split-coordinate serialization behaviour that the whole fix design turns on;
  a dict-only test could not have seen it.
- **Mutation-verified — five mutations, and the first round found a real hole.** Reverting
  `include_local_variables=False` in `server.py` initially **failed nothing**, because the
  behavioural tests configure their own client and never assert on `server.py`'s wiring. That is
  why `pipeda_sentry_options()` exists and why there is an AST test on the `init` call. All five
  mutations now fail:

  | Mutation | Caught by |
  |---|---|
  | Flip `include_local_variables` to `True` in the builder | `test_pipeda_options_pin_the_controls` |
  | Stop spreading `pipeda_sentry_options()` in `server.py` | `test_server_init_uses_the_pipeda_options_...` |
  | Add `include_local_variables=True` **after** the spread (silently wins at runtime) | `test_server_init_uses_the_pipeda_options_...` |
  | Drop the `_scrub_frames(exc)` call from `scrub_event` | 2 tests |
  | Disable key-name redaction, leaving value patterns only | 12 tests |

- **Anti-vacuity:** `test_locals_leak_when_the_option_is_left_at_its_default` characterizes the
  original bug, so the fix is provably not a no-op. Every negative assertion is paired with a
  positive anchor (`ride_id` survives, `ride_type` survives, the traceback survives, the
  exception message survives).
- **Anti-silent-no-op:** `test_sentry_sdk_is_the_real_module_not_a_session_stub` fails loudly if
  a future `sys.modules` stub mocks the SDK out from under these tests, which is exactly the
  failure mode that hid here once already.
- **Full suite:** `pytest -m "not slow"` → **5796 passed, 8 skipped, 1 xfailed, 1 failed**
  (5766 passed before this change; +30 new).
- **`test_admin_security.py` re-verified both standalone (14 pass) and paired with the new file
  (44 pass)** — the stub removal did not break the isolation it existed for.
- **Lint:** `ruff check` clean on all changed files except one **pre-existing** `S110`
  (`try/except/pass` in `tags_from_log_extra`) — confirmed present at HEAD by stashing, and left
  alone as out of scope for a PII fix (it is a deliberate never-raise hook).
- **The 1 remaining suite failure is pre-existing and unrelated:**
  `test_compliance_reports.py::TestInsurancePeriodRows::test_joins_driver_name`, a timestamp
  format mismatch. Proven pre-existing in the T1 change log by stashing and re-running. Still
  needs its own `[CR]` per CLAUDE.md gate 8.

## What was NOT verified

- **No production build applies** — backend-only; no `admin-dashboard`/`rider-app`/`driver-app`
  code touched, so `npm run build` is not applicable.
- **Not verified against a real Sentry project.** All assertions use a fake transport, so this
  proves what the SDK *would* send, not what Sentry ingests or displays. Server-side Sentry
  scrubbing rules (if any are configured on the project) are neither relied on nor checked.
- **`profiles_sample_rate=0.1` and `traces_sample_rate=0.1` were not audited.** Performance
  transactions and profiles are a separate egress path from error events, and whether they carry
  PII (e.g. in span descriptions or transaction names built from URLs with IDs) was not examined
  here. Flagging rather than silently implying it is covered.
- **`max_request_body_size` left at its `"medium"` default.** Bodies now go through key-name
  redaction, which is a real control, but it is pattern/key-based rather than structural — a
  sensitive field whose key does not match `_SENSITIVE_KEY_RE` still egresses. Setting it to
  `"never"` would be the structural equivalent of `include_local_variables=False`; that is a
  debuggability trade-off worth an explicit decision rather than a unilateral one, so it is
  called out here instead of changed.
- **The `_SENSITIVE_KEY_RE` denylist is inherently incomplete.** It covers the fields this
  codebase actually carries, but a new sensitive column with an unmatched name will egress. This
  is the acknowledged weakness of the defense-in-depth layer; `include_local_variables=False` is
  why that weakness is not load-bearing for frame vars.
- **`loguru`, `logging_utils` and `firebase_admin` are still permanently stubbed** by
  `test_admin_security.py`. Only `sentry_sdk` was removed — whether the other three are equally
  vestigial was not checked, and any test of those modules collected after `test_admin_security`
  has the same silent-mock hazard. Worth a follow-up; not fixed here to keep this change scoped.
- **No load or latency measurement.** `before_send` now walks frame vars and does key matching on
  every event. Sentry events are low-volume and `before_send` is off the request path, so no
  benchmark was taken.
