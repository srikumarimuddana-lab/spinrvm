# Next-Session Handoff — P1 E2E Hardening

**Read this first.** This document is a self-contained brief for a new
Claude Code session picking up the E2E hardening track. The prior
session shipped P0-1 through P0-5 and left P1+ open.

---

## Task

Close the **P1 gaps** from `docs/E2E_TEST_GAP_ANALYSIS.md` §4, in the
order listed there, following the test-first + small-refactor pattern
established by the P0 work. Once P1 is done, move to P2 on the same
pattern. P3 (native mobile E2E) is explicitly deferred.

---

## Branches — which one to work on

Two active branches, kept in parallel:

| Branch | Purpose | Status |
|---|---|---|
| `claude/plan-e2e-testing-SK3bX` | E2E test suite + gap analysis + P0-1,2,3,4 closures | **Work P1+ here.** |
| `claude/p0-5-stripe-card-charge` | Stripe card charge wire-up | Phases A-E shipped; awaits staging access to execute Phase E |

**Start on `claude/plan-e2e-testing-SK3bX`** (`git checkout
claude/plan-e2e-testing-SK3bX`). Periodically `git merge origin/main`
to keep fresh. Do NOT merge P0-5 into this branch — that track is
ready-but-gated pending human Phase E validation.

---

## Mandatory context — read before first commit

1. `docs/E2E_TEST_GAP_ANALYSIS.md` — **the backlog.** §4 lists P1 through
   P3 items in priority order.
2. `docs/scoping/P0-5_STRIPE_CARD_CHARGE.md` — shows the template for
   how a multi-phase feature is scoped (A through E).
3. `docs/scoping/P0-5_PHASE_E_RUNBOOK.md` — shows the runbook template
   for manual validation steps.
4. `backend/tests/test_p0_ship_blockers.py` — the pattern for pinning
   a behavior with a real test + xfail-as-documentation for unbuilt
   features.
5. `backend/utils/estimate_token.py` + `backend/tests/test_estimate_token.py`
   — example of a small, well-tested addition (P0-4 surge-lock) with
   security properties explicitly enumerated.
6. `CLAUDE.md` — project rules (graphify rebuild after code edits).

---

## P1 items (in priority order, with starting pointers)

From `docs/E2E_TEST_GAP_ANALYSIS.md` §4:

### P1-6 — WebSocket reconnect with state preservation (C8)
- **Problem**: if the rider's phone drops the WS during a ride, does
  the client re-subscribe + re-sync state without missing a status
  transition?
- **Starting files**: `rider-app/hooks/useRiderSocket.ts`,
  `driver-app/hooks/useDriverDashboard.ts` (reconnect with exp backoff
  already exists here — pin it), `backend/socket_manager.py`
- **Approach**: Jest test that simulates a WS close mid-ride + asserts
  the store re-syncs via the next `GET /rides/active` response. Backend
  test that `ride_accepted` events sent while a client was disconnected
  are re-delivered on next connection (or at minimum, the HTTP poll
  recovers the state).

### P1-7 — Mid-trip restart restore (R14, D12)
- **Problem**: if the rider or driver app is killed mid-ride, does
  relaunching restore the active-ride state?
- **Starting files**: `rider-app/store/rideStore.ts::_persistRide`
  (exists — ride-completed already persists to AsyncStorage with key
  `@spinr:active_ride`), `driver-app/store/driverStore.ts::_persistDriverState`
- **Approach**: Jest test that sets a mid-trip state in AsyncStorage,
  calls `hydrateActiveRide()` / `hydrateDriverRideState()`, asserts the
  store rehydrates to the correct state. Cross-app E2E in
  `tests/test_cross_app_ride_lifecycle.py` can assert the backend's
  `GET /rides/active` correctly returns the in-progress ride to a
  "restarted" client.

### P1-8 — Role-claim tampering guard (S3, S8)
- **Problem**: a rider crafts a JWT with `role: "driver"` — does the
  backend accept it?
- **Starting files**: `backend/dependencies.py` (look for
  `get_current_user`, `get_admin_user`, `get_driver_user`),
  `backend/core/security.py`
- **Approach**: Backend tests that tampered tokens → 401/403. If the
  role comes from the DB row (not the JWT claim), that's the right
  shape and we just need pin tests. If it comes from the claim, that's
  a vuln — fix + test.
- **Related**: S8 — a driver tries to view another driver's earnings.
  Test `GET /drivers/earnings` authorizes by `current_user.id`, not a
  path param.

### P1-9 — Multi-stop E2E (R11)
- **Problem**: a rider adds a stop mid-trip — does the fare, ETA, and
  route update, and does the driver app receive the change?
- **Starting files**: `backend/routes/rides.py` — search for
  `stops`; `POST /rides/{ride_id}/stops` route already exists (lines ~1521
  in the rides.py on the P0-5 branch)
- **Approach**: Backend test that adding a stop mid-ride recalculates
  fare + publishes a WS event to the driver channel. Rider-app Jest
  test for `addStop` / `removeStop` store actions (already exist —
  test they persist + trigger fare re-estimate).

### P1-10 — Driver offline mid-trip (E5)
- **Problem**: driver goes offline while carrying a rider — rider
  should NOT be stranded or re-dispatched. Ride continues on the
  current driver.
- **Starting files**: `backend/routes/drivers.py::set_driver_status`
  (the `/drivers/status` toggle); `backend/socket_manager.py` (driver
  disconnect handling)
- **Approach**: Backend test that `POST /drivers/status` with
  `is_online: false` while `active_ride is not None` either rejects
  (409) or stays active on the ride (driver can't toggle off until
  trip completes). Pick the right behavior, document it, test it.

### P1-11 — Token refresh mid-trip (E11)
- **Problem**: the 15-min access token expires while a ride is active.
  Does the app refresh without dropping the ride?
- **Starting files**: `shared/api/client.ts` — search for
  `refresh_token`; `backend/routes/auth.py::refresh_token`
- **Approach**: Jest test that an expired-token response triggers
  refresh + retries the original request once. Cross-app E2E (optional)
  that a mid-trip token expiry doesn't break WS subscription.

### P1-12 — CORS on web exports (S9)
- **Problem**: does the backend refuse requests from origins not in
  `ALLOWED_ORIGINS`?
- **Starting files**: `backend/core/middleware.py` — CORS
  initialization; `backend/core/config.py` — `ALLOWED_ORIGINS` setting
- **Approach**: Backend test that a `OPTIONS` preflight from an
  unlisted origin gets a restrictive CORS response. Check that `"*"`
  is rejected in production per existing comment in config.py:47.

---

## Conventions — follow these exactly

### Branch + commit
- Work on `claude/plan-e2e-testing-SK3bX` unless user says otherwise.
- One logical change per commit. Prefer many small commits over one big one.
- Commit message format:
  ```
  <type>(<scope>): <summary> (close P1-N)

  <body describing what & why; cite file:line where useful>

  https://claude.ai/code/session_<session-id>
  ```
  Types used so far: `feat`, `test`, `fix`, `docs`.
- Never push to `main` or `master`.
- After each commit: `git push -u origin claude/plan-e2e-testing-SK3bX`.

### Code style
- **Never add backwards-compat shims, `# removed X` comments, or
  feature flags** for the old behavior unless the user asks. Just
  change the code.
- Default to no comments. Add one only when the WHY is non-obvious —
  see existing code for tone. Never narrate the task or cite the issue
  number in source comments.
- Python: `Decimal` for money, not float. Use existing `_d`, `_f`,
  `_round` helpers in `backend/routes/rides.py`.
- TypeScript: prefer pure helpers (see
  `rider-app/utils/attemptRidePayment.ts` as the template — returns
  alert descriptors, no UI coupling; easy to unit test).

### Testing
- **Test-first on P0 ship-blockers. Test-after-implementation is fine
  for P1+** if the change is surgical.
- Backend tests live in `backend/tests/test_*.py`. Use pytest classes
  grouped by scenario. Use `@pytest.mark.asyncio` for async handlers.
  Use `@pytest.mark.e2e` for lifecycle / cross-app tests so unit runs
  stay fast (`pytest -m "not e2e"`).
- Rider-app tests: Jest unit under
  `rider-app/{store,utils,components}/__tests__/`; Playwright under
  `rider-app/e2e/`.
- Driver-app tests: Jest unit under `driver-app/__tests__/` or
  `driver-app/store/__tests__/`; Playwright under `driver-app/e2e/`.
- **Syntax-check every file before commit**:
  ```
  python3 -c "import ast; ast.parse(open('PATH').read())"
  tsc --noEmit --allowJs --esModuleInterop --skipLibCheck \
      --target ES2020 --jsx preserve --module ESNext \
      --moduleResolution node --ignoreDeprecations 6.0 PATH
  ```
- Don't install dependencies for testing. If deps aren't installed
  locally (common on this machine), note the missing-pkg errors but
  ship — the file will work when the project's real test runner runs
  with deps installed.

### Refactors
- Lift function-scope imports to module-scope when you need them
  patchable in tests (see
  `backend/routes/rides.py::charge_ride` import for the pattern).
- If a behavior is buried in a closure and you need to test it,
  extract to module scope — see `ride_search_timeout` extraction
  in commit cca6daa.
- Never bulk-reformat files; keep diffs focused on the change.

### Document gaps, don't hide them
- If an implementation gap exists and you can't close it in-scope,
  write an `xfail(strict=False)` test that **documents the gap as a
  living TODO**. See
  `backend/tests/test_p0_ship_blockers.py::TestPaymentFailureAtComplete::test_card_decline_marks_payment_failed_and_allows_retry`
  for the pattern (before it was flipped).
- Update `docs/E2E_TEST_GAP_ANALYSIS.md` §4 each time you close an
  item. Change ⚠️ partial → ✅ closed with a one-line citation.

---

## Worked example — how I closed P0-4 (surge-lock)

This is the template for a new P-item close:

1. Read the agent/subagent report to understand current state (EXISTS /
   PARTIAL / MISSING).
2. If MISSING or PARTIAL, decide: close the gap in this commit, or
   xfail-document it? Closing is preferred when the scope is <500
   LOC + no external service.
3. Write the helper (e.g., `backend/utils/estimate_token.py`).
4. Wire it into the existing code path (`rides.py::create_ride`
   `surge` variable).
5. Propagate through the client (`rider-app/store/rideStore.ts` echoes
   token on create).
6. Tests: unit tests for the helper (8-12 cases covering security
   properties explicitly), plus one E2E / handler-level test that
   proves the glue works.
7. Flip the xfail in `test_p0_ship_blockers.py` from
   `assert False, "not implemented"` to a real passing assertion.
8. Update the gap analysis doc.
9. One commit with a detailed message citing files + line numbers.

The template produces ~200-500 LOC + tests per item and takes roughly
one Claude session per item at medium effort.

---

## Escalation points — ASK BEFORE doing these

- **Merging branches** — don't. The user merges PRs via GitHub.
- **Running against real third-party services** (Stripe live, real
  Firebase, real Supabase prod) — never. Staging/test mode only, and
  only if the user has confirmed credentials are safe.
- **Changing `backend/core/config.py` secrets** — ask.
- **Modifying `render.yaml`, `railway.json`, `Dockerfile`,
  `.github/workflows/*`** — ask. Infrastructure changes have blast
  radius beyond local.
- **Deleting tests** — ask. Always prefer xfail over delete.
- **Reaching P2 or P3** — confirm P1 is fully closed first.

---

## Starter prompt to paste into the new session

```
You are picking up the E2E hardening track. Read
docs/handoff/P1_NEXT_SESSION_PROMPT.md in full before doing anything
else. Then:

1. git checkout claude/plan-e2e-testing-SK3bX
2. git merge origin/main --no-edit (resolve conflicts if any)
3. Read docs/E2E_TEST_GAP_ANALYSIS.md §4 to see the P1 list
4. Start with P1-6 (WebSocket reconnect with state preservation)
5. Use the worked example in the handoff doc as your template: survey
   → close-or-xfail → test → update gap doc → commit → push

Ship one P1 per commit. Don't batch. Follow the conventions in the
handoff doc exactly — especially: no backwards-compat shims, no
feature flags for old behavior, pure helpers where possible, xfail
to document gaps you can't close in-scope.

When all P1 items are closed, stop and ask the user whether to
continue to P2.
```

---

## What's shipped on `claude/plan-e2e-testing-SK3bX` so far

Commits on this branch (`git log --oneline origin/main..`):

- `test(e2e): add driver-app Playwright suite + cross-app lifecycle tests`
  — driver-app E2E harness (port 3003), cross-app rider+driver tests,
  backend ride-lifecycle E2E
- `test(e2e): pin P0 ship-blockers + document surge/card gaps`
  — `test_p0_ship_blockers.py` with 5 classes (one per P0-1..P0-5),
  plus the `ride_search_timeout` refactor
- `feat(fares): lock surge between estimate and create (close P0-4)`
  — HMAC-signed `estimate_token`; rider-app echoes token on create;
  flipped xfail → real pass

Key additions you can reuse:

- `backend/tests/test_p0_ship_blockers.py` — class-per-scenario pattern
- `backend/tests/test_e2e_ride_lifecycle.py` — happy-path + concurrency
- `tests/test_cross_app_ride_lifecycle.py` — rider + driver against
  shared DB (pins "driver accepted but rider still searching" regression)
- `backend/utils/estimate_token.py` — HMAC helper pattern

## What's shipped on `claude/p0-5-stripe-card-charge`

Do NOT depend on these in your P1 work. They merge separately.

- `backend/utils/stripe_charge.py` + `charge_ride()` wiring
- `rider-app/utils/attemptRidePayment.ts` + ride-completed UX
- `rider-app/e2e/payment-completion.spec.ts`
- `scripts/smoke/stripe_charge_smoke.py` + `P0-5_PHASE_E_RUNBOOK.md`

## Done

When P1 is fully closed, re-read `docs/E2E_TEST_GAP_ANALYSIS.md` §4
and confirm every P1 row shows ✅. Then ask the user whether to
proceed to P2 (chat E2E, SOS, promo/wallet/loyalty, payout/T4A,
scheduled rides+DST).
