# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | safety |
| PR / commit link | (local commits — not pushed; see commit SHAs in agent report) |
| Related issue or gap ID | Tracker task #13 (P1) |

## 1. Issue / gap identified

CLAUDE.md's Saskatchewan Regulatory → Accessibility section states "Service
animal accommodation is mandatory; drivers cannot refuse" — implying a
refusal should be a tracked terms violation subject to account review. But
`service_animal` did not exist anywhere in the ride decline/cancel data
model: a pre-accept decline (`declineRide()` in `driverStore.ts`) took no
reason parameter at all, and the post-accept cancel reason list
(`DRIVER_CANCEL_REASONS` in `driver-app/components/CancelReasonSheet.tsx`)
had no animal-related option. There was no way for trust & safety to ever
detect this refusal pattern.

## 2. Root cause

The decline flow was built for speed (a ~15s countdown, single tap, no
reason UI at all) and never had a reason field added. The cancel flow does
have a reason (free-text `cancellation_reason` column, preset list +
optional note), but the preset list was written before this specific
accessibility requirement was called out and nobody added an entry for it.
Neither flow writes a dedicated audit event for a specific reason category —
`cancellation_reason` is a plain text column with no downstream audit hook,
and driver cancels don't write to `audit_logs` at all today (only the
pre-accept `decline` endpoint does, unconditionally, for every reason).

## 3. Fix / remediation

**Scope of this pass: data capture + audit log only.** Automated
account-review/suspension on repeated refusals, and any admin dashboard UI
to review these events, are explicitly deferred as separate follow-up work
— not delivered here.

- `backend/routes/drivers/ride_flow.py` (`decline_ride`): now accepts an
  optional `reason` in the JSON body (never a query string, so it can't leak
  into proxy/access logs). The existing per-decline `audit_logs` insert now
  includes `reason` in `details`. When `reason == "service_animal"`, an
  additional `logger.info(...)` is emitted with `extra={"domain": "safety",
  "surface": "backend", ...}` per CLAUDE.md's Sentry-tag conventions, so the
  event is queryable/correlatable independent of the audit_logs row.
- `backend/routes/drivers/ride_cancel.py` (`cancel_ride`): the existing
  free-text `cancellation_reason` write is unchanged. When the reason
  contains the case-insensitive substring "service animal", a dedicated
  `audit_logs` row (`action: "ride_cancel_service_animal_refusal"`) is now
  written — driver cancels previously wrote no audit_logs row at all — plus
  the same domain=safety info log as above.
- `driver-app/store/driverStore.ts`: `declineRide(rideId, reason?)` now
  takes an optional second argument; omitting it (the default/existing call
  sites) posts the exact same no-body request as before.
- `driver-app/components/panels/RideOfferPanel.tsx`: the Decline button's
  plain tap is unchanged. A **long-press** on Decline now opens a native
  alert (reusing the existing `showAlert` utility, no new UI component)
  offering "Service animal — report & decline" or "Cancel". Choosing the
  former calls `onDecline('service_animal')`.
- `driver-app/components/CancelReasonSheet.tsx`: added
  `'Service animal — could not accommodate'` to `DRIVER_CANCEL_REASONS`,
  between `'Pickup too far'` and `'Other'`.

No DB migration. `rides.cancellation_reason` is already a free-text `TEXT`
column (migration 37) and `audit_logs.details` is already `JSONB` — both
already accept this new value/shape with no schema change. This was a
deliberate choice per the task's explicit preference for the lower-risk
option over adding a new column/table.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the driver decline/cancel flow, single
consumer on each surface.**

- `CancelReasonSheet.tsx` (driver-app) blast radius: grepped for every
  importer — only `driver-app/components/dashboard/ActiveRidePanel.tsx`
  imports it (plus its own test file and the driver-app's dedicated
  `ActiveRidePanel.test.tsx`). The **rider-app** has its own separate
  `rider-app/components/CancelReasonSheet.tsx` with a different reasons
  list (`RIDER_CANCEL_REASONS`) — not touched by this change, confirmed by
  grep; a rider would never see this option (correct — riders don't decline
  rides for lack of animal accommodation, drivers do).
- `RideOfferPanel` (driver-app) blast radius: grepped for every importer —
  only `driver-app/app/driver/(tabs)/index.tsx` (plus the barrel re-export
  in `components/index.ts`, which nothing else consumes, and its own test
  file). A second, unused, dead-code copy of an offer-card renderer exists
  in `index.tsx` (`renderRideOfferPanel`, defined but never invoked) — left
  untouched; it's unreachable so it carries zero risk either way.
- `declineRide` (driverStore) blast radius: grepped every caller —
  `app/driver/(tabs)/index.tsx` (both a live call site, updated to pass the
  new optional reason, and the same unused dead-code function above, not
  updated since it's never invoked), `app/_layout.tsx` (push-notification
  action handler), `lib/androidAuto/register.ts` (Android Auto hand-off),
  and the store's own auto-decline-on-timeout call in `setCountdown`. All
  four continue to call `declineRide(rideId)` with no second argument —
  backward compatible by construction (optional parameter, no-body branch
  preserved exactly).
- Backend `decline_ride`/`cancel_ride` blast radius: grepped for every
  caller — only the router registration in
  `backend/routes/drivers/__init__.py` and the test suite. No other backend
  module calls either function directly; both are driver-only REST
  endpoints with no admin or rider path reusing them.
- `cancellation_reason` (rides table) blast radius: this column is already
  free text read by admin ride-detail views and earnings/analytics
  aggregate functions (migrations 163/165/227) that reference the column
  name generically (they don't branch on specific string values), so
  appending one more possible value carries no risk to those aggregates.
- `audit_logs` blast radius: this table is written by 38+ backend modules;
  the new rows use existing columns (`action`, `entity_type`, `entity_id`,
  `actor_id`, `details`, `created_at`) with a new `action` value
  (`ride_cancel_service_animal_refusal`) — additive, no existing query
  filters on `action` would need to change to keep working, and none would
  suddenly start matching rows they shouldn't.
- No interaction with the ride state machine (no new transitions, no new
  states), no money/wallet deltas, and no change to any of the 18
  background loops.

## 5. User-experience effect

**Driver-facing only** (no rider, corporate-admin, or internal-admin visible
change in this pass — no admin dashboard UI was built; that's explicitly
deferred).

- Post-accept cancel: a driver cancelling an active ride now sees one more
  option in the existing reason sheet. No change to the sheet's layout,
  behavior, or the six reasons already there.
- Pre-accept decline: **not visible unless discovered** — the fast, common
  single-tap decline (used on a ~15s countdown) is pixel- and
  behavior-identical to before. The new option is a long-press, which is
  not currently hinted at in the UI (no icon, no label, no "hold for more
  options" text). This was a deliberate minimal choice to avoid touching
  the countdown-timer decline UX for the common case, but it does mean
  real-world driver discovery of the flag is likely to be low without a
  follow-up affordance (e.g. a hint label) — noting this as a known
  limitation rather than a completed close of the discoverability gap.
- Neither change is visible mid-session to a rider or another driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_flow.py` | `decline_ride` accepts optional JSON-body `reason`; records it in the existing `audit_logs` insert; adds a `domain=safety` info log when `reason == "service_animal"` | Data capture for pre-accept decline, which had no reason field at all |
| `backend/routes/drivers/ride_cancel.py` | Detects `"service animal"` (case-insensitive substring) in the existing `reason`; writes a dedicated `audit_logs` row (`ride_cancel_service_animal_refusal`) and a `domain=safety` info log | Post-accept cancel had no audit event for any reason; this is a targeted addition for the one reason that needs to be queryable |
| `backend/tests/test_drivers_extended.py` | New tests: decline with/without `service_animal` reason (incl. malformed body), backward-compat no-`request` call; cancel with the service-animal preset text vs. an ordinary reason | Regression coverage for the above, including a no-PII assertion on the audit row |
| `driver-app/store/driverStore.ts` | `declineRide(rideId, reason?)` — optional second arg; omits the body when absent (unchanged request shape) | Lets the UI pass a reason through to the backend |
| `driver-app/store/__tests__/driverStore.test.ts` | New tests: `declineRide` posts `{reason}` when given, falls back to no-body on empty/whitespace reason | Regression coverage |
| `driver-app/components/panels/RideOfferPanel.tsx` | `onDecline` now takes `(reason?: string)`; adds `onLongPress` on the Decline button opening a `showAlert` prompt with a "Service animal — report & decline" option; plain tap unchanged | Makes the pre-accept decline reason actually reachable, without altering the fast/common decline path |
| `driver-app/__tests__/components/RideOfferPanel.test.tsx` | New tests: plain tap declines with no reason and doesn't open the alert; long-press opens the prompt; selecting the service-animal option calls `onDecline('service_animal')`; Cancel does not decline | Regression coverage |
| `driver-app/components/CancelReasonSheet.tsx` | Adds `'Service animal — could not accommodate'` to `DRIVER_CANCEL_REASONS` | Post-accept cancel reason list was missing this option |
| `driver-app/app/driver/(tabs)/index.tsx` | `onDecline={(reason) => declineRide(incomingRide.ride_id, reason)}` (was `onDecline={() => declineRide(incomingRide.ride_id)}`) | Wires the new reason through from the panel to the store |

## 7. Before / after

**`declineRide` (driver-app/store/driverStore.ts):**

```ts
// Before
declineRide: async (rideId: string) => {
    try {
        await api.post(`/drivers/rides/${rideId}/decline`);
    } catch {
        // Decline failure is non-critical — reset state regardless
    }
    set({ rideState: 'idle', incomingRide: null, countdownSeconds: 0 });
},
```

```ts
// After
declineRide: async (rideId: string, reason?: string) => {
    try {
        const trimmed = reason?.trim();
        if (trimmed) {
            await api.post(`/drivers/rides/${rideId}/decline`, { reason: trimmed });
        } else {
            await api.post(`/drivers/rides/${rideId}/decline`);
        }
    } catch {
        // Decline failure is non-critical — reset state regardless
    }
    set({ rideState: 'idle', incomingRide: null, countdownSeconds: 0 });
},
```

**`decline_ride` (backend/routes/drivers/ride_flow.py):**

```python
# Before
@router.post("/rides/{ride_id}/decline")
async def decline_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = ...
```

```python
# After
@router.post("/rides/{ride_id}/decline")
async def decline_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    reason = None
    if request is not None:
        try:
            _body = await request.json()
            if isinstance(_body, dict):
                _r = _body.get("reason")
                reason = str(_r).strip() or None if _r else None
        except Exception:
            reason = None
    driver = ...
```

## 8. Rollback plan

No feature flag was added — this is intentionally additive-only (a new
optional parameter, a new preset string, a new conditional audit write) with
no behavior change to any existing path (confirmed by the backward-compat
tests above: omitting the reason reproduces byte-identical requests to
before this change).

- **Code-level revert is sufficient and safe here** (unlike Stripe
  charges/wallet deltas/ride state, which the CLAUDE.md template calls out
  as needing more than a revert): the only "live data" this change writes
  is (a) new, additional rows in `audit_logs` — an append-only table where
  removing the write path just stops new rows, doesn't corrupt existing
  ones — and (b) new possible values for an already-freeform
  `cancellation_reason` text column, not a new column or constraint. A
  `git revert` of these commits removes the new UI option, the new endpoint
  parameter, and the new audit-log branches, with nothing left in an
  inconsistent state.
- If only the frontend needs to roll back (e.g. the long-press affordance
  causes unexpected support tickets) while keeping backend capture live:
  revert `driver-app/components/panels/RideOfferPanel.tsx` and
  `driver-app/components/CancelReasonSheet.tsx` only — the backend
  endpoints stay optional-reason-accepting and harmless with no caller
  sending `service_animal`.
- No `app_settings` flag exists for this because the change has no
  meaningful "on/off" runtime behavior to gate — it's dormant until a driver
  specifically selects the new reason.

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `pytest -q --no-cov tests/test_drivers_extended.py
    tests/test_idor_ownership_guards.py tests/test_driver_ride_flow_coverage.py
    tests/test_c2_driver_cancel_atomic.py` → **219 passed**, 0 failed
    (includes 9 new decline/cancel tests + all pre-existing tests in the
    same files, confirming no regression).
  - driver-app (jest): `npx jest store/__tests__/driverStore.test.ts
    __tests__/components/RideOfferPanel.test.tsx
    __tests__/components/ActiveRidePanel.test.tsx
    lib/androidAuto/__tests__/register.test.ts` → **all suites passed**
    (34 + 14 + 45 + 10 tests across the four files, no failures).
  - `npx tsc --noEmit -p tsconfig.json` in `driver-app/` → clean, no type
    errors (confirms the `onDecline`/`declineRide` signature changes
    type-check across every caller).
  - `npx expo export --platform web` in `driver-app/` → succeeded, produced
    a real production web bundle including the changed files. This is the
    closest equivalent to `npm run build` this Expo project has.
- [x] Blast-radius grep performed — see section 4 above (every importer of
  `CancelReasonSheet.tsx`, `RideOfferPanel.tsx`, `declineRide`, and every
  caller of the two backend endpoints).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA logging
  (audit rows carry only `driver_id`/`ride_id`, never rider name/phone/
  address — asserted directly in the new backend tests), observability
  (Sentry-tag `domain`/`surface` conventions reused on the info logs),
  additive-over-destructive (no column/table added, no existing value
  changed), do-not-silently-swallow-errors (the new audit inserts use the
  same try/except-log-error pattern already established in this file, not
  a new swallow).
- [ ] Feature-flagged — not applicable; justified above (no meaningful
  on/off state, dormant until a driver opts in by selecting the reason).
- [ ] Manual repro in a running app/staging environment — not performed
  (no staging environment available in this session); verification is
  test- and typecheck-based only, see below.

## 10. What was NOT verified

- **Not exercised against a real device, simulator, or staging backend.**
  The long-press-triggers-`showAlert` flow was verified via jest/RTL
  (`fireEvent(getByText('Decline'), 'longPress')` + asserting on the mocked
  `showAlert` call args) and by a successful `expo export --platform web`,
  not by manually tapping/holding on a running app or watching the alert
  render.
- **No native (EAS) build was run** — only `expo export --platform web`.
  Mobile builds in this repo only trigger via `[build]` in the commit
  message per CLAUDE.md, and a full EAS build was out of scope for a
  same-session verification pass.
- **No automated visual/snapshot regression tooling exists for driver-app**
  (a standing gap, not rediscovered here for the first time). The new long-
  press alert's exact visual appearance (Android's custom `AlertDialog`
  overlay vs. iOS's native `Alert.alert`) was reasoned about from the
  existing, already-shipped `showAlert` implementation, not screenshotted.
- **Discoverability of the long-press decline flag was not user-tested.**
  As noted in section 5, there's no in-UI hint that a long-press exists.
  Whether real drivers will ever find and use it is unverified and is a
  reasonable candidate for a fast follow-up (e.g. a small hint label) —
  not addressed in this pass to keep the change additive/minimal per the
  task's explicit scope.
- **The cancel-side detection is a case-insensitive substring match on a
  free-text column**, not a structured code/flag. If the preset string in
  `CancelReasonSheet.tsx` is edited later without updating the matching
  constant in `ride_cancel.py`, detection silently stops working (no test
  or CI check enforces the two stay in sync beyond the code comments in
  both files pointing at each other). A more robust design (a separate
  `cancellation_reason_code` column) was considered and deliberately not
  built here, per the task's explicit preference for the lower-risk,
  no-new-column option.
- **Automated account-review/suspension on repeated refusals is not
  built** — explicitly out of scope for this pass (see section 3). Today,
  a human would need to query `audit_logs` for `action IN
  ('ride_declined', 'ride_cancel_service_animal_refusal')` and inspect
  `details.reason` / the log lines tagged `domain=safety` by hand; there is
  no dashboard or alerting on top of this yet.
- **No admin dashboard UI to review these events** — also explicitly out
  of scope (see section 3).
- Coverage percentage for `routes/drivers/ride_flow.py` /
  `routes/drivers/ride_cancel.py` was not separately measured (ran with
  `--no-cov` per this session's speed constraints); the new code paths are
  exercised by the new tests but a coverage-tool number for these two
  files specifically was not captured.
