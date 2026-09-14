# Live-ride triage — driver-app mid-ride process death (2026-09-13, 13:38–13:53 Regina)

> Superseded conclusions: see [the validated findings and plan](2026-09-13-last-two-test-rides-findings-and-plan.md).
> PR #5348 review corrected the native-capture claim, confirmed the middle ride
> was batch-offered, and distinguished fallback distance from a proven fare change.
> The native termination cause remains unknown. The insurance fix is migration 421;
> no production application or historical correction has occurred.

**Status:** findings only — **no code changed yet.** This is a handoff for the agent picking up the fix.
**Investigator:** Claude Code session, 2026-09-13
**Trigger:** operator ran two live test rides and reported "iOS crashed on the second ride, I had to log in again."
**Timezone:** all times below are **Regina (CST, UTC−6, no DST)** unless suffixed `Z`.

> **Read this first:** the headline is *not* a new regression. The mid-ride process-death class was
> already characterised on **2026-09-11** (see `driver-app/app/_layout.tsx:470-474`: *"seven mid-ride
> process deaths … five of them left no crash record at all"*). This document adds a second dated
> occurrence, a fully-correlated timeline, and three **new** findings that fell out of it — the most
> actionable being F2 (session loss) and F3 (fare billed off the estimate when GPS dies).

---

## 1. Scope — what actually happened

Three rides, one rider (`3e22f6d7…`), one driver (`drivers.id = 483bf09e…`, `users.id = 6d2732f9…`):

| Ride | Code | Window | Status | GPS points |
|---|---|---|---|---|
| `5827b146-d69f-44d9-b1bb-86c3bc88fd19` | SPR-5BNURH | 13:38:00 → 13:43:26 | completed | **189** ✅ |
| `ac38399b-c754-489b-b405-f03f1cb08885` | SPR-HLYUDM | 13:45:54 → 13:46:07 | cancelled by rider, 13 s in, never dispatched | 0 |
| `294ed8e4-e963-487f-beae-c6fd4fbeb034` | SPR-BCPJPV | 13:46:19 → 13:53:15 | completed | **5** ❌ |

Ride 1 was healthy. Ride 3 ("the second ride") ran essentially blind.

### Correlated timeline

Three independent sources agree to the second: Sentry lifecycle markers, `refresh_tokens` rows, and
`driver_insurance_periods`.

```
13:36:12  Android driver token 5b5e1bc8 issued (okhttp/4.12.0)
13:38:00  Ride 1 booked ───────────────────────────────── healthy, 189 fixes
13:43:26  Ride 1 completed
13:45:18  Insurance Period 0 opens  ← app fully off, 29 s hole mid-session
13:45:22  Android token 5b5e1bc8 REVOKED, replaced_by = NULL  ← chain broken
13:45:30  ★ driver-app cold start (Sentry, iPhone15,3 / iOS 26.2.1)
13:45:43  FRESH LOGIN #1 — token 04ffe5d7, SpinrDriver/29 (iOS), no predecessor
13:45:47  Period 1 resumes
13:45:56  Period 2 opens, ride_id = ac38399b (the ride that gets cancelled)
13:46:19  Ride 3 booked
13:46:36  Ride 3 starts → Period 3
13:46:54  GPS fix 1
13:47:44  GPS fix 2
13:49:17  GPS fix 3
13:50:29  ★★ driver-app COLD START — MID-RIDE ★★
   …  3 min 53 s with zero location data, app unusable  …
13:53:10  FRESH LOGIN #2 — token 5db45710, no predecessor
          (04ffe5d7 was still valid and NOT revoked — see F2)
13:53:14  GPS fix 4  +  "Points fall outside completed ride retention window"
13:53:15  Ride 3 completed
13:53:24  GPS fix 5  +  rider-app cold start
13:53:29  Payment captured, $2.31
```

---

## 2. Findings

### F1 — Mid-ride process death on iOS, uncaptured (SEV: high, pre-existing)

The driver app restarted twice (13:45:30, 13:50:29). The second was mid-ride and cost ~4 minutes of
tracking.

**Evidence:** `captureMessage('driver-app cold start', …)` fires from `driver-app/app/_layout.tsx:475`
on the cold-start init effect, so each marker is a genuine process (re)start, not a remount.
Corroborated by `driver_insurance_periods` Period 0 at 13:45:18 and by two no-predecessor logins.

**Not a new regression.** `_layout.tsx:470-474` documents seven such deaths on the 2026-09-11 test
ride. Sentry issue `CRIMSON-SMOKE-7445-T8` shows `firstSeen 2026-09-11T23:28:11Z` — but that is
**40 minutes after `ac4f83a2a` (2026-09-11 16:48) added the fingerprint**, so first-seen marks when
the marker started grouping, *not* when the problem began. Do not read it as an onset date.

**Cause not established.** The only device context captured, from the 13:50:29 event:

| Field | Value |
|---|---|
| `thermal_state` | **`serious`** |
| `launch_duration` | **32.8 s** |
| `app_memory` / `free_memory` | 83 MB / 1.4 GB — **not** memory pressure |
| device / OS | iPhone15,3 / iOS 26.2.1 |
| runtime / RN | expo runtime 2.7.0, RN 0.86.3, Hermes, Fabric, `turbo_module: true` |
| `is_embedded_launch` | `false` (running an OTA bundle) |

Thermal pressure is the leading hypothesis — it is consistent with sustained GPS + map rendering, and
with two deaths five minutes apart — but it is **one data point and is not proven.**

**Blocking sub-finding — no iOS crash capture exists.**
- Sentry: `tags[surface]:driver-app error.handled:false` → **zero events in 24 h.**
- TestFlight: `testflight_crashes(com.spinr.driver)` → **"No crash submissions found."**
- `ac4f83a2a` added **Android** tombstone + NDK app-hang capture. iOS got no equivalent.

Until iOS native crash capture lands, root-causing F1 is guesswork. **Fix the instrumentation first.**

---

### F2 — Process death loses the persisted refresh token (SEV: high, NEW) ⭐

This is the most actionable finding and the one the operator felt directly.

At **13:53:10** the app performed a **fresh login** (`5db45710`, no predecessor) while its previous
refresh token `04ffe5d7` was **still valid and had never been revoked**.

That asymmetry is the whole finding:
- If the app had *presented* a token and been rejected, the server would show it revoked. It doesn't.
- Therefore the app **never presented it** → the token was **gone from device storage** after the
  13:50:29 process death.

Compare the rider app across the same window: unbroken rotation chain
`4f7599d3 → 021c5510 → 3cac5492`, three cold starts, **zero** re-logins. The driver app is the outlier.

**Why this is surprising:** `driver-app/__tests__/store/authStore.initialize.test.ts` exists precisely
to pin this behaviour — *"Users got sent back to the OTP screen every cold start"* — and asserts that
`initialize()` falls back to `refresh_token` when `auth_token` is absent. Code under test:
`shared/store/authStore.ts::initialize` (refresh path). **That regression test passes, yet the live
device re-authenticated twice.** Either the path is bypassed on iOS, or the SecureStore write is lost
on abnormal termination, or the running bundle predates the fix.

**Note on the 13:45:22 revocation** — the *first* re-login has a different and legitimate-looking
cause: Android token `5b5e1bc8` was revoked with `replaced_by = NULL`, which is a logout or a
reuse-detection revocation, not a rotation. Device-switch (Android → iOS) plausibly explains login #1.
**It does not explain login #2.** Treat them separately; #2 is the bug.

**Where to look:** `shared/store/authStore.ts` (`initialize`, `setTokens`), SecureStore write
durability on iOS, and whether `setTokens()` deleting persisted `auth_token` leaves a window where
neither token is on disk.

---

### F3 — Fare bills the estimate when GPS dies (SEV: medium, NEW)

Ride 3 recorded `actual_distance_km = 2.37`, **exactly equal** to `planned_distance_km = 2.37`.
With 5 GPS fixes there was nothing to measure, so settlement fell back to the estimate.

Contrast ride 1: `planned 2.06` vs `actual 2.796` — a real **+36%** delta. The measured distance on a
healthy ride is materially different from the estimate, so silently substituting the estimate is a
real mis-billing path, in either direction.

Money at stake here was trivial ($2.31), **but the mechanism is systemic** and fires on every
GPS-starved ride. Per `CLAUDE.md`, anything touching fare settlement needs the money-auditor path.

Two `ride_location_gap_events` rows for ride 3 carry `status = 'unresolved_at_completion'` — **the
backend knew it had lost the driver and completed + captured payment anyway.** Whether that should
block settlement, flag for review, or reconcile later is a product decision, not an obvious bug fix;
raise it rather than silently picking one.

**Where to look** (verified to exist):
- Gap detection / finalisation: `backend/utils/route_gap_monitor.py`, `backend/utils/route_finalizer.py`
  (tests: `backend/tests/test_route_gap_monitor{,_coverage}.py`)
- `actual_distance_km` readers/writers: `backend/services/fare_service.py`,
  `backend/routes/drivers/ride_complete.py`, `backend/routes/rides/_shared.py`,
  `backend/routes/admin/rides.py`, `backend/services/incentive_service.py`
- Note `incentive_service.py` also consumes `actual_distance_km` — an estimate-substituted distance
  may propagate into driver incentives, not just the fare. Include it in the blast radius.

---

### F4 — Insurance Period 2 missing for ride 3, misattributed to the cancelled ride (SEV: medium, regulatory, NEW)

```
13:45:56 → 13:46:36   Period 2   ride_id = ac38399b   ← CANCELLED at 13:46:07
13:46:36 → 13:53:15   Period 3   ride_id = 294ed8e4
```

Ride `294ed8e4` has **no Period 2 row at all**, despite the driver being en route to its pickup from
13:46:25 (`driver_accepted_at`) to 13:46:36. That window is booked against a ride that had already
been cancelled 18 seconds earlier.

**Root cause — confirmed in SQL.** `backend/migrations/253_insurance_period_transition_rpc.sql:40-46`:

```sql
SELECT period INTO v_current_period
FROM driver_insurance_periods
WHERE driver_id = p_driver_id AND ended_at IS NULL;

IF v_current_period IS NOT NULL AND v_current_period = p_new_period THEN
    RETURN jsonb_build_object('status', 'noop', 'closed', 0, 'opened', false);
END IF;
```

**The no-op check compares `period` only. It never looks at `ride_id`.** So when
`backend/routes/rides/matching.py:1268` fired `record_period_transition(driver, 2, ride_id='294ed8e4…')`
for the new ride, the driver was already in an open Period 2 (for the cancelled `ac38399b`) → the RPC
returned `noop` → **the new ride's Period 2 row was never inserted, and the open row kept the stale
`ride_id` of a cancelled ride.**

**The code comment contradicts the implementation.** `backend/routes/drivers/ride_flow.py:396-398`
claims the function *"no-ops if Period 2 **with this ride_id** is already open … and **re-confirms
it's open with the right ride_id** if anything closed it between claim and accept."* The RPC does
neither — it no-ops on period alone and never re-confirms or corrects `ride_id`. Whoever fixes this
should correct the comment too, or the next reader will re-derive the same wrong mental model.

Verified call sites of `record_period_transition`: `core/lifespan.py`, `routes/admin/rides.py`,
`routes/rides/matching.py` (:1268, the claim/offer trigger), `routes/drivers/{profile,ride_cancel,
ride_complete,ride_flow,status,subscriptions}.py`. Python wrapper: `backend/utils/insurance_periods.py:65`
— it only validates and delegates; all dedupe logic is in the RPC.

Note `ride_cancel.py` **is** a call site, yet cancelling `ac38399b` at 13:46:07 did not close its
Period 2 row (it stayed open until 13:46:36). Worth checking as part of the same fix.

`CLAUDE.md` is explicit that these rows are a regulatory/insurance-liability surface and append-only.
**Append a correction row — never mutate or delete the existing ones.** A schema change here is a
migration against a live table; `driver_insurance_period_corrections` already exists and may be the
intended vehicle.

---

### F5 — Ride-3 GPS fixes look computed, not device-read (SEV: low, unexplained)

| Ride | Accuracy values |
|---|---|
| Ride 1 (189 fixes) | `1.02`, `1.53`, `2.04`, `2.55`, `3.57`, `4.08`, `5.10`, `6.63`, `13.87` — device-quantised (multiples of ≈0.5102) |
| Ride 3 (5 fixes) | `2.00000000176076`, `1.9999999483692`, `2.00000001760303` — float-artefact values around exactly 2.0 |

Ride 3's speeds carry the same signature (`9.74674676722621` vs ride 1's clean `8.75`, `10.35`).
These look **derived/interpolated**, not raw CoreLocation fixes. Possibly a fallback path, a
server-side backfill, or a replay of the planned route.

**Unresolved.** Worth identifying what wrote them before trusting any GPS-derived number from a
degraded ride. Related Sentry signal in-window: `SpinrApiError: Points fall outside completed ride
retention window` at 13:53:14, driver-app surface.

**Where to look:** `driver-app/utils/tripLocationRecorder.ts` (the on-device outbox; the F1 cold-start
marker fires from its `.catch` at `driver-app/hooks/useDriverDashboard.ts:790`), and the server-side
ingest that stamps `accuracy`. Prior art worth reading first — this subsystem has recent history:
`6ba7f0fcd` *serialise trip-location outbox SQLite operations* (2026-09-11) and `12a10cb40`
*sign-out threw on an unparseable SQLite upsert* (2026-09-12).

⚠️ A second copy exists at `.worktrees/corrected-bplus-roadmap/driver-app/utils/tripLocationRecorder.ts`.
Confirm which tree you are editing; check `docs/known-forks.md` before changing either.

---

### F6 — Driver app is issued `audience: 'rider'` refresh tokens (SEV: low–medium, NEW)

Every token for driver user `6d2732f9…` has `audience = 'rider'`, **including both fresh logins from
`SpinrDriver/29`**.

**Cause located:** `backend/routes/auth.py` hardcodes `audience="rider"` at the token-issuing call
sites — **lines 693, 1125, 1227, 1363** — irrespective of which app is authenticating. Note the same
file carries a `B-P1-1 / DV-10: enforce audience binding unconditionally` block at :1412 and an
audience-bound driver path at :1485, so the intent to separate audiences clearly exists; the refresh
token issuance just doesn't honour it.

May be cosmetic if audience is never enforced on read — in which case the finding is *that it is not
enforced*, which is the more interesting version. Check against the JWT trust model in `CLAUDE.md`
before deciding severity.

---

## 3. Answering "was this caused by a recent code change?"

**Short answer: the process-death class predates today's commits.** It was documented 2026-09-11.

Two driver-app map commits landed on `main` this morning, hours before the rides:

| Commit | Merged (Regina) | Subject |
|---|---|---|
| `b37adfa26` | 2026-09-13 05:57 | `fix(driver-app): stop re-keying the route subtree on ride state` |
| `78b883294` | 2026-09-13 07:58 | `fix(driver-app): restore the rideState key main dropped (merge origin/main)` |

Both touch route-subtree keying — the exact "map child churn" area, and the current branch is
`claude/map-child-churn-mitigation`. Both reached the device: `.github/workflows/eas-build.yml`
("EAS Mobile Update") **auto-publishes an OTA to the `production` channel on every push to `main`
touching `driver-app/**` or `shared/**`**, and `gh run list` confirms successful runs at
`13:49:27Z` and `13:58:42Z` (= 07:49 / 07:58 Regina). The device reported
`expo.updates.channel: production`, `is_embedded_launch: false`, `checkAutomatically: always`.

**But an OTA cannot be the trigger for the mid-ride restart.** `reloadAsync`, `fetchUpdateAsync` and
`checkForUpdateAsync` appear **nowhere** in `driver-app/` — verified by ripgrep across `*.ts`/`*.tsx`.
With no programmatic reload, `expo-updates` downloads in the background and applies on the *next
natural launch*; it cannot restart a running app mid-ride.

So: **not ruled in, not fully ruled out.** The map commits were live on the device and are plausible
contributors to thermal/render load, but nothing ties them to the restarts, and the same failure
predates them by two days. The 32.8 s `launch_duration` is *consistent with* applying a freshly
downloaded bundle, which would fit the 13:45:30 launch picking up the 07:58 OTA.

**Do not start by reverting the map commits.** Land iOS crash capture (F1) and get one real crash
report; that answers this question definitively instead of by elimination.

---

## 4. Recommended order of work

1. **F1-instrumentation — iOS native crash capture.** Everything else is inference until this exists.
   Android got tombstone/NDK capture in `ac4f83a2a`; iOS has nothing. Highest leverage.
2. **F4 — insurance Period 2.** The only finding with a **confirmed root cause read from source**
   (migration 253's period-only no-op check). Regulatory, small, and fully understood — the cheapest
   real win here. Note it is a live-table migration, so it still needs the full gate.
3. **F2 — session loss.** Directly hurts every driver who suffers a process death, and there is
   already a regression test to extend rather than a design to invent.
4. **F3 — fare-on-estimate fallback.** Needs a product decision first; do not unilaterally change
   settlement behaviour.
5. **F5 / F6** — investigate and report; no fix implied yet.

## 5. Gates that apply (from `CLAUDE.md`)

- Rides, payments, auth and safety are **all** in scope here → Change Impact & Risk Log entry is
  **mandatory** for any commit, plus a blast-radius grep of every consumer before writing the fix.
- `shared/store/authStore.ts` is shared by rider-app **and** driver-app — F2 must not be fixed in one
  app's copy alone. Check `docs/known-forks.md`; this is the exact failure mode the 2026-09-12 audit
  called out.
- Insurance-period rows are **append-only**. Never mutate or delete.
- rider-app and driver-app have **no** visual-regression tooling — the "reasoned about, not
  screenshotted" disclosure is mandatory for any UI-visible change.
- Money/state-machine changes need a `mock_supabase_client` dry run plus a concrete before/after
  scenario.

## 6. Verification queries

Supabase project `soavhtdhefowwvforzwb` (`spinrmobileapp`, ca-central-1):

```sql
-- Gap events for the affected rides (expect 2 rows 'unresolved_at_completion' on 294ed8e4)
select * from ride_location_gap_events
where ride_id in ('5827b146-d69f-44d9-b1bb-86c3bc88fd19',
                  '294ed8e4-e963-487f-beae-c6fd4fbeb034')
order by created_at;

-- The F2 smoking gun: fresh logins with no predecessor, prior token never revoked
select id, issued_at, revoked_at, replaced_by, audience, user_agent
from refresh_tokens
where user_id = '6d2732f9-59cd-4b36-b53f-e42eda95a63c'
order by issued_at desc limit 10;

-- F4: note the missing Period 2 for 294ed8e4
select started_at, ended_at, period, ride_id
from driver_insurance_periods
where driver_id = '483bf09e-459e-4351-8c57-94029228d7c8'
  and started_at > '2026-09-13 19:30:00+00'
order by started_at;

-- F5: accuracy values on ride 3 vs ride 1
select received_at, ride_id, accuracy, speed
from driver_location_history
where driver_id = '483bf09e-459e-4351-8c57-94029228d7c8'
  and received_at between '2026-09-13 19:37:00+00' and '2026-09-13 19:56:00+00'
order by received_at;
```

Sentry — org `spinr-backend`, project `crimson-smoke-7445`:
- Cold-start markers: issue `CRIMSON-SMOKE-7445-T8` (escalating; 49 events / 16 users)
- Window sweep: `timestamp:>2026-09-13T19:35:00 timestamp:<2026-09-13T19:56:00`
- Absence-of-crash check: `tags[surface]:driver-app error.handled:false` → expect **zero**

## 7. What was NOT verified

- **Root cause of the process death.** Thermal is a hypothesis from a single event's context. No
  native crash report exists on any platform for these two deaths.
- **Fly.io logs were unavailable.** The live buffer only reached back to ~14:00 Regina; the rides
  were 13:38–13:53. Backend-side request logs for the incident window are **gone** — Supabase and
  Sentry carried the entire investigation. If backend log retention matters for live testing, that
  is its own gap.
- **Which commit the running OTA bundle (`01a09b13-4a8d-76b3-a593-8bfa6fdf7d41`) was built from.**
  Not resolved to a SHA, so "was the F2 auth fix in the running bundle?" is **open**. Resolve via EAS
  before concluding the fix is broken rather than absent.
- **Whether F2 reproduces on Android**, or is iOS-specific. Only iOS was observed re-authenticating;
  the Android session in the same window ended by revocation, which is a different path.
- **F4 is the only finding with a root cause read directly from source** (migration 253's RPC body).
  It is high-confidence. Every other finding is telemetry + inference.
- **F3's settlement logic was never read.** The files are located and confirmed to exist, but *how*
  the estimate gets substituted for the measured distance was inferred from the DB values
  (`actual_distance_km == planned_distance_km` exactly), not from reading `fare_service.py`. Confirm
  the mechanism before changing it.
- **F2's failure mode is inferred, not observed in code.** "Token was gone from device storage" is
  the only explanation consistent with a fresh login against a valid, never-revoked server-side
  token — but `shared/store/authStore.ts` and the SecureStore write path were **not** read.
- **No tests were run and nothing was built** in this session. Findings are from production
  telemetry, SQL, and git history only.
- **Nothing was committed.** This file is the sole artifact; no source file was modified.
