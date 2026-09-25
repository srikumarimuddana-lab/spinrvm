# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (SOS contact SMS), auth (OTP), admin (cloud messaging and marketing SMS). All three send through `sms_service.py`. |
| PR / commit link | Branch `claude/fix-twilio-bounded-executor`, not pushed and no PR yet. Commits: `2c176e1` (bounded pools), `31edd8a` (SOS pool), `125edfb` (general-pool sizing), `6f087fe` (contextvars), `f8c241c` and `7d12c7d` (SOS caller switch), `7da6b8e` (metrics and domain tag), `b4260de` (OTP headroom). |
| Related issue or gap ID | Security-auditor SHOULD-FIX on #5784. Follow-up to `2026-09-25-twilio-timeout.md`. |

## 1. Issue / gap identified

After #5784, Twilio sends run as `asyncio.wait_for(asyncio.to_thread(_send), 15.0)`, which puts them on the event loop's shared default executor. WebSocket auth, Stripe calls and other `to_thread` callers use that same pool.

During a Twilio or DNS outage, SMS sends can use up that shared pool and stall unrelated work. Examples: an SOS fan-out, an OTP burst, or an admin broadcast of up to 50 concurrent sends.

## 2. Root cause

- `TwilioHttpClient(timeout=10.0)` limits the socket connect and read. It does not limit a stalled `getaddrinfo` DNS lookup.
- `asyncio.wait_for` cancels only the awaiting coroutine. The worker thread keeps running.
- So each hung send keeps its default-executor thread after the caller has given up. That pool has `min(32, cpu+4)` threads, which is about 5 to 8 on the backend's small VMs.

## 3. Fix / remediation

Twilio sends now run on three dedicated pools built from `utils/bounded_executor.BoundedExecutor`, the same class that backs `_DB_EXECUTOR`. Each send is submitted with `loop.run_in_executor(executor, ctx.run, _send)`.

### Pool sizes

| Pool | Entry point / callers | Workers + queue | Justification (from call volume visible in code) |
|---|---|---|---|
| SOS `spinr-sms-sos` | `send_sos_sms`, used only by the ride SOS and rideless SOS fan-outs in `routes/rides/safety.py` | 4 + 28 | Each SOS texts at most 3 contacts (`MAX_EMERGENCY_CONTACTS = 3`). 4 workers send one fan-out in a single Twilio round trip. 32 slots hold about 10 simultaneous SOS triggers before a send fails fast. |
| OTP `spinr-sms-otp` | `send_otp_sms`, from `/auth/send-otp` | 8 + 56 | One SMS per request. The 6-per-minute limiter is per IP, so a login burst after an outage, spread across many users, is not bounded by it. The old 4 + 8 (12 slots) could reject healthy sends. 8 workers give about 16 sends per second at ~0.5 s, and 64 slots drain in about 4 s, inside the 15 s wait. Raised on the perf reviewer's should-fix. |
| General `spinr-sms` | `send_sms`: admin cloud messaging, marketing, guest ride notices, SOS contact opt-out notice | 16 + 240 | See "Bulk-caller audit" below. |

### Bulk-caller audit (maximum in-flight sends)

- **Admin cloud messaging.** `routes/admin/messaging.py` `_fan_out` runs `asyncio.gather` over recipients behind `asyncio.Semaphore(50)`. Each recipient's SMS is awaited inside its semaphore slot, so one broadcast has at most 50 SMS in flight. `_fan_out` is started only from the immediate-send endpoint, as a `BackgroundTasks` job. No background loop dispatches scheduled broadcasts (grepped `utils/`, `core/`, `services/`).
- **Marketing.** `utils/marketing_sms.py` `send_marketing_sms` sends one SMS per recipient and awaits it. Its only caller is `_send_sms_one` inside that same semaphore, so it is not a separate bulk source.
- **Fire-and-forget callers.** Guest notices are started with `spawn(notify_guest_*)` from `routes/drivers/ride_flow.py`, `routes/corporate_company_bookings.py` and `services/company_booking_service.py`. The opt-out notice is started with `spawn(...)` from `routes/users.py`. Each of these sends one SMS per event, so they scale with the number of ride events, not with a loop over recipients.
- **Sizing decision.** 256 slots fit one broadcast plus about 200 concurrent one-off sends, or about 5 simultaneous broadcasts, before anything fails fast.
- **Why 16 workers.** 16 workers drain all 256 slots in about 8 s at a healthy ~0.5 s round trip, inside the 15 s wait. A queue deeper than the workers can drain in 15 s would not help: those sends would simply time out instead of being rejected.
- **Assumption behind all the drain-time figures.** Every figure above (SOS, OTP, general) assumes a healthy Twilio latency of about 0.5 s per send. If latency is 2 s, a full general pool needs about 32 s to drain, so the tail of the queue times out and is not sent. Drain time grows linearly with latency.
- **Memory headroom.** At most 28 SMS threads exist per replica (4 SOS + 8 OTP + 16 general). `ThreadPoolExecutor` creates threads lazily, one per submit, up to `max_workers`. An idle replica has none, and a normal day has only as many as peak concurrency needed. Each thread costs one OS thread stack (typically up to 8 MB virtual, mostly untouched). Queued items are small closures, and at most 324 can be queued across the three pools.

### Fail-fast behaviour

When a pool's workers and queue are all full, `BoundedExecutor.submit` raises `ExecutorSaturated`. `send_sms` catches it and returns `{"success": False, "provider": "twilio", "error": "ExecutorSaturated"}`. Before returning, it:
- increments `spinr_sms_executor_saturated_total{pool}`;
- logs an error bound with `sms_pool`, plus `domain=safety` for the SOS pool or `domain=auth` for the OTP pool, so Sentry can filter on it. The general pool is mixed-use and gets no domain tag.

If a queued send's 15 s wait expires, the send is cancelled and never runs.

### Residual risk: stalled DNS, and how a wedged pool is detected

A send that is already running when `wait_for` fires cannot be cancelled. A typical case is a `getaddrinfo` DNS lookup, which the 10 s HTTP timeout does not cover. The thread keeps its slot until the lookup returns. Enough of these can fill a pool, and every later send on that pool then fails fast. The shared default executor is still protected. This is the bounded form of the original finding, not a fix for DNS stalls.

Two metrics make a wedged pool alertable. Both are pre-registered at 0 for `pool=sos|otp|general`, following the `utils/stripe_reconcile.py` pattern:
- **`spinr_sms_executor_saturated_total{pool}`** counts rejected sends. A non-zero rate means a pool is full.
- **`spinr_sms_executor_occupied_slots{pool}`** is a gauge of running plus queued slots. It is read from `BoundedExecutor`'s admission semaphore, which is cheap. It is sampled at every submit and every finish, not when a slot is released, because release happens inside `BoundedExecutor`'s worker thread and adding a hook there would change the shared class that `_DB_EXECUTOR` also uses. As a result, the gauge can stay high after an abandoned thread finishes, until the next send on that pool samples it again.

Suggested alert: occupied slots at pool capacity, or any saturated rate for `pool=sos`.

The following are unchanged: the 10 s HTTP timeout, the 15 s `wait_for`, the console fallback when Twilio is not configured, and the PII-free `error` string.

### Context variables

`asyncio.to_thread` copied the caller's context into the thread. `run_in_executor` does not.

Sentry's default Stdlib and Threading integrations are active: `server.py` does not disable default integrations, and `traces_sample_rate=0.1`. They read the current scope and span from contextvars when they instrument the Twilio HTTP call. Without a copy, a pool thread would run with an empty context, or with the context of the request that first spawned it.

The send therefore runs via `contextvars.copy_context().run`, the same as `to_thread`. Nothing inside `_send` reads the request deadline (`utils/deadline.py`) or the request_id (`utils/log_context.py`). The copy covers them anyway.

### SOS vs OTP vs broadcasts

SOS now has its own pool. An OTP flood (public, unauthenticated) or a pile-up of broadcasts during a Twilio stall cannot take SOS capacity.

The SOS contact opt-out notice and guest ride notices stay on the general pool:
- Neither is an emergency alert. The opt-out notice is a one-time courtesy text when a contact is added. Guest notices are ride-status updates.
- Both are fire-and-forget background tasks, one SMS per event, and both already treat a failure as best-effort.
- Moving them to the SOS pool would let routine traffic compete with real emergency alerts, which defeats the purpose of reserving that pool.

### Alternatives rejected

- **An `asyncio.Semaphore` around `to_thread`.** `wait_for` releases the semaphore on timeout while the hung thread keeps its slot, so this limits callers, not stuck threads.
- **A plain `ThreadPoolExecutor`.** Its queue is unbounded, so nothing fails fast.
- **Classifying SOS inside `send_sms`** (for example with a contextvar or by inspecting the call stack). This is implicit and fragile. An explicit `send_sos_sms` at the two call sites is simpler to read and review.

## 4. Risk & impact on existing functionality

**Blast radius.** Grepped with `grep -rn "sms_service\|send_sms" backend --include=*.py`:
- `routes/auth.py` calls `send_otp_sms` (OTP pool).
- `routes/rides/safety.py` calls `send_sos_sms` at 2 sites, through `routes/rides/_deps.py` (SOS pool). This is the only caller-side change.
- `utils/sos_contact_notice.py`, `services/guest_notification_service.py`, `routes/admin/messaging.py` and `utils/marketing_sms.py` call `send_sms` (general pool).
- `routes/rides/__init__.py` still re-exports `send_sms` from `_deps`, which still imports it.

**Tests retargeted.** Four test files patched `routes.rides._deps.send_sms` for SOS and now patch `_deps.send_sos_sms`: `test_p2_sos`, `test_sos_rideless`, `test_sos_paging`, and the `trigger_emergency` case in `test_coverage_rides`. Commit `f8c241c` retargets only `test_p2_sos`. The other three are fixed in `7d12c7d` because of the 3-file-per-commit limit, so they fail if `f8c241c` is checked out on its own.

**What could regress:**
- **General-pool saturation cascades.** Found by the new broadcast test: when the general pool is full, each fast rejection frees `_fan_out`'s semaphore slot immediately, so the rest of the broadcast fails in microseconds. At the first-pass size of 8 + 56, a healthy 50-wide broadcast plus 100 one-off sends produced `failed_count=192/200`. The 256-slot sizing removes this for healthy traffic. It can still happen in a real outage pile-up (6 or more simultaneous broadcasts, or a long Twilio stall), where those sends would time out anyway.
- **Queue time counts toward the 15 s wait.** If Twilio slows to about 1 s or more per send, the tail of a very large backlog times out and those sends are cancelled without being sent.
- **Permanent pool shutdown on thread-creation failure.** If the OS refuses to create a thread, `BoundedExecutor` shuts that pool down, and its sends return `RuntimeError` failures until the process restarts. `_DB_EXECUTOR` has the same accepted behaviour.
- **No interaction** with the ride state machine, money or wallet paths, insurance periods, or background loops.

## 5. User-experience effect

- **Riders (SOS):** in normal operation, nothing changes. SOS SMS can no longer be starved by OTP or broadcast traffic. The failure paths (`contacts_notified` and the existing warning) are unchanged.
- **Riders (OTP):** in normal operation, nothing changes, including a login burst of up to 64 in-flight sends per replica. Only in an outage pile-up, with more than 64 OTP sends in flight on one replica, does an extra send return the existing 503 "Failed to send verification code" immediately, instead of after up to 15 s.
- **Internal admin (behaviour change):** a broadcast is never rejected while Twilio is healthy. Sends are rejected with `ExecutorSaturated`, and counted in `failed_count`, only in an outage pile-up: more than 256 sends in flight on one replica. Before this change, excess sends queued without limit on the shared default pool.
- **Mid-session:** takes effect when the process restarts after deploy. No client-visible state changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/sms_service.py` | Adds 3 `BoundedExecutor` pools and `send_sos_sms`. `send_sms`, `send_otp_sms` and `send_sos_sms` all delegate to `_send_sms_on(pool, ...)`, which submits with `run_in_executor` inside a copy of the caller's context. Adds a fail-fast branch for `ExecutorSaturated` that increments the saturation counter and logs with a domain tag. Adds the saturation counter and occupancy gauge. | Keep Twilio outages off the shared default executor, and isolate SOS and OTP from each other and from broadcasts. |
| `backend/routes/rides/_deps.py` | Also imports `send_sos_sms` (both halves of the dual import). | Expose it to `safety.py`. |
| `backend/routes/rides/safety.py` | The 2 SOS fan-outs call `_deps.send_sos_sms(...)` with the same arguments. | Put SOS on its dedicated pool. |
| `backend/tests/test_sms.py` | Adds 6 tests in `TestSMSBoundedExecutor`. | Regression coverage (section 9). |
| `backend/tests/test_p2_sos.py`, `test_sos_rideless.py`, `test_sos_paging.py`, `test_coverage_rides.py` | Patch target changed from `_deps.send_sms` to `_deps.send_sos_sms`. | Follow the call-site switch. |
| `docs/change-log/2026-09-25-twilio-bounded-executor.md` | This file. | CLAUDE.md gate. |

## 7. Before / after

Before:

```python
sid = await asyncio.wait_for(asyncio.to_thread(_send), timeout=_TWILIO_THREAD_TIMEOUT_S)
# safety.py
_deps.send_sms(c["phone"], sms_body, twilio_sid=..., twilio_token=..., twilio_from=...)
```

After:

```python
ctx = contextvars.copy_context()
sid = await asyncio.wait_for(loop.run_in_executor(executor, ctx.run, _send), timeout=_TWILIO_THREAD_TIMEOUT_S)
...
except ExecutorSaturated:
    return {"success": False, "provider": "twilio", "error": "ExecutorSaturated"}
# safety.py
_deps.send_sos_sms(c["phone"], sms_body, twilio_sid=..., twilio_token=..., twilio_from=...)
```

## 8. Rollback plan

There is no feature flag. The pools are built when the module is imported, and their sizes are code constants, not `app_settings` values.

Rollback is a redeploy with the branch's commits reverted. This is acceptable because the change is backend-only, keeps no state, and writes nothing to live data. No SMS, Stripe, wallet or ride row depends on which pool ran a send.

For a partial rollback, reverting `f8c241c` and `7d12c7d` moves SOS back onto the general pool and leaves the rest of the change in place.

## 9. Verification performed

- [x] **Automated suites.** The rerun after `7da6b8e` and `b4260de` gave 480 passed, 0 failed. It covered the list below except the three guest suites, which do not touch the changed code. The earlier full list gave 519 passed. Suites: `test_sms`, the auth/OTP suites (`test_auth_send_otp`, `test_auth`, `test_verify_otp_login_flow`), the SOS/safety suites (`test_p2_sos`, `test_sos_rideless`, `test_e2e_sos_flow`, `test_sos_paging`, `test_sos_contact_notice`, `test_sos_contact_consent`, `test_driver_discreet_sos_flag`, `test_sos_expired_token`, `test_safety_notify_import`, `test_admin_safety_incidents`), the guest suites (`test_guest_sms`, `test_guest_notification_service_coverage`, `test_company_guest_booking`), the admin-messaging suites (`test_admin_messaging_coverage`, `test_messaging_fan_out`), `test_marketing_sms`, `test_bounded_executor`, `test_loguru_call_conventions` and `test_coverage_rides`.
- [x] **New tests in `test_sms.py`:**
  1. Each entry point runs on its own named pool, never on the default `asyncio_*` threads.
  2. The caller's contextvars are visible inside the send, per call.
  3. A general pool saturated with hung sends rejects in under 50 ms, pins only its 16 threads, and leaves the default executor free.
  4. SOS still sends while the OTP and general pools are both saturated with hung sends.
  5. A max-size broadcast through the real `_fan_out` (200 recipients, 50 in flight), plus 100 concurrent one-off sends, gives 0 failures when Twilio is healthy.
  6. A 60-send OTP login burst with Twilio healthy has 0 rejections. The test also pins the OTP pool at 8 + 56.
  7. Saturation metrics are pre-registered for all 3 pools. On saturation, the counter goes up by 1 and the gauge reads full capacity, for the SOS, OTP and general pools. The SOS and OTP saturation logs carry `domain=safety` and `domain=auth` respectively.
- [x] **Mutation checks.**
  - Reverting to `to_thread` fails the pool tests.
  - Routing OTP to the general pool fails the isolation tests.
  - Restoring the 8 + 56 general sizing fails the broadcast test (192 of 200 failed).
  - Dropping `ctx.run` fails the contextvar test.
- [x] **Lint.** `ruff check` and `ruff format --check` pass. The pre-commit hook passed on every commit, with one unrelated doc-path warning.
- [ ] **Staging or manual repro:** not done.

## 10. What was NOT verified

- Not tested against real Twilio, and no real DNS stall was reproduced. Hangs and latency were simulated with mocks.
- Pool sizes come from call volume visible in code, not from production SMS rates or Twilio's per-account concurrency limit.
- The Sentry span-parenting benefit was reasoned about. Only contextvar propagation was tested, not a real Sentry transaction.
- Review: the coordinator ran `spinr-safety-sos-reviewer` and `spinr-performance-sla-reviewer`, and both returned SAFE TO MERGE. Their nit (SOS domain tag) and should-fixes (metrics, OTP headroom, residual-risk notes) are addressed in `7da6b8e`, `b4260de` and this log. Neither reviewer has re-reviewed those follow-up commits.
- No alert rule or dashboard for the new metrics was created. The alert suggested in section 3 is not wired anywhere.
- The full backend suite was not run.

## 11. Out of scope: moving OTP sending off the request path

Today `/send-otp` waits for Twilio, so a failed send reaches the rider as an immediate 503. Queuing the send in the background would return faster and fully separate the request from Twilio. The cost is that the rider is told "sent" and cannot be told the SMS failed. They would only notice when no code arrives and press resend. This is a UX decision for the owner.
