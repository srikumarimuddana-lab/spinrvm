# Live-ride triage — Sentry, ops metrics and evidence (2026-09-22, 12:11–12:20 Regina)

**Status:** findings only — **no code changed.** This is a handoff for whoever picks up the fixes.
**Investigator:** Claude Code session, 2026-09-22
**Trigger:** the operator ran a live test ride (`64fd2674-34c3-4027-8845-4e898dba8ae3`, 18:11–18:20 UTC). They asked for a Sentry review, then server utilization and metrics, then logs and proof for every claim.
**Window:** 17:00–19:05 UTC, with 24 h context.
**Timezone:** all times are **UTC**. Regina is UTC−6 (CST, no DST).

**How to use this.** Each claim is followed by the raw record it rests on:
- a Sentry event, with a link;
- a database row, with the exact read-only SQL so it can be re-run in the Supabase SQL editor;
- a code excerpt (`file:line` on `main` at `f3ecdfd`).

Only IDs were pulled: no names, phone numbers, emails, addresses or GPS.

**Sources**
- Sentry `spinr-backend / crimson-smoke-7445`.
- Supabase production `spinrmobileapp` (`soavhtdhefowwvforzwb`), SELECT only.
- Railway `cooperative-harmony`: services `spinrvm` and `Redis-1mDD`.
- The repository.

**Not reached**
- The Fly.io primary backend (repo policy, see §10).
- The Stripe dashboard (connector failed to connect).
- Vendor status pages (blocked by this environment's network).

---

## Verdicts

| # | Finding | Severity | Proven? | New or known? |
|---|---|---|---|---|
| 1 | The test ride `64fd2674` completed and was charged exactly **$2.61** | no issue | **Yes** (ride row + ledger + both Stripe webhooks) | — |
| 2 | Rider app crash loop on Android | **High** | **Yes** (unhandled crash events + a token refresh on every relaunch) | **New** (first seen 18:06:27 today) |
| 3 | 18:42 "sign out everywhere" → 18:50 false "token theft" alarm → forced sign-outs | **Medium** | **Mostly** — the button press itself isn't logged (§3) | **New** (cross-app case) |
| 4 | Driver app (Android) repeatedly losing its session | **Medium** | Correlation, root cause not proven | Related to 2026-09-13 F2 and the 2026-09-16 background-refresh work (§4) |
| 5 | Insurance Period 2 left open after a rider cancels during an offer | **Medium** (regulatory audit) | **Yes** (rows + code + production-wide count) | **Known, unfixed** — flagged in `docs/change-log/2026-09-21-insurance-release-helper.md`; this adds production impact |
| 6 | Webhook "underpay" alert is a false alarm and leaves `paid_at` empty | Low | **Yes** | **New** |
| 7 | Tips under $0.50 dropped on cards whose hold can't be raised | Low | **Yes** | **Known** — fix shipped off (`docs/change-log/2026-09-21-minimum-tip-one-dollar.md`); still off in production |
| 8 | Meta purchase events rejected (HTTP 400) | Low | **Yes** | Not in `ACTION_ITEMS.md` |
| 9 | Dispatch offers a rider's request to their own driver account | Low | **Yes** | **New** |
| 10 | Server utilization: standby idle, database healthy, 0 server errors; Fly primary not checked | — | Partial | — |

---

## 1. The test ride — completed and charged correctly

**Ride row**
```sql
SELECT id, status, payment_status, payment_method, total_fare, grand_total, tip_amount,
       authorized_amount, auth_status, auth_incrementable, payment_intent_id, payment_retry_count,
       driver_earnings, rider_id, driver_id, ride_requested_at, driver_accepted_at,
       driver_arrived_at, ride_started_at, ride_completed_at, paid_at, updated_at
FROM rides WHERE id = '64fd2674-34c3-4027-8845-4e898dba8ae3';
```
| field | value |
|---|---|
| status / payment_status / payment_method | `completed` / `paid` / `card` |
| grand_total / tip_amount | 2.11 / 0.50 → **owed 2.61** |
| authorized_amount / auth_status / auth_incrementable | 2.11 / `captured` / **false** |
| payment_intent_id | `pi_3UIYAGFXFgLO2LdO0HQqFYI8` |
| payment_retry_count / payment_failure_reason | 0 / null |
| driver_earnings | 0.54 (0.04 fare + 0.50 tip; driver keeps 100 %) |
| rider_id / driver_id | `b08be706-…` / `9aead16b-…` |
| requested → accepted → arrived → started → completed | 18:11:35.6 → 18:11:43.7 → 18:14:05.5 → 18:14:52.0 → 18:19:48.6 |
| paid_at | **null** (see §6) |

**Ledger** — a single charge entry for the full amount:
```sql
SELECT event_type, delta_cents, ref, created_at FROM financial_events
WHERE ride_id = '64fd2674-34c3-4027-8845-4e898dba8ae3';
```
| event_type | delta_cents | ref | created_at |
|---|---|---|---|
| stripe_charge | **261** | pi_3UIYAGFXFgLO2LdO0HQqFYI8 | 18:20:03.013 |

**Stripe's own notifications, as logged by the backend** (Sentry [CRIMSON-SMOKE-7445-C7](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-C7), logger `routes.webhooks`):
```
18:20:02  [webhook][security] underpay ride=64fd2674-… pi=pi_3UIYAGFXFgLO2LdO0HQqFYI8 received=211 owed=261 — refusing to mark paid
18:20:03  [webhook][security] underpay ride=64fd2674-… pi=pi_3UIYIQFXFgLO2LdO09eFnwfy received=50  owed=261 — refusing to mark paid
          (event 008a9187b18648eaa411a89fda322030, Stripe event evt_3UIYIQFXFgLO2LdO06Hu4HQx, server 863994ce20016d)
```
211 + 50 = **261 cents**. These are two Stripe charges, and together they equal what was owed. No third charge appears in the webhook log or the ledger, and `payment_retry_count` is 0.

**Dispatch offer**
| offered_at | responded_at | status | eta_seconds |
|---|---|---|---|
| 18:11:37.960 | 18:11:43.862 | accepted | 102 |

**Driver insurance periods for this ride**: correct.
```sql
SELECT period, ride_id, started_at, ended_at FROM driver_insurance_periods
WHERE driver_id = '9aead16b-47ba-4225-9f75-09b7607d33af'
  AND started_at BETWEEN '2026-09-22 18:10:00+00' AND '2026-09-22 18:29:00+00' ORDER BY started_at;
```
| period | ride | started | ended |
|---|---|---|---|
| 1 | — | 18:10:22.419 | 18:11:38.012 |
| 2 | 64fd2674 | 18:11:38.012 (offer time) | 18:14:52.049 |
| 3 | 64fd2674 | 18:14:52.049 (trip start) | 18:19:48.735 |
| 1 | — | 18:19:48.735 (trip end) | 18:28:13.786 |

**Why there were two charges** (by design). The rider's card couldn't have its hold increased (`auth_incrementable = false`), so the backend captured the $2.11 hold and charged the $0.50 tip separately. From `backend/services/payment_service.py`:
```python
2017    if total_charge > authorized and ride.get("auth_incrementable"):   # skipped: false for this card
2043    capture_amount = _round(min(total_charge, authorized))             # 2.11
2044    cap = await capture_ride(ride_id=ride_id, payment_intent_id=held_pi, amount=capture_amount)
2120    remainder = _round(total_charge - capture_amount)                  # 0.50
2123    if remainder > 0:
2125        over = await charge_ride(... total_amount=remainder ...)       # second PaymentIntent
```
*Limit:* I couldn't open Stripe itself. The amounts above come from Stripe's webhooks and our own ledger.

---

## 2. Rider app crash loop (Android) — HIGH

**Crash events**: Sentry [CRIMSON-SMOKE-7445-110](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-110). All have `error.handled = 0` (unhandled, so the app crashed). Release `com.spinr.user@2.0.0+31`, user `b08be706-…`.

| time | exception (cause) |
|---|---|
| 18:06:27 | addViewAt: failed to insert view [1204] into parent [1232] at index 1 |
| 18:06:36 | addViewAt: failed to insert view [1204] into parent [1232] at index 1 |
| 18:06:46 | addViewAt: failed to insert view [1212] into parent [1240] at index 1 |
| 18:07:16 | addViewAt: failed to insert view [1202] into parent [1230] at index 1 |
| 18:20:10 | addViewAt: failed to insert view [12582] into parent [12610] at index 1 |
| 18:20:56 | addViewAt: failed to insert view [1212] into parent [1240] at index 1 |

Latest event `9be68914f3b84b15bdc7176b3cd6adb0` in detail:
- Top exception: `IllegalStateException: HorizontalScrollView can host only one direct child`.
- Stack: React Native Fabric `SurfaceMountingManager.addViewAt` → `android.widget.HorizontalScrollView.addView`.
- Device and build: Pixel 9 Pro XL, Android 17 (`CP41.260828.004.A8`), OTA update `01a0ca46-bd5a-7c71-83b1-97d47c6f75e5`.
- The app had launched at 18:20:45.7 and was on the route `/notifications` in the foreground.

The driver app crashed once with the same class of error. Sentry [CRIMSON-SMOKE-7445-113](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-113), 18:34:08, `com.spinr.driver@2.0.0+39`, handled 0: `Cannot remove child at index 1 from parent ViewGroup [1], only 3 children in parent`.

**Each crash comes a few seconds after a launch.** The Sentry "cold start" events (`-10Z`, info level, `rider-app cold start`) line up with the crashes:

| launch | crash | seconds |
|---|---|---|
| 18:06:23 | 18:06:27 | 4 |
| 18:06:32 | 18:06:36 | 4 |
| 18:06:41 | 18:06:46 | 5 |
| 18:07:12 | 18:07:16 | 4 |
| 18:20:49 | 18:20:56 | 7 |

**Independent confirmation from the database.** The rider app refreshes its login on every launch. Its session chain (a single chain, started at 02:35) was renewed at exactly the launch times Sentry recorded:
```sql
SELECT id, issued_at, revoked_at, replaced_by FROM refresh_tokens
WHERE user_id = 'b08be706-c82c-42a6-b77c-d78c8d10ec27'
  AND issued_at BETWEEN '2026-09-22 18:05:00+00' AND '2026-09-22 18:23:00+00' ORDER BY issued_at;
```
The renewals were at 18:05:48, **18:05:59, 18:06:23, 18:06:32, 18:06:41**, 18:06:50, **18:07:12, 18:07:21**, **18:20:49**, and 18:22:05. That's 8 renewals in 93 seconds at 18:05–18:07. The 7 times in bold match Sentry cold-start events to the second. The same chain later carries the token (`ab80c867`) behind the 18:50 alarm in §3, which came from an Android client.

**Likely location, not confirmed.** `/notifications` has a horizontal list of category tabs, `rider-app/app/notifications.tsx:258-279`:
```tsx
258      <View style={styles.tabsRow} accessibilityRole="tablist">
259        <FlatList
260          horizontal
261          showsHorizontalScrollIndicator={false}
262          data={CATEGORY_TABS}
```
*Limit:* the native crash report has no JavaScript frames, so it can't name the component. The crash was first seen today (18:06:27), and so far only on this Android 17 device.

Checked and not an obvious cause: `rider-app/patches/react-native+0.86.3.patch` does touch `HScrollViewNativeComponents.js`, but only to add an `import View` (the Android crash workaround from `docs/audit/2026-09-10-react-native-patch-regeneration-handoff.md`). It doesn't change how the horizontal content view is created.

> **Note on the ops-triage "unexplained, climbing" flag for `-10X` / `-10Z` / `-T8`.** These are `level: info` "rider-app / driver-app cold start" markers sent on every launch, not errors. `-10Z`'s spike is the crash loop above.

---

## 3. Sign-out at 18:42 and the false "token theft" alarm at 18:50 — MEDIUM

**Six sessions killed in the same microsecond.** None was replaced; that's the signature of a revoke-everything call, not a normal token refresh.
```sql
SELECT id, issued_at, revoked_at, replaced_by FROM refresh_tokens
WHERE user_id = 'b08be706-c82c-42a6-b77c-d78c8d10ec27' AND revoked_at >= '2026-09-22 18:40:00+00' ORDER BY revoked_at, issued_at;
```
| token row | issued | revoked | replaced_by |
|---|---|---|---|
| 22f49ee6-… | 18:02:30.255 | **18:42:27.450094** | null |
| 6a2cb750-… | 18:03:53.345 | **18:42:27.450094** | null |
| 31ee2558-… | 18:04:59.572 | **18:42:27.450094** | null |
| **ab80c867-…** (rider app) | 18:22:05.725 | **18:42:27.450094** | null |
| 99f43731-… | 18:27:40.221 | **18:42:27.450094** | null |
| 73c2860f-… | 18:41:25.287 | **18:42:27.450094** | null |
| 3b804cde-… (new login) | 18:49:49.153 | 18:50:21.466 (killed by the alarm) | null |
| 7ba4b13c-… (new login) | 18:50:39.614 | 18:50:58.020 (normal sign-out) | null |
| 4c277166-… (new login) | 18:51:51.068 | active | — |

**What was logged around it.** No audit row exists for this account at 18:42:27. The other tester signed out normally 2 seconds earlier, revoking one session:
```sql
SELECT created_at, action, entity_id FROM audit_logs
WHERE created_at BETWEEN '2026-09-22 18:42:00+00' AND '2026-09-22 18:43:00+00' ORDER BY created_at;
-- 18:42:20.054  ride_cancelled   (other tester's ride f187d87f)
-- 18:42:25.786  user_logged_out  entity 3e22f6d7-… (the other tester, 1 session revoked)
--               nothing for b08be706 at 18:42:27
```

**Which code path revokes everything without an audit row.** `POST /auth/logout-all` (`backend/routes/auth.py:2145-2181`) revokes every session and only writes a server log line (`logger.info("logout-all: …")`), which lives on Fly.io.

A search of `rider-app`, `driver-app`, `shared` and `admin-dashboard` found one caller of this endpoint: the driver app's Profile screen, `driver-app/app/driver/(tabs)/profile.tsx:309-321`. The admin dashboard has its own `/api/admin/auth/logout-all`, which only acts on the calling admin's own sessions (`routes/admin/auth.py:654-714`).
```tsx
311      'Sign out of all devices?',
312      'You will be signed out everywhere this driver account is logged in. ...',
320              await logoutAll();
```
The warning says "this driver account", but the rider app runs on the same user account. The phone-number login path gives every app session audience `"rider"` (`routes/auth.py:716, 1175, 1277, 1428`), so the rider app's session is revoked too. The Firebase driver login at `:1629` issues `"driver"`, but no `"driver"`-audience sessions were issued in the last 7 days (213 `"rider"`, 54 `"admin"`).

The other revoke-everything paths don't fit:
- Account deletion (`routes/users.py:495`): the account signed in again at 18:49.
- The reuse cascade: it writes an audit row, and none exists at 18:42.
- Admin logout-all: it only revokes the calling admin's own sessions.

**What happened at 18:50** (Sentry [CRIMSON-SMOKE-7445-C](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-C), event `cbf91d86029b40569713633bb66e967e`, request_id `addebba3-5ca3-4e89-ab01-e3eac12f00ff`, Android client):
```
18:50:21.35  REFRESH TOKEN REUSE DETECTED — possible theft. row_id=ab80c867-… user_id=b08be706-…
             audience=rider original_revoked_at=2026-09-22T18:42:27.450094+00:00 replaced_by=None
18:50:21.57  audit_logs: refresh_token_reuse_detected
18:50:36     REFRESH TOKEN REPLAY (already cascaded)          (Sentry CRIMSON-SMOKE-7445-VW)
```
The rider app, still holding the token that was bulk-revoked at 18:42, tried to refresh. The server treated that as theft. It bumped `token_version`, stamped `sessions_invalid_before`, and revoked everything again, which also killed the fresh 18:49:49 login.

The account row after the alarm:
```sql
SELECT token_version, sessions_invalid_before FROM users WHERE id = 'b08be706-c82c-42a6-b77c-d78c8d10ec27';
-- 19 | 2026-09-22 18:50:21.418515+00
```

**Why a sign-out triggers the theft alarm.** `backend/utils/refresh_tokens.py:126-129`: a rider or driver token that was revoked by a sign-out (`replaced_by` empty) gets no grace period, so any later use escalates:
```python
126    if row.get("replaced_by"):
127        return 0 <= age <= REFRESH_REUSE_GRACE_SECONDS      # 10-min grace only for rotated tokens
128    if row.get("audience") not in _ADMIN_STAFF_AUDIENCES:
129        return False                                          # rider/driver: a signed-out token always escalates
```
The cascade itself, same file: `_handle_refresh_token_reuse` (484) bumps `token_version` (526) and `sessions_invalid_before` (533), then revokes all sessions (550).

Scale: this Sentry issue has **692 events since 2026-06-10**.

*Limit:* I can't prove a person pressed "Sign out of all devices". The endpoint writes no audit row. The Fly.io log line `logout-all: user=b08be706… revoked_refresh=6` would prove it, and I didn't access Fly.

---

## 4. Driver app (Android) repeatedly losing its session — MEDIUM, correlation only

> **Related prior work.**
> - `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md` F2: process death loses the persisted refresh token (observed on iOS).
> - `docs/change-log/2026-09-16-background-token-refresh.md`: background renewal serialized with a SQLite file lock.
>
> What's below is Android, with no process death involved. It shows background refreshes failing at the same moments as foreground renewals. Whether the 09-16 lock is in the running driver build (`2.0.0+39`, OTA `01a0ca47-5eec-706b-8e86-7eb16e422af1`) was not checked.

Three records line up at the same moments:
- Sentry errors from the driver app on the Pixel (`com.spinr.driver@2.0.0+39`, user `b08be706-…`).
- Two back-to-back token renewals in the session table.
- The driver going offline or online (insurance periods for driver `d491bb3c-…`).

| Sentry (driver app) | Session table | Driver status |
|---|---|---|
| 18:02:29 `-10F` "Background token refresh returned invalid credentials"; 18:02:40 `-ZT` "No authorization token provided" | renewed 18:02:24.389 and again 18:02:30.255 | — |
| — | new sign-in (OTP) 18:03:36.231 | offline 18:03:56 |
| 18:03:52 `-10Y` "invalid credentials" | renewed 18:03:52.850 and again 18:03:53.345 (0.5 s apart) | — |
| — | new sign-in (OTP) 18:04:18.715 | online 18:04:24 |
| 18:04:59 `-10Y` "invalid credentials" | renewed 18:04:56.301 and again 18:04:59.572 | **offline 18:05:00** |
| — | new sign-in (OTP) 18:05:23.812 | online 18:05:27, offline 18:05:37 |
| 18:27:39 `-10Y`; 18:28:14 `-SE` "HTTP 401" | renewed 18:27:40.221 | online since 18:27:33 |
| 18:41:24 `-10Y`; 18:41:56 `-SE` "HTTP 401" | renewed 18:41:17.911 and again 18:41:25.287 | online 18:41:24, offline 18:42:19 |

The pattern: each "invalid credentials" error comes as the session is renewed twice in quick succession, which looks like the app's foreground and background refreshes racing each other. Several are followed by a fresh sign-in or by the driver dropping offline.

This is also the best available explanation for why **4 ride requests from the other tester got no offer while this driver account was online.** I checked each request time against this driver's Period 1 intervals:

| request | ride | driver online interval containing it |
|---|---|---|
| 18:30:58 | 1dfae837 | 18:27:34–18:31:14 |
| 18:32:53 | 74d8e788 | 18:31:15–18:33:44 |
| 18:35:17 | b376fafc | 18:34:52–18:35:57 |
| 18:41:52 | f187d87f | 18:41:24–18:42:19 |

*Limit:* the dispatch decision logs are on Fly.io, so the reason no offer was sent is **not proven**.

---

## 5. Insurance Period 2 left open after a rider cancels during an offer — MEDIUM

> **Known, unfixed.** `docs/change-log/2026-09-21-insurance-release-helper.md` already lists this site among the "four further copies not converted". It's cited there as `routes/rides/cancellation.py:524`; it is now `:615-617` on `main`. That entry says it "records no period at all, so every pending-offer driver keeps the Period 2 their claim opened until some later transition happens to close it." This section adds a live occurrence and the production-wide impact.

**The 18:40 test cancel**
- Ride `dcfce424-…`: created 18:40:05.567, cancelled by the rider at 18:40:17.042. The ride row's `driver_id` is null, because offers live in `ride_offers`.
- The offer: to driver `9aead16b-…` at 18:40:08.044, status `cancelled` at 18:40:17.169.
- The insurance record: Period 2 for this ride ran 18:40:08.109 → **18:41:12.451**, when the driver went offline. It should have ended at 18:40:17, so it ran **55 s past the cancel**.

**Code** (`backend/routes/rides/cancellation.py`). The helper that closes the period only runs when the ride row has a driver:
```python
550    if driver_id:                                             # null for an offer-stage cancel
555        await _deps.release_driver_and_close_period(driver_id, reason="rider_cancelled", ride_id=ride_id)
...
615            for offer_row in pending_offers.data:
616                _offer_did = offer_row["driver_id"]
617                await _deps.db_supabase.set_driver_available(_offer_did, True)   # no period close
```
`release_driver_and_close_period`'s own docstring (`backend/utils/insurance_periods.py:258-294`) warns that leaving Period 2 open "claims **primary** commercial cover — worse than the mislabel it replaces."

**Across production**
```sql
WITH p2 AS (
  SELECT p.started_at, p.ended_at, r.cancelled_at
  FROM driver_insurance_periods p JOIN rides r ON r.id = p.ride_id
  WHERE p.period = 2 AND r.status = 'cancelled' AND r.cancelled_by = 'rider' AND r.driver_id IS NULL)
SELECT count(*) AS total,
       count(*) FILTER (WHERE ended_at IS NULL) AS still_open,
       count(*) FILTER (WHERE ended_at > cancelled_at + interval '5 seconds') AS overran,
       max(ended_at - cancelled_at) AS worst,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (ended_at - cancelled_at)))
         FILTER (WHERE ended_at > cancelled_at + interval '5 seconds') AS median_overrun_s,
       min(started_at) AS earliest
FROM p2;
-- total 54 | still_open 0 | overran 13 | worst 06:36:05 | median_overrun_s 55.4 | earliest 2026-08-20
```

---

## 6. Webhook "underpay" alert: false alarm that leaves `paid_at` empty — LOW

The amount check runs **before** the "already settled" check, so a ride already paid by the app still gets flagged. From `backend/routes/webhooks.py`:
```python
874    owed_d = Decimal(str(owed or 0)) + Decimal(str(ride.get("tip_amount") or 0))   # 2.61
877    if received_cents < owed_cents:                     # checked per charge: 2.11 and 0.50
883        logger.error("[webhook][security] underpay ... refusing to mark paid", ...)
892        return {"received": True, "underpaid": True, ...}
901    _already_settled = ride.get("payment_status") in ("paid", "waived_admin", "processing")   # never reached
917        "payment_status": "paid", ... "paid_at": ...   # the only place a card ride gets paid_at
```

Only the rides that took this split-charge path are missing `paid_at`:
```sql
SELECT id, grand_total, tip_amount, authorized_amount, auth_incrementable FROM rides
WHERE payment_method = 'card' AND payment_status = 'paid' AND paid_at IS NULL;
-- 64fd2674-…  2.11 | 0.50 | 2.11 | false   (the test ride)
-- 3ab865d5-…  2.58 | 1.00 | 2.58 | false   (2026-09-15)
```
That's 2 of 291 paid card rides, and both are split charges.

Impact: a search of backend and apps found **no code that reads `rides.paid_at`**. Other `paid_at` hits are the `referral_payouts` table or writers only. So this is data-completeness only.

---

## 7. Tips under $0.50 dropped on cards whose hold can't be raised — LOW

> **Known; fix shipped off and still off.** `docs/change-log/2026-09-21-minimum-tip-one-dollar.md` (migration 438) added `settings.min_tip_amount`. It shipped at 0.00 (off), to be turned on once the updated rider app is live. Production still has it off:
> ```sql
> SELECT min_tip_amount FROM settings LIMIT 1;   -- 0.00 at ~19:20 UTC
> ```
> So the two occurrences below are expected until the setting is raised.

Stripe won't charge less than $0.50 CAD, so the separate tip charge from §1 fails when the tip is under that. Sentry [CRIMSON-SMOKE-7445-10W](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-10W), event `adb663d1e5664313b8e829eee2bd37b4`, 02:23:48:
```
[PAYMENT] over-buffer tip charge failed for ride 55ca3705-… (Request req_78jhMfgysvyLYi:
Amount must be at least $0.50 CAD); captured 2.17, tip collected 0.00, excess 0.05 uncollected
```
In the DB, rides `55ca3705-…` (02:23) and `649d0b9c-…` (02:40, Sentry `-P8`) both show `paid`, `tip_amount 0`, `auth_incrementable false`. For `55ca3705` the log above shows the $0.05 tip was not collected. For `649d0b9c` I only have the ops-triage agent's summary of the same Stripe "$0.50 minimum" error, so it's the same pattern but less directly shown.

The test ride's $0.50 tip was exactly at the minimum, so it went through.

---

## 8. Meta purchase events rejected — LOW

Sentry [CRIMSON-SMOKE-7445-PS](https://spinr-backend.sentry.io/issues/CRIMSON-SMOKE-7445-PS), event `46c154f9c9e14d9f83f6b5bc98e833fb`, 18:20:05, during the test ride's `POST /rides/64fd2674-…/process-payment`:
```
meta_capi: failed to send batch [Purchase] after 3 attempt(s) status=400
"Invalid extended device info parameter" (error_subcode 2804043, is_transient false)
```
The payment request itself returned **200**; the Meta send runs separately (`backend/routes/rides/payments.py:349-371`). **35 events since 2026-08-20.**

---

## 9. Dispatch offers a rider's request to their own driver account — LOW

```sql
SELECT o.ride_id, o.driver_id, d.user_id, r.rider_id, o.status, o.eta_seconds, o.offered_at
FROM ride_offers o JOIN drivers d ON d.id = o.driver_id JOIN rides r ON r.id = o.ride_id
WHERE d.user_id = r.rider_id;
-- 2 rows ever (2026-09-21 17:25 and 2026-09-22 18:28), 0 accepted
-- 18:28:13  ride c95f2f0c-…  driver 9aead16b-… (user 3e22f6d7-…) = rider 3e22f6d7-…  eta 0 s  expired 18:28:28
```
Dispatch has no self-exclusion:
- `backend/routes/rides/matching.py:466-473` (candidate filter);
- `backend/services/dispatch_service.py:234-253` (ranking).

Accepting is blocked (`backend/routes/drivers/ride_flow.py:117-120`, "Cannot accept your own ride"), so the rider just waits out the 15 s offer. That driver was also recorded in Period 2 for 13 s on their own ride.

---

## 10. Server utilization and other metrics

### 10a. Which server handled the test ride
Every backend Sentry event I opened carries `server_name: 863994ce20016d`, which is in Fly Machine ID format. Examples: events `008a9187…` (C7), `46c154f9…` (PS), `cbf91d86…` (C), `adb663d1…` (10W).

Railway `http-requests` for `spinrvm`, last 24 h: **0 requests** (2xx 0, 4xx 0, 5xx 0).

So live traffic goes to the **Fly.io primary**, and the Railway numbers below describe an idle standby.

### 10b. Railway `spinrvm` (standby)
Limits are 8 vCPU / 8 GB. Numbers from the ops-triage agent (`get-service-metrics`):

| window | CPU avg | CPU max | memory avg | memory max |
|---|---|---|---|---|
| 2 h | 0.025 vCPU (0.31 %) | 0.70 (8.8 %, one sample) | 0.97 GB (12.1 %) | 1.01 GB (12.7 %) |
| 24 h | 0.030 (0.37 %) | 1.31 (16.3 %, brief) | 0.96 GB (12.0 %) | 1.89 GB (23.6 %, brief) |

- Network is negligible.
- Status: online, 1/1 replicas, 0 crashes, 0 warnings, 0 failed deploys.
- Active deployment `c0655890-1505-4ef4-8874-b1c99154def7` since 17:28:34 (commit `f3ecdfd`, PR #5705). There were 9 clean deploys today between 13:36 and 17:28.
- A stale Railway "pending work" record from 2026-04-14 (`deploy:3ff0759c…`) is cosmetic.

### 10c. Railway `Redis-1mDD` (24 h)
- CPU: avg 0.0016 of 8 vCPU (0.02 %).
- Memory: **15 MB** of 8 GB.
- Disk: 0.46 GB; network about 0.

It's essentially idle, so it's probably not the Redis the Fly primary uses for presence and rate limits. I can't confirm that without Fly.

### 10d. Supabase Postgres (production)
`get_project`: **ACTIVE_HEALTHY**, region `ca-central-1`, Postgres 17.6.1.127.
```sql
-- run against pg_settings / pg_stat_activity / pg_stat_database / pg_statio_user_indexes
```
| metric | value |
|---|---|
| connections | **14 of 90** (1 active, 6 idle, 0 idle-in-transaction) |
| waiting on locks / longest running query | 0 / 0 s |
| deadlocks since 2026-05-22 | 0 |
| cache hit / index hit | **100.00 %** / 99.97 % |
| database size | 137 MB |
| commits / rollbacks since 2026-05-22 | 18,021,218 / 221,642 (1.2 %) |

Heaviest statements (cumulative since 2026-06-06, `pg_stat_statements`):
- The top 4 are Supabase dashboard and PostgREST schema-introspection queries (mean 0.4–0.9 s).
- App queries are fast: `driver_documents` lookup 0.6 ms mean (403 k calls); `rides` reads 10 ms (22 k calls); `drivers` by status 45 ms (4.6 k calls).
- The one mildly slow app query is the `users` by-ID read: 36 ms mean over 13 k calls, max 1.0 s.

### 10e. Backend request metrics (Sentry performance)
The traces are sampled at 10 %, and counts are Sentry's estimates. Endpoints with small counts rest on 1–3 real samples.

- **HTTP status mix, 24 h:** 200 = 14,441 · 401 = 42 · 404 = 40 · 409 = 10 · **5xx = 0**.
- **All endpoints except `/metrics`, last 2 h:** about 1,276 requests; **p50 125 ms · p95 304 ms · p99 609 ms**.

| endpoint (24 h) | est. count | p50 ms | p95 ms | max ms | target |
|---|---|---|---|---|---|
| `/location-live` (driver location write) | 220 | 213 | **342** | 436 | p95 < 150 → **above** |
| `/location-batch` | 150 | 164 | **309** | 312 | p95 < 150 → **above** |
| `/auth/refresh` | 30 | 155 | 181 | 181 | p95 < 200 → OK |
| `/webhooks/stripe` | 80 | 91 | 1,070 | 1,070 | p95 < 500 → above (few samples) |
| `/rides` (book) | 16 | 304 | 902 | 958 | — |
| `/{ride_id}/cancel` | 10 | 886 | 886 | 886 | — |
| `/{ride_id}/rate` | 20 | 217 | 370 | 370 | — |
| `/nearby` | 150 | 80 | 136 | 140 | — |
| `/{ride_id}` | 90 | 208 | 326 | 326 | — |
| `/rides/active` | 90 | 189 | 240 | 240 | — |
| `/drivers/expiring` (admin) | 157 | 540 | 651 | 1,684 | — |
| `/drivers/approval-queue` (admin) | 156 | 457 | 633 | 2,027 | — |
| `/metrics` (monitoring scrape) | 11,790 | 15 | 26 | 370 | — |

`process-payment` and the fare estimate had no sampled traces in 24 h, so there's no latency data for them.

### 10f. Error volume (ops-triage agent)
- 117 error events project-wide in 24 h.
- Other backend issues in 24 h: `-JK` / `-JH`, a foreign-key violation inserting a notification from `/webhooks/stripe` around 13:00 (1 event); `-P8` and `-10W` / `-10V` (§7).

### 10g. Not checked
- **Fly.io primary:** CPU, memory and logs. Repo policy (`/ops-triage` "Known gap") requires the operator's explicit go-ahead to use this session's Fly access.
- **Stripe / Twilio / Google Cloud status pages:** blocked by this environment's network egress, so vendor health is **unverified**.
- **Redis connector:** failed to connect.

---

## 11. Suggested next steps (not done; each needs its own PR and Change Impact Log)

| # | Suggested fix | Reviewer to run |
|---|---|---|
| 2 | Reproduce on Android 17 with the rider build `2.0.0+31`: cold start, then open Notifications. Check whether the horizontal tab `FlatList` (`notifications.tsx:259`) is the view being re-mounted. | `spinr-design-consistency-reviewer` |
| 3 | Pick one: (a) give a post-sign-out replay from a *different* app on the same account a no-cascade path, as admin tokens already get; (b) make "Sign out of all devices" say it also signs out the rider app; (c) record `logout-all` in `audit_logs` so the trigger is provable. | `spinr-security-auditor` (auth path; pre-merge gate 9 applies) |
| 4 | Check whether the 09-16 background-refresh lock is in driver OTA `01a0ca47…`; if it is, reproduce the Android double renewal. | `spinr-realtime-reliability-reviewer` |
| 5 | Replace the bare `set_driver_available` at `cancellation.py:617` with `release_driver_and_close_period(..., reason="rider_cancelled")`. That's the fix the 09-21 change-log already scoped. | `spinr-insurance-period-auditor` |
| 6 | Move the underpay check after the `_already_settled` check (or skip it when the ride is already `paid`). Stamp `paid_at` in `_finalize_card_settlement`. | `spinr-money-auditor` |
| 7 | Raise `settings.min_tip_amount` once the updated rider app is live (per the 09-21 plan). | operator decision |
| 8 | Fix the device-info (`extinfo`) payload sent to Meta, and stop retrying a non-transient 400. | `spinr-observability-reviewer` |
| 9 | Exclude drivers whose `user_id` equals the ride's `rider_id` from the candidate filter (`matching.py:466-473`). | `spinr-dispatch-reviewer` |
| 10 | Decide whether to inspect the Fly.io primary (CPU, memory, logs). It would also confirm the §3 trigger and the §4 no-offer cause. The driver location write p95 (~340 ms vs the 150 ms target) needs a look either way. | operator decision; `spinr-performance-sla-reviewer` |

---

## 12. Re-check it yourself

- **All key Sentry events in one list** (crashes, underpay, Meta, token alarm): [Discover query](https://spinr-backend.sentry.io/explore/discover/homepage/?dataset=errors&queryDataset=error-events&query=issue%3A%5BCRIMSON-SMOKE-7445-110%2CCRIMSON-SMOKE-7445-113%2CCRIMSON-SMOKE-7445-C7%2CCRIMSON-SMOKE-7445-C%2CCRIMSON-SMOKE-7445-VW%2CCRIMSON-SMOKE-7445-PS%5D&project=4511514049380352&field=id&field=timestamp&field=issue&field=title&field=error.handled&field=release&field=user.id&field=transaction&sort=timestamp&start=2026-09-22T17%3A00%3A00&end=2026-09-22T19%3A10%3A00&utc=true). Fixed window, 17:00–19:10 UTC.
- **Endpoint latency, 24 h:** [Sentry traces explorer](https://spinr-backend.sentry.io/explore/traces/?query=is_transaction%3Atrue+span.op%3Ahttp.server&project=4511514049380352&aggregateField=%7B%22groupBy%22%3A%22transaction%22%7D&aggregateField=%7B%22yAxes%22%3A%5B%22count%28%29%22%2C%22p50%28span.duration%29%22%2C%22p95%28span.duration%29%22%2C%22max%28span.duration%29%22%5D%7D&mode=aggregate&sort=-count%28%29&start=2026-09-21T19%3A10%3A00&end=2026-09-22T19%3A10%3A00&utc=true&table=span). Fixed 24 h window ending 19:10 UTC.
- **Database:** every SQL block above is read-only and can be pasted into the Supabase SQL editor for project `spinrmobileapp`.
- **Code:** line numbers refer to `main` at commit `f3ecdfd`.
