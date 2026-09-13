# Change Impact & Risk Log — live-ride triage (GPS outbox, pre-auth webhook, /live-route, /health)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | srikumarimuddana-lab (with Claude Code) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | payments, rides, drivers, dispatch |
| PR / commit link | _pending_ |
| Related issue or gap ID | Sentry `CRIMSON-SMOKE-7445-` `RK`/`TZ` (SQLite), `T4`/`T5`/`T6`/`TN` (webhook), `QR`/`QS`/`V0`/`V1`/`V2` (live-route), `CB` (health), `JH`/`JK` (notifications FK), `TY` (WS), `TX`/`V4` (Android Auto) |

Triage of the two rides of 2026-09-13 (`SPR-CYDP69` 00:34–00:51, 4 GPS points; `SPR-S5ZKQC` 01:48–02:04, 564 GPS points). Both completed and charged correctly — no money or ride-state damage. Seven distinct defects were found behind them; six are fixed here, one is deliberately deferred (§11).

## 1. Issue / gap identified

1. **Sign-out threw on every device, both platforms.** `purgeAll()` issued SQL that SQLite cannot parse (`near "DO": syntax error`), so unflushed trip GPS points were neither preserved to quarantine nor cleared.
2. **Every booking produced a 500 from `POST /api/v1/webhooks/stripe`** (`Ride lookup failed — Stripe will retry`).
3. **`GET /live-route` 503'd six times** during the 564-point ride, and the live trail returned the **oldest** 200 points rather than the newest.
4. **`/health DB readiness check failed:`** — 111 Sentry events over 6 days with an empty message after the colon.
5. **A mid-trip WebSocket outage was permanent.** After ~198 s of backoff the driver socket gave up for the rest of the trip.
6. **`notifications_user_id_fkey` 23503** logged as an error on the Stripe webhook path.
7. **False "Car location starved" alarms**, including one during the ride that successfully streamed 564 points.

## 2. Root cause

1. SQLite cannot attach an UPSERT to an `INSERT..SELECT` with no `WHERE` — it cannot disambiguate `ON CONFLICT` from a join's `ON`. The other three quarantine inserts in the same file were safe only because they already filter. **The existing test passed because `jest.mock('expo-sqlite')` replaces the engine with a JS class that string-matches SQL and never parses it.**
2. The booking-time pre-auth PaymentIntent is created **before** the ride row is inserted, so its `payment_intent.payment_failed` arrives when no row exists. `rides.created_at` is stamped when the Pydantic model is built — *ahead of* the pre-auth — so it is **not** the insert time and cannot be used to argue the row already existed. Commit `08a9ae1d1` only partially addressed this: its suppression cache is process-local, so one 500 per replica per restart survived. Worse, a genuinely *declined* pre-auth means the row is never inserted at all, so that event 500s on **every Stripe retry for 3 days** — Stripe disables endpoints that fail persistently.
3. `live_breadcrumbs.get_gap_filled_breadcrumbs` ran a sequential, uncached gap-fill loop of up to 20 gaps × (2 s OSRM + 2 s Google), each on a freshly-opened `httpx` client — up to 80 s inside the client's 15 s budget. Whichever `run_sync` landed after it got a near-zero deadline and raised `ServiceUnavailableException` (`9002`). It had never run at volume before because the GPS table was empty. Separately, `get_rows`' `desc` defaults to `False`, so the query asked for the **first** 200 points.
4. `str(asyncio.TimeoutError())` is the **empty string** — verified by execution. The f-string interpolated it to nothing, so the type and traceback never reached the log or the Sentry bridge. All 111 events are the 3 s ping timeout.
5. `reconnectAttemptRef` is reset only on `auth_success` or `AppState 'active'`. A driver with the phone in a dash mount never leaves `'active'`, and the exhaustion branch `return`ed with no timer armed. The advertised recovery ("pull down to retry") does not exist — the dashboard has no `RefreshControl`.
6. `metadata.user_id` is read back off a Stripe object stamped weeks or months earlier and is the one recipient id never existence-checked (`rides.rider_id` and `drivers.user_id` are both protected by the PIPEDA purge's own ordering). A terminal, expected condition logged as a DB fault.
7. `consumeFixCount()` counted only `publishCarFix()`. The car surface's own watcher and its staleness watchdog call `adoptCarFix()` instead, and the phone dashboard's watcher does not touch the channel at all — so the counter read 0 while the pipeline was healthy.

## 3. Fix / remediation

1. Added the SQLite-required `WHERE true` before `ON CONFLICT`, and added a **real-SQLite** test suite that runs the module's actual SQL through `node:sqlite` (built into Node 22+, no new dependency) via the outbox's own `openDatabase` injection point.
2. Ack booking-stage pre-auth failures instead of 500ing — gated on **both** `metadata.source == "ride_booking_authorization"` **and the ride row being absent** (`current is None`). Behind an `app_settings` kill switch. See §13: an earlier revision keyed on the source alone and was rejected in review.
3. Bounded the live gap-fill to 4 connectors / 1.5 s (falling through to the already-tested haversine branch), fetched the **newest** 200 points with an explicit chronological sort, and narrowed the select to the three columns actually read.
4. Log the exception type, the `DatabaseError` original, and the traceback.
5. Report once at the threshold, then keep retrying at the capped 30 s tier; added a `NetInfo` listener so network regain resets the backoff.
6. Reclassify SQLSTATE 23503 to `warning` via the existing `pg_error_code()`; every other error keeps `logger.error` + traceback.
7. Moved the arrival counter into `adoptCarFix()`, the single choke point `publishCarFix()` delegates to.

Also fixed while verifying #2: `payouts.py` called `.get()` on a `stripe.Account`, which raises in stripe-python 15. It was swallowed by a best-effort `except`, so **every driver was asked for their SIN twice** — the exact double-entry `prefill_sin_to_stripe` exists to prevent.

## 4. Risk & impact on existing functionality

**Blast radius, stated per change:**

- `tripLocationOutbox.purgeAll()` — one caller (`sessionTeardown.ts:82` via `tripLocationRecorder.ts:477`). No forked copy in rider-app; not in `docs/known-forks.md`. **Isolated.**
- `_dispatch_stripe_event` — two callers: the live webhook and the admin replay endpoint (`routes/admin/stripe_events.py:238`). Replayed pre-auth orphans now stamp `processed_at`, so they leave the "stuck events" list and a replay returns 409 — intended, they are resolved. `metadata.source` has exactly one producer (`utils/stripe_charge.py:660`) and previously **zero** readers, so this introduces a new producer→consumer contract; siblings are `ride_completion_charge` and `fee_type`, neither of which matches. **Single-surface.** Deliberately **not** touched: the `payment_intent.succeeded` twin, because a captured PI is real money and its unclaim+500 retry is correct.
- `live_breadcrumbs.build_breadcrumb_trail` — one production caller (`routes/rides/tracking.py:142`). `compute_gap_route_via_osrm/google` are **shared** with the post-trip finalizer (`route_reconstruction.py`), which has no client deadline — those functions and `MAX_INFERRED_CONNECTORS` are left untouched. **Cross-surface (rider + driver maps).**
- `_db_ready` — two callers, `/health` and `/ready`, sharing `_health_cache`. Consumers: `fly.toml` check, two Dockerfile `HEALTHCHECK`s, `deploy-fly.yml` post-deploy poll, CI smoke test, `railway.json` healthcheckPath. The change is **log-only**; no status code or body changed.
- `repositories/_base.py` `run_sync` — the choke point for ~66 DB helpers. The added handler catches only `asyncio.CancelledError`, a path that previously released nothing and re-raised regardless, so no currently-working call changes behaviour.
- `features.py` `_record_inbox_notification` — the choke point for ~25 push call sites. Only one SQLSTATE is reclassified, and it was already failing to insert. Delivery, opt-out, throttling and the retry queue all run afterwards and are untouched.
- `useDriverDashboard` — mounted in exactly one place (`app/driver/(tabs)/index.tsx:187`). No new state value, so `DriverTopBar`'s `ConnectionState` import is unaffected.
- `carFixChannel` — 6 non-test importers. `getLastCarFix` is separately read by `register.ts:40` for the SOS payload; this change touches only a counter, never `lastFix`, so the SOS path is unaffected.

**Interactions:**
- Fixing #5 *raises* steady-state WS load. Net-safe: the WS marker write is hard-throttled to ≤1 per 3 s per driver because it passes `unthrottled_before=False`, which honours the window regardless of the `location_marker_write_gate_enabled` flag.
- No ride state transition, wallet delta, or insurance-period write is touched by any change here. Change #2 *prevents* a write (`payment_status="failed"`) that should never have happened pre-trip.

**Latent bug found and fixed in passing (§2.4's neighbour):** when `/health`'s 3 s `wait_for` cancelled a `run_sync` that happened to be the circuit breaker's single half-open probe, `_probe_in_flight` leaked `True` and **every DB call 503'd until process restart** — `CancelledError` is BaseException-only, so neither existing handler saw it while every other exit path released the probe.

## 5. User-experience effect

| Who | Change | Visible mid-session? |
|---|---|---|
| Rider | Live trip map now advances past 200 GPS points instead of freezing on the first third; `/live-route` stops 503ing. Gap-filled trail may fall back to straight-line interpolation after 4 gaps (previously up to 20) — slightly less road-snapped on a very gappy trip. | **Yes** — a rider mid-ride sees the map behave differently. |
| Rider | No longer receives a "Payment Failed ❌" push at booking time for a hold that is about to succeed on the fallback. This is the one deliberate user-visible suppression. | Yes |
| Driver | A mid-trip socket drop now recovers instead of staying dead. Error copy changed from "Unable to connect to server. Pull down to retry or toggle offline/online." (which advertised a gesture the screen does not implement) to "Connection lost — still retrying." | **Yes** |
| Driver | Will no longer be asked for their SIN twice during Stripe onboarding. | No (onboarding only) |
| Driver | Sign-out no longer throws; unflushed points are preserved. | No |
| Internal admin | Pre-auth orphans no longer appear as stuck Stripe events. `/health` logs now name a real cause. | No |
| Corporate admin | None. | — |

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/tripLocationOutbox.ts` | `WHERE true` before `ON CONFLICT` in `purgeAll()` | SQLite cannot parse the UPSERT without it |
| `driver-app/utils/__tests__/tripLocationOutbox.realsqlite.test.ts` | **New** — real-SQLite suite via `node:sqlite` | The existing suite mocks SQL away and cannot catch a parse error |
| `driver-app/hooks/useDriverDashboard.ts` | Exhaustion branch reports once then keeps retrying; new `NetInfo` reset effect | A foregrounded driver never recovered mid-trip |
| `driver-app/lib/androidAuto/carFixChannel.ts` | Arrival counter moved into `adoptCarFix()` | Counter saw one of four producers |
| `backend/routes/webhooks.py` | `_PREAUTH_METADATA_SOURCE` + flagged ack branch | Pre-auth failures are unlinkable and move no money |
| `backend/tests/test_webhook_payment_failed_guard.py` | New `TestPreauthStageFailureIsAcked` (7 tests) | Pins the ack, the no-write, the no-push, and both over-reach guards |
| `backend/utils/live_breadcrumbs.py` | Connector budget/cap; `desc=True` + chronological sort; narrowed `columns` | 80 s of HTTP in a 15 s budget; oldest-200 correctness bug |
| `backend/server.py` | `/health` failure logs type + original + traceback | `str(TimeoutError())` is empty |
| `backend/repositories/_base.py` | `except asyncio.CancelledError` releases the breaker probe | Leaked probe 503s the whole API until restart |
| `backend/tests/test_db_run_sync_cancellation_releases_probe.py` | **New** — 3 tests pinning the probe release, the untouched success path, and that cancellation still propagates | Riskiest change in the batch; `run_sync` is the choke point for ~66 DB helpers |
| `backend/features.py` | 23503 reclassified to `warning` | Terminal expected condition, not a DB fault |
| `backend/routes/drivers/payouts.py` | `.get()` → `getattr` on `stripe.Account` | `.get()` raises in stripe-python 15 |
| `backend/tests/test_driver_sin_collection.py` | 8 dict mocks → real `stripe.Account` objects | Dict mocks hid the raise |

## 7. Before / after

**#1 — the parse error**
```sql
-- Before (raises: near "DO": syntax error)
SELECT ... FROM trip_location_outbox
ON CONFLICT(session_id, sequence_number) DO NOTHING
```
```sql
-- After
SELECT ... FROM trip_location_outbox
WHERE true
ON CONFLICT(session_id, sequence_number) DO NOTHING
```

**#3 — the oldest-200 bug**
```python
# Before — get_rows' `desc` defaults to False: the FIRST 200 points
points = await get_rows(..., order="captured_at", limit=MAX_BREADCRUMB_POINTS)
```
```python
# After — the LAST 200, restored to chronological order
points = await get_rows(..., order="captured_at", desc=True,
                        limit=MAX_BREADCRUMB_POINTS, columns="lat,lng,captured_at")
points.sort(key=lambda p: parse_iso_utc(p.get("captured_at")) or _SORT_FLOOR)
```

**#5 — the permanent give-up**
```ts
// Before
if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
  setConnectionState('disconnected');
  setWsError('Unable to connect to server. Pull down to retry or toggle offline/online.');
  captureException(...);
  return;                       // no timer armed — dead for the rest of the trip
}
```
```ts
// After
if (reconnectAttemptRef.current === MAX_RECONNECT_ATTEMPTS) {
  setWsError('Connection lost — still retrying.');
  captureException(...);        // report ONCE, then fall through to the capped tier
}
```

**#4 — the empty message**
```python
# Before — logs "/health DB readiness check failed: " and nothing else
_logging.getLogger(__name__).error(f"/health DB readiness check failed: {exc}")
```
```python
# After — type, original, traceback
_logging.getLogger(__name__).error(
    "/health DB readiness check failed after %.1fs: %s: %s%s",
    _HEALTH_PING_TIMEOUT, type(exc).__name__, _detail,
    f" | original={_orig}" if _orig else "", exc_info=True,
)
```

## 8. Rollback plan

| Change | Rollback without redeploy |
|---|---|
| #2 webhook ack | Set `app_settings.webhook_preauth_failure_ack_enabled = false`. The guard only *stops* writes, so there is no data to unwind. |
| #3 live-route | `LIVE_MAX_CONNECTORS` / `LIVE_CONNECTOR_BUDGET_S` are module constants — **redeploy required**. Acceptable: the fallback path is the pre-existing, already-tested haversine branch, and the change cannot produce a wrong polyline, only a less road-snapped one. If a flag is wanted, add one beside `driver_turn_by_turn_enabled` before merge. |
| #4, #6 | Log-only. No rollback needed. |
| #5, #7, #1 | Client-side; requires an OTA/JS update to revert. No persisted state is written differently, and #1 strictly replaces a throw with a successful write. |
| `_base.py` CancelledError | Redeploy. The handler only releases a lock on a path that previously released nothing and re-raised either way. |

No migration, no schema change, no backfill. Nothing in this batch writes to `rides.status`, `driver_insurance_periods`, or any wallet.

## 9. Verification performed

- [x] **Automated tests run (final, after the §13 blocker fixes).** Backend: **126/126** across `test_webhook_payment_failed_guard.py` (24), `test_live_breadcrumbs.py`, `test_live_route.py`, `test_db_run_sync_cancellation_releases_probe.py` (3), `test_server_coverage.py`, `test_driver_sin_collection.py`. driver-app: **372/373** across 36 suites (`hooks/__tests__`, `__tests__/hooks`, `lib/androidAuto/__tests__` 234, the new real-SQLite suite 6) — the single failure is the pre-existing CRLF artifact documented below.
- [x] **`ruff check` and `ruff format --check`** clean on all 9 changed/added backend files.
- [x] **`tsc --noEmit`** clean for every changed driver-app file.
- [x] **Rename safety.** `connectWebSocket` was split into a private `openWebSocket` plus a mutex wrapper; grepped the driver-app test tree for source-text references to either name — none exist, and all 5 reconnect call sites go through the wrapper.
- [x] **Regression tests proven to actually fail without the fix.** Reverting the `WHERE true` makes the new suite fail with the exact production error, `near "DO": syntax error`. This is the check the old mocked suite could not perform.
- [x] **Root causes proven by execution, not inference.** `str(asyncio.TimeoutError())` was executed and reproduces the Sentry title's bare trailing colon verbatim. The SQLite parse failure was reproduced against real SQLite 3.45.3, and the three sibling `ON CONFLICT` statements were each confirmed to parse. `stripe.Account.construct_from(...).get()` was executed and raises; `getattr` returns `True`.
- [x] **Soft-delete hypothesis retired on evidence** — `SELECT deleted_at` on both rides returned null, and each settled on a *different* PI than the one in the failed webhook, confirming the doomed-pre-auth diagnosis rather than a missing row.
- [x] **Blast-radius greps performed** — searched for callers of `purgeAll`, `_dispatch_stripe_event`, `build_breadcrumb_trail`, `_db_ready`, `_record_inbox_notification`, `useDriverDashboard`, `carFixChannel`, `adoptCarFix`/`publishCarFix`, `MAX_INFERRED_CONNECTORS`, `Account.retrieve` in tests, plus `docs/known-forks.md` for a forked outbox (none). Results in §4.
- [x] **`ruff check`** clean on all 8 changed backend files.
- [x] Reviewed against CLAUDE.md's Decimal-only money rule (no arithmetic added), dual-import pattern (preserved in `live_breadcrumbs.py` and the new `pg_error_code` import), no-silent-swallowing rule (§3.6 reclassifies exactly one SQLSTATE and keeps the traceback on every other), and the loguru-vs-stdlib `exc_info=` trap (`server.py`'s `_db_ready` uses the **stdlib** logger, so `exc_info=` is honoured — confirmed before using it).
- [x] Feature-flagged: #2, the only change that alters what a live-tested money path does.

## 10. What was NOT verified

- **No run against live Supabase or live Stripe.** All backend tests use `mock_supabase_client` / patched bindings.
- **No device build.** Every driver-app change here is JS and was reasoned about plus unit-tested; none was exercised on a physical Android or iOS device. Per CLAUDE.md gate 6, **driver-app has no visual-regression tooling of any kind** (its `e2e/` holds functional Playwright specs with `screenshot: 'only-on-failure'`, a debugging artifact, not a baseline). The rider-facing live-map change in #3 is likewise reasoned about, not screenshotted — rider-app has no visual tooling either. No admin-dashboard page was touched, so the merge-blocking Playwright visual job is not involved.
- **`onlineResync.test.ts:52` fails on a Windows working tree** — proven pre-existing and unrelated: running **pristine `HEAD` content with CRLF endings** reproduces the identical failure with zero changes applied. The tree is `w/crlf` (`git ls-files --eol`) and the assertion hardcodes `\n`. Expected to pass in CI on Linux; **not** re-verified on Linux from here.
- **Two items still need production confirmation:** the `waited_seconds` value on the 8 `Executor wait` Sentry events (would falsify the budget-exhaustion diagnosis if ≥ 1 s); and whether migration 239's `idx_dlh_ride_captured` is actually applied, since migration 08's `CREATE TABLE IF NOT EXISTS driver_location_history` was a no-op in production and index state cannot be inferred from the repo.

## 10a. Two items CLOSED on evidence after the fixes above (live Stripe + Supabase)

**The Sep-12 reconciliation gap is not a missed webhook — it is a foreign charge on the same Stripe account.** Reconciled live Stripe against Supabase for 2026-09-12 UTC:

| Charge | Amount | Captured | `metadata` shape | In `financial_events`? |
|---|---|---|---|---|
| `ch_3UF0L8…` | **2200¢** | yes | `{booking, type, user_id}` — **Mongo ObjectIds** | **NO** |
| `ch_3UEwNF…` | 219¢ | yes | `{ride_id, rider_id, source}` | yes |
| `ch_3UEwMl…` | 219¢ | **no** (auth only) | `{ride_id, rider_id, source}` | n/a |
| `ch_3UEwLR…` | 219¢ | **no** (auth only) | `{ride_id, rider_id, source}` | n/a |
| `ch_3UEvOZ…` | 218¢ | yes | `{ride_id, rider_id, source}` | yes |
| `ch_3UEg7V…` | 323¢ | yes | `{ride_id, rider_id, source}` | yes |
| `ch_3UEfnL…` | 336¢ | yes | `{ride_id, rider_id, source}` | yes |

Captured total `2200+219+218+323+336 = 3296¢` — exactly the alert's `stripe=3296¢`. The five reconciled rows `219+218+323+336 = 1096¢` — exactly its `db=1096¢`. **The entire $22.00 gap is the single charge `pi_3UF0L8FXFgLO2LdO1gQKkc44`**, captured 2026-09-12 23:28:10 UTC against customer `cus_VFUlYf4aRpV7sn` (a different rider from the test account).

Its metadata is `{"booking": "6aa5d89dca6c0d82be72f2aa", "type": "card", "user_id": "6aa5d5d0ca6c0d82be722535"}` — a `booking` key and 24-hex **MongoDB ObjectIds**, not this backend's `ride_id`/`rider_id`/`source` UUID shape. Verified in Supabase: that user id is **not** in `users`, that booking id is **not** in `rides`, there is **no** `financial_events` row for the PI, and `users` contains **zero** Mongo-shaped ids at all. So this charge was created by a different system sharing the same live Stripe account, not lost by this one. **No money is missing from Spinr's ledger.** Operational follow-up (not a code fix here): either separate the Stripe accounts or have `utils/stripe_reconcile.py` scope its comparison to charges carrying this backend's own `metadata.source`, otherwise this alert will keep firing daily.

**That same charge is also the cause of the `notifications_user_id_fkey` 23503.** The Sentry event is timestamped 2026-09-12 **23:28:11** — one second after that charge was created at 23:28:10. The webhook read `metadata.user_id = "6aa5d5d0ca6c0d82be722535"`, which does not exist in `users`, and the insert violated the FK. This confirms §2.6's root cause on evidence and identifies the literal offending value; the `features.py` change handles exactly this case.

## 11. Deliberately deferred

**The recurring Android map crash** (`IllegalStateException: Cannot remove child at index 1 from parent ViewGroup`, `com.google.android.gms.dynamic.DeferredLifecycleHelper`, 5 events, still occurring). Diagnosed as a confirmed bug in `react-native-maps@1.27.2`: `MapView.onDetachedFromWindow` clears its `features` list behind React's back while restore is async, `removeFeatureAt` has no bounds check, and `safeAddFeature` uses `set` where `remove` uses `remove` — so any mid-list insert desyncs permanently. Amplified by the driver dashboard re-keying up to 24 sibling `<Polyline>`s on ride-state change.

Not fixed here because the remedy is a `patch-package` patch to native Java that **cannot be verified without a physical Android device**, and this repo's recorded experience is that inferred native rendering fixes fail — three inferred car-marker fixes failed in one day before a device observation identified the real cause. It needs a device repro first. Tracked as the top follow-up.

**Two facts for whoever picks it up, so the decision does not have to be re-derived:**

1. **Fix it in the `react-native-maps` patch, not in `shared/components/RouteLine.tsx`.** The tempting app-side fix — give `RouteLine` a stable child count instead of a variable one — would ship to **8 screens across two apps** (6 rider-app screens: `driver-arrived`, `driver-arriving`, `ride-completed`, `ride-details`, `ride-in-progress`, `ride-options`; plus `driver-app/app/driver/(tabs)/index.tsx` and `driver-app/app/driver/ride-detail.tsx`; `RoutePins` adds `lib/androidAuto/carSurface.tsx`). And there is **zero real test coverage of either component anywhere in the monorepo**: every consumer's test stubs them to `() => null`, and the only two suites that do not stub assert on *import-statement source text* rather than rendering. Per CLAUDE.md pre-merge gate 1 that is zero coverage, not coverage — so a shared-component change would reach 8 live screens with no visual tooling and no functional test that renders it even once. The native patch has zero JS blast radius by comparison.
2. **The patch must be added to `rider-app/patches/` as well as `driver-app/patches/`.** rider-app has its own `node_modules`, renders the same variable-child-count polyline pattern on 6 map screens, and consumes a third `MapView` surface (`AppMap`) — so it is exposed to the identical crash. Patching only driver-app would leave riders crashing.

## 12. Sign-off

- [x] Rollback plan is concrete and testable (§8), with the two redeploy-only cases named and justified rather than glossed.
- [x] Blast radius is stated per change, not assumed (§4).
- [x] No silent behaviour change to an already-shipped flow — the four user-visible effects are enumerated in §5, including the suppressed booking-time push and the changed driver error copy.

## 13. Adversarial review findings (CLAUDE.md pre-merge gate 10) — 3 blockers found and fixed

Reviewer agents were run against the actual diff, not the plan. They rejected three things. Recording them because each was a real defect that unit tests alone would not have caught.

**Blocker 1 — `spinr-money-auditor`: the pre-auth ack could swallow a REAL payment failure.**
The first revision gated the ack on `metadata.source` alone. But Stripe metadata is stamped once at PaymentIntent creation and **never updated** — `increment_authorization` and `capture_ride` pass no `metadata` kwarg, so the *same* PI carries `source: ride_booking_authorization` for the ride's entire life. A **capture declined at settlement** (`services/payment_service.py`'s `_settle_against_hold`) is therefore indistinguishable by source from a booking-time hold failure — and it is a genuine failure on a real, linked, completed ride. Worse, this handler's pushes are its **only** rider/driver notification, because `settle_card` returns early before its own push block. The ack would also have made the event permanently unreplayable, since admin replay refuses an already-processed event (409).
*Fix:* the ack now additionally requires `current is None` — the ride row must be absent. That covers exactly the intended orphan case and makes swallowing a capture failure structurally impossible. Regression test `test_capture_decline_on_an_existing_ride_is_still_recorded` added, and **verified to fail against the rejected design** (I temporarily reinstated source-only gating and confirmed it goes red).
*Also noted and acted on:* the auditor observed the original test class only ever used `ride_row=None`, so the adversarial case was untested. Two tests were added for existing-ride shapes.

**Blocker 2 — `spinr-realtime-reliability-reviewer`: the new NetInfo trigger was a reconnect-storm vector.**
`connectWebSocket` awaits `ensureFreshToken()` before assigning `wsRef.current`, so it always yields. The AppState `'active'` listener and the new NetInfo listener both run "if the socket looks closed, reconnect" with no in-flight guard — a driver picking up a locked phone as signal returns could trigger both before either assigns, producing **two live authenticated sockets**. The orphan is never closed (`onclose`'s `ws !== wsRef.current` guard correctly no-ops it) and its `onmessage` still dispatches, so **ride offers would be processed twice**. Separately, the NetInfo handler had no debounce: a flapping connection would zero `reconnectAttemptRef` on every tick, pinning every retry at tier 0 (~1s) and defeating the 30s cap entirely.
*Fix:* `openWebSocket` (the real connect) is now private, wrapped by a `connectWebSocket` that holds a `wsConnectingRef` mutex; all 5 reconnect sites go through the wrapper. The NetInfo handler takes a 5s cooldown **before** zeroing the counter.

**Blocker 3 — same reviewer: `desc=True` needed a NULL guard.**
Postgres sorts NULLs **first** under `ORDER BY … DESC`, so a row with no `captured_at` would consume one of the 200 newest slots ahead of genuinely recent points. This is the exact failure migration 371 was written to fix on the same table and column (ride SPR-PE7TTB — an 11-minute mid-trip outage went undetected), and such rows are still reachable today because `routes/websocket.py` falls back through `device_timestamp`/`timestamp` and can persist a NULL.
*Fix:* added `"captured_at": {"$notnull": True}` to the filter, matching 371's guard.

**Accepted, not fixed (pre-existing, filed rather than forced):** `future.cancel()` does not stop a callable already running on an executor thread, so an outer cancellation leaves the Supabase call running with its result discarded. This is true of the pre-existing `TimeoutError` branch too and is not introduced here; the one path this batch actually adds (`/health`'s ping) is a harmless `SELECT`. The residual risk is a non-conditional write whose caller treats `CancelledError` as "definitely didn't happen" — worth its own ticket, not a change in this diff. Ride acceptance is already self-healing against it (the atomic `{'status': 'searching'}` filter makes a duplicate claim return 0 rows).
