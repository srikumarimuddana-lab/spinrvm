# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "we still the issue with map not displaying in the live monitoring page ? and the setting save in the operation tab of setting section still not working 'validation error'" |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Follow-up to the settings state-wipe fix and the heat-map card silent-no-op fix on the same page (both this session) |

## 1. Issue / gap identified

Two separate live-reported problems, both about **diagnosability**, not (only) the underlying failure:

1. **Settings → Operations tab**: saving after toggling "Enable refreshed admin theme" / "Enable command palette" shows a red "Settings not saved — Validation error" toast with no indication of which field is invalid or why. This happens because the frontend saves the *entire* `settings` object on every save (not just the field the operator touched, per the backend model's own docstring), so a stray invalid value anywhere in that object 422s the whole save — but the toast gave no way to tell which field.
2. **Monitoring → Live Ride Monitoring**: the map/ride list shows "Live data paused — map and ride list may be stale (Backend WebSocket auth error: snapshot_failed)". `snapshot_failed` is not an auth failure at all — it fires when the backend's post-auth `get_drivers_snapshot`/`get_rides_snapshot` DB query throws for any reason — but the frontend hard-coded "auth error" for every WS `{type: "error"}` message regardless of cause, actively misleading whoever tries to diagnose it toward the wrong subsystem (token/auth) instead of the real one (the drivers/rides snapshot query).

## 2. Root cause

1. **Settings validation error**: `backend/utils/error_handling.py`'s `validation_exception_handler` (registered for every `RequestValidationError` in the app, not just this endpoint) returns the generic string `"Validation error"` as `error.message`, with the actual per-field detail — `field`, `message`, `type` for each failing Pydantic field — nested under `error.details.errors`. `admin-dashboard/src/lib/api/client.ts`'s shared `request()` error-message extraction never read that `details.errors` array; it fell through straight to the generic `body.error?.message`, so **every 422 from every save action across the entire admin-dashboard** (not just Settings) has always surfaced as the same unhelpful "Validation error" with zero field-level detail. This made the specific field actually failing on this Settings save impossible to identify from the UI, and equally impossible for me to pin down here without live Supabase/browser access (this sandbox has neither) — the exact invalid field is not yet known and needs the next attempt's now-detailed toast (or a Network-tab check) to confirm.
2. **Monitoring WS mislabeling**: `admin-dashboard/src/hooks/use-monitoring-socket.ts` labeled *every* backend `{type: "error"}` WS message as `` `Backend WebSocket auth error: ${message}` ``, but the backend (`backend/routes/websocket.py`) sends that same message type for many unrelated post-auth failures too — `snapshot_failed`, `not_ride_participant`, `message_too_large`, `invalid_json` — none of which are auth problems. The hook already maintains `FATAL_ERROR_MESSAGES`, an explicit allowlist of the actually-auth-related codes (used to decide whether to stop reconnecting) — it just wasn't also used to decide the *wording*.
3. **Underlying snapshot failure is under-observed**: the backend's `except Exception as _snap_exc` around the drivers/rides DB fetch logged at `logger.warning` and never reported to Sentry — a violation of this repo's own Observability Conventions ("Never `logger.warning(...)` and continue on a DB/auth/payment error... User-visible errors → Sentry + error log"). A DB-read failure that breaks the live map for every connected admin is exactly the class of error that rule exists for, and at `warning` level it likely isn't reliably alerted on, which is consistent with this apparently going unnoticed until reported live.

**What this fix does NOT establish**: the actual reason `fetch_monitoring_drivers()`/`fetch_monitoring_rides()` is throwing in production. I read both functions (`backend/routes/admin/monitoring.py`) end-to-end and found nothing structurally wrong in the query logic itself; this sandbox has no live Supabase connection and no access to production logs/Sentry to see the real exception text, so the underlying trigger (a transient DB error, a schema drift, something else) is still unknown. This PR makes that failure loud and correctly labeled instead of silent and mislabeled — it is a diagnosability fix, not a confirmed root-cause fix, for issue 2.

## 3. Fix / remediation

1. `admin-dashboard/src/lib/api/client.ts`: `request()`'s error-message extraction now reads `body.error?.details?.errors` (when present) and formats each entry as `field: message`, joined with `; `, inserted ahead of the generic `error.message` fallback. Every 422 anywhere in the admin-dashboard now shows the actual invalid field(s) and reason(s) instead of the bare string "Validation error".
2. `admin-dashboard/src/hooks/use-monitoring-socket.ts`: the WS `{type: "error"}` handler now checks `FATAL_ERROR_MESSAGES.has(data.message)` before choosing wording — `"Backend WebSocket auth error: ..."` only for the genuinely auth-related codes already tracked in that set, `"Backend WebSocket error: ..."` for everything else (unchanged: still stops reconnection only for the same fatal set as before).
3. `backend/routes/websocket.py`: the snapshot-fetch exception handler now logs at `logger.error` (was `logger.warning`) and reports to Sentry with `domain=admin`/`surface=backend`/`ws_snapshot_type` tags, best-effort (mirrors the existing `_capture_export_failure` pattern in `routes/admin/compliance.py` — never raises, no-ops if Sentry isn't configured).

## 4. Risk & impact on existing functionality

- **Blast radius**: `client.ts`'s `request()` is the single shared fetch wrapper for the *entire* admin-dashboard — every API call goes through it. The change only touches the `!res.ok` error-message *construction* (an additional fallback tier inserted between two existing ones); the success path, the 401 refresh-retry path, and the 429 `RateLimitError` path are untouched. Grepped for other consumers of the error message shape — none parse `err.message` structurally (they all just display it in a toast), so a longer/differently-worded message cannot break other call sites.
- `use-monitoring-socket.ts`: only the wording of `lastError` and nothing else changes; `FATAL_ERROR_MESSAGES` itself (which still gates reconnection behavior) is unmodified, so reconnect/close semantics are identical to before.
- `routes/websocket.py`: only the log level and the addition of a best-effort, exception-swallowed Sentry call — the actual WS response sent to the client (`{"type": "error", "message": "snapshot_failed"}`) is byte-for-byte unchanged, so no frontend-visible behavior changes there.
- None of these three changes touch ride state, dispatch, payments, or auth logic.

## 5. User-experience effect

Admin-facing only. (1) A 422 anywhere in the admin-dashboard now shows which field was rejected and why, instead of an opaque "Validation error" — this is a diagnosability improvement, not a new failure mode; saves that previously failed still fail (the underlying invalid-field problem in Settings is not yet fixed, only made visible). (2) The Live Ride Monitoring "Live data paused" banner now correctly says "Backend WebSocket error: snapshot_failed" instead of the misleading "...auth error: snapshot_failed" for this and other non-auth error codes — genuinely auth-related errors are unaffected and still say "auth error". Neither change is flag-gated: both are bugfixes to already-shipped, currently-live error paths, not new product surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/client.ts` | `request()`'s `!res.ok` branch: read `body.error.details.errors` and format field-level detail into the thrown error message | Surface which field failed Pydantic validation instead of the generic "Validation error" |
| `admin-dashboard/src/hooks/use-monitoring-socket.ts` | WS `{type: "error"}` handler: wording now keyed off `FATAL_ERROR_MESSAGES.has(data.message)` instead of unconditionally saying "auth error" | Stop mislabeling non-auth WS errors (e.g. `snapshot_failed`) as auth errors |
| `backend/routes/websocket.py` | Snapshot-fetch exception handler: `logger.warning` → `logger.error` + best-effort Sentry capture (`domain=admin`, `surface=backend`) | Match Observability Conventions for a user-visible DB-read failure; make the real underlying exception discoverable |

## 7. Before / after

```ts
// Before — client.ts: generic message only, per-field detail discarded
const msg =
    body.detail ||
    body.error?.detail ||
    body.error?.message ||   // "Validation error" — no field, no reason
    body.message ||
    res.statusText;

// After — reads the field-level detail the backend already sends
const fieldErrors = body.error?.details?.errors;
const fieldErrorMsg = Array.isArray(fieldErrors) && fieldErrors.length > 0
    ? fieldErrors.map((e: any) => `${e.field || "field"}: ${e.message || "invalid value"}`).join("; ")
    : null;
const msg =
    body.detail ||
    fieldErrorMsg ||          // e.g. "notification_quiet_hours_start: string does not match regex"
    body.error?.detail ||
    body.error?.message ||
    body.message ||
    res.statusText;
```

```ts
// Before — use-monitoring-socket.ts: every error is "an auth error"
setLastError(
    data.message ? `Backend WebSocket auth error: ${data.message}` : "Backend WebSocket returned an error.",
);

// After — only genuinely auth-related codes get that wording
const isAuthError = data.message ? FATAL_ERROR_MESSAGES.has(data.message) : false;
setLastError(
    data.message
        ? isAuthError
            ? `Backend WebSocket auth error: ${data.message}`
            : `Backend WebSocket error: ${data.message}`
        : "Backend WebSocket returned an error.",
);
```

```python
# Before — routes/websocket.py
except Exception as _snap_exc:
    logger.warning(f"[WS] snapshot fetch failed for {connection_key}: {_snap_exc}")
    await websocket.send_json({"type": "error", "message": "snapshot_failed"})

# After
except Exception as _snap_exc:
    logger.error(f"[WS] snapshot fetch failed for {connection_key}: {_snap_exc}")
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("domain", "admin")
            scope.set_tag("surface", "backend")
            scope.set_tag("ws_snapshot_type", data["type"])
            sentry_sdk.capture_exception(_snap_exc)
    except Exception as _sentry_err:
        logger.debug(f"[WS] snapshot-failure Sentry capture skipped: {_sentry_err}")
    await websocket.send_json({"type": "error", "message": "snapshot_failed"})
```

## 8. Rollback plan

Plain `git revert` on any of the three changes — no data, no migration, no schema/API contract change (the WS wire message and the HTTP error JSON shape are both unchanged; only how the frontend interprets/labels them, and the backend's log level/telemetry, changed).

## 9. Verification performed

- [x] Read `backend/utils/error_handling.py`'s `validation_exception_handler` end-to-end to confirm the exact response shape (`error.message` = generic string, `error.details.errors` = per-field array) rather than assuming.
- [x] Read `backend/routes/websocket.py` for every `{"type": "error", ...}` send site (11 call sites) to confirm which codes are genuinely auth-related vs. not, and confirmed the frontend's existing `FATAL_ERROR_MESSAGES` set already matches the auth-related subset correctly — reused it rather than inventing a new classification.
- [x] Read `backend/routes/admin/monitoring.py`'s `fetch_monitoring_drivers`/`fetch_monitoring_rides` in full — no structural bug found in the query logic itself; this fix does not claim to have found or fixed the underlying DB-query failure, only made it correctly logged/alertable and correctly labeled to the operator.
- [x] Followed the existing `_capture_export_failure` (`routes/admin/compliance.py`) best-effort Sentry pattern for the new capture, rather than inventing a new one.
- [x] `tsc --noEmit` — clean.
- [x] `eslint` on both changed frontend files — 0 errors; 1 pre-existing warning on an unrelated line (141, `client.ts`, `@next/next/no-location-assign-relative-destination`, in the unrelated session-expiry redirect).
- [x] `python3 -c "import ast; ast.parse(...)"` on `websocket.py` — syntax valid.
- [x] `pytest tests/test_websocket_coverage.py -k snapshot` — 2 passed (no test asserted on the specific log level, so no test needed updating).
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Ran the existing `client.ts` test file (`api-client-csrf-retry.test.ts`) — 3 passed.
- [x] Grepped for other consumers of the WS error message / `request()`'s thrown error to confirm no other code parses these strings structurally (both are display-only).

## What was NOT verified

- **The actual field causing the Settings → Operations 422 is still unknown.** I cross-checked every field this page sends against `SettingsUpdateRequest`'s declared constraints (ranges, patterns, max-lengths) against their known schema/migration defaults and found nothing obviously out-of-range — the live DB row may hold a previously-set value outside those bounds that I cannot see from this sandbox (no live Supabase access). This fix makes that field visible on the next attempt (via the new field-level error message) but does not itself identify or fix it. **Next step once the specific field is known** (from the improved toast, or a shared Network-tab screenshot of the PUT response body): a targeted follow-up fix to either the stored value or the field's constraint.
- **The actual cause of the Monitoring snapshot-fetch exception is still unknown**, for the same reason (no live backend/Supabase/Sentry access from this sandbox). This fix upgrades it from a silently-swallowed warning with a misleading UI label to a correctly-logged, Sentry-reported, correctly-labeled error — the next real occurrence in production will be visible in Sentry with a full traceback, which was not true before this change.
- **No live browser/visual reproduction of either fix** — same standing gap as every other admin-dashboard change this session; verified by direct source reading, not a live repro.
