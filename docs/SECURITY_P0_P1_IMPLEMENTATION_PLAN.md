# Security P0/P1 Implementation Plan

**Branch:** `claude/p0-p1-security-payment-plan-vnooax`
**Scope:** one P0 (unauthenticated document access), four P1s (orphan Stripe refunds, token-refresh concurrency + frequent driver logout, GPS-blind dispatchable driver, email-OTP brute force), six P2s.
**Status of findings:** every item below was re-verified against the current tree (line numbers current as of the branch point, `main@5f75cd9`). Corrections to the original report are called out inline.

---

## Workstream 1 — P0: Unauthenticated access to driver government-ID documents

### Verified facts

- `backend/documents.py:875` — `files_router = APIRouter(prefix="/documents", tags=["Files"])`, **no** `dependencies=[...]`.
- `backend/documents.py:977-1003` — `get_document_file(file_id: str)` takes zero auth. Two branches:
  - `:981-991` legacy: base64-decodes a `document_files` row and streams it.
  - `:997-1001` current: loads `driver_documents` by id and 302-redirects to `doc["document_url"]` — which `upload_file` (`:951`) stores as a **live Supabase signed URL** (1-hour TTL). An anonymous caller with any document UUID gets a working link to a driver's licence/insurance/registration.
- Mounted twice: `backend/server.py:403` (`/api`) and `:404` (`/api/v1`). `/api/documents` is already in `_DEPRECATED_API_PREFIXES` (`server.py:41`).
- `document_files` is **dead**: only production read is `documents.py:982`; writers exist only in tests (`tests/test_documents.py:266,283,302`, `tests/test_db.py:112`); no migration creates it; the upload docstring (`documents.py:898-903`) says the base64-in-DB approach was replaced.
- Auth deps are already imported in this file (`documents.py:12-19`): `get_current_user`, `get_admin_user` from `backend/dependencies/__init__.py` (`:345`, `:600`).
- Ownership pattern to copy verbatim: `delete_driver_document` (`documents.py:675-710`) — resolve driver via `get_rows("drivers", {"user_id": current_user["id"]}, limit=1)`, then `if doc.get("driver_id") != driver["id"]: raise HTTPException(403, ...)`.
- Route-ordering caveat: `admin_documents_router` (same `/documents` prefix, admin-guarded, `documents.py:98-102`) is mounted before `files_router` inside the v1 tree, so only unmatched subpaths fall through to the open handler — but that is exactly the `/{file_id}` case.

### Fix (2 commits, ≤3 files each)

**Commit 1 — `fix(security): require auth + ownership on document file serving`**
Files: `backend/documents.py`

1. Change the handler signature to `async def get_document_file(file_id: str, current_user: dict = Depends(get_current_user))`.
2. Delete the `document_files` legacy branch (`:981-991`) entirely — dead code, and it is the only branch that streams bytes with no owner column to check.
3. For the `driver_documents` branch, authorize before redirecting:
   - If `current_user["role"]` is an admin-class role → allow (or, cleaner: check via the same allowlist `get_admin_user` uses; simplest correct form is: attempt driver-ownership first, else require admin — mirror `documents.py:675-710`).
   - Otherwise resolve the caller's driver row (`get_rows("drivers", {"user_id": current_user["id"]}, limit=1)`); 404 if none; 403 if `doc["driver_id"] != driver["id"]`.
   - Keep 404 for unknown `file_id` (present today at `:1003`).
4. Do **not** remove the dual mount yet — legacy `document_url` values embed `/api/v1/documents/{id}` (`server.py:400-402`); auth applies equally to both mounts.

**Commit 2 — `test(security): pin auth + ownership on /documents/{file_id}`**
Files: `backend/tests/test_documents.py`

- Unauthenticated GET → 401.
- Authenticated non-owner driver → 403.
- Owner driver → 302 to signed URL.
- Admin → 302.
- Remove/adapt the now-dead `document_files` fixtures at `test_documents.py:266,283,302`.
- Keep `test_deprecated_route_admin_exempt.py:51` green (deprecation header behavior unchanged).

### Rollout / breach-protocol note

Per CLAUDE.md, exposure of government-ID documents is P0-incident class. After merge, two follow-ups **outside this PR** (flag to the team, do not block the fix):
1. Audit access logs for `GET /api(/v1)?/documents/{uuid}` from non-authenticated sources to scope whether the hole was exploited (24h scope assessment per breach protocol).
2. Consider re-issuing signed URLs / rotating the storage bucket policy since previously-leaked signed URLs live for 1 hour only (self-expiring — low residual risk).

---

## Workstream 2 — P1: Frequent driver logout (token-refresh concurrency cluster)

This workstream merges the original "token-refresh concurrency flaws" finding with the user-reported symptom **"drivers get signed out often, sometimes right after completing a ride."** Root-cause investigation confirmed five interlocking defects.

**Why it clusters right after ride completion (confirmed mechanism):** the driver app runs **two independent auth stacks** — the foreground shared client, and the background location task which uses raw `fetch` with its own token cache precisely so it can refresh outside the shared client (acknowledged in-code at `driver-app/utils/tripLocationTransport.ts:19-22`). At `completeRide` (`driver-app/store/driverStore.ts:704-792`) the foreground does a **forced 8-second drain of the location outbox** (`flushPendingWithTimeout(..., 8_000)`, `:714-717`) plus the complete-ride call, WS-triggered `fetchEarnings`, and activity refetches — while the background task is *simultaneously* draining the **same outbox** on its own stack at 4-second trip cadence (`backgroundLocation.ts:178`). Ride completion is the single highest-concurrency moment for the cross-context refresh race: if the 15-min token crosses either refresh threshold there (foreground <2 min, background <60 s), both contexts rotate the shared single-use refresh token; the loser gets a hard 401 (the backend's 600 s grace suppresses only the revocation cascade, never the 401), the foreground polls SecureStore for only 450 ms, finds nothing fresher, and logs out at `shared/store/authStore.ts:334-335`.

Two design parts were verified **sound** and must not be "fixed": foreground 401s correctly coalesce onto a single `_refreshPromise` with a subscriber queue (`client.ts:782-784`), and the hard sign-out path at `client.ts:737-768` is guarded by `_suppressRefreshSignOutDepth` (`:751-753`) held by every foreground refresh — the live logout site is `authStore.ts:334-335`, not the client interceptor.

### Root causes (all confirmed in code)

Ranked by contribution to the observed logouts:

| # | Defect | Where |
|---|--------|-------|
| 1 | Foreground's rotation-race recovery polls SecureStore for only **450 ms** (`[0,150,300]`), but the background refresh takes 1–5 s (Firebase AppCheck init + network) before persisting its rotated token — foreground gives up and calls `logout()`. The existing regression test for this bug class (`driver-app/__tests__/store/authStore.refreshRace.test.ts`) mocks SecureStore as instant, so it can never catch the timing hole | `shared/store/authStore.ts:322`, `:334-335`; `backgroundLocation.ts:72-73,96` |
| 2 | Transient network failure during startup refresh bounces the driver to `/login` with a **valid 30-day refresh token still in SecureStore** — no retry, `hasNavigated` latches; highest-frequency for drivers cold-starting in parking garages/dead zones | `shared/store/authStore.ts:457-463` (also `:442-447`), `driver-app/app/index.tsx:13-24`, `driver-app/hooks/useAuth.ts:25` |
| 3 | Background task computes token expiry from **server absolute time** vs device clock — with clock skew ahead of server, its cache check fails forever and it does a **full token rotation on every location fire** (4 s in-trip / 30 s idle), converting #1 from occasional race into continuous storm | `driver-app/utils/backgroundLocation.ts:95` vs `:50-53`; foreground does it skew-immune at `shared/store/authStore.ts:239` |
| 4 | Two refresh actors (foreground client + background task) share one single-use rotating credential with **no lock, no CAS, no cross-context notification** — structural collision roughly every 13–14 min given the 15-min TTL and 2-min/1-min thresholds; background write-back is blind last-write-wins | `shared/api/client.ts:162,168-171`; `backgroundLocation.ts:52,61,96`; `authStore.ts:83-118,244` |
| 5 | Backend rotation is immediate and non-atomic: the in-grace "benign replay" path (`REFRESH_REUSE_GRACE_SECONDS = 600`) still returns `None` → 401, so the race loser is always rejected — recovery is 100% client-side; and `lookup_refresh_token` has no atomic claim, so two simultaneous refreshes can mint two live chains, orphaning one | `backend/utils/refresh_tokens.py:73,91-107,189,211-218`; `backend/routes/auth.py:1463-1469,1520-1526` |
| 6 | SecureStore read **error** is swallowed to `null`; a null token candidate with a live session triggers `logout()` — a transient Android keystore error becomes a sign-out (low frequency) | `shared/store/authStore.ts:88-94`, `:259-272` |

Ruled out: single-session enforcement (explicitly removed, `backend/dependencies/__init__.py:410-412,467-470`), session kills on go_online/go_offline (none exist), foreground clock skew (expiry is relative), and DB blips (refresh endpoint correctly returns 503, `backend/routes/auth.py:1493-1502`).

Additional confirmed client flaws from the original finding (same files, same workstream):

- **Unbounded 401→refresh→retry loop** — `shared/api/client.ts` has no attempt counter; each verb's `retryFn` re-enters the public method and mints a fresh closure (`:982, :1011, :1045, :1074, :1102`); `refreshAttempted` (`:732,:780`) is per-`handleApiError`-invocation only. The 503 branch already contains the exact fix template: `_inflight503Retries` keyed `"${method} ${url}"` (`:724, :834-855`).
- **Proactive-refresh subscriber deadlock** — `ensureFreshToken()` (`client.ts:164-187`) sets `_refreshPromise` (`:112`) but never calls `_onRefreshed` (only call sites are `:814,:819,:822` in the 401 branch) and nulls the promise in `finally` (`:185`) without flushing `_refreshSubscribers`. A 401 arriving during a proactive refresh subscribes at `:789-790` to a promise **nobody ever settles** — the request hangs indefinitely (the 15 s abort does not apply; the fetch already returned).
- **Keystore-error logout** — the SecureStore wrapper swallows read errors to `null` (`authStore.ts:88-94`); combined with `:258-272`, a transient Android keystore error while a session is live triggers `void get().logout()`.

### Fix plan (ordered by impact; 6 commits)

**Commit 1 — `fix(driver-app): compute background token expiry from relative expires_in`**
Files: `driver-app/utils/backgroundLocation.ts`
Store `Date.now() + expires_in * 1000` instead of parsing the server's absolute `access_expires_at` (`:95`). Kills the every-4-seconds rotation storm — the single highest-impact line in this plan.

**Commit 2 — `fix(shared): single refresh owner — background task stops rotating the shared token`**
Files: `driver-app/utils/backgroundLocation.ts`, `shared/store/authStore.ts`
Route background refreshes through one owner instead of a second independent actor. Preferred design: the background task **never** calls `/auth/refresh` itself; it reads the foreground-persisted access token, and if expired, defers the upload to the outbox (it already defers on refresh failure, `backgroundLocation.ts:82-85` — this makes deferral the only path). Foreground proactive refresh (2-min buffer) keeps tokens fresh far more often than the background 30 s cadence needs. If a headless-only refresh is truly required (app killed, geofence re-arm), gate it behind a SecureStore mutex + generation counter both contexts respect, and have the background write-back use compare-and-swap against the value read at `backgroundLocation.ts:61`.
Extend the race window as defense-in-depth: `authStore.ts:322` poll `[0,150,300]` → `[0, 250, 500, 1000, 2000]`.
Update `driver-app/__tests__/store/authStore.refreshRace.test.ts` — its SecureStore mock resolves instantly and cannot catch the timing hole; add a delayed-mock variant.

**Commit 3 — `fix(shared): cap 401 refresh-retries and settle proactive-refresh subscribers`**
Files: `shared/api/client.ts`
1. Retry cap: mirror the `_inflight503Retries` pattern (`client.ts:724,834-855`) with a `_inflight401Retries` set keyed `"${method} ${url}"` — one refresh-retry per logical request; on a second 401 for the same key, surface the error instead of recursing. (Avoids changing the five call-site signatures.)
2. Deadlock: in `ensureFreshToken()`'s `finally` (`:185`), flush subscribers — call `_onRefreshed(newToken)` on success and, on failure, reject/drain `_refreshSubscribers` so queued requests fail fast instead of hanging (the `/auth/refresh`-401 guard at `:737-768` already reasons about not stranding subscribers; unify with it).
3. Better: extract one `coordinateRefresh()` used by both `ensureFreshToken` and the 401 branch so there is a single settle path.

**Commit 4 — `fix(shared): transient refresh failure is not a logout`**
Files: `shared/store/authStore.ts`, `driver-app/app/index.tsx`, `driver-app/hooks/useAuth.ts`
Introduce a distinct `sessionRecoverable` (or `authStatus: 'retrying'`) state when `initialize()` fails transiently while a refresh token exists (`authStore.ts:457-463`); router shows a "reconnecting" state instead of `/login` (`index.tsx:21-24`); add an AppState-resume re-`initialize()` retry. Also stop treating a SecureStore read **error** as an absent token (`authStore.ts:88-94` + `:258-272`): distinguish `null` (absent) from thrown (retry, don't logout). Apply the same recoverable state to the `/auth/me`-failed path (`authStore.ts:442-447`).

**Commit 5 — `fix(auth): atomic refresh rotation + benign-replay returns current tokens`**
Files: `backend/utils/refresh_tokens.py`, `backend/routes/auth.py`
1. Make lookup+rotate atomic: conditional update (`UPDATE ... WHERE id = :id AND revoked_at IS NULL RETURNING ...`) so exactly one of two simultaneous refreshes wins; loser falls into the replay path.
2. Change `_is_benign_rotation_replay` (`refresh_tokens.py:91-107,211-218`) from "log + return None (→401)" to returning the **already-minted successor tokens** for in-grace replays. This eliminates the entire client race class server-side: the race loser gets valid tokens instead of a 401. Keep the out-of-grace reuse cascade (`_handle_refresh_token_reuse`) untouched.
3. Requires storing the successor's tokens retrievably for the grace window (e.g. successor row id via `replaced_by` + re-mint access JWT; do **not** store raw refresh tokens — return the successor only if its hash chain matches, or mint a fresh access token bound to the successor session). Design detail to settle in review; the fallback if too complex: return a distinct error code (`REFRESH_RACE_LOST`) the client maps to "re-read storage and retry" instead of logout.

**Commit 6 — `test(auth): refresh rotation race + replay grace regression suite`**
Files: `backend/tests/test_refresh_tokens.py` (extend), `driver-app/__tests__/store/authStore.refreshRace.test.ts`
Backend: concurrent refresh → exactly one rotation; in-grace replay → success (or `REFRESH_RACE_LOST`), never a session-killing 401; out-of-grace reuse → cascade unchanged. Client: delayed-SecureStore race test; 401-loop cap test; deadlock test (401 during proactive refresh resolves).

### Acceptance criteria

- A driver completing a ride with device clock skewed +5 min stays signed in.
- Airplane-mode app launch shows "reconnecting", then recovers on network return without OTP.
- An endpoint that persistently 401s fails after exactly one refresh-retry (no spinner hang, no hammering).
- Backend: two concurrent `/auth/refresh` calls with the same token → both callers end with a valid session.

---

## Workstream 3 — P1: Unlinked Stripe refunds vanish from the books

### Verified facts

- `backend/routes/webhooks.py:897-906` — both orphan branches of `charge.refunded` (`payment_intent` matches no ride; charge has no `payment_intent`) do `logger.warning` and fall through to `mark_stripe_event_processed(event_id)` at `:1517`. Stripe never retries; no ledger row; reconciliation blind spot. Direct violation of the "never `logger.warning` and continue on a payment error" rule.
- The `payment_intent.succeeded` orphan pattern to mirror (`:566-588`): `logger.error` → `unclaim_stripe_event(event_id)` (`:578`, releases the idempotency claim so Stripe's retry re-processes; `logger.critical` at `:582-587` if the unclaim itself fails) → `raise HTTPException(500)` forcing retry.
- Orphan-persistence precedent already exists: `charge.dispute.created` inserts a `stripe_disputes` row with `ride_id=None` (`:915-941`).
- Admin broadcast template: `manager.broadcast_to_admins` block at `:953-970` including the lazy import and the WS-failure guard.
- Helpers: `claim/mark/unclaim_stripe_event` in `backend/repositories/wallet_repo.py:306/356/379`; `record_refund_event` in `backend/services/payment_service.py:183`.

### Fix (3 commits)

**Commit 1 — `migration: orphan_stripe_refunds table`**
Files: `backend/migrations/NN_orphan_stripe_refunds.sql` (pick next free number via `ls backend/migrations | sort -V | tail -1`; run `/migration-check` conventions — RLS admin-only, append-only)
Columns: `id, stripe_event_id UNIQUE, charge_id, payment_intent_id NULL, amount_refunded, currency, created_at, resolved_at NULL, resolved_ride_id NULL, notes`. `UNIQUE(stripe_event_id)` makes the write idempotent under Stripe retries.

**Commit 2 — `fix(payments): orphan charge.refunded — persist, alert, never silently drop`**
Files: `backend/routes/webhooks.py`
Replace both `logger.warning` branches (`:897-906`):
1. `logger.error` with `extra={"domain": "payments", "event_id": ..., "payment_intent": ...}`.
2. Upsert an `orphan_stripe_refunds` row (idempotent on `stripe_event_id`). Refund amounts through `_d()`/`_round()` per money conventions.
3. `broadcast_to_admins` alert (copy `:953-970` block, type `orphan_refund`).
4. Only fall through to `mark_stripe_event_processed` **after** the orphan row write succeeds; if the write fails, mirror `:578-588` — `unclaim_stripe_event` + `HTTPException(500)` so Stripe retries. (Persist-then-process is chosen over always-force-retry because an orphan refund can be a *permanently* unlinked event — e.g. a manual dashboard refund — and endless Stripe retries would trip false "STUCK" alerts, the same reasoning as the underpay case at `:600-615`.)
5. Emit `spinr_payment_settlement_total{outcome=failed}` (or a dedicated orphan counter in `utils/metrics.py` if adding a metric — follow snake_case naming).

**Commit 3 — `test(payments): orphan refund webhook regression`**
Files: `backend/tests/test_webhooks.py` (or the existing webhook test module)
Cases: orphan refund → row written + processed marked + admin broadcast; duplicate delivery → single row (idempotent); row-write failure → unclaim + 500; linked refund path unchanged.

Run the `spinr-money-auditor` agent over the diff before merging (touches refunds).

---

## Workstream 4 — P1: Email-OTP path has no brute-force lockout

### Verified facts

- `backend/routes/auth.py:637-702` — `verify_company_email_otp`: only slowapi `5/minute` (IP-keyed, XFF-spoofable). No `_check_otp_lockout` before lookup, no `_record_otp_failure` on the wrong-code branch (`:658-665`), no `_clear_otp_failures` on success (`:697-702`). Code space is 4–6 digits (`:120-122`).
- Helpers are **generic-string keyed** despite the `phone` param name — `_FAIL_KEY = "otp_fail:{}"` / `_LOCK_KEY = "otp_lock:{}"` (`:127-128`): `_check_otp_lockout` (`:176`), `_record_otp_failure` (`:209`), `_clear_otp_failures` (`:244`), `_enforce_otp_send_cap` (`:142`).
- Caveat: `_record_otp_failure` logs `phone[-4:]` (`:225,:231-235`) — a raw email would leak its last 4 chars. Fix: key by the existing `_synthetic_phone_for_company_email(email)` → `"email:{sha256[:32]}"` (`:466-468`), which makes the slice harmless and matches how the corporate session already pseudo-identifies.
- Phone-path reference wiring: `verify_otp` at `:720` (check), `:751-753` (record), `:835` (clear); DB-error path deliberately does not count a failure (`:733-749`) — the email path already mirrors that part.

### Fix (1 commit)

**Commit — `fix(auth): wire OTP lockout into company-email verify + send cap`**
Files: `backend/routes/auth.py`, `backend/tests/test_auth_send_otp.py`
1. In `verify_company_email_otp`: `key = _synthetic_phone_for_company_email(email)`; `await _check_otp_lockout(key)` after normalizing the email; `await _record_otp_failure(key)` on the invalid-code and expired branches; `await _clear_otp_failures(key)` on success.
2. In `send_company_email_otp` (`:589-591`): add `await _enforce_otp_send_cap(key)` (30 s min-interval + 5/hour per destination) to close the send-side gap symmetrically.
3. Tests: extend `test_auth_send_otp.py` (template at `:136-215`) — 5 failures → 429 with `Retry-After`; lockout persists across the hour; success clears the counter; DB error does not count as a failure. Keep `test_rate_limit_response_shape.py:193-230` green.

---

## Workstream 5 — P1: GPS-blind driver stays dispatchable

### Verified facts

- `driver-app/hooks/useDriverDashboard.ts:646-742` — `watchPositionAsync` has **no error-handler third argument**, and the enclosing IIFE (`:641-744`) has no `.catch()`: a rejection (permission revoked between going online and the call) is an unhandled rejection and `locationSubRef.current` stays null. Same gap in `driver-app/lib/androidAuto/useCarLocation.ts:67`.
- Revocation is only detected on foreground: `refreshLocation` (`:422-512`, permission check `:435-449`) runs on mount and AppState `'active'` only (`:514-521`). The 30 s GPS heartbeat (`:555-585`) checks `hasServicesEnabledAsync` only, and only during trip phases — never while online-and-idle.
- When detected, the app only blocks the UI (`driver-app/app/driver/(tabs)/index.tsx:652-689`) — **no** `updateDriverStatus(false)`, so `is_online` stays true server-side and dispatch keeps offering rides. (The only mid-lifecycle self-heal is the go-online-time background-permission rollback at `useDriverDashboard.ts:1416-1422`.)
- `backgroundLocation.ts` no-ops silently on revocation: `:236-239` bare returns; geofence re-arm ignores `startBackgroundLocation`'s `false` and logs success anyway (`:314-321`); `startGeofenceRecovery`'s `false` return is ignored at its call site (`useDriverDashboard.ts:1429-1431`).
- Server-side toggle: `PUT /api/v1/drivers/{driver_id}/status` via `updateDriverStatus` (`shared/store/authStore.ts:582-604`).
- **Backend gap:** `route_gap_monitor` (15 s tick) covers `in_progress` rides only; `stale_intent_reconciler` (15-min loop, 4-hour threshold) is keyed on WS/presence heartbeat, not GPS freshness. Nothing detects "online, idle, no GPS fix for N minutes, WS still alive."

### Fix (3 commits — client watchdog, backend sweeper, tests)

**Commit 1 — `fix(driver-app): location watcher error handling + revocation force-offline`**
Files: `driver-app/hooks/useDriverDashboard.ts`, `driver-app/utils/backgroundLocation.ts`
1. Pass an error handler to `watchPositionAsync` and `.catch()` the IIFE; on watcher death while `isOnline`, run a `handleLocationLost()` path.
2. Extend the 30 s heartbeat (`:555-585`) to also run while online-and-idle and to check `getForegroundPermissionsAsync` (not just services-enabled).
3. `handleLocationLost()`: `setIsOnline(false)` + `await updateDriverStatus(false)` (reuse the `:1416-1422` rollback pattern) + explanatory alert with "Open Settings". This closes the insurance-Period-1 misclassification (Period 1 presumes a functioning location contract).
4. `backgroundLocation.ts`: make `updateBackgroundLocationCadence` and the geofence re-arm report revocation (return value/callback) instead of bare-returning; call sites stop `.catch(() => {})`-swallowing and route into `handleLocationLost()`.

**Commit 2 — `feat(dispatch): backend stale-GPS sweeper force-offlines blind drivers`**
Files: `backend/utils/stale_location_sweeper.py` (new), `backend/core/lifespan.py`
Use the `spinr-background-loop` skill recipe. Loop (~60 s): drivers with `is_online = true`, no active ride, and last location fix older than N minutes (config `stale_gps_offline_minutes`, default 10) → set `is_online = false, is_available = false` (invariant `is_available ⇒ is_online` preserved), push `{"type": "auto_offline", "reason": "location_stale"}` (mirroring `stale_intent_reconciler.py:229`), FCM nudge, info log + metric `spinr_dispatch_stale_gps_offline_total`. Replay-safe: the update is idempotent and filtered on `is_online = true`. Skip drivers on active rides (route_gap_monitor owns that surface).

**Commit 3 — `test: GPS-loss force-offline paths`**
Files: `backend/tests/test_stale_location_sweeper.py`, `driver-app/__tests__/hooks/` (extend)
Backend: stale-fix driver flipped offline exactly once; on-ride driver skipped; fresh-fix untouched. Client: watcher error → `updateDriverStatus(false)` called.

---

## Workstream 6 — P2 batch (before next PIPEDA-relevant merge)

Verification produced three corrections to the original report — two items are *documented deliberate decisions* needing policy action rather than straight code fixes, and one is lower-risk than reported.

| # | Item | Verified state | Action | Files |
|---|------|----------------|--------|-------|
| P2-1 | Disputes bulk list re-derives full legal names | Confirmed at `backend/routes/disputes.py:166` (projects `first_name,last_name`) and `:182-184` (rebuilds `user_name`). **Nuance:** migration `142_fix_rls_financial_tables.sql:157` scrubbed the at-rest copy and its comment says read-time enrichment is by design; the open gap is ACTION_ITEMS **B2**'s intent (alias-only responses). | Emit alias (`first_name + last-initial`), drop `last_name` from the projection. Also re-check the `int(_round(amount * 100))` rounding sub-item B2 cites at `disputes.py:227`. Update B2 wording (its RLS half is already done by 142). | `backend/routes/disputes.py`, `ACTION_ITEMS.md`, test |
| P2-2 | VIN plaintext migration 244 | Confirmed — but the migration header (`244_vehicle_vin_plaintext_at_rest.sql:1-43`) documents it as a deliberate, ops-requested privacy-posture change; `license_number` stays encrypted. | **Policy decision, not a code commit.** Escalate for documented privacy sign-off; if reversal is chosen: re-add `vehicle_vin` to `_VAULT_PII_FIELDS` + re-encrypt migration (header notes re-encryption mints new vault secrets — not a clean inverse). Track as its own ticket. | ticket only (this PR: none) |
| P2-3 | Reviewer allow-list unlocks `pi_mock_*` in production | Confirmed: `REVIEW_LOGIN_ACCOUNTS` (`core/config.py:176`, parsed `:287-308`) grants both password-less login (`auth.py:328-337`) and the `pi_mock_*` production bypass (`payments.py:329-346`, skip at `:336`). Runbook mitigation is procedural only. | Split capabilities: payment bypass requires a separate `REVIEW_MOCK_PAYMENTS_UNTIL` (ISO timestamp) — expired/absent → bypass off even for reviewer accounts; cap per-account mock rides per day. Keep login side unchanged (App Store reviews need it). | `backend/core/config.py`, `backend/routes/payments.py`, test |
| P2-4 | WS rate limit per-replica | Confirmed: in-memory `_user_msg_timestamps` (`socket_manager.py:26-51`, enforcement `:131-155`). `utils/redis_client.py` already exposes async `redis_incr`/`redis_expire` with in-process fallback — B4's fallback requirement comes free. | Rewrite `check_user_message_rate` body to `redis_incr` + `redis_expire(1)` on `ws:rate:{user_id}:{epoch_second}`; caller (`routes/websocket.py:697-711`) unchanged. Close ACTION_ITEMS B4. | `backend/socket_manager.py`, `ACTION_ITEMS.md`, test |
| P2-5 | `/metrics` open when token unset | Confirmed: `server.py:246-249` warns and serves anyway in production; also accepts `?token=` query param (`:242-244`) which leaks the token into access logs. | In production with no token: return 503 instead of serving. Drop the query-param auth path (Bearer only). | `backend/server.py`, test |
| P2-6 | Admin set-cookie route | **Partially** confirmed — no structure/exp validation (`route.ts:6-28`) and it logs the rejected token value (`:16`), but cookie flags are correct, `middleware.ts:239-256` bounces bad cookies on next navigation, and signature checks are intentionally backend-side. Residual risk low. | Add decode + `exp` guard (reuse middleware's `isTokenValid`); remove token value from the log line. | `admin-dashboard/src/app/api/auth/set-cookie/route.ts` |

Each row = one commit (≤3 files). P2-2 produces a ticket/escalation, not code.

---

## Sequencing

```
Week 1:  WS1 (P0, ship first, 2 commits)
         WS2 commits 1-4 (client-side logout fixes — user-facing pain, no backend risk)
Week 2:  WS2 commits 5-6 (backend refresh atomicity — needs design review on benign-replay return)
         WS3 (orphan refunds, migration first)
         WS4 (email OTP, single commit)
Week 3:  WS5 (GPS watchdog, client then backend loop)
         WS6 (P2 batch, one commit per row; P2-2 escalated as ticket)
```

Dependencies: WS2 commit 5 benefits from commits 1–2 landing first (removes the storm that makes races frequent, so the backend change ships into a calm environment). WS3 commit 2 depends on its migration. Everything else is independent.

Review gates per repo conventions: `spinr-security-auditor` on WS1/WS2/WS4/WS6; `spinr-money-auditor` on WS3 and P2-3; `spinr-migration-reviewer` + `/migration-check` on the WS3 migration; mobile changes need a `[build]` commit for EAS only when we want a review build.

## Test & verification summary

- Every fix lands with a regression test in the same PR (per repo testing conventions; coverage floors: payments ≥90%, auth paths per-domain).
- WS2 has an end-to-end manual verification script: skew device clock +5 min → complete a ride → assert session survives; airplane-mode cold start → assert recovery without OTP.
- Monitoring after WS2 ships: watch `REFRESH_REUSE` warning volume (`refresh_tokens.py:211-218` logs) — it should drop to ~zero; that log is the live signal that the rotation storm is gone.

## Explicitly out of scope

- Retiring the deprecated `/api/documents` mount (tracked by `_DEPRECATED_API_PREFIXES` middleware; removal is a separate deprecation-window decision).
- VIN re-encryption (P2-2) — pending privacy sign-off.
- Any change to the out-of-grace refresh-reuse revocation cascade (correct as designed).
