# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (see `claude/b15-sos-db-fallback` branch) |
| Related issue or gap ID | ACTION_ITEMS.md B15 |

## 1. Issue / gap identified

`trigger_emergency` (`backend/routes/rides/safety.py` — the rider/driver in-ride SOS endpoint) called `db_supabase.insert_one("safety_incidents", incident)` with no surrounding `try`/`except`, unlike its sibling endpoint `backend/routes/safety.py`'s `submit_safety_report` (`POST /safety/report`), which wraps the identical-purpose insert and returns a clean 503.

## 2. Root cause

Not a design decision — an inconsistency introduced when the rider SOS path was consolidated onto `safety_incidents` (migration 94, per the code's own comment). The sibling endpoint already had its own try/except from an earlier fix; `trigger_emergency` was never brought in line with it.

## 3. Fix / remediation

Wrapped the `insert_one` call in a `try`/`except` that logs the full exception (`logger.error(..., exc_info=True)`) and raises `HTTPException(503, ...)`, mirroring `backend/routes/safety.py`'s exact pattern.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for all callers of `trigger_emergency` — it's only invoked via the route registration in `backend/routes/rides/__init__.py` (import/re-export, not a second call site). No other backend code calls it directly.
- **Client callers:** `shared/api/client.ts`'s `triggerEmergency`, used by `rider-app/store/rideStore.ts` and `driver-app/app/driver/(tabs)/index.tsx` (via `shared/components/SOSButton.tsx`'s `onTrigger` prop) — the single shared client used by both apps.
- **Behavior before this fix, on a DB failure:** the unhandled exception already aborted the function before any of the downstream steps (admin WS broadcast, `notify_safety_team`, contact SMS loop) ran — Python execution stops at the raise point either way. FastAPI's default handler converted the unhandled exception to a generic 500. So downstream notifications were **already** skipped on a DB failure before this change; this fix does not newly gate anything that used to fire.
- **What actually changes:** (1) a domain-specific, PIPEDA-safe structured log line (`ride_id`, `user_id`, exception type — no PII) now exists for this failure mode where previously only a generic unhandled-exception traceback existed; (2) the client receives a 503 instead of a 500. `SOSButton.tsx`'s retry loop (`shared/components/SOSButton.tsx`) catches *any* thrown error from `onTrigger` in a bare `try {} catch {}` inside its 3-attempt retry (1s/2s backoff), so both status codes were already treated identically by the client — this fix doesn't change client-visible retry behavior, it makes the server-side failure mode observable and brings the endpoint in line with the sibling pattern and CLAUDE.md's "Do not silently swallow errors" convention.
- No other table, background loop, or money/wallet path is touched.

## 5. User-experience effect

None, visible or otherwise. The client already retries identically on any thrown error (500 or 503) and already shows its persistent "Alert Not Sent — Call 911 directly, tap to retry" state once all 3 attempts are exhausted (`SOSButton.tsx`'s `showFailureAlert`). This fix changes only what's observable server-side (logs) and the HTTP status code, not what the rider or driver sees.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/safety.py` | Wrapped the `safety_incidents` insert in `trigger_emergency` with `try`/`except` → `logger.error(exc_info=True)` + `raise HTTPException(503, ...)` | Bring the rider/driver SOS insert in line with its sibling (`routes/safety.py`'s `submit_safety_report`) and CLAUDE.md's error-surfacing convention |
| `backend/tests/test_p2_sos.py` | Added `test_db_insert_failure_returns_503_not_silent_500` | Regression coverage: pins the 503 status and that admin WS notification does not fire for an incident that was never persisted |

## 7. Before / after

```python
# Before
await _deps.db_supabase.insert_one("safety_incidents", incident)

# Notify admin dashboard via WebSocket. ...
```

```python
# After
try:
    await _deps.db_supabase.insert_one("safety_incidents", incident)
except Exception as exc:
    logger.error(
        f"[SOS] Failed to persist emergency incident ride_id={ride_id} user_id={current_user['id']}: {exc}",
        exc_info=True,
    )
    raise HTTPException(
        status_code=503, detail="Unable to send emergency alert. Please try again or call 911."
    ) from exc

# Notify admin dashboard via WebSocket. ...
```

## 8. Rollback plan

`git revert` is sufficient and complete — this is a pure code change with no data migration, no `app_settings` flag, and no state written anywhere that would need cleanup. Reverting restores the exact prior (unhandled-500) behavior.

## 9. Verification performed

- [x] Automated tests run (unit) — `backend/tests/test_p2_sos.py::TestTriggerEmergency` (new `test_db_insert_failure_returns_503_not_silent_500` plus the full existing class, all mocked via `mock_supabase_client`-style patches, no live Supabase).
- [ ] Manual repro steps followed in staging — not performed; no staging environment available in this session.
- [x] Blast-radius grep performed — see section 4 (searched for all `trigger_emergency` callers backend-side and all `/emergency` references client-side).
- [x] Reviewed against relevant CLAUDE.md convention(s) — "Do not silently swallow errors" (DB/auth/payment/dispatch errors must surface loudly); this fix directly implements that convention for a previously-noncompliant endpoint.
- [ ] Feature-flagged — not applicable; this is strictly additive error-handling around an existing call, not a behavior change a flag would gate, and per CLAUDE.md's own guidance a feature flag is for user-visible/non-trivial UX changes, which this is not (see section 5).

**What was NOT verified:** not tested against a live Supabase instance or a real deployed backend — only mocked `db_supabase.insert_one` raising `RuntimeError` in a unit test. No visual/E2E verification of `SOSButton.tsx`'s client-side retry UI against this specific backend change (reasoned about via reading the client code, not screenshotted or run end-to-end).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated, not assumed (grep performed, results listed above).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (section 5 explicitly states: no client-visible change).
