# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (local commit only — not pushed; see commit SHA in PR description when integrated) |
| Related issue or gap ID | Production-readiness tracker task #11 (P1); underlying feature is ACTION_ITEMS.md E3 |

## 1. Issue / gap identified

`ForcedUpgradeMiddleware` (`backend/core/middleware.py`) has no carve-out for a driver who is already mid-trip. If an admin ever sets `min_driver_app_version` above a driver's installed build while that driver is `in_progress` with a passenger aboard, their next call to `arrive`/`verify-otp`/`start`/`complete`/`location-batch` (or the active-ride resync GET used on reconnect) would 426, and the shared `ForceUpdateOverlay` is a full-screen, non-dismissible modal — stranding the trip mid-ride with no way for the driver to finish it, cancel it (cancel is invalid once `in_progress`, per the ride state machine), or even see the ride any more.

## 2. Root cause

`_FORCED_UPGRADE_EXEMPT_PREFIXES` was designed only for the pre-auth/bootstrap case (a too-old client needs to reach `/settings` and OTP endpoints to learn it must update before it can render anything). It was never extended to the in-trip case: an already-authenticated driver on an already-accepted ride whose only remaining job is to finish that one ride safely. The gate applies uniformly to all of `/api/v1/*` with no notion of "this call is required to close out state that already exists," so a version bump becomes destructive rather than merely blocking new activity.

## 3. Fix / remediation

Added a second, additive exemption path — `_is_ride_carveout_path()` — checked immediately after the existing prefix exemption in `ForcedUpgradeMiddleware.dispatch()`. It is a pure path allowlist (no DB call):

- Exact-path exemptions: `POST /api/v1/drivers/location-batch`, `GET /api/v1/drivers/rides/active`.
- Suffix-match exemptions (path must start with `/api/v1/drivers/rides/` **and** end with one of `/arrive`, `/verify-otp`, `/start`, `/complete`) — suffix matching is necessary because `{ride_id}` sits in the middle of the path, so a simple prefix match would also have caught `/accept`, `/decline`, `/cancel`, `/rate-rider`, which must stay gated (see §4).

Endpoints exempted, and why each is required to safely finish an already-accepted trip (verified against `driver-app/store/driverStore.ts` and `driver-app/utils/tripLocationTransport.ts`, not guessed):

| Endpoint | Called when | Why it must not 426 mid-trip |
|---|---|---|
| `POST /api/v1/drivers/rides/{id}/arrive` | Driver reaches pickup (`driver_arrived`) | Ride-flow transition; blocking it strands the driver at the curb |
| `POST /api/v1/drivers/rides/{id}/verify-otp` | Rider hands driver the pickup OTP | Gate before `start`; without it the trip can never begin |
| `POST /api/v1/drivers/rides/{id}/start` | Trip begins (`in_progress`) | Enters Insurance Period 3 (passenger aboard) — blocking it leaves the ride stuck at `driver_arrived` indefinitely |
| `POST /api/v1/drivers/rides/{id}/complete` | Trip ends (`completed`) | The one action that actually frees the driver and settles the fare; this is the literal "stranded trip" scenario |
| `POST /api/v1/drivers/location-batch` | Continuous, every few seconds while online/on-trip | Also an explicit SLA row (<150ms) — see §4 for why this stayed a path allowlist, not a DB check |
| `GET /api/v1/drivers/rides/active` | WS-reconnect / app-foreground resync | If the driver's app is backgrounded or killed mid-trip (common on longer rides) and reopens, this is how it learns "you are still on ride X, state Y" — without it a reconnect after `in_progress` would show nothing rather than resuming the trip screen |

Endpoints deliberately left gated (not part of the carve-out, still subject to forced upgrade):
- `POST /api/v1/drivers/rides/{id}/accept` / `/decline` — new-offer intake. A driver cannot have a second active ride while one is already active (`active_statuses` invariant in CLAUDE.md), so these never fire during an in-progress trip; gating them for an old build push is the intended behavior (push the driver to update before taking a *new* ride).
- `POST /api/v1/drivers/rides/{id}/cancel` — invalid after `in_progress` per the ride state machine anyway; not part of the in-progress-trip-completion path.
- `POST /api/v1/drivers/rides/{id}/rate-rider` — post-completion, not required to *finish* the trip itself.
- All other `/api/v1/drivers/*` endpoints (profile, earnings, payouts, subscriptions, etc.) — unrelated to finishing an active trip.

## 4. Risk & impact on existing functionality

**Blast radius grep performed** (`grep -rln "ForcedUpgradeMiddleware\|_FORCED_UPGRADE"`): the only non-test consumers are `backend/core/middleware.py` itself (definition + the single `app.add_middleware(ForcedUpgradeMiddleware)` call in `init_middleware`), `backend/schemas.py` (the `min_rider_app_version`/`min_driver_app_version` field definitions on the settings schema, unrelated to this change), and `backend/routes/admin/settings.py` (the admin endpoint that lets an admin set those two values — also unrelated, no code path there reads `_FORCED_UPGRADE_EXEMPT_PREFIXES` or the new carve-out list). No other backend module, and no frontend code, references the exemption list directly — the frontend only reacts to a 426 status code via the shared `onForceUpgrade()` callback, which is unaware of *which* endpoint triggered it. **Conclusion: isolated to this one middleware class.**

- This is a pure allowlist addition (widening which requests bypass a check), not a change to any table, ride-state transition, or money/wallet code path. It cannot itself alter `ride.status`, insurance-period logging, or fare settlement — it only decides whether the *version-floor* check runs before the existing handler logic (which still fully enforces auth, ownership, and `_require_ride_in_state()`).
- **Currently inert in production**: no `min_driver_app_version` is set today (empty string = gate self-disables per the existing `min_version is None` short-circuit), so this change has **zero live behavioral effect** until an admin sets a non-empty minimum. The practical risk of *this commit* shipping is near-zero; the risk being managed is entirely forward-looking (what happens the day the gate is turned on).
- **Deliberate non-DB-lookup design**: a ride-state-aware check (fetch the ride, confirm the caller is really its assigned driver and it's really `in_progress`) was considered and rejected for this pass, per the task's explicit guidance to avoid a slow DB call on `location-batch`, which carries a <150ms SLA in CLAUDE.md's performance table (a DB round-trip on every location write is exactly the anti-pattern that table warns against). Both `arrive`/`verify-otp`/`start`/`complete` and the `GET rides/active` resync already re-validate real ride ownership and state downstream in their own handlers (`_require_ride_in_state()` etc.) — this gate only decides whether an old-but-functional client may *reach* those handlers, not whether the action is actually authorized. No new authority is granted by the exemption.
- **Residual risk being accepted**: an old driver build can now always poll `GET /rides/active` and call the four ride-lifecycle mutation endpoints on **its own ride** (via a valid driver JWT, still fully authenticated) regardless of the version floor, whether or not that driver is genuinely mid-trip. This is intentional and narrow — these endpoints are meaningless / no-op or 404/409 against a ride the caller doesn't currently own or that isn't in the right state, so there's no privilege escalation, only a version-floor bypass on a driver's own already-scoped ride actions.
- **Who else reads/writes the same code**: nobody else touches `_FORCED_UPGRADE_EXEMPT_PREFIXES` or the new `_FORCED_UPGRADE_RIDE_CARVEOUT_*` constants (confirmed by the grep above) — no risk of another feature silently relying on the old (narrower) exemption set.

## 5. User-experience effect

- **Driver-facing.** Today: no effect (gate is inert — no minimum version is configured). Once an admin ever sets `min_driver_app_version`: a driver on an old build who is mid-trip will now be able to complete arrive → OTP → start → complete and keep sending location pings, instead of hitting the full-screen "Update Required" overlay mid-ride. They will still see the overlay on their *next* action once no longer on an active ride (e.g. going online again, viewing earnings) — the carve-out is scoped to finishing the current trip only, not a general reprieve.
- **Rider-facing.** Indirect only: a rider whose driver is on an old build no longer risks having their driver stranded mid-trip if a version bump lands during the ride. No visible change to the rider app itself.
- **Not** visible mid-session today because the feature is inert in production; this only becomes observable the day the gate is activated.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Added `_FORCED_UPGRADE_RIDE_CARVEOUT_PREFIX`, `_FORCED_UPGRADE_RIDE_CARVEOUT_SUFFIXES`, `_FORCED_UPGRADE_RIDE_CARVEOUT_EXACT`, and `_is_ride_carveout_path()`; added one `if _is_ride_carveout_path(path): return await call_next(request)` check in `ForcedUpgradeMiddleware.dispatch()`, right after the existing exempt-prefix check. Existing `_FORCED_UPGRADE_EXEMPT_PREFIXES` untouched. | Close the active-ride stranding gap described in §1, additively. |
| `backend/tests/test_forced_upgrade_middleware.py` | New test file (did not exist before). Covers: each carve-out path bypasses 426 for an old client; non-carve-out ride paths (`accept`/`decline`/`cancel`/`rate-rider`) stay blocked; an unrelated driver endpoint stays blocked; existing `settings`/OTP exemptions are unaffected; the whole gate stays inert when no minimum is configured. | Regression coverage for the fix per CLAUDE.md testing conventions (every new gate/exemption needs both allow and deny paths tested). |
| `docs/change-log/2026-08-11-forced-upgrade-ride-carveout.md` | This file. | Mandatory Change Impact & Risk Log for a fix touching a live-tested surface (rides/drivers). |

## 7. Before / after

```python
# Before
if any(path.startswith(p) for p in _FORCED_UPGRADE_EXEMPT_PREFIXES):
    return await call_next(request)

platform = request.headers.get("X-App-Platform")
```

```python
# After
if any(path.startswith(p) for p in _FORCED_UPGRADE_EXEMPT_PREFIXES):
    return await call_next(request)
if _is_ride_carveout_path(path):
    return await call_next(request)

platform = request.headers.get("X-App-Platform")
```

## 8. Rollback plan

Trivial — this is a pure code-level allowlist addition with no data or migration involved:
- `git revert` this commit. There is nothing to reconcile in the database (no rows written, no ride/wallet state touched by this change itself) and no `app_settings` flag was added or changed, so a plain revert is a complete and sufficient rollback here — unlike a change that touches live ride/wallet/Stripe state, there is no "already-applied-to-live-data" residue to clean up.
- No feature flag needed: the entire `ForcedUpgradeMiddleware` mechanism is already self-disabling whenever `min_driver_app_version`/`min_rider_app_version` is empty (today's production state), so there is no rollout step to gate on — the change is inert until an admin opts in by setting a minimum version, at which point the carve-out is the safety net, not a new risk surface.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python -m pytest tests/test_forced_upgrade_middleware.py -q --no-cov` → 17 passed. Also re-ran the adjacent middleware suites for regression safety: `tests/test_csrf_middleware.py`, `tests/test_middleware_production_config_guard.py`, `tests/test_middleware_user_id.py`, `tests/test_p1_cors.py` → 77 passed total, 0 failed.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment was exercised (see "What was NOT verified" below).
- [x] Blast-radius grep performed: `grep -rln "ForcedUpgradeMiddleware\|_FORCED_UPGRADE" --include=*.py --include=*.md .` → only `backend/core/middleware.py`, `backend/schemas.py`, `backend/routes/admin/settings.py`, `ACTION_ITEMS.md` (doc), and the new test file. Findings summarized in §4.
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (no transition logic touched — handlers still own `_require_ride_in_state()`), performance SLAs (explicitly avoided a DB call on the <150ms `location-batch` path), do-not-silently-swallow-errors (no error handling was touched by this change).
- [x] `cd backend && ruff check core/middleware.py tests/test_forced_upgrade_middleware.py` → all checks passed. `ruff format --check` on both files → already formatted. (Repo-wide `ruff check .` has 43 pre-existing findings in unrelated files, confirmed unrelated to and untouched by this change — not addressed here per CLAUDE.md's "not my problem for unrelated red gates" guidance, and no new findings were introduced.)
- [ ] Feature-flagged if user-visible and non-trivial — not applicable: the underlying `ForcedUpgradeMiddleware` feature already has its own dark-launch mechanism (`app_settings.min_driver_app_version`/`min_rider_app_version`, empty = off), and this change only extends what's exempted from a check that is itself currently off. No new flag needed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data migration).
- [x] Blast radius is stated, not assumed: isolated to `backend/core/middleware.py`'s `ForcedUpgradeMiddleware`; no other backend module or frontend surface reads the exemption lists.
- [x] No silent behavior change to an already-shipped flow: the feature this modifies is currently inert in production (no minimum version set), so no already-shipped, currently-active flow changes behavior as a result of this commit landing. The UX field above documents the behavior once the (currently unused) gate is later activated.

## What was NOT verified

- **Not tested against a real driver-app build or a live backend deploy** — verification was backend-only: a minimal FastAPI app mounting only `ForcedUpgradeMiddleware`, with `settings_loader.get_app_settings` mocked to return a fixed `min_driver_app_version`. The real `driver-app` client (Expo/React Native) was not run against this change; its request paths were confirmed by reading `driver-app/store/driverStore.ts` and `driver-app/utils/tripLocationTransport.ts` (grep + read), not by executing the app.
- **Not exercised in staging** — no staging environment was available in this session; only local `pytest` runs.
- **No production build step applies here** — this is a `backend/` (Python) change only, not `admin-dashboard`/`rider-app`/`driver-app`, so the CLAUDE.md requirement to run `npm run build` for those surfaces does not apply to this diff. Backend verification was `pytest` + `ruff check`/`ruff format --check`, no separate "build" step exists for the Python backend.
- **`GET /api/v1/drivers/rides/active` inclusion is a judgment-call extension** beyond the four literally-named endpoints in the original bug report (arrive/start/complete/location-batch). It was added because the code shows it's the driver-app's WS-reconnect/app-foreground resync call, and without it a driver whose app restarts mid-trip could see nothing instead of resuming — but this reasoning was not validated against an actual mobile-app reconnect scenario, only against the source code's comments and call sites.
- **No load/perf test was run** against `location-batch` to reconfirm the <150ms SLA is unaffected — the change adds a single tuple membership + string `.endswith()` check ahead of the existing header-parsing logic (no I/O), so the reasoning that it's negligible is analytical, not measured.
