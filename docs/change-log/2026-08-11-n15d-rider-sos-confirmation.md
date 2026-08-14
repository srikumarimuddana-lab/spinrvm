# Change Impact & Risk Log — N15/R38: rider SOS confirmation push

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (session claude/n15d-rider-sos-confirmation) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| Related issue or gap ID | ACTION_ITEMS.md N15 (R38 sub-item) |

## 1. Issue / gap identified

`trigger_emergency` (`backend/routes/rides/safety.py`) notifies the admin
dashboard (WS), the safety-team email list, on-call paging, and the rider's
emergency contacts (SMS) — but sends the triggering user themselves no
confirmation beyond the synchronous HTTP 200 response.

## 2. Root cause

The endpoint was built outward-facing (notify everyone *else* who needs to
act) and never grew a loop back to the caller. The synchronous HTTP response
does reach `SOSButton.tsx`, which shows an "Alert Sent" dialog — but only
while the app is foregrounded and the request completes normally. A
backgrounded or killed app after the tap, or a request that the client gave
up retrying, gets a fired API call with no client-visible confirmation of
what happened next.

## 3. Fix / remediation

Added one additive fire-and-forget push call — `_deps.spawn(_deps.send_push_notification(...))`
— to the end of `trigger_emergency`'s existing notify block, addressed to
`current_user["id"]` (the triggering rider or driver), `priority="safety"`
(one of the three guaranteed-delivery tiers per `features.py::send_push_notification`'s
own docstring — bypasses the push opt-out, falls back to the push-retry queue
on transient failure), `target_app="rider" if is_rider else "driver"`.
Wrapped in the same self-swallowing `try/except Exception: logger.error(...)`
pattern as every other side effect in this function — a failure here can
never turn into a 500 or block the SMS loop after it.

Copy: "SOS Alert Received" / "Your emergency alert reached our safety team
and emergency contacts. If you're in immediate danger, call 911." —
deliberately confirms receipt only, never claims help is guaranteed or that
this replaces 911 (`CLAUDE.md` → "What Spinr Is NOT" → "Not a 911
replacement"), matching the phrasing already required by
`.claude/context/domain-safety.md` ("We'll alert your emergency contacts and
our safety team").

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** `trigger_emergency` has exactly one call site —
the `POST /{ride_id}/emergency` route itself, mounted once from
`backend/routes/rides/__init__.py`. Grepped the whole backend for other
callers/importers of the function name: none found outside test files
(`test_p2_sos.py`, `test_e2e_sos_flow.py`, `test_coverage_rides.py`,
`test_sos_expired_token.py`, `test_rate_limit_user_keying.py`,
`test_rate_limit_metric_cardinality.py` — the last three exercise routing/
rate-limit/token behavior around the endpoint but never call the function
body directly, so they're unaffected). No other route, background loop, or
service imports or calls `trigger_emergency`.

- **No existing SOS behavior changed.** The DB insert, its try/except and
  503 response, the admin WS broadcast, `notify_safety_team`, `page_sos_on_call`,
  and the emergency-contact SMS loop are all untouched — the new block is
  inserted between the paging call and the SMS loop, and touches none of
  their state or control flow.
- **`send_push_notification` is a shared function** (used by dispatch offers,
  ride-cancellation pushes, account-status pushes, marketing pushes, etc.) —
  this change adds one more caller, it does not modify the function itself.
  Per the task's explicit boundary, `send_push_notification`'s own default
  `target_app` behavior was not touched.
- **`push_retry_queue`'s `priority` CHECK constraint already allows
  `'safety'`** (migration 76, reconfirmed by migration 272) — no migration
  needed.
- Fire-and-forget via `spawn()`: if the push fails (bad token, FCM down),
  it's caught internally by `send_push_notification`'s own guaranteed-delivery
  fallback (push_retry_queue) and/or logged — it can never raise back into
  the SOS response, matching the existing pattern for `notify_safety_team`
  and `page_sos_on_call` in the same function.

## 5. User-experience effect

Rider- and driver-facing. A user who triggers SOS now receives a push
notification confirming their alert reached the safety team and emergency
contacts, in addition to the existing in-app "Alert Sent" dialog shown
synchronously by `SOSButton.tsx` on a 200 response. This is additive only —
no existing SOS UI, copy, or flow was changed. Not visible mid-session to
anyone except the person who just triggered SOS (by design — this is a
personal confirmation, not a broadcast).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/rides/safety.py` | Added one `_deps.spawn(_deps.send_push_notification(...))` call (self-swallowing try/except) after the existing on-call paging block in `trigger_emergency` | Close the rider/driver-facing confirmation gap (R38) |
| `backend/tests/test_p2_sos.py` | Added `test_rider_sos_confirmation_push_sent_to_triggering_rider` and `test_driver_sos_confirmation_push_targets_driver_app` | Prove the confirmation push fires for both roles with the right target_app/priority/copy |

## 7. Before/after snippet

Before: `trigger_emergency` ended its notify block with on-call paging, then
went straight to the emergency-contact SMS loop — no push to the caller
anywhere in the function.

After:

```python
    try:
        _deps.spawn(
            _deps.send_push_notification(
                current_user["id"],
                "SOS Alert Received",
                "Your emergency alert reached our safety team and emergency contacts. "
                "If you're in immediate danger, call 911.",
                data={"type": "sos_confirmation", "ride_id": str(ride_id), "incident_id": incident["id"]},
                priority="safety",
                target_app="rider" if is_rider else "driver",
            )
        )
    except Exception:  # pragma: no cover - best effort, never block the SMS path below
        logger.error(
            f"SOS confirmation push failed to spawn for ride={ride_id} incident={incident['id']}",
            exc_info=True,
        )
```

## 8. Rollback plan

Pure code revert — no migration, no data written by this change, no flag
introduced. `git revert` the commit (or delete the added block) restores the
prior behavior exactly; nothing downstream depends on the new push firing.

## 9. Verification performed

- `pytest backend/tests/test_p2_sos.py backend/tests/test_e2e_sos_flow.py backend/tests/test_coverage_rides.py -k "trigger_emergency or SOS or Emergency"` — 26 passed.
- `pytest backend/tests/test_sos_paging.py backend/tests/test_sos_expired_token.py backend/tests/test_rate_limit_user_keying.py backend/tests/test_rate_limit_metric_cardinality.py` — 49 passed (confirms the surrounding SOS/rate-limit surfaces are unaffected).
- `pytest backend/tests/test_p3_push_notifications.py` — 33 passed (confirms no regression in the shared push helper's own test suite).
- `ruff check` and `ruff format --check` on both changed files — clean.
- This is a backend-only change; no `admin-dashboard`/`rider-app`/`driver-app` build was needed or run.

## 10. What was NOT verified

- Not tested against a live Supabase, real FCM/Expo push provider, or real
  device — all DB/push calls are mocked in the unit-level tests above.
- No manual/staging repro of an actual SOS trigger with a real device
  receiving the push.
- No visual regression tooling exists in this repo for the client-side push
  banner; this is a backend-only change and does not touch `SOSButton.tsx`
  or any client code, so no client rendering was checked.
