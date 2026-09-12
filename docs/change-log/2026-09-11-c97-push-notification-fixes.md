# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (session `claude/c97-push-notification-fixes`) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | see PR opened from branch `claude/c97-push-notification-fixes` |
| Related issue or gap ID | ACTION_ITEMS.md C97 |

## 1. Issue / gap identified

C97: driver-app push notifications reported "not visible," a long-standing user complaint. The
2026-09-10 audit (`docs/audit/2026-09-10-driver-app-notification-delivery-audit.md`) found two
independent, compounding root causes and six ranked recommendations. This entry covers this
session's slice of that work: recommendation #2 (make the backend's Firebase Admin SDK init
failure loud) and recommendation #4 (driver-app fallback for unhandled FCM message types in the
background/killed handler). **Recommendations #3 (delivery-outcome metric) and #4's foreground
half were found ALREADY SHIPPED on `main`** by other sessions before this one started — see
"What this session actually changed" below; this doc does not re-claim that work, only records
that it was independently re-verified.

## 2. Root cause

- **#2 (backend):** `core/security.py`'s Firebase Admin SDK init, on the no-`FIREBASE_SERVICE_ACCOUNT_JSON`
  / Application Default Credentials fallback path, used to be a bare `except Exception: pass` —
  zero log, no metric, no Sentry event. A prior session (before this one) had already replaced
  that with a loud `logger.error(..., exc_info=True)` at both failure sites. What was still
  missing: that stdlib-`logging`-based error is auto-forwarded to Sentry by `server.py`'s
  `LoggingIntegration(event_level="ERROR", ...)`, but arrives **untagged by `domain`** — the
  helper that lifts `domain`/`surface` context into Sentry tags (`tags_from_log_extra`) is wired
  only to the loguru→Sentry bridge (`server.py:676`, `utils/sentry_runtime.py:55`), not to stdlib
  `logging`'s `extra=`. So this module's events were the one Sentry-integration-covered module in
  the codebase NOT triageable by domain like every other capture site
  (`utils/driver_statement_pdf.py`, `ai/orchestrator.py`, `utils/outbox_worker.py`, etc.).
- **#4 (driver-app, background/killed half):** `backgroundMessaging.ts`'s background message
  handler only branches on `new_ride_assignment` / `ride_cancelled` / `location_health`; every
  other data-only FCM type falls through with a bare `return` and no trace. A prior session's
  investigation (documented in ACTION_ITEMS.md's C97 entry, "Correction" section) established
  this is **not** currently a live bug for background/killed state: per `backend/features.py`'s
  `is_data_only` gate, only `new_ride_assignment`/`live_activity` are ever sent data-only —
  every other type carries a real FCM `notification` block that Android/iOS auto-display with
  zero app code running, foreground, background, or killed. The gap is **forward-looking only**:
  if a future push type is ever added to `is_data_only` without its own background display path,
  it would be silently invisible today with nothing catching the mismatch.

## 3. Fix / remediation

- **#2:** Added `core/security.py::_report_firebase_init_failure()`, called from both existing
  failure sites (the inner ADC-fallback catch and the outer catch-all), doing a lazily-imported
  `sentry_sdk.capture_exception(tags={"domain": "drivers", "surface": "backend"})` — same
  lazy-import-inside-try/except shape as `utils/driver_statement_pdf.py`'s existing capture site,
  so a Sentry-capture failure itself can never be the reason Firebase init fails to complete (falls
  back to a `logger.debug`). `env`/`environment` is not passed explicitly — it's already the
  global `environment=settings.ENV` set once on `sentry_sdk.init(...)` in `server.py`, not a
  per-event tag in this codebase's convention.
- **#4:** Added a log-only fallback branch in `backgroundMessaging.ts`'s message handler for any
  `data.type` outside the three explicitly-handled ones. Deliberately does **not** call
  `displayRideOfferNotification` or schedule any notification of its own — doing so for a type the
  OS already auto-displays (every non-data-only type) would produce a **duplicate** notification,
  not close a gap. It exists solely so a future data-only type added without its own background
  display path leaves a trace (`console.warn`) instead of vanishing silently — the exact
  "un-actioned, forward-looking risk" ACTION_ITEMS.md's C97 entry already names.
- **What this session actually changed vs. found already shipped:**
  - Recommendation #2's *logging* half — already shipped (prior session). This session added the
    *Sentry-tagging* half only.
  - Recommendation #3 (`spinr_push_send_total{outcome=...}` metric) — already shipped in full
    (`backend/features.py::_record_push_outcome`, called at all 9 outcome points in
    `_send_expo_push`/`_deliver_push_now`; see `docs/change-log/2026-09-11-push-send-outcome-metric.md`).
    Re-verified directly by reading the current code; no changes made.
  - Recommendation #4's **foreground** half (`useDriverDashboard.ts`) — already shipped in full
    (PR #5160, 2026-09-09), re-verified directly by reading the current code. No changes made.
  - Recommendation #4's **background/killed** half (`backgroundMessaging.ts`) — this session's
    actual new code (the log-only fallback above). ACTION_ITEMS.md's C97 entry had already
    reasoned through this and *deliberately* left it unchanged for the reason given above; this
    session's fix is additive to that reasoning (a trace for the forward-looking risk), not a
    reversal of the prior decision not to add a display path.
  - Recommendation #6 (`expo-notifications` dead-code cleanup) — already shipped by a separate,
    parallel session (see `docs/change-log/2026-09-11-c97-rec6-fcm-tap-routing.md`). Not touched
    here, per this task's own scope (left as a note-only recommendation originally; superseded by
    that other session's actual fix).
  - Recommendations #1 (ops check on Fly/Railway Firebase credential) and #5 (iOS
    `UIBackgroundModes` confirmation against a real build) remain open — both need access this
    session does not have (tracked as C99 for #1; #5 needs a compiled iOS artifact).

## 4. Risk & impact on existing functionality

- **`core/security.py::init_firebase()`** is called only from `server.py:157` (app startup) and
  `worker.py:113` (background worker process startup). Both call sites' own tests
  (`test_worker_app.py`) mock `init_firebase` itself and are unaffected by this internal change.
  No other caller of the new private `_report_firebase_init_failure()` helper exists (grepped
  the whole `backend/` tree — zero hits outside `security.py` and its test file).
- **Blast radius, backend:** isolated to `core/security.py`. The change is purely additive (a new
  private function, two new call sites inside existing except blocks); no existing log message,
  return value, or control flow changed. Firebase init still never raises past `init_firebase()` —
  verified by the existing `# must not raise` test assertions, all still passing.
- **`backgroundMessaging.ts`'s `registerBackgroundMessageHandlers()`** is called once, from
  `driver-app/index.js`'s headless-JS registration path (grepped: single call site, confirmed via
  `grep -rn registerBackgroundMessageHandlers driver-app --include=*.ts --include=*.tsx --include=*.js`)
  — not imported anywhere else. The new branch is inserted between the existing
  `location_health` check and the pre-existing `new_ride_assignment` guard, as a pure addition: it
  only intercepts messages whose `type` is present and is not `new_ride_assignment` — a message
  with no `type` at all still falls through to the original guard unchanged (still silently
  returns, exactly as before this change). The three existing explicit branches
  (`ride_cancelled`, `location_health`, `new_ride_assignment`) are byte-for-byte unchanged.
- **Blast radius, driver-app:** isolated to `backgroundMessaging.ts`'s background message handler.
  No change to the foreground handler (`useDriverDashboard.ts`, already fixed by a prior PR and
  untouched here), no change to `notifeeService.ts`, no change to the Notifee background-event
  (accept/decline) routing later in the same file.
- **Shared/cross-surface:** neither change touches a shared component, hook, or utility consumed
  by 3+ pages/screens — `core/security.py` is backend-only infra init, and the new
  `backgroundMessaging.ts` branch is local to that one file's single registered handler.

## 5. User-experience effect

- **Backend Sentry-tagging fix:** no user-facing effect at all — purely an internal-admin/on-call
  observability improvement (Firebase init failures are now triageable by `domain=drivers` in
  Sentry instead of being lumped under an untagged bucket). Not visible to any rider, driver,
  corporate admin, or internal admin inside the product itself.
- **Driver-app background fallback:** no visible UI change for drivers today, because no push
  type currently reaches this new branch in a live-traffic sense the way the code is configured
  right now (every currently-shipped type is either one of the three explicitly handled ones, or a
  non-data-only type the OS already displays on its own). The change is a safety net for a type
  that does not exist yet, not a behavior change for any type that does.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/security.py` | Added `_report_firebase_init_failure()`; called from both existing Firebase-init failure except blocks | Tag the already-loud log failure with `domain=drivers`/`surface=backend` in Sentry (C97 #2) |
| `backend/tests/test_core_security_coverage.py` | Added `fake_sentry_sdk` fixture; extended 4 existing tests + 1 new test to assert the tagged capture fires (or doesn't, on the silent `ValueError` path) and that a Sentry-capture failure itself is swallowed | Cover the new behavior |
| `driver-app/services/backgroundMessaging.ts` | Added a log-only fallback branch for `data.type` outside the 3 explicitly-handled types | Close the "silently invisible with zero trace" forward-looking risk (C97 #4, background half) |
| `driver-app/__tests__/services/backgroundMessaging.android.test.ts` | Added 2 tests: unhandled data-only type logs and displays nothing; a typeless message still triggers no new log | Cover the new branch |
| `docs/change-log/2026-09-11-c97-push-notification-fixes.md` | This file | Mandatory Change Impact Log for a live-tested surface (driver-app, dispatch-adjacent) |
| `ACTION_ITEMS.md` | C97 entry: added a 2026-09-11 status line | Record which of the 6 recommendations are addressed vs. still open |

## 7. Before / after

**`backend/core/security.py`** (the ADC-fallback branch; the outer catch-all is symmetric):

```python
# Before
except Exception:
    logger.error(
        "Firebase initialization failed (no FIREBASE_SERVICE_ACCOUNT_JSON "
        "set, and Application Default Credentials are also unavailable) — "
        "all FCM pushes will be silently dropped",
        exc_info=True,
    )
```

```python
# After
except Exception:
    logger.error(
        "Firebase initialization failed (no FIREBASE_SERVICE_ACCOUNT_JSON "
        "set, and Application Default Credentials are also unavailable) — "
        "all FCM pushes will be silently dropped",
        exc_info=True,
    )
    _report_firebase_init_failure()
```

**`driver-app/services/backgroundMessaging.ts`**:

```ts
// Before
if (data?.type !== 'new_ride_assignment' || !data?.ride_id) return;
```

```ts
// After
if (data?.type && data.type !== 'new_ride_assignment') {
  console.warn('[Push] Unhandled background/killed FCM data-only message type (no display path here):', data.type);
  return;
}

if (data?.type !== 'new_ride_assignment' || !data?.ride_id) return;
```

## 8. Rollback plan

- **Backend:** `git revert` the commit. No live data, migration, or feature flag involved — the
  change adds a Sentry capture call only, nothing that mutates persisted state or affects any
  request/response shape. A revert takes effect on next deploy with zero data-level cleanup
  needed (this is exactly the case CLAUDE.md's rollback-plan rule calls "acceptable... for
  genuinely isolated, low-risk changes" — no Stripe charge, wallet delta, or ride-state row is
  ever touched by this code path).
- **Driver-app:** `git revert` the commit and ship a new build. No feature flag was added (not
  warranted — this is a log-only addition with no user-visible behavior today, per the UX section
  above; CLAUDE.md's flagging rule is for user-visible/non-trivial changes, and this qualifies as
  neither). No migration or data involved either. Because mobile changes need a new build to
  reach devices (no OTA-only toggle in play here), rollback realistically means shipping a
  follow-up build rather than an instant flip — acceptable given the change carries zero live-user
  risk (it cannot fire for any push type this backend sends today).

## 9. Verification performed

- [x] Automated tests run — unit only:
  - Backend: `cd backend && python3 -m pytest tests/test_core_security_coverage.py -q --no-cov` →
    9 passed (see full output below). Also ran `tests/test_loguru_call_conventions.py` as a sanity
    check (unaffected, since `security.py` uses stdlib `logging`, not loguru) → 8 passed.
  - `ruff check` and `ruff format --check` on both touched backend files → clean.
  - driver-app: `node_modules` had to be installed from scratch (none were present in this
    sandbox) via `yarn install --frozen-lockfile --ignore-scripts` (42.8s, exit 0). Then:
    `npx jest __tests__/services/backgroundMessaging.android.test.ts` → 20 passed (18 pre-existing
    + 2 new); `npx jest __tests__/services/backgroundMessaging.test.ts` (the iOS-focused sibling
    file) → 8 passed, unaffected; full suite `npx jest` → **141 test suites, 1599 tests, all
    passing** — no regression anywhere in the app from this change. `npx eslint
    services/backgroundMessaging.ts __tests__/services/backgroundMessaging.android.test.ts` → 0
    errors (4 pre-existing `no-require-imports` warnings, same style already used elsewhere in
    this test file, one of which is this change's own new test following that same convention).
    `npx tsc --noEmit -p tsconfig.json` (whole-project type check) → clean, 0 errors.
- [ ] Manual repro steps followed in staging — NOT done (no staging access from this session).
- [x] Blast-radius grep performed — see section 4 above for exact greps run (`init_firebase`
  callers, `registerBackgroundMessageHandlers` callers, `_report_firebase_init_failure` callers).
- [x] Reviewed against relevant CLAUDE.md conventions: Observability Conventions (stdlib logging
  vs. loguru API — `security.py` correctly uses `logging.getLogger`/`exc_info=`, not loguru's
  `.bind()`/`.opt()`; verified via the existing loguru-convention static-scan test still passing),
  "Do not silently swallow errors" (both changes exist specifically to stop something from being
  silently swallowed further than it already was).
- [ ] Feature-flagged if user-visible and non-trivial — not applicable; neither change is
  user-visible (see section 5).
- **No `npm run build` / production Expo/EAS build was run for driver-app** — only `tsc --noEmit`
  and the Jest suite, which CLAUDE.md's own convention says is explicitly not equivalent to a real
  build. See "What was NOT verified" below for the exact boundary.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` + redeploy/rebuild for both
  changes; no live data ever touched).
- [x] Blast radius is stated, not assumed — exact greps and their results are listed in section 4.
- [x] No silent behavior change to an already-shipped flow — both changes are additive; the UX
  field above is filled in for both (no visible effect on any currently-shipped notification type).

## What was NOT verified

- **Whether Sentry is actually receiving these new tagged events in production** — needs a real
  `SENTRY_DSN` and a live Firebase-init failure to trigger it; not reproducible from this sandbox.
- **`driver-app`: no real production build was run** (`expo export`/EAS build) — the full Jest
  suite (1599 tests) and a whole-project `tsc --noEmit` both pass clean, but per CLAUDE.md's own
  rule neither is equivalent to a real Expo/EAS build, and no such build was attempted or is
  available in this environment.
- **No real device / no compiled iOS or Android build** — the background-handler fallback's
  actual on-device behavior (that the OS truly does not show a duplicate notification for a
  non-data-only type, and that the new `console.warn` is genuinely inert otherwise) rests on
  reading `backend/features.py`'s `is_data_only` gate and this file's own prior, already-merged
  reasoning (ACTION_ITEMS.md's C97 entry) rather than an on-device screenshot or log capture.
- **No visual regression tooling exists for driver-app** (per CLAUDE.md's pre-merge gate #6) — not
  applicable here regardless, since neither change renders anything new on screen.
- **Live Fly/Railway Firebase credential state** — still unconfirmed (recommendation #1, tracked
  separately as C99). This session's fix makes a future failure loud and Sentry-tagged; it does
  not tell you whether such a failure is happening right now.
- **iOS `UIBackgroundModes` gap** (recommendation #5) — still unconfirmed, needs a real compiled
  iOS build not available in this session.
