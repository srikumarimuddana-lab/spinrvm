# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth (OTP), safety (SOS contact SMS), admin (cloud messaging / marketing SMS) — all through `sms_service.py` |
| PR / commit link | branch `claude/fix-twilio-bounded-executor`, commit `2c176e1` (not pushed, no PR yet) |
| Related issue or gap ID | security-auditor SHOULD-FIX on #5784 (follow-up to `2026-09-25-twilio-timeout.md`) |

## 1. Issue / gap identified

After #5784, Twilio sends run as `asyncio.wait_for(asyncio.to_thread(_send), 15.0)`. That puts them on the event loop's **shared default executor**. WebSocket auth, Stripe calls and other `to_thread` users share that pool. During a Twilio or DNS outage, SMS sends (an SOS fan-out, an OTP burst, an admin broadcast of up to 50 concurrent sends) can use up that shared pool and stall unrelated work.

## 2. Root cause

- `TwilioHttpClient(timeout=10.0)` bounds the socket connect and read. It does **not** bound a stalled `getaddrinfo` DNS lookup.
- `asyncio.wait_for` cancels only the awaiting coroutine. The worker thread keeps running.
- So each hung send keeps holding a default-executor thread after its caller has gone. That pool has `min(32, cpu+4)` workers, which is about 5–8 on the backend's small VMs, so a handful of hung sends is enough to exhaust it.

## 3. Fix / remediation

`sms_service.py` now submits Twilio sends to two dedicated pools built with the existing `utils/bounded_executor.BoundedExecutor` (the same primitive `repositories/_base.py` uses for `_DB_EXECUTOR`). The pools are used through `loop.run_in_executor(executor, _send)`.

| Pool | Used by | Workers | Queue | Justification from call volume in code |
|---|---|---|---|---|
| `_OTP_SMS_EXECUTOR` (`spinr-sms-otp`) | `send_otp_sms` only (`routes/auth.py` `/send-otp`) | 4 | 8 | `/send-otp` sends one SMS per request and is rate-limited to 6/min per client. At a sub-second Twilio round trip, 4 workers handle 4+ sends/s (240+/min per replica), far above organic login volume. The queue absorbs a burst. |
| `_SMS_EXECUTOR` (`spinr-sms`) | `send_sms`: SOS (ride and rideless), SOS opt-out notice, guest notices, admin cloud messaging, marketing | 8 | 56 | An SOS fan-out is at most 3 contacts (`MAX_EMERGENCY_CONTACTS = 3`). Admin `_fan_out` runs at most 50 concurrent sends (`Semaphore(50)`). 8 workers is no fewer than the default pool gave these sends on a 1–4 vCPU host. 8 + 56 = 64 slots, so a 50-wide broadcast plus a concurrent SOS still queues instead of being rejected. |

- **When all workers and queue slots are full:** `BoundedExecutor.submit` raises `ExecutorSaturated` synchronously. `send_sms` catches it, logs it at error level with an `sms_pool` bind, and returns `{"success": False, "provider": "twilio", "error": "ExecutorSaturated"}`. Every caller already handles this shape. Nothing queues without limit.
- **When a queued send's 15 s wait times out:** the executor future is cancelled, so the send never runs. Its slot is released when a worker dequeues it (the `BoundedExecutor` contract).
- **Unchanged:** the 10 s HTTP timeout, the 15 s `wait_for`, the console fallback when Twilio is not configured, the PII-free `error` string, and every caller signature.

### SOS vs OTP: shared or separate pools?

The pools are separate. OTP is the only public, unauthenticated SMS trigger, and it can be flooded from many IPs. In a single shared pool, an OTP flood during a slow Twilio could leave an SOS fan-out with `ExecutorSaturated`. With two pools it cannot. The OTP split needs no caller change, because OTP already has its own entry point (`send_otp_sms`).

**Residual, and why it isn't fixed here:** SOS still shares the general pool with admin and marketing broadcasts. `send_sms` cannot tell an SOS send from a broadcast send without a caller-side signal, and this task keeps callers unchanged. With the 64-slot sizing above, one 50-wide broadcast still leaves room for SOS. Two simultaneous large broadcasts during a Twilio slowdown could saturate the pool and reject an SOS SMS. This is no worse than today, where everything shares one default pool with WebSocket auth and Stripe. **Suggested follow-up for the owner:** add a `send_sos_sms` entry point with its own small pool, and switch the two `_deps.send_sms` call sites in `routes/rides/safety.py` to it.

### Alternative considered and rejected

- **An `asyncio.Semaphore` around the existing `to_thread` call.** Rejected because `wait_for` releases the semaphore as soon as it times out, while the hung thread keeps its default-executor slot. It caps concurrent *callers*, not stuck *threads*, which is the actual finding.
- **A plain `ThreadPoolExecutor(max_workers=N)`.** It bounds threads, but its queue is unbounded. Queued closures would pile up behind hung workers, and nothing would fail fast. `BoundedExecutor` bounds both and already exists in the repo.

## 4. Risk & impact on existing functionality

Blast radius: the two functions in `backend/sms_service.py`. The callers were confirmed by `grep -rn "sms_service\|send_sms" backend --include=*.py`:

- `routes/auth.py` → `send_otp_sms` (OTP pool)
- `routes/rides/safety.py` (2 sites, through `routes/rides/_deps.py`, re-exported by `routes/rides/__init__.py`) → `send_sms`
- `utils/sos_contact_notice.py` → `send_sms`
- `services/guest_notification_service.py` → `send_sms`
- `routes/admin/messaging.py` `_send_sms_one` → `send_sms`, or `utils/marketing_sms.py` → `send_sms`

`scripts/preview_notification_templates.py` only names these functions in strings. `utils/notification_throttle.py` only mentions them in a comment.

What could regress:

- **Admin and marketing broadcasts.** Before, sends past the default pool's capacity queued without limit. Now sends past 64 in flight get `ExecutorSaturated` and count toward `failed_count`. One broadcast (at most 50 concurrent) never reaches that limit. Two large broadcasts started together, or one broadcast during a Twilio slowdown, can.
- **Queue time counts toward the 15 s `wait_for`.** At a healthy ~0.5 s per send, a 56-deep queue drains in about 3.5 s. If Twilio slows to about 2 s per send, the tail of a large broadcast times out, and those items are cancelled without being sent.
- **Context variables are no longer copied into the thread.** `asyncio.to_thread` copied them; `run_in_executor` does not. `_send` logs nothing and reads no context variables. A Sentry span opened inside the Twilio HTTP call may lose its parent. That affects observability only.
- **Pool shutdown on thread-creation failure.** If the OS refuses to create a thread (for example a resource limit), `BoundedExecutor.submit` shuts that pool down permanently. After that, every send from that pool returns `success: False` (`RuntimeError`) until the process restarts. `_DB_EXECUTOR` has the same accepted behaviour.
- **No interaction** with the ride state machine, money or wallet paths, insurance periods, or background loops.

## 5. User-experience effect

- **Riders (OTP):** in normal operation, nobody sees a difference. When Twilio is down or slow and more than 12 OTP sends are in flight on one replica, an extra send now fails at once with the existing 503 "Failed to send verification code". Before, it waited up to 15 s and then failed the same way. The copy is unchanged.
- **Riders (SOS):** in normal operation, nothing changes. SOS SMS can no longer be starved by an OTP flood. If SMS does fail, the existing `contacts_notified` count and warning path are unchanged.
- **Internal admin:** during two concurrent large broadcasts or a Twilio slowdown, `failed_count` may include `ExecutorSaturated` rejections that used to queue.
- **Mid-session:** the change takes effect when the process restarts after deploy. There is no client-visible state.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/sms_service.py` | Adds `_OTP_SMS_EXECUTOR` and `_SMS_EXECUTOR` (`BoundedExecutor`). `send_sms` and `send_otp_sms` delegate to a new `_send_sms_on(executor, ...)`, which uses `loop.run_in_executor`. `ExecutorSaturated` gets its own fail-fast branch. | Keep Twilio outages off the shared default executor, and keep OTP floods away from SOS |
| `backend/tests/test_sms.py` | Adds 3 tests in `TestSMSBoundedExecutor` | Regression coverage (section 9) |
| `docs/change-log/2026-09-25-twilio-bounded-executor.md` | This file | CLAUDE.md gate |

## 7. Before / after

```python
# Before
sid = await asyncio.wait_for(asyncio.to_thread(_send), timeout=_TWILIO_THREAD_TIMEOUT_S)
...
async def send_otp_sms(...):
    return await send_sms(phone, message, ...)
```

```python
# After
loop = asyncio.get_running_loop()
sid = await asyncio.wait_for(loop.run_in_executor(executor, _send), timeout=_TWILIO_THREAD_TIMEOUT_S)
...
except ExecutorSaturated:
    ...
    return {"success": False, "provider": "twilio", "error": "ExecutorSaturated"}
...
async def send_otp_sms(...):
    return await _send_sms_on(_OTP_SMS_EXECUTOR, phone, message, ...)
```

## 8. Rollback plan

There is no feature flag. The pool sizes are code constants, not `app_settings` values, because the executors are built when the module is imported. Rollback is a redeploy of the parent commit (`git revert 2c176e1`). That is acceptable here because the change is backend-only, keeps no state, and writes nothing to live data. No SMS, Stripe, wallet or ride row depends on which pool ran a send.

## 9. Verification performed

- [x] **Automated tests.** `test_sms.py` (18, including 3 new), `test_auth_send_otp.py`, `test_sos_contact_notice.py`, `test_marketing_sms.py`, `test_p2_sos.py`, `test_sos_rideless.py`, `test_e2e_sos_flow.py`, `test_sos_paging.py`, `test_guest_sms.py`, `test_guest_notification_service_coverage.py`, `test_admin_messaging_coverage.py`, `test_messaging_fan_out.py`, `test_loguru_call_conventions.py` and `test_bounded_executor.py`: **206 passed** (203 before the change).
- [x] **New tests:**
  1. Sends run on `spinr-sms_*` and `spinr-sms-otp_*` threads, not the default `asyncio_*` threads.
  2. With the general pool filled by 64 hung sends, the 65th returns `ExecutorSaturated` in under 50 ms. Only 8 threads are pinned, and a default-executor `to_thread` probe still completes.
  3. With the OTP pool saturated, a 3-contact SOS fan-out through `send_sms` still succeeds.
- [x] **Mutation checks.** Reverting to `asyncio.to_thread` makes all 3 new tests fail. Routing OTP to the general pool makes tests 1 and 3 fail.
- [x] **Lint.** `ruff check` and `ruff format --check` pass. The pre-commit hook passes (11/11, with one unrelated doc-path warning).
- [x] **Blast-radius grep** as listed in section 4.
- [ ] **Staging or manual repro:** not done.
- [ ] **Feature flag:** none. Not user-visible in normal operation, and not practical for a pool built at import time.

## 10. What was NOT verified

- Not run against real Twilio, and no real DNS stall was reproduced. Hangs were simulated with a blocking mock.
- Pool sizes come from code-visible call volume, not production SMS rate data. No SMS rate metrics were examined.
- The broadcast-saturation case (two concurrent large admin broadcasts) was reasoned about, not tested.
- The effect of losing context-variable propagation on Sentry spans was reasoned about, not observed.
- No `spinr-*` reviewer agent was run on the diff: that tool was not available in this session. It should be run before merge.
- The full backend suite was not run, only the suites listed above.

## 11. Out of scope: moving OTP sending off the request path

Today `/send-otp` waits for Twilio, so the rider gets a 503 at once when the send fails. Queuing the send in the background would return faster and fully decouple the request thread from Twilio. The cost is that the rider is told "sent" and cannot be told the SMS failed. They would only find out when no code arrives, and would have to press resend. This is a UX trade-off for the owner to decide.
