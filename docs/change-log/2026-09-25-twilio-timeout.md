# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth (also affects `safety`/SOS notifications and `admin` manual SMS, all via the same shared helper) |
| PR / commit link | (branch `claude/fix-twilio-timeout`, not yet pushed/opened) |
| Related issue or gap ID | ROADMAP N8 / finding INT-001 (`docs/audit/clean-sheet/02-findings/integrations.md`) |

## 1. Issue / gap identified

`backend/sms_service.py` builds the Twilio REST client with no HTTP timeout (`Client(twilio_sid, twilio_token)`), so a slow or unresponsive Twilio API can hang the OTP/login SMS path — and every other caller of `send_sms`/`send_otp_sms` (SOS notifications, admin manual SMS, marketing SMS, guest-ride notifications) — for an unbounded amount of time.

## 2. Root cause

The Twilio Python SDK's default `http_client` (`TwilioHttpClient`, backed by `requests`) has no timeout unless one is explicitly passed at construction. `send_sms()` never passed one, so a hung TCP connection or a slow Twilio response blocks the worker thread the call runs on (`asyncio.to_thread`) indefinitely, and — because the awaiting coroutine has no bound either — the caller (e.g. the OTP send in `routes/auth.py`) hangs with it, tying up an event-loop task and a thread-pool slot for as long as the underlying socket stays open.

## 3. Fix / remediation

- `send_sms()` now builds the Twilio client with an explicit `TwilioHttpClient(timeout=_TWILIO_HTTP_TIMEOUT_S)` (10.0s default — a sensible bound for a synchronous OTP/login/SOS SMS send).
- The `asyncio.to_thread(_send)` call is now wrapped in `asyncio.wait_for(..., timeout=_TWILIO_THREAD_TIMEOUT_S)` (15.0s = HTTP timeout + 5s buffer) as a backstop, so even if the HTTP-level timeout somehow doesn't fire (e.g. a hang before the socket timeout applies), the awaiting coroutine — and therefore the caller — is unblocked within a bounded window.
- On timeout, the existing `except Exception as e:` handler already catches the resulting `TimeoutError` (a built-in `Exception` subclass), logs it via `logger.error(...)` with the exception type (no PII, consistent with the existing PIPEDA-safe error contract), and returns the same `{"success": False, "provider": "twilio", "error": ...}` shape every caller already handles. No new fallback path was added — a timeout is treated exactly like any other Twilio send failure.
- Happy path (fast, successful Twilio response) is unchanged: same `Client(...)` call shape, same return value, same log line on success.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), but shared by every SMS caller** — `send_sms`/`send_otp_sms` in `backend/sms_service.py` are the sole Twilio entry point; all callers found by grep:

| Caller | Path |
|---|---|
| `routes/auth.py:513` | `send_otp_sms` — login/signup OTP |
| `routes/rides/safety.py:414, :854` (via `_deps.send_sms`) | SOS emergency SMS notifications |
| `utils/sos_contact_notice.py:49` | SOS emergency-contact SMS |
| `services/guest_notification_service.py:69` | Guest-ride SMS notifications |
| `routes/admin/messaging.py:206` | Admin-triggered manual SMS |
| `utils/marketing_sms.py:72` | Marketing SMS |

All six go through the same two functions, so the fix is centralized — no caller code changes were needed, and none was made. Every caller already branches on `result["success"]` and handles a `False` result as a normal send failure (this was already the contract for a Twilio 4xx/5xx; a timeout now uses the identical path, so no caller needed new handling).

- **No regression to the happy path**: successful sends are unaffected (same client construction pattern, just with a `http_client` kwarg added).
- **Behavior change**: a Twilio hang, which previously blocked forever, now fails after ~10-15s instead. This is strictly an improvement (bounded failure vs. unbounded hang) but it is a *new* failure mode observable in practice — a caller that was implicitly relying on "SMS send always eventually returns" will now see a timeout-shaped failure under a slow-Twilio condition, exactly as it already sees other Twilio failures.
- **Known limitation, not a regression**: `asyncio.wait_for` unblocks the *awaiting coroutine* once the timeout fires, but it does not forcibly kill the underlying OS thread running `_send()` — Python's `asyncio.to_thread` threads aren't cancellable. In practice the HTTP-level `TwilioHttpClient(timeout=10.0)` should make the underlying `requests` call itself raise (and the thread exit) within ~10s in the overwhelming majority of cases; the 15s `asyncio.wait_for` backstop exists for the narrow case where that doesn't happen. Before this fix there was no bound on either layer, so this is a strict improvement, not a new risk.
- No other table, state field, or background loop reads/writes anything touched by this change — it's confined to how the Twilio HTTP client is constructed and how its call is awaited.

## 5. User-experience effect

- Rider/driver: OTP login/signup SMS sends now fail cleanly (existing "SMS failed" UX, whatever the caller already does for `success: False`) after ~10-15s instead of the request hanging indefinitely under a slow-Twilio condition. This only changes behavior when Twilio is already degraded — the normal case is unaffected.
- Not visible mid-session to anyone already using the app; this only affects the SMS-send call path itself.
- No copy/notification text changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/sms_service.py` | Added explicit `TwilioHttpClient(timeout=10.0)` to the Twilio `Client` construction, and wrapped the `asyncio.to_thread(_send)` call in `asyncio.wait_for(..., timeout=15.0)` | Bound the previously-unbounded Twilio HTTP call so a slow/unresponsive Twilio can't hang the OTP/login/SOS SMS path indefinitely (INT-001) |
| `backend/tests/test_sms.py` | Added `test_send_sms_twilio_client_built_with_timeout` and `test_send_sms_twilio_hang_times_out` | Regression coverage proving the client is built with a timeout and that a hung send times out rather than blocking forever |

## 7. Before / after

```python
# Before
def _send() -> str:
    client = Client(twilio_sid, twilio_token)
    return client.messages.create(body=message, from_=twilio_from, to=to_phone).sid

sid = await asyncio.to_thread(_send)
```

```python
# After
def _send() -> str:
    http_client = TwilioHttpClient(timeout=_TWILIO_HTTP_TIMEOUT_S)  # 10.0s
    client = Client(twilio_sid, twilio_token, http_client=http_client)
    return client.messages.create(body=message, from_=twilio_from, to=to_phone).sid

sid = await asyncio.wait_for(asyncio.to_thread(_send), timeout=_TWILIO_THREAD_TIMEOUT_S)  # 15.0s
```

## 8. Rollback plan

No feature flag or DB config gates this — it's a pure code change to how the Twilio HTTP client is constructed, with no schema/data changes and no `app_settings` dependency. Rollback is a straightforward `git revert` of this commit (or redeploy of the prior backend version): it touches no live data (no Stripe charges, no wallet deltas, no ride state, no insurance-period rows), so a code-only revert is sufficient and complete — this is one of the "explicitly state why a git revert is fine" cases the template calls out, since nothing here is applied to live data that would need separate remediation.

## 9. Verification performed

- [x] Automated tests run (unit, mocked Twilio, no network):
  - `pytest tests/test_sms.py tests/test_auth_send_otp.py tests/test_sos_contact_notice.py tests/test_marketing_sms.py` — 43 passed
  - `pytest tests/test_sos_paging.py tests/test_sos_rideless.py tests/test_p2_sos.py tests/test_guest_sms.py tests/test_guest_notification_service_coverage.py tests/test_admin_messaging_coverage.py tests/test_auth.py` — 163 passed
  - `pytest tests/test_loguru_call_conventions.py` — 8 passed (confirms the unchanged `logger.error(f"...")` call in the except block still complies with the loguru-call-convention rules)
- [x] `ruff check backend/sms_service.py backend/tests/test_sms.py` — all checks passed
- [x] `ruff format --check backend/sms_service.py backend/tests/test_sms.py` — already formatted
- [x] Blast-radius grep performed: `send_sms\(|send_otp_sms\(` across `backend/` — all 6 real callers listed in §4, all route through the two functions modified here
- [x] Reviewed against relevant `CLAUDE.md` conventions: error handling ("do not silently swallow errors" — timeout is logged at `error` with the exception, not swallowed or silently downgraded; no generic fallback path added), loguru call conventions (verified by the existing static test), dual-import pattern (unaffected — no import-path changes)
- [ ] Manual repro steps followed in staging — **not done** (see below)
- [ ] Feature-flagged — not applicable; see rollback plan for why a flag wasn't used

**Note on "production build":** this is a `backend/` (Python) change, not `admin-dashboard`/`rider-app`/`driver-app`, so the CLAUDE.md requirement to run a real `npm run build` does not apply here. No frontend build was run or needed.

## 10. What was NOT verified

- **Not tested against a real Twilio account or the network.** All tests mock `twilio.rest.Client` and `twilio.http.http_client.TwilioHttpClient`; no real Twilio API call was made, and no real slow/hanging Twilio endpoint was exercised. The hang scenario is simulated with `time.sleep()` inside a mocked `messages.create`, not a real network stall.
- **Not verified in staging.** No staging deploy or manual OTP-login repro was performed; verification is limited to the mocked unit-test suite listed above.
- **Thread-leak behavior under a real hang was not directly observed.** The claim in §4 that `TwilioHttpClient`'s own socket timeout will make the underlying `requests` call raise (and free the thread) within ~10s in practice is based on how `requests`/`urllib3` timeouts are documented to behave, not on an end-to-end test against a deliberately non-responding server.
- **Concurrency/load behavior not tested** — e.g. what happens to the `asyncio.to_thread` default executor's limited worker pool if many SMS sends hang simultaneously (SOS fan-out) was not load-tested; the fix bounds each individual call but does not add a circuit breaker or backoff across calls.
- Self-review was performed by reading `.claude/agents/spinr-security-auditor.md` and checking this diff against its checklist manually — the `spinr-security-auditor` subagent itself was not invoked (no Agent/Task tool was available in this session). See the PR/commit report for the manual review notes.
