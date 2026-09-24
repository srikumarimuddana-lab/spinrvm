<!--
Provenance: architect design pass for PR #5727's remaining mobile/auth work
(Tasks 7–11, T12-mobile), produced 2026-09-24 against commit fb1777b by a
read-only architect agent. Master spec: docs/superpowers/plans/2026-09-23-driver-availability.md.
Backend counterpart: .claude/plans/2026-09-24-driver-availability-backend-design.md.

Lead decisions on the §5 "decisions for a human" (defaults adopted for implementation;
all remain open for the product owner to overturn before any mobile release):
D1 yes (v2 envelopes only) · D2 yes · D3 yes · D4 driver-only · D5 flag stays off pending security audit ·
D6 yes · D7/D8/D9 out of scope for this PR.
-->

# Driver-app + auth design for PR #5727 (T7–T12 mobile, T11)

Everything below comes from the code at `fb1777b`. Line references use the current files. I made no edits.

## 0. Decisions, in brief

1. **Types go in a types-only `shared/types/driverAvailability.ts`.** It holds only `export type` and `export interface`, and callers import it with `import type`.
   - Rider `tsc` also compiles `../shared/types/**` (rider-app/tsconfig.json:93-97).
   - Driver tests have two `@shared` alias layers: Babel `module-resolver` (babel.config.js) and a jest `moduleNameMapper` to `__mocks__/@shared` (jest.config.js, last entry). A type-only file works under both.
   - Precedent: index.tsx:57 already does `import type … from '@shared/types/safety'`.
2. **The network adapter moves to `driver-app/services/driverAvailabilityApi.ts`.** This departs from the plan's `shared/api/driverAvailability.ts`. Reasons:
   - `shared/api/client.ts` is not Axios. It is a fetch client that throws `SpinrApiError` (client.ts:752-787).
   - Every driver test already mocks `@shared/api/client`, so a driver-local adapter gets tested for real in driver CI. A shared one would only be tested under rider CI (rider jest `roots` includes `../shared`).
   - No rider blast radius.
   - The plan's rule still holds: the adapter calls `useAuthStore.getState().updateDriverStatus(isOnline, loc, fields)`, so authStore stays the only status write path.
   - **Every proposed-contract name lives as a constant in this one file** (paths, `confirm_ready`, receipt events). A final rename touches this file plus the types file.
3. **Pure logic is headless-safe**, with no React Native or API-client imports. It lives in `driver-app/utils/driverAvailabilityWire.ts` (parse, classify, compare) and `driver-app/utils/driverAvailabilityState.ts` (reducer, selectors). This lets `backgroundLocation.ts` and `backgroundMessaging.ts` import it.
4. **Foreground state lives in `driver-app/store/availabilityStore.ts`** (zustand, like `driverStore`). It holds the reducer state plus a coalescing reconciler: at most one request in flight and one queued re-run.
5. **The tracker fence is `driver-app/utils/availabilityMarker.ts`.** It is stored in SecureStore with `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`, like `TRIP_ACTIVE_KEY`. It uses positive evidence, following `sessionMarker.ts`: no marker means today's behaviour; an explicit deny for the current generation blocks idle starts and uploads.
6. **Offer admission is `driver-app/services/offerAdmission.ts`.** It holds the envelope adapter, a deadline built from the server-time offset plus a monotonic clock, and a ledger of final outcomes. It serves every offer channel.
7. **Auth changes stay out of the rider's normal flows.**
   - Two fixes apply to both apps but only matter in a race or crash: the write order and the generation check.
   - Every other change sits behind a hook in `shared/auth/sessionLock.ts` that only the driver app installs, the same way `installSessionLock` works today (nativeSessionLock.ts:5-12). The rider app never installs it.
8. **Lost refresh response: the client proposes the next refresh token.** Details in §2.8. It needs a backend change. It stays off until a server flag is set, and old servers ignore the extra field (pydantic `RefreshRequest`, auth.py:1852).

## 1. Findings

| # | Plan claim → verdict | Evidence | Consequence / fix |
|---|---|---|---|
|1|One-time hydration → **confirmed, and worse**|Seed `useState(driverData?.is_online \|\| false)` (useDriverDashboard.ts:335); latch (2164-2195). If `/drivers/me` fails at cold start, authStore keeps the **7-day cached** driver (authStore.ts:525-531, 574-579, 599-609).|A days-old `is_online:true` starts the WS (1575-1623) and native tracking (652-680) without server confirmation. → T8|
|2|"Cached online replayed as Go" → **partly**|No status PUT is ever replayed. Two things act like a replay: #1, and the optimistic `setIsOnline(next)` before the request (1844-1845), which opens the WS before any acknowledgement.|T8|
|3|Grey GO → **confirmed, and worse**|Missing driver/status becomes `'pending'` and the button goes grey (DriverIdlePanel.tsx:120-121). The grey button can still be pressed (no `disabled`, :288-295). With `driver===null` the tap hits the vehicle pre-check (useDriverDashboard.ts:1807-1810).|A missing profile shows "Add your vehicle" and opens /vehicle-info. → T8|
|4|409 loop → **confirmed**|Background live upload throws on 409 and retries on every callback (backgroundLocation.ts:429-443). WS and foreground-push `auto_offline` only call `setIsOnline(false)` (1117-1126, 2283-2290); nothing stops the native task or turns off the geofence (only the toggle does, 1978-1983). Leaving the geofence restarts idle tracking without checking intent (backgroundLocation.ts:955-964). A background push `auto_offline` is only logged (backgroundMessaging.ts:207-210). Under v2 the same loop returns `DRIVER_OFFLINE`/`ONLINE_EPOCH_STALE` (location.py:978-1015).|T9|
|5|Every 409 batch is discarded → **confirmed, one correction**|Background drains {404,409,410,422} (tripLocationRecorder.ts:56; backgroundLocation.ts:452-463). **The foreground drain never runs:** `axios.isAxiosError` (tripLocationTransport.ts:27) never matches `SpinrApiError`, so the completion flush (driverStore.ts:795) throws. The dashboard's own transport (useDriverDashboard.ts:685-690) never classifies, so a bad idle batch blocks every foreground flush (tripLocationRecorder.ts:526-586). Under v2, trip batches can also get `SESSION_SUPERSEDED`/`SESSION_RECONCILE_REQUIRED` (location.py:83-96); draining those loses trip history.|T9|
|6|Stored offer deleted before the expiry check → **true, but that part is intended**|Delete-then-decide (pendingRideOffer.ts:48-61) is pinned by pendingRideOffer.test.ts. The real defects: (a) a boolean result mixes up expired, none and busy; (b) a live offer is deleted when the ride state isn't idle (:52 then :56); (c) expiry uses the device clock only; (d) `setIncomingRide` starts a **fresh 15-second countdown** and ignores `offer_expires_at` (driverStore.ts:547-557); (e) the resume resync runs only on an app-state change, never at cold start (index.tsx:663-682). Background push persists and displays offers without checking expiry (backgroundMessaging.ts:256-341). Android Auto shows background offers without checking expiry (carSession.ts:229-238).|T10|
|7|**New: expired offers get stuck on car-only launches**|The countdown and auto-decline live only in the phone screen (index.tsx:628-651 → driverStore.ts:574-586). Car-only launches mount no screen (pendingRideOffer.ts:9-13). register.ts:560-563 assumes "the store runs its own countdown". `fetchActiveRide` keeps `ride_offered` when the server has no ride (driverStore.ts:1056-1065).|Every later offer is blocked (driverStore.ts:537-546). → T10 (a deadline timer in the store)|
|8|**New: local auto-decline resets the missed-offer count**|At countdown 0 the app sends a decline (driverStore.ts:574-586). The backend decline calls `reset_miss_streak` (ride_flow.py:685); its comment names auto-decline-on-timeout (:581).|An unattended phone with the screen on is never paused for missed offers. → T10 (decision D1)|
|9|**New: accepting an expired offer**|Backend returns 403 "No active offer" (ride_flow.py ~312). The store treats it as a generic error (driverStore.ts:619-621, 677-679).|The panel lingers. → T10, using T5's `OFFER_EXPIRED`|
|10|Refresh write order → **confirmed**|Foreground writes refresh → expiry → access (authStore.ts:273-278); background writes refresh → access → expiry (backgroundAuth.ts:73-77). A crash or throw between :274 and :278 pairs the **old** access token with a **new** 15-minute expiry.|Background returns the stale token (backgroundAuth.ts:17-19) for up to 15 minutes, so uploads get 401 and the rider's live map freezes. Foreground adopts it too (authStore.ts:383-385). → T11-1|
|11|Stale-generation 401 → **confirmed**|`refreshTokens` never records the login generation (authStore.ts:363-495). With the driver lock, a new sign-in (generation bumped synchronously at :357) queues behind an in-flight refresh. A late 401 then clears everything (:483-488 → :289-331): the session-ended marker, user/driver, and the logout callbacks, including teardown and outbox purge. In the rider app the lock does nothing (sessionLock.ts:4), so a late **success** can write the old session's tokens over the new login.|T11-1|
|12|**New: an App Check 401 logs the driver out**|`/api/v1/auth/refresh` is not exempt from App Check (middleware.py:90-150). A missing or bad token returns 401 `{"detail":"App Check token required"}` or `"Invalid App Check token"` (:454-482). Foreground treats any refresh 401 as final (authStore.ts:448-488). Background blacklists the token permanently (backgroundAuth.ts:44-51). The hazard is already documented at carSession.ts:390-402. A real credential rejection is distinguishable: its body has `error.code:1003` (error_handling.py:173-185, 215-224).|T11-2..4|
|13|**New (backend): a DB error at refresh returns 401**|`lookup_refresh_token` turns a DB failure into None (refresh_tokens.py:250-254, pinned by test_refresh_tokens_lifecycle.py:203-213), which becomes a 401.|A database blip logs the driver out. → ask X7|
|14|Lost refresh response → **confirmed gap**|Rotation revokes the old token (refresh_tokens.py:190-201). A replay within 600 s is treated as harmless but is still a 401 (:285-297); after 600 s it cascades (:298-317).|The 10-minute window only prevents the cascade; it never recovers the session. → T11-5..7|
|15|Storage read failure is recoverable → **already correct**|authStore.ts:106-115, 364-381, 513-519, 626-631; tests exist (authStore.initialize.test.ts:132-210).|Tests only|
|16|503 vs definitive rejection → **mostly correct**|Anything other than 401 is transient (authStore.ts:448-454).|The gaps are #12 and #13|
|17|**New: v2 breaks two existing calls**|Logout's go-offline sends `{is_online:false}` only (authStore.ts:790-795). The go-online rollback uses the legacy PUT (useDriverDashboard.ts:1937-1938). With v2 on, both get 409 `AVAILABILITY_UPGRADE_REQUIRED` (status.py:325-332).|The driver stays online on the server. → T8-6/9/10, ask X5|
|18|**New: flag-off snapshot misreports online drivers**|`accepting_requests` defaults to false and legacy writes never set it (migration 457:18). So an online driver's snapshot reads `paused/REQUESTS_PAUSED` (driver_availability_service.py:206, 223-232).|When `protocol_enabled:false`, the normalizer must read `is_online`. → T7|
|19|**New: WS `session_superseded` reconnects forever**|Server sends an error, then closes with 1008 (websocket.py:315-325). The client shows "Connection lost" (useDriverDashboard.ts:1408-1417) and keeps reconnecting (:1487-1538).|T8-11|
|20|**New: v2 live location needs the epoch even on a trip**|`_require_presence_epoch` runs before the ride lookup (location.py:989 vs 1000-1015).|An on-trip driver with no known epoch gets 409 for live location. → ask X6. The client sends the marker's epoch when it has one.|
|21|**New: a network error wipes the local trip**|`fetchActiveRide` catch (driverStore.ts:1066-1075), pinned by driverStore.reconnect.test.ts:118-132.|The trip panel disappears mid-trip. → T8-12 (decision D2)|
|22|**New: precise locations in logs**|Pickup/dropoff lat/lng logged at useDriverDashboard.ts:1026-1030. Full push payloads logged at _layout.tsx:308, 369, 383.|Fixed in T10-5 and T10-8|
|23|**New: tests pinned to source text will break**|`hooks/__tests__/onlineResync.test.ts`, `__tests__/screens/driverOfferPanelWiring.test.ts`, `utils/__tests__/backgroundLocation.test.ts:613-624` (the 409 drain), `hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` (relies on cached `is_online`), driverStore.reconnect.test.ts (only if D2 is accepted).|Each is updated in the commit that changes the matching code.|

## 2. Contracts

### 2.1 `shared/types/driverAvailability.ts` (types only; no runtime exports)
```ts
export type DecimalString = string;          // /^\d{1,19}$/ — never convert to number
export type IsoTimestamp = string;           // may carry 6 fractional digits
export type AvailabilityStateWire = 'offline' | 'ready' | 'reconnecting' | 'paused' | 'blocked';
export type EligibilityReasonCode = 'ACCOUNT_SUSPENDED' | 'ACCOUNT_REVIEW' | 'ACCOUNT_INELIGIBLE'
  | 'DOCUMENT_EXPIRED' | 'LICENSE_EXPIRED' | 'INSURANCE_EXPIRED' | 'DRIVER_UNVERIFIED';
export type AvailabilityReasonCode = EligibilityReasonCode | 'ACTIVE_TRIP' | 'OFFER_PENDING' | 'RECOVERY_REQUIRED'
  | 'SESSION_RECONCILE_REQUIRED' | 'SESSION_SUPERSEDED' | 'READY_TIMEOUT' | 'PRESENCE_UNAVAILABLE' | 'LOCATION_STALE'
  | 'POLICY_BLOCKED' | 'MISSED_OFFERS' | 'REQUESTS_STOPPED' | 'REQUESTS_PAUSED' | 'OFFLINE_INTENT'
  | 'QUOTA_EXHAUSTED';                        // client-only (WS auto_offline reason quota_exhausted)

export interface AvailabilitySnapshotWire {            // driver_availability_service.py:252-270
  protocol_enabled: boolean; state_version: DecimalString; online_epoch: DecimalString;
  is_online: boolean; accepting_requests: boolean; is_available: boolean;
  availability_state: AvailabilityStateWire; reason_code: AvailabilityReasonCode | null;
  eligibility_reason: EligibilityReasonCode | null;
  controller_session_id: string | null;               // opaque: never read, log or persist
  server_time: IsoTimestamp; snapshot_issued_at: IsoTimestamp;
  last_contact_at: IsoTimestamp | null; ready_until: IsoTimestamp | null;
  active_ride: { id: string; status: string; updated_at: IsoTimestamp | null } | null;
  pending_offer: { id: string; ride_id: string; offered_at: IsoTimestamp | null;
                   expires_at: IsoTimestamp | null; ride_status: string | null } | null;
  offer_reconciliation_required: boolean;
  readiness_policy_enabled?: boolean;                 // PROPOSED (T12, ask X4); absent ⇒ false
}
export interface StatusCommandSuccessWire extends AvailabilitySnapshotWire { success: true; code: 'OK'; transition: Record<string, unknown> }
export interface LegacyStatusSuccessWire { success: true; is_online: boolean }      // status.py:1273
export type AvailabilityCommandAction = 'go_online' | 'go_offline' | 'stop_requests' | 'confirm_ready'; // confirm_ready PROPOSED
export interface AvailabilityCommandFields { online_epoch: DecimalString; request_id: string; availability_action: AvailabilityCommandAction }
export type AvailabilityErrorCode = 'DRIVER_OFFLINE' | 'ONLINE_EPOCH_STALE' | 'CONTACT_GAP' | 'SESSION_SUPERSEDED'
  | 'SESSION_RECONCILE_REQUIRED' | 'PRESENCE_UNAVAILABLE' | 'ELIGIBILITY_UNAVAILABLE' | 'ELIGIBILITY_BLOCKED'
  | 'AVAILABILITY_UPGRADE_REQUIRED' | 'AVAILABILITY_V2_DISABLED' | 'UNAUTHORIZED_SESSION' | 'CONTROLLER_SESSION_MISMATCH'
  | 'IDEMPOTENCY_KEY_CONFLICT' | 'OBLIGATION_ACTIVE' | 'INVALID_AVAILABILITY_COMMAND' | 'DRIVER_NOT_FOUND'
  | 'SESSION_AUTHORITY_UNAVAILABLE' | 'OFFER_EXPIRED' | 'RIDE_STATE_CONFLICT';   // last two PROPOSED (T5)
export interface AvailabilityErrorDetailWire {           // status.py:46-49, 86-107; location.py:147-172
  code: AvailabilityErrorCode | string; message?: string; reason_code?: string | null;
  online_epoch?: DecimalString | null; state_version?: DecimalString; has_trip?: boolean; has_pending_offer?: boolean;
  snapshot?: AvailabilitySnapshotWire;                // PROPOSED (T5 OFFER_EXPIRED)
}
export interface WsAuthSuccessAvailabilityWire { availability_protocol?: 'v2'; presence_status?: 'bound' | 'reconcile_required'; online_epoch?: DecimalString | null }
export interface WsAvailabilityReconcileRequiredWire { type: 'availability_reconcile_required'; code: string; online_epoch: DecimalString | null }
// Offers — PROPOSED T5/T6
export interface OfferEnvelopeWire { ride_id: string; offer_id?: string | null; claim_id?: string | null;
  online_epoch?: DecimalString | null; server_time?: IsoTimestamp | null; expires_at?: IsoTimestamp | null;
  offer_expires_at?: IsoTimestamp | null; countdown_seconds?: number | string | null }
export type OfferReceiptEvent = 'received' | 'presented';
export type OfferReceiptChannel = 'ws' | 'push' | 'stored' | 'android_auto';
export interface OfferReceiptRequestWire { claim_id: string; event: OfferReceiptEvent; channel: OfferReceiptChannel;
  app_state: 'active' | 'background' | 'inactive' | 'unknown'; remaining_ms: number }
// Normalized domain model
export type AuthDomain = 'restoring' | 'authenticated' | 'recoverable' | 'reauth_required';
export type AvailabilityDomain = 'unknown' | 'offline' | 'connecting' | 'ready' | 'reconnecting' | 'paused' | 'blocked';
export type WorkDomain = 'idle' | 'offer_pending' | 'on_trip';
export interface AvailabilitySnapshot {
  protocol: 'v2' | 'legacy'; serverClock: boolean;      // false only for the /drivers/me fallback
  synthetic: boolean;                                   // true for local server-signal placeholders
  stateVersion: DecimalString; onlineEpoch: DecimalString | null;
  isOnline: boolean; acceptingRequests: boolean;
  availability: Exclude<AvailabilityDomain, 'unknown' | 'connecting'>;
  reasonCode: AvailabilityReasonCode | null; eligibilityReason: EligibilityReasonCode | null;
  serverTimeMs: number; readyUntilMs: number | null;
  activeRide: { id: string; status: string } | null;
  pendingOffer: { offerId: string; rideId: string; expiresAtMs: number | null } | null;
  offerReconciliationRequired: boolean; readinessPolicyEnabled: boolean;
}
export type AvailabilityErrorKind = 'conflict' | 'unavailable' | 'auth' | 'network' | 'unsupported' | 'invalid' | 'rejected';
export interface AvailabilityErrorInfo { kind: AvailabilityErrorKind; status: number | null; code: AvailabilityErrorCode | null;
  reasonCode: string | null; onlineEpoch: DecimalString | null; legacyDetail: string | null }
```

### 2.2 Normalizing and classifying (`driverAvailabilityWire.ts`, pure)
- **`compareDecimal(a, b)`**: check `/^\d{1,19}$/`, strip leading zeros, compare by length, then by characters. No `BigInt`, so there is no Hermes dependency.
- **`parseServerTimeMs(v)`**: cut fractional seconds to 3 digits (`s.replace(/(\.\d{3})\d+/, '$1')`) before `Date.parse`. This keeps microsecond Postgres timestamps safe on Hermes.
- **`normalizeSnapshot(raw)`** returns null when `is_online` or `server_time` is invalid, or when a v2 payload's version, epoch or state is invalid.
  - **v2**: fields map one to one.
  - **Legacy** (`protocol_enabled !== true`): `onlineEpoch = null`; `stateVersion = '0'` unless valid.
    - `availability = eligibility_reason ? 'blocked' : is_online ? (active_ride ? 'paused' : 'ready') : 'offline'`.
    - `reasonCode = eligibility_reason ?? (active_ride ? 'ACTIVE_TRIP' : is_online ? null : 'OFFLINE_INTENT')`.
    - Ignore `availability_state` and `reason_code` (finding 18).
  - `serverTimeMs` comes from `snapshot_issued_at ?? server_time`.
  - `readinessPolicyEnabled = v2 && raw.readiness_policy_enabled === true`.
- **`legacySnapshotFromProfile(profile, receivedAtMs)`**: used when the snapshot GET returns 404/405 (server older than this PR). Uses `/drivers/me`'s `is_online`, `serverClock:false`, `serverTimeMs=receivedAtMs`.
- **`classifyAvailabilityError(err)`**:
  - Reads `err.status ?? err.response?.status`. Reads `detail = err.data?.detail ?? err.response?.data?.detail`: an object gives `code`, `reason_code` and `online_epoch`; a string becomes `legacyDetail`.
  - Kind by status: no status → `network`; 401 → `auth`; 409 → `conflict`; 422 → `invalid`; 400/402/403 → `rejected`; 5xx → `unavailable`.
  - 404/405 on the snapshot GET → `unsupported`. The adapter decides this.
- **`newRequestId(random = Math.random)`**: a UUIDv4-format string, the same method as `generateRequestId` (client.ts:53-58). It is an idempotency key, not a secret, and it avoids needing an expo-crypto mock.

### 2.3 Reducer contract (`driverAvailabilityState.ts`)
```ts
export const RECONCILE_DEADLINE_MS = 10_000;
export interface PendingStop { requestId: string; action: 'stop_requests' | 'go_offline'; epoch: DecimalString | null; attempts: number }
export interface AvailabilityState {
  generation: string | null;             // SESSION_GENERATION_KEY this state belongs to
  snapshot: AvailabilitySnapshot | null;
  minSeq: number;                        // responses from reconcile seq < minSeq are obsolete (init -1)
  serverOffsetMs: number | null;         // server − device (midpoint estimate)
  reconcile: { seq: number; status: 'idle' | 'in_flight' | 'timed_out' | 'failed'; startedAtMs: number | null; error: AvailabilityErrorInfo | null };
  eligibility: 'eligible' | 'blocked' | 'unknown';
  command: { action: AvailabilityCommandAction; requestId: string } | null;
  lastCommandError: AvailabilityErrorInfo | null;
  pendingStop: PendingStop | null;       // mirrors the persisted marker
}
export type AvailabilityEvent =
  | { type: 'session_changed'; generation: string | null }
  | { type: 'reconcile_started'; generation: string | null; seq: number; atMs: number }
  | { type: 'reconcile_deadline'; seq: number }
  | { type: 'reconcile_failed'; generation: string | null; seq: number; error: AvailabilityErrorInfo }
  | { type: 'snapshot_received'; generation: string | null; seq: number | null; snapshot: AvailabilitySnapshot; requestedAtMs?: number; receivedAtMs?: number }
  | { type: 'server_signal'; generation: string | null; signal: 'auto_offline' | 'driver_offline' | 'epoch_stale' | 'session_superseded'; reason: AvailabilityReasonCode | null }
  | { type: 'profile_read'; ok: boolean }
  | { type: 'pending_stop'; generation: string | null; pendingStop: PendingStop | null }
  | { type: 'command_started'; generation: string | null; action: AvailabilityCommandAction; requestId: string }
  | { type: 'command_finished'; generation: string | null; requestId: string; error: AvailabilityErrorInfo | null };
export function initialAvailabilityState(generation?: string | null): AvailabilityState;
export function applyAvailabilitySnapshot(s: AvailabilityState, snap: AvailabilitySnapshot, localGeneration: string | null,
  meta: { seq: number | null; requestedAtMs?: number; receivedAtMs?: number }): AvailabilityState;
export function reduceAvailability(s: AvailabilityState, e: AvailabilityEvent): AvailabilityState;
export function selectIntendedOnline(s: AvailabilityState, localWork: WorkDomain): boolean | null;
export function selectTrackerDirective(s: AvailabilityState, localWork: WorkDomain, fence: IdleFence): TrackerDirective;
export function serverNowMs(s: AvailabilityState, deviceNowMs: number): number;   // device + (offset ?? 0)
export type TrackerDirective = { kind: 'unknown' } | { kind: 'trip' }
  | { kind: 'idle_allowed'; epoch: DecimalString | null } | { kind: 'idle_denied'; epoch: DecimalString | null };
```
**`applyAvailabilitySnapshot` rules, in order:**
1. `localGeneration !== s.generation` → unchanged. This is the session fence.
2. `meta.seq !== null && meta.seq < s.minSeq` → unchanged. This drops obsolete requests.
3. If the previous snapshot is not synthetic and has the same protocol, compare versions with `compareDecimal`:
   - lower → unchanged, but clear the in-flight status for this seq;
   - equal and `serverTimeMs <= prev.serverTimeMs` → unchanged, same clearing.
   - Legacy versions are always '0', so legacy ordering falls back to the issue time.
   - A protocol change always applies.
4. Apply:
   - `minSeq = max(minSeq, seq)`;
   - `serverOffsetMs = serverTimeMs − (requestedAtMs + receivedAtMs) / 2`, only when `serverClock`;
   - `eligibility = availability === 'blocked' ? 'blocked' : 'eligible'`;
   - reconcile status becomes idle if the seq matches.

**Other events:**
- `server_signal`: sets `minSeq = reconcile.seq + 1`, so requests started before the signal are obsolete. Replaces the snapshot with a synthetic `{...prev, isOnline:false, acceptingRequests:false, availability:'paused', reasonCode}`. For `epoch_stale` it keeps `isOnline` and sets `availability:'reconnecting'`. For `session_superseded` it sets `reasonCode:'SESSION_SUPERSEDED'`.
- `profile_read {ok:false}`: sets `eligibility = 'unknown'` and leaves the snapshot alone. The plan's example: `profileReadFailed(ready).eligibility === 'unknown'`.
- `reconcile_deadline {seq}`: `in_flight` → `timed_out`. A late response for that seq still applies.

**Selectors:**
- `selectIntendedOnline`:
  - pending stop for this generation → `false` (a local stop always wins);
  - snapshot → `snapshot.isOnline`;
  - `localWork === 'on_trip'` → `true`;
  - otherwise `null`, meaning unknown: keep the current value. **Never restore "online" from the cache.**
- `selectTrackerDirective`:
  - on a trip → `trip`;
  - pending stop, or fence `deny` → `idle_denied`;
  - snapshot is `ready`, `reconnecting` with reason `PRESENCE_UNAVAILABLE`, `paused/OFFER_PENDING`, or legacy online → `idle_allowed{epoch}`;
  - snapshot is offline, paused, blocked, or a session problem → `idle_denied`;
  - no snapshot → `unknown`, meaning leave native tracking alone.

### 2.4 Reconciliation (`availabilityStore.ts`)
```ts
export type ReconcileTrigger = 'cold_start' | 'foreground' | 'ws_reconnect' | 'ws_conflict' | 'status_conflict'
  | 'notification_tap' | 'server_signal' | 'manual_retry' | 'offer_settled' | 'car_connect' | 'ready_deadline';
export interface AvailabilityStoreState extends AvailabilityState {
  requestReconcile(t: ReconcileTrigger): Promise<void>;
  runCommand(a: 'go_online' | 'stop_requests' | 'go_offline' | 'confirm_ready', o?: { location?: { lat: number; lng: number } }): Promise<CommandOutcome>;
  noteServerSignal(s: 'auto_offline' | 'driver_offline' | 'epoch_stale' | 'session_superseded', reason?: AvailabilityReasonCode | null): void;
  noteProfileRead(ok: boolean): void;
  registerEffect(fn: (s: AvailabilityState) => void | Promise<void>): () => void;
  reset(): void;
}
export type CommandOutcome = { kind: 'ok'; snapshot: AvailabilitySnapshot } | { kind: 'failed'; error: AvailabilityErrorInfo; raw: unknown }
  | { kind: 'needs_reconcile' } | { kind: 'busy' };
```
**The state machine:**
- **Idle + trigger** → in flight with seq n:
  - If `useAuthStore.getState().token` is missing, fail with kind `auth`; there is no request without a session.
  - Read `SESSION_GENERATION_KEY`. If it differs, dispatch `session_changed`.
  - Dispatch `reconcile_started(n)`, start the 10 s UI deadline timer, and call `fetchAvailability()`.
- **In flight + trigger** → set `rerun = true`. No second request is sent.
- **Response** →
  - Re-read the generation; if it changed, tag the response as stale.
  - Dispatch `snapshot_received` or `reconcile_failed`, then clear the timer.
  - **Effects, in order:** (1) work: if the snapshot's active ride doesn't match local ride state, call `fetchActiveRide()`; (2) the offer effect (T10); (3) the tracker effect, registered by the hook (T9); (4) retry a pending stop (below); (5) if the snapshot `onlineEpoch` differs from the epoch the socket was opened with, close the socket with 4002.
  - If `rerun` is set, run once more (after 2 s if the last attempt failed); at most one re-run per failure.
- **`SESSION_RECONCILE_REQUIRED`** → call `useAuthStore.getState().refreshTokens()` at most once per 5 minutes, then reconcile.

**`runCommand`:**
1. Read the generation and the snapshot.
2. v2 with a null epoch → return `needs_reconcile` and reconcile.
3. Create a `requestId`.
4. **For stop or go-offline, first write `denyIdleUploads({generation, epoch, pendingStop})`** (T9).
5. Dispatch `command_started`.
6. Call `sendAvailabilityCommand`, which goes through `authStore.updateDriverStatus`.
7. On a v2 success, apply the snapshot. On a legacy success, build a legacy snapshot from the requested state.
8. For go-online or confirm-ready, only after the success: `allowIdleUploads({generation, epoch})`.
9. For stop, clear the pending stop once the snapshot shows the driver is not accepting requests.
10. On a conflict (`ONLINE_EPOCH_STALE`, `CONTROLLER_SESSION_MISMATCH`, `AVAILABILITY_UPGRADE_REQUIRED`, `SESSION_RECONCILE_REQUIRED`, `OBLIGATION_ACTIVE`, `IDEMPOTENCY_KEY_CONFLICT`) → reconcile with `status_conflict`.
11. A stop that fails on the network keeps its pending stop.

**Pending-stop retry**, after a reconcile:
- If the snapshot still shows the driver online and accepting, resend. Reuse `requestId` when `epoch === pendingStop.epoch`, which the server treats as idempotent (migration 457:83-95); otherwise use a new id and the current epoch.
- At most 3 attempts; after that the view shows STOP again.
- **Go-online is never retried automatically.**

**Coalescing test:** three triggers during one in-flight request produce exactly 2 GETs.

### 2.5 Tracker fence (`availabilityMarker.ts`, headless-safe)
```ts
export const AVAILABILITY_MARKER_KEY = 'spinr_availability_marker';
export interface AvailabilityMarker { v: 1; sessionGeneration: string; onlineEpoch: DecimalString | null;
  idleUploadsAllowed: boolean; pendingStop: PendingStop | null; reconcileRequested: boolean;
  sessionSuperseded: boolean; updatedAtMs: number }
export type MarkerRead = { ok: true; marker: AvailabilityMarker | null } | { ok: false };
export type IdleFence = { idle: 'allow' | 'deny' | 'defer'; epoch: DecimalString | null; liveBlocked: boolean };
export async function readAvailabilityMarker(): Promise<MarkerRead>;            // malformed ⇒ {ok:true, marker:null}
export function idleFence(read: MarkerRead, generation: string | null | undefined): IdleFence;
export async function denyIdleUploads(p: { generation: string; epoch: DecimalString | null; pendingStop?: PendingStop | null; reconcileRequested?: boolean }): Promise<boolean>;
export async function allowIdleUploads(p: { generation: string; epoch: DecimalString | null }): Promise<boolean>;
export async function denyIdleIfCurrent(p: { generation: string; epochSent: DecimalString | null; sessionSuperseded?: boolean }): Promise<'denied' | 'superseded' | 'failed'>;
export async function takeReconcileRequest(generation: string): Promise<boolean>;
export async function clearAvailabilityMarker(): Promise<void>;
```
**Rules:**
- **Fence decision:**
  - Read failed, or generation unreadable (`undefined`) → `defer`: no idle network call and no idle start this callback. This matches "failed storage access must defer upload" (backgroundLocation.ts:393-398).
  - No marker, or a marker from another generation → `allow` with epoch null (today's behaviour).
  - Marker for this generation → `allowed ? allow : deny`, with `liveBlocked = sessionSuperseded`.
- **Writes:** all run inside `withSessionLock` with `{keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY}`.
  - **Stop writes deny first**, before the command and before `stopBackgroundLocation`. A failed write is reported, and the native stop still runs.
  - **Only an acknowledged go-online or confirm, or a server-confirmed ready snapshot with no pending stop, writes allow.**
- **`denyIdleIfCurrent`** (background conflicts) is compare-and-set. It writes only if `marker.sessionGeneration === generation` and `marker.onlineEpoch === epochSent`, or there is no marker and `epochSent === null`. So a late callback cannot deny a newer epoch.
- Trip recording is never fenced; only idle presence is. Epochs go on **live and idle batches only, never on trip batches**. Trip batches don't need the epoch (location.py:604-609), and adding it would create new 409s.

**Every start and stop caller of background location, and its new rule:**

| Caller | Today | New rule |
|---|---|---|
|useDriverDashboard.ts:668 `ensureTracking` (mount and every resume while online)|`startBackgroundLocation(cadence, canStart)`|Read the fence first. For idle cadence with `deny` or `defer`: do nothing and show no error (today :669 would show a false permission error). The tracker effect calls `ensureTracking` again after it writes allow.|
|:1933 go-online start|Starts after the PUT|Starts only after the acknowledgement and after `allowIdleUploads`.|
|:1937-1938 rollback when permission is denied|Legacy `updateDriverStatus(false)`|`store.runCommand('stop_requests')`, which is v2-aware.|
|:1973 geofence arm|After start|Only after allow.|
|:1978-1983 stop|Stop native, turn off geofence|Deny marker with pending stop → command → native stop and geofence off (skipped if the directive is `trip`).|
|:798 cadence change|Retunes a running task|Unchanged (never starts).|
|:1182 WS `location_health` → `recoverTripLocation`|Can start trip cadence|Unchanged; trips are authorized separately.|
|backgroundMessaging.ts:185 push `location_health`|Same|Unchanged.|
|backgroundLocation.ts:842 `recoverTripLocation` → start trip cadence|Trip|Unchanged.|
|backgroundLocation.ts:962 geofence exit|Restarts idle or trip tracking|Idle needs `allow`. If `deny` and `TRIP_ACTIVE_KEY` reads successfully and is not `'true'`, stop tracking and turn off the geofence instead of re-arming.|
|backgroundLocation.ts:343 self-heal `reassertDispatchTask`|Re-applies options if running|Unchanged (never starts).|
|backgroundLocation.ts:429-443 background live upload|Posts every callback|Allowed if trip or `allow`, and never when `liveBlocked`. Add `online_epoch` from the fence. Handle conflicts per §2.6.|
|backgroundLocation.ts:445-477 history flush|Drains 4 status codes|Classifier plus `skipIdleSessions` when the fence isn't `allow`.|
|carLocationTask.ts:296-420 car task|Display-only; never starts dispatch|Unchanged.|
|sessionTeardown.ts:37-101 logout|Stops everything|Also `clearAvailabilityMarker()` and availability `reset()`.|

### 2.6 Location response classification (`driverAvailabilityWire.ts`)
**Live location (`classifyLiveResponse(status, body)`):**
- **200 with `presence_code`** in {ONLINE_EPOCH_STALE, DRIVER_OFFLINE, SESSION_SUPERSEDED, CONTACT_GAP} → OK, and mark that a reconcile is wanted.
- **409 `DRIVER_OFFLINE`, or the legacy string "Driver is not online"** → deny idle for the epoch sent and ask for a reconcile. If `TRIP_ACTIVE_KEY` is positively not `'true'`, stop tracking and turn off the geofence.
- **409 `ONLINE_EPOCH_STALE` (including `CONTACT_GAP`)** → deny idle for the epoch sent and ask for a reconcile, but don't stop the native task.
- **409 `SESSION_SUPERSEDED`** → deny idle and block all live uploads for this generation, with a reconcile.
- **409 `AVAILABILITY_UPGRADE_REQUIRED` or `SESSION_RECONCILE_REQUIRED`** → defer and reconcile.
- **422** → drop this fix.
- **401, 403, 429, 5xx, network** → defer.

**Batch (`classifyBatchResponse(kind, status, body)`)**: a drain turns every point into a rejection with that reason, so the outbox quarantines them with a recorded reason (tripLocationOutbox.ts:325-361):

| Status and detail | Trip batch | Idle batch |
|---|---|---|
|2xx with acknowledgement|Acknowledge (unchanged)|Same|
|404|Drain `ride_not_found`|Drain `not_found`|
|410|Drain `gone`|Drain `gone`|
|422|Drain `rejected_invalid`|Same|
|409 "Ride cannot accept location points in its current state" (location.py:618)|Drain `ride_state_conflict`|—|
|409 "Idle location recording is not enabled" (:498)|—|Drain `idle_disabled`|
|409 "Driver is not online" / `DRIVER_OFFLINE` (:500)|Keep|Deny idle, then drain `idle_driver_offline`|
|409 `ONLINE_EPOCH_STALE` / `SESSION_SUPERSEDED` / `SESSION_RECONCILE_REQUIRED` / `AVAILABILITY_UPGRADE_REQUIRED`|Keep and reconcile|Deny idle, keep the points, reconcile|
|409 unrecognized|**Keep — never drain trip data**|Drain `idle_conflict_unknown` (today's behaviour)|
|401, 403, 429, 5xx, network|Keep (throw, existing backoff)|Keep|

When the idle outcome is "deny and keep", the transport returns `{recording_session_id, acked_through: null}`. The flush then ends without triggering backoff, and later flushes skip idle sessions.

### 2.7 Offer admission (`offerAdmission.ts`)
```ts
export interface NormalizedOffer { offerKey: string /* offer_id ?? `ride:${ride_id}` */; offerId: string | null; claimId: string | null;
  rideId: string; onlineEpoch: DecimalString | null; expiresAtMs: number | null; windowMs: number | null;
  receivedAtDeviceMs: number; deadlineMonoMs: number | null; channel: OfferReceiptChannel; protocol: 'v2' | 'legacy' }
export type OfferAdmission = { kind: 'offer'; offer: NormalizedOffer; remainingMs: number | null }
  | { kind: 'expired'; offer: NormalizedOffer } | { kind: 'duplicate'; offerKey: string } | { kind: 'none'; reason: 'malformed' | 'busy' };
export const monoNow = () => globalThis.performance?.now?.() ?? Date.now();
export function normalizeOfferEnvelope(raw: Record<string, unknown>, ctx: { channel: OfferReceiptChannel; deviceNowMs: number;
  monoNowMs: number; serverOffsetMs: number | null; storedAtMs?: number | null }): NormalizedOffer | null;
export async function admitOffer(raw: Record<string, unknown>, ctx: /* above */ & { rideState: string; currentOfferKey: string | null }): Promise<OfferAdmission>;
export function remainingOfferMs(o: NormalizedOffer, monoNowMs: number): number | null;
export function reanchorOffer(o: NormalizedOffer, deviceNowMs: number, monoNowMs: number, serverOffsetMs: number | null): NormalizedOffer;
export async function recordOfferOutcome(key: string, outcome: 'accepted' | 'declined' | 'expired' | 'gone'): Promise<void>;
export function buildOfferReceipt(o: NormalizedOffer, event: OfferReceiptEvent, ctx: { appState: OfferReceiptRequestWire['app_state']; monoNowMs: number }): { offerId: string; body: OfferReceiptRequestWire } | null; // null unless offerId && claimId
```
**Rules:**
- **Expiry:** `expiresAtMs = min(parse(expires_at), parse(offer_expires_at))`. If the two differ by more than 1 s, emit a diagnostic with IDs only.
- **Server "now" at receipt:** `parse(server_time) ?? deviceNow + (serverOffsetMs ?? 0)`.
- **Remaining time:**
  - With an expiry: `expiresAt − serverNowAtReceipt`.
  - Otherwise: `countdown_seconds × 1000 − (storedAtMs ? deviceNow − storedAtMs : 0)`.
  - Otherwise null, which keeps today's behaviour.
  - `deadlineMonoMs = monoNow + remaining`.
- **On resume:** re-anchor from wall clock plus offset, because the monotonic clock may pause while the device sleeps.
- **Admission order:** malformed → `none`; key is in the final-outcome ledger → `duplicate`; `remaining ≤ 500 ms` → `expired` (record it); the key is the offer on screen → `duplicate` (the caller may merge richer fields, as today at 2237-2247); ride state not idle → `none/busy` (**don't delete the stored record**); otherwise `offer`.
- **The ledger holds final outcomes only.** A "shown" offer never blocks another channel. It is stored in AsyncStorage key `spinr_offer_ledger`: 20 entries, 10-minute TTL, shared by the foreground and headless runtimes.
- **Protocol:** `v2` when `offer_id` and `claim_id` are both present. Receipts are sent only for v2 offers.

### 2.8 Auth rules
- **Write order (T11-1):** `publishTokensUnlocked` writes `refresh_token` → `fg_access_token` → `token_expires_at`. The expiry acts as the commit marker, matching backgroundAuth.ts:73-77.
- **Generation check (T11-1):** record `loginGeneration` when the locked body starts. Before publishing (:438) or clearing (:487), if `gen !== loginGeneration`, return false and do nothing.
- **401 classification (T11-2..4).** Add to `shared/auth/sessionLock.ts`:
  ```ts
  export type RefreshRejection = 'credential' | 'transient';
  export let classifyRefreshRejection = (status: number, _body: unknown): RefreshRejection => (status === 401 ? 'credential' : 'transient'); // legacy default
  export function installRefreshRejectionClassifier(fn: typeof classifyRefreshRejection): void { classifyRefreshRejection = fn; }
  ```
  - This is the same live-binding pattern as `sessionKeychainOptions`.
  - authStore reads the 401 body. The raw `Response` from client.ts:1005-1006 is still unread, so call `await e.json?.().catch(() => null)` if present.
  - The driver installer (nativeSessionLock.ts) classifies: `body?.error?.code === 1003` → credential; `/App Check token/i` in `detail` → transient; any other 401 → credential. That keeps the existing test with `{status:401, ok:false}` passing.
  - backgroundAuth applies the same rule. A transient 401 gets a 30-second backoff and no blacklist.
- **Refresh-token proposal (T11-5..7):**
  - Add to `sessionLock.ts`: `installRefreshCommitment({fingerprint(raw), newSuccessor()})` and `REFRESH_SUCCESSOR_PENDING_KEY = 'spinr_refresh_successor_pending'`.
  - Inside the lock and before posting: read the pending record `{parentFp, successor}`. If the parent fingerprint doesn't match, create a new 64-hex successor (expo-crypto `getRandomBytesAsync(32)`) and **persist it before sending**. If that write fails, return false (transient).
  - Post `{refresh_token, proposed_refresh_token}`.
  - On success, persist the returned `refresh_token`: it equals the proposal if the server honoured it, otherwise it is the server's own value. Then delete the pending record.
  - A transient failure keeps the pending record. A definitive 401 deletes it.
  - Add the key to all three delete lists (authStore.ts:78-94, 292-295, and on new sign-in).
  - The rider never installs this, so the body stays `{refresh_token}`, which the existing tests pin.

## 3. Commit plan (at most 3 files and about 200 lines each; Change Impact entry goes in the PR body)

**Verification:**
- Driver: `cd driver-app && yarn test <paths> --runInBand && yarn tsc --noEmit`.
- Shared-auth commits also run rider: `cd rider-app && yarn tsc --noEmit && yarn test __tests__/auth.integration.ts __tests__/api-client-401-refresh.test.ts __tests__/api-client-503-retry.test.ts ../shared/api/__tests__ --runInBand`.
- Backend: `cd backend && pytest tests/<file> -q`.
- This sandbox has no node_modules. The check is the CI run on the pushed commit; quote the job and test names and never claim a local run.

**Test patterns to copy:**
- Pure modules: plain jest.
- Store: driverStore.reconnect.test.ts:15-70 (factory mock of `@shared/api/client`).
- Hook: useDriverDashboard.socketLifecycle.test.ts:1-120 (full mock block plus `Socket` class).
- Panel: DriverIdlePanel.test.tsx:1-55 (SafeAreaProvider plus mocks; `t = key`).
- Real authStore: authStore.refreshRace.test.ts:24-127 (relative imports, SecureStore backing map, FIFO `installSessionLock`).
- Background auth: backgroundAuth.test.ts:1-37.
- Background location: backgroundLocation.test.ts:1-120.
- Pending offer: pendingRideOffer.test.ts:1-45.

### T7 — contract and reducer
|#|Files|Change|Tests|
|---|---|---|---|
|7-1|`shared/types/driverAvailability.ts` (new)|§2.1, types only|`tsc` in both apps|
|7-2|`driver-app/utils/driverAvailabilityWire.ts` (new), `driver-app/__tests__/utils/driverAvailabilityWire.test.ts` (new)|§2.2|Decimal compare: max int64, leading zeros, bad input. 6-digit fractions. v2 vs legacy (legacy online stays ready despite `REQUESTS_PAUSED`). Malformed returns null. `controller_session_id` dropped. Error kinds for `SpinrApiError`, raw Response, string detail, network.|
|7-3|`driver-app/utils/driverAvailabilityState.ts` (new), `driver-app/__tests__/utils/driverAvailabilityState.test.ts` (new)|§2.3|v8 current vs stale v7 unchanged. Same version with older issue time unchanged. New login vs old snapshot unchanged. Seq n−1 after n dropped. Server signal drops in-flight older seq. Profile failure gives eligibility unknown. 10 s deadline gives `timed_out`, then a late response applies. Intended-online and tracker-directive tables.|
|7-4|`driver-app/utils/idlePanelView.ts` (new), `driver-app/__tests__/utils/idlePanelView.test.ts` (new)|`selectIdlePanelView` (§4 table) plus `selectReadinessPrompt`|One case per row in §4. Asserts no view kind leaves the button grey without text.|

### T8 — reconciliation and explained controls
|#|Files|Change|Tests|
|---|---|---|---|
|8-1|`shared/store/authStore.ts`, `driver-app/__tests__/store/authStore.driverStatus.test.ts` (new)|`updateDriverStatus(isOnline, location?, command?: AvailabilityCommandFields)` spreads `command` into the body and **returns `res.data`**. Sets `driver.is_online` from the response boolean when present (legacy returns the same value).|Legacy body unchanged. v2 body fields. Returns data. Error rethrown. Plus the rider checks.|
|8-2|`driver-app/services/driverAvailabilityApi.ts` (new), `driver-app/__tests__/services/driverAvailabilityApi.test.ts` (new)|`fetchAvailability` (404/405 falls back to `/drivers/me`). `sendAvailabilityCommand` goes through authStore. Constants for paths and actions.|v2, flag-off, 404 fallback, 503, network, 409 details.|
|8-3|`driver-app/store/availabilityStore.ts` (new), `driver-app/store/__tests__/availabilityStore.test.ts` (new)|Reconcile core (§2.4): coalescing, deadline, generation, effects list, `noteServerSignal`, `reset`|3 triggers give 2 GETs. Deadline shows Retry. Late response applies. `auto_offline` during an in-flight request. Old generation dropped. No token means no GET.|
|8-4 (after 9-1)|`availabilityStore.ts`, its test|`runCommand` plus pending-stop retry (§2.4)|Deny written before the command. Allow only after acknowledgement. Stop network failure keeps the pending stop. Idempotent resend. At most 3 attempts. Go never auto-retried. Conflicts reconcile.|
|8-5|`driver-app/hooks/useDriverDashboard.ts`, `driver-app/hooks/__tests__/useDriverDashboard.availability.test.ts` (new)|Triggers: mount `cold_start`; app active `foreground`; `auth_success` reconnect (:1434) `ws_reconnect`; new WS case `availability_reconcile_required` → `noteServerSignal` plus `ws_conflict`; WS and foreground-push `auto_offline` → `noteServerSignal('auto_offline', mapped reason)` plus reconcile (existing alert kept); `takeReconcileRequest` on resume|One GET for simultaneous resume, reconnect and conflict. `auto_offline` gives a signal and a reconcile.|
|8-6|`useDriverDashboard.ts`, `hooks/__tests__/onlineResync.test.ts` (rewrite), `hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` (serve a READY snapshot for `/drivers/me/availability`, add `await act(async()=>{})` after mount, reset the store in `beforeEach`)|Seed `useState(false)`. Delete :2164-2195. Effect: `intended = useAvailabilityStore(s => selectIntendedOnline(s, work))`; if not null and not toggling, `setIsOnline(intended)`|Cached `is_online:true` with server paused stays offline (plan example). Unknown keeps offline. On a trip, online. Existing socket tests pass with the fixture.|
|8-7|`useDriverDashboard.ts`, `useDriverDashboard.availability.test.ts`|`toggleOnline`: no optimistic flip; keep pre-checks; `runCommand`; v2 error toasts (§4); legacy 402/403/5011/1006 mapping unchanged; rollback via `runCommand('stop_requests')`; `if (!driverData)` → retry, never the vehicle pre-check|Go acknowledged then native start. Stop order. Rollback is v2-aware. Null driver never routes to /vehicle-info.|
|8-8|`driver-app/components/dashboard/DriverIdlePanel.tsx`, `driver-app/__tests__/components/DriverIdlePanel.test.tsx`, `driver-app/i18n/en.json`|Optional `view?: IdlePanelView` and `onAction?`. When `view` is undefined, rendering is byte-identical (both existing test files still pass). Otherwise: status pill plus detail line plus circular button (label and action from the view; spinner when busy; **never the grey gradient**) plus secondary link. All §4 keys, including T12.|Each kind renders its text keys and a11y labels. `onAction` payloads. The grey `[colors.border,'#D1D5DB']` never appears when `view` is set. Busy has `accessibilityState.busy`.|
|8-9|`useDriverDashboard.ts` (return `availabilityView` and `performIdleAction`), `app/driver/(tabs)/index.tsx` (:1941-1946 pass `view`/`onAction`), `__tests__/app/driverDashboardScreen.test.tsx` (mock state and wiring)|Actions: go/stop/go-offline → commands; retry → `refreshProfile()` plus reconcile; resolve → `/documents`, `/vehicle-info`, `/appeal`, `/driver/help`, `/driver/subscription`, `/crc-consent`; sign_in → `logout()` then `/login`|Wiring|
|8-10|`shared/store/authStore.ts`, `driver-app/__mocks__/@shared/store/authStore.js`, `authStore.driverStatus.test.ts`|`registerDriverStatusFieldsProvider(fn \| null)`; logout's go-offline body becomes `{is_online:false, ...fn?.(false)}`; no-op export in the manual mock|No provider: body unchanged (refreshRace:261-292 still passes). With provider: fields added.|
|8-11|`availabilityStore.ts`, `driver-app/app/_layout.tsx` (module scope next to :57), store test|`installAvailabilityAuthBridge()` registers a provider returning v2 stop fields (new request id, current epoch) or null|Provider output|
|8-12|`useDriverDashboard.ts`, `socketLifecycle.test.ts`|WS error `session_superseded` or close 1008 `session_superseded`: don't schedule a reconnect; `noteServerSignal('session_superseded')` plus reconcile|A 1008 close doesn't create a second socket.|
|8-13 (D2)|`driver-app/store/driverStore.ts`, `store/__tests__/driverStore.reconnect.test.ts`|`fetchActiveRide` catch: when there is **no HTTP status** and the ride state is a trip phase, keep it; clear only on a definitive server answer|Updates the pinned :118-132 case|

### T9 — native tracking fence and response classification
|#|Files|Change|Tests|
|---|---|---|---|
|9-1|`driver-app/utils/availabilityMarker.ts` (new), `driver-app/__tests__/utils/availabilityMarker.test.ts` (new)|§2.5|Fence decision table. Compare-and-set protects a newer epoch. Unreadable defers. Keychain option asserted. Deny is written even if the next step fails.|
|9-2|`driverAvailabilityWire.ts`, `driver-app/utils/__tests__/tripLocationTransport.ack.test.ts` (new)|`classifyLiveResponse`, `classifyBatchResponse` (§2.6)|Every row, including a body-less 409, where trip keeps and idle drains.|
|9-3|`driver-app/utils/backgroundLocation.ts`, `driver-app/utils/__tests__/backgroundLocation.fence.test.ts` (new; copy the harness, SecureStore serving marker and generation)|`handleBackgroundLocationTask`: fence; `online_epoch` on live; live conflict handling; stop plus geofence off only when the trip flag is positively inactive|Plan example: stopped at epoch 9 (gen A), then an old callback: **zero live uploads, trip history unchanged**. `DRIVER_OFFLINE` loop stops after one 409. Legacy "Driver is not online" also fenced. Trip live still sent. `liveBlocked` blocks trip live too.|
|9-4|`backgroundLocation.ts`, `backgroundLocation.fence.test.ts`|`startBackgroundLocation` idle fence (trip cadence exempt). Geofence exit: fenced, turns the geofence off when denied and idle.|Recovery after stop doesn't restart. Logout then new login starts again.|
|9-5|`driver-app/utils/tripLocationRecorder.ts`, `driver-app/utils/tripLocationTransport.ts`, `tripLocationTransport.ack.test.ts`|`FlushPendingOptions.skipIdleSessions`. `drainWithReason(request, reason)` (quarantine). Keep the `TERMINAL_STATUS_CODES` export but mark it deprecated. The foreground transport detects `SpinrApiError` by `.status`/`.response.status` (drops the axios check) and uses the classifier.|Skip-idle still flushes trip sessions. Drain quarantines with a reason.|
|9-6|`backgroundLocation.ts` (history transport), `utils/__tests__/backgroundLocation.test.ts` (update :613-624: 409 needs a known body; acknowledgement now carries rejections), `backgroundLocation.fence.test.ts`|Background history uses the classifier plus `skipIdleSessions: fence.idle !== 'allow'`|Every existing rejection code, plus unknown trip 409 retained.|
|9-7|`useDriverDashboard.ts`, `useDriverDashboard.availability.test.ts`|`foregroundLocationTransport` becomes `apiLocationBatchTransport`. WS auth adds `online_epoch` (v2 only). Tracker effect registered with the store: `idle_denied` → deny, stop native, geofence off (not on a trip); `idle_allowed` → allow, then `ensureTracking`; on epoch change, close with 4002.|Paused snapshot stops native. Ready restarts. Epoch change re-authenticates the WS.|
|9-8|`driver-app/utils/sessionTeardown.ts`, `utils/__tests__/sessionTeardown.test.ts`|Add `clearAvailabilityMarker()` and `useAvailabilityStore.getState().reset()` as independently guarded steps|Order and isolation|
|9-9 (optional)|`driver-app/services/backgroundMessaging.ts`, `__tests__/services/backgroundMessaging.test.ts`|Background push `auto_offline`, if it reaches the handler: `denyIdleIfCurrent` plus stop when positively idle|—|

### T10 — offers
|#|Files|Change|Tests|
|---|---|---|---|
|10-1|`driver-app/services/offerAdmission.ts` (new), `driver-app/__tests__/services/offerAdmission.test.ts` (new)|§2.7|Plan example: `expiresAt = serverNow − 1` is expired. Skew uses `server_time`. `expires_at` vs `offer_expires_at` mismatch takes the earlier. Duplicate across channels. Only final outcomes dedupe. Busy state keeps the record. Re-anchor on resume.|
|10-2|`driver-app/services/pendingRideOffer.ts`, `__tests__/services/pendingRideOffer.test.ts`|`consumePendingRideOfferOutcome(): {kind:'offer',offerKey} \| {kind:'expired',offerKey,rideId} \| {kind:'none'}`. Keep the boolean `consumePendingRideOffer` wrapper. Busy and live keeps the record. Store the envelope through `setIncomingRide`.|Existing cases plus typed outcomes plus busy-keeps.|
|10-3|`driver-app/store/driverStore.ts`, `driver-app/store/__tests__/driverStore.offerDeadline.test.ts` (new)|`IncomingRide` gains `offer_key/offer_id/claim_id/deadline_mono_ms/window_ms/channel`. `setIncomingRide` sets `countdownSeconds` from the deadline. **Store-level expiry timer** → `expireOffer(key)`: v2 settles with no decline and sets `lastOfferOutcome`; legacy calls `setCountdown(0)` (unchanged auto-decline).|No fresh 15 s. Car-only expiry unsticks. v2 sends no decline POST.|
|10-4|`driverStore.ts`, `driverStore.offerDeadline.test.ts`|accept/decline: `OFFER_EXPIRED` or `RIDE_STATE_CONFLICT` (409/410 `detail.code`) settles immediately and records to the ledger. Legacy statuses unchanged.|Accept/expiry race. Lost accept response followed by a snapshot.|
|10-5|`useDriverDashboard.ts`, `driverAvailabilityApi.ts` (`postOfferReceipt`, `fetchOfferDetails`), `driver-app/hooks/__tests__/useDriverDashboard.offerDeadline.test.ts` (new)|WS offer, foreground push, and `consumePendingOffer` go through `admitOffer`. `received` receipt on admission. `presented` once per offer when the app is active, the panel is visible, and no accept is in flight. `lastOfferOutcome` → toast (§4) plus reconcile. Offer effect: snapshot `pending_offer` missing locally → fetch details → admit `'stored'`; local offer missing from a snapshot issued later → settle `gone`. Remove coordinates from the :1026-1030 log.|Duplicate WS/push. Delayed push. Presented only while visible.|
|10-6|`app/driver/(tabs)/index.tsx`, `__tests__/screens/driverOfferPanelWiring.test.ts` (rewrite the resync assertions)|Countdown = `ceil((deadline_mono_ms − monoNow)/1000)`, re-rendered every 250 ms while an offer is shown. Remove the fresh seed (:628-651) and wall-clock resync (:663-682), since re-anchoring lives in the store's `AppState` listener. `maxCountdown = window_ms/1000`.|Pinned wiring|
|10-7|`driver-app/services/backgroundMessaging.ts`, `driver-app/services/notifeeService.ts`, `driver-app/__tests__/services/offerDelivery.test.ts` (new)|Background: admit before persisting or displaying; skip expired or ledger-resolved; store `stored_at_ms` plus envelope; send a best-effort `received` receipt after display (5 s timeout, `getBackgroundAuthToken`). Notifee takes an optional `remaining_ms` for `timeoutAfter`, and dismisses if ≤ 0.|Plan example: no visible offer, accept disabled, no notification for an expired offer.|
|10-8|`driver-app/utils/pushNotificationRouting.ts`, `driver-app/app/_layout.tsx`, `__tests__/utils/pushNotificationRouting.test.ts`|Optional `onReconcile` hook, called for `new_ride_assignment`, `auto_offline`, and readiness types; `_layout` passes the store's reconcile. Logs at :308, :369, :383 print only `data.type`.|Tap reconciles|
|10-9|`driver-app/lib/androidAuto/carSession.ts`, `driver-app/lib/androidAuto/__tests__/carSession.test.ts`|`onBackgroundDispatch` goes through `admitOffer('android_auto')` plus `received`. Use the typed consume. `reconcile('car_connect')` after `ensureSession`. `register.ts` needs no change, since `offerDurationMs` reads the now deadline-based `countdownSeconds`.|Expired dispatch never surfaced on the car.|
|10-10 (defer, D9)|iOS NSE `NotificationService.swift`|Downgrade expired offer content|Device only|

### T11 — auth (run the rider checks on every commit that touches `shared/`)
|#|Files|Change|Tests|
|---|---|---|---|
|11-1|`shared/store/authStore.ts`, `driver-app/__tests__/store/authStore.refreshRace.test.ts`|§2.8 write order plus generation check|Record `setItemAsync` order. A throw on `token_expires_at` leaves the old expiry with the new access token. **Old refresh 401 after a new login**: no session-ended marker written after it, no logout callbacks, new credentials intact (plan example). A late success doesn't overwrite.|
|11-2|`shared/auth/sessionLock.ts`, `shared/store/authStore.ts`, `driver-app/__tests__/store/authStore.refreshReject.test.ts` (new)|Classifier hook with the legacy default; authStore reads the 401 body|Default: any 401 clears (rider unchanged). Installed: App Check body keeps the session recoverable; code 1003 clears; unknown clears.|
|11-3|`driver-app/utils/nativeSessionLock.ts`, `driver-app/__tests__/utils/nativeSessionLock.test.ts`|Install the driver classifier|Installer test|
|11-4|`driver-app/utils/backgroundAuth.ts`, `__tests__/utils/backgroundAuth.test.ts` (update the `it.each([401,503])` case to include a 1003 body)|App Check 401 is transient with 30 s backoff and no blacklist|Restart does not replay a *credential*-rejected token (existing)|
|11-5a (backend)|`backend/utils/refresh_tokens.py`, `backend/tests/test_refresh_tokens_lifecycle.py` (update :203-213)|Refresh-token lookup DB failure raises `DatabaseError` (503) (ask X7). This also affects `routes/admin/auth.py:503`, which is intended; also run `test_admin_auth_coverage_gap.py`.|`pytest …lifecycle.py test_refresh_token_reuse_detection.py`|
|11-5b (backend; needs a security audit)|`backend/utils/refresh_tokens.py`, `backend/routes/auth.py`, `backend/tests/test_refresh_successor_commitment.py` (new)|See §5 X8|See X8|
|11-6|`shared/auth/sessionLock.ts`, `shared/store/authStore.ts`, `driver-app/__tests__/store/authStore.refreshCommit.test.ts` (new)|Proposal protocol (§2.8), active only when installed|**Lost response then cold start recovers the proposed token.** Server ignoring it persists the server token and clears the pending record. 401 clears. Pending is keyed by parent (a background rotation discards a stale record). Uninstalled body unchanged.|
|11-7|`nativeSessionLock.ts` (install provider using expo-crypto), `backgroundAuth.ts` (same protocol), `backgroundAuth.test.ts` (update the :40-50 body assertion)|Background parity|Background lost-response recovery|

### T12 — mobile
|#|Files|Change|Tests|
|---|---|---|---|
|12-1|`driver-app/utils/idlePanelView.ts`, `idlePanelView.test.ts`|Prompt shows when v2, `readinessPolicyEnabled`, view is `ready`, local work idle, no active or pending offer, and `0 < readyUntil − serverNow ≤ 120 s`|Suppressed on a trip or offer; server time and offset used.|
|12-2|`driverAvailabilityApi.ts` (`confirmStillReady`: PUT with `is_online:true` and `availability_action:'confirm_ready'`), `availabilityStore.ts` (`runCommand('confirm_ready')`), store test|A 422 `INVALID_AVAILABILITY_COMMAND` returns `failed`. **No fallback to go_online**, so the missed-offer count isn't reset.|Confirm extends `ready_until`.|
|12-3|`DriverIdlePanel.tsx`, `driver-app/__tests__/components/DriverReadinessPrompt.test.tsx` (new)|Prompt card above the pill. Its 1 s ticker runs only while visible. Buttons: confirm, go offline.|One tap confirms. mm:ss countdown. a11y.|
|12-4|`useDriverDashboard.ts`, `useDriverDashboard.availability.test.ts`|While in the foreground, after `readyUntil − serverNow + 1.5 s` → `reconcile('ready_deadline')` (a single timer, not polling). Timer re-armed on snapshot or resume.|Deadline reconciles to paused `READY_TIMEOUT` with GO.|

**Order:** 7-1 → 7-2 → 7-3 → 7-4. 9-1 and 11-x are independent. 8-1 → 8-2 → 8-3 → (9-1) → 8-4 → 8-5…8-13. 9-2 → 9-3…9-6. 9-7 needs 8-6. 10-1 needs 7-1; 10-5/10-6 need 10-3. T12 needs 8-8 and backend X4.

## 4. Copy and UX (`availability.*` in en.json; fr/es fall back to English through i18n/index.ts:62-71)

Reuse existing pieces:
- the pill styles (DriverIdlePanel.tsx:394-437);
- `SPACING` and `FONT` (already imported, :13);
- Ionicons (`alert-circle-outline`, `time-outline`);
- `ActivityIndicator` (spinners, not skeletons);
- `showToast` for outcomes.

**The GO/STOP button keeps its size, gradient (`colors.primary → colors.primaryDark`) and pulse.** It is a design-system exception that must never be dimmed. Pill and detail text use `colors.text` / `colors.textSecondary`: amber, emerald and red text on their tints are about 2–3.6:1, below WCAG AA. Tone shows through the dot and border using `warning`/`warningBg` and `error`/`dangerBg`. The detail line allows font scaling with `numberOfLines={3}`. The status block uses `accessibilityLiveRegion="polite"`, plus `AccessibilityInfo.announceForAccessibility(title)` on iOS when a warning or error kind appears. Secondary links have a minimum 44 pt hit area.

|View kind|Pill (key → text)|Detail|Button → action (a11y label)|
|---|---|---|---|
|offline|`offline` "You're offline"|`offlineDetail` "Tap GO when you're ready for ride requests."|GO → go_online ("Go online")|
|going_online / stopping|"Going online…" / "Stopping requests…"|—|Spinner, busy ("Please wait")|
|checking|`checking` "Checking your status…"|—|Spinner|
|check_failed (timed out or network)|"We couldn't check your status"|"Check your connection, then try again."|RETRY ("Try checking your status again")|
|eligibility_unknown|"We couldn't check your eligibility"|"This is usually temporary. Try again in a moment."|RETRY|
|ready|"You're online"|"Ride requests will show here."|STOP ("Stop new ride requests")|
|reconnecting|"Reconnecting — new requests paused"|On a trip: "Your current trip isn't affected." Otherwise: "We'll resume requests once you're reconnected."|STOP, plus link "Retry"|
|gps_waiting (`LOCATION_STALE`)|"Waiting for your location — new requests paused"|"Make sure location is turned on."|STOP|
|paused_idle (`READY_TIMEOUT`)|"Requests paused"|"We paused new requests because we didn't hear back from you. Tap GO when you're ready."|GO|
|paused_misses (`MISSED_OFFERS`)|"Requests paused"|"We paused new requests after several went unanswered. Tap GO when you're ready."|GO|
|not_taking_requests (`REQUESTS_STOPPED`)|"You're not taking new requests"|"Tap GO to start receiving requests again."|GO, plus link "Go offline"|
|quota|"Daily ride limit reached"|"You've used today's Spinr Pass rides. They reset at midnight." (existing :1884 copy)|Link "View Spinr Pass"|
|blocked: documents, licence, insurance|"A document needs updating" / "Your driver's licence has expired" / "Your insurance has expired"|"Update your documents to go online."|UPDATE → /documents ("Open documents to update them")|
|blocked: review / unverified|"Your account is being reviewed" / "Your account isn't verified yet"|"We'll let you know when it's done." / "Finish your documents so we can verify your account."|STATUS → /documents|
|blocked: suspended / inactive / policy|"Your account is on hold" / "Your account isn't active" / "Requests paused for an account check"|"You can contact support or submit an appeal."|HELP → /appeal or /driver/help|
|blocked: no vehicle (profile)|"Add your vehicle to go online"|—|ADD → /vehicle-info|
|session_ended|"Your session ended"|"You signed in on another phone. Sign in again to use this one."|SIGN IN|
|Readiness prompt|`stillAvailableTitle` "Still available for ride requests?"|`stillAvailableBody` "New requests pause in {time} unless you confirm." (`{time}` replaced by the caller)|"Yes, I'm available" (confirm_ready) / "Go offline"|
|Toasts|"This offer expired" / "You're still online. New requests will show here." · "This request is no longer available" · Command errors: `OBLIGATION_ACTIVE` "Finish your current trip or request first." · `ONLINE_EPOCH_STALE`/controller mismatch "Your status changed. Check it and try again." · 503 "We couldn't check your eligibility. Try again in a moment." · stop on network "We'll finish stopping requests when you're back online." · confirm failed "Couldn't confirm. Check your connection and try again."|||

The copy avoids "must", "required", "shift" and "penalty"; every pause names a factual reason and puts the next step in the driver's hands ("Tap GO when you're ready").

## 5. Risks, rider blast radius, decisions, cross-team asks

**Rider blast radius (shared files):**
- `shared/types/driverAvailability.ts`: types only; rider `tsc` compiles it.
- `shared/auth/sessionLock.ts`: new installers; the rider never installs them.
- `shared/store/authStore.ts`:
  - `updateDriverStatus` has an optional parameter and returns data (no rider callers);
  - the logout provider stays unregistered in the rider app;
  - the new write order makes crashes safer;
  - **the generation check only matters when a sign-in lands during an in-flight refresh; the rider would see a bug fix (D3).**
- `shared/api/client.ts` is **not touched**.
- Rider tests to run: `rider-app/__tests__/auth.integration.ts` (real authStore), `api-client-401-refresh`, `api-client-503-retry`, `otpScreen`, `loginScreen`, `accountScreen`, `shared/api/__tests__/client.refresh`, `client.sos`, and the full rider suite (its roots include shared). On the driver side, run the real-authStore suites `authStore.initialize` and `authStore.refreshRace`.
- Known forks: no mobile file here is in docs/known-forks.md. `backend/routes/auth.py` is (its twin is `routes/admin/auth.py`). 11-5b must keep **exactly 5** `get_real_client_ip(request)` calls (test_async_limiter.py:176-200) and leave the admin twin unchanged, since admin uses a web cookie flow.

**Risks:**
- Native behaviour is not proven by jest: Doze, force-kill, keychain after first unlock, geofence wakes. The device matrix from plan §9 is needed.
- driver-app has no visual regression tooling, so panel changes are reasoned about, not screenshotted, and the Change Impact entry must say so.
- Tests pinned to source text are listed in finding 23.
- Against a flag-off server, the visible changes are:
  - GO shows a spinner until acknowledged;
  - a stop that fails on the network keeps "Stopping…" instead of reverting (D6);
  - `auto_offline` now stops native tracking;
  - explained states replace the grey GO;
  - trip batches with an unknown 409 are no longer drained;
  - stored offers show the true time left.
- **Rollback:** all of this is JS. Ship through a staged EAS Update and revert with `.github/workflows/eas-rollout-control.yml` `action=revert`. v2 behaviours also sit behind the server flag.
- **Import rule for implementers:** never add a runtime shared module that driver code imports. If one becomes unavoidable, add `driver-app/__mocks__/@shared/...` in the same commit.

**Decisions for a human** (defaults in parentheses):
- **D1.** Stop auto-declining when an offer's local countdown expires (v2 envelopes only; legacy unchanged).
- **D2.** Keep a trip on a `fetchActiveRide` network error (yes). This reverses a pinned test.
- **D3.** Apply the generation check to the rider app too (yes).
- **D4.** Treat App Check 401 as transient for the rider app too (driver-only for now).
- **D5.** Enable the refresh-token proposal flag only after a security audit.
- **D6.** Pending stop instead of revert also for legacy servers (yes).
- **D7.** A "stop new requests" control inside ActiveRidePanel (not in this PR).
- **D8.** Offers dropped while on `trip_completed` (existing; not in this PR).
- **D9.** iOS notification service extension changes (defer).

**Asks for the backend architect:**
- **X1 (T5 offer envelope):**
  - `offer_id` equals `ride_offers.id`, the same as `snapshot.pending_offer.id`;
  - include `claim_id`, a decimal-string `online_epoch`, ISO `server_time` and `expires_at`;
  - keep `offer_expires_at` equal to `expires_at`;
  - FCM values are strings.
- **X2 (T5 errors):** 409 `{detail:{code:'OFFER_EXPIRED'|'RIDE_STATE_CONFLICT', snapshot}}`.
- **X3 (T6 receipts):** the proposed path and body, idempotent per (offer, session, event); the client ignores 404 and 409.
- **X4 (T12):** status `availability_action:'confirm_ready'` with `is_online:true`, preferably no epoch bump and no missed-offer reset; add `readiness_policy_enabled` to the snapshot.
- **X5 (logout):** `/auth/logout` should run `stop_requests` for v2 drivers.
- **X6 (live location):** don't require the epoch when a ride is assigned; move `_require_presence_epoch` after the ride lookup (location.py:989).
- **X7 (refresh DB error):** a refresh-token lookup DB error should return 503 (11-5a).
- **X8 (refresh-token proposal):**
  - Migration **461** adds `settings.refresh_successor_commitment_enabled boolean NOT NULL DEFAULT false`.
  - `RefreshRequest.proposed_refresh_token: Optional[str]` (must match `^[A-Za-z0-9_-]{64}$`).
  - With the flag on, and before `lookup_refresh_token`, add `classify_committed_replay(parent, proposed)`:
    - **Recover** when the parent is revoked, its `replaced_by` row's hash matches sha256 of the proposal (compared with `hmac.compare_digest`), and that row is unrevoked, unexpired and the same generation. Share the existing access-token and cookie code, return `refresh_token = proposed`, and **do not call** `issue_refresh_token`.
    - **Dead** when the hash matches but the successor is revoked, expired or from another generation: return 401 with code 1003 and **no cascade**.
    - **No match**: use the existing path.
  - On a normal rotation, call `issue_refresh_token(..., raw=proposed)`; if the insert conflicts, retry with a server-generated value.
  - Backend tests: flag off, proposal ignored; fresh parent stores the hash of the proposal; lost response returns the same token with no new row and no cascade; a revoked successor gives 401 with no cascade even after 600 s; a parent without a proposal keeps today's behaviour; a bad proposal is ignored; a generation mismatch gives 401.
- **X9:** include `online_epoch`/`state_version` on WS `auto_offline`.
- **X10:** consider dropping `controller_session_id` from the snapshot; the client never uses it.

### Critical Files for Implementation
- /home/user/spinrvm/driver-app/hooks/useDriverDashboard.ts
- /home/user/spinrvm/shared/store/authStore.ts
- /home/user/spinrvm/driver-app/utils/backgroundLocation.ts
- /home/user/spinrvm/driver-app/store/driverStore.ts
- /home/user/spinrvm/driver-app/components/dashboard/DriverIdlePanel.tsx
- Also read closely: /home/user/spinrvm/backend/services/driver_availability_service.py, /home/user/spinrvm/backend/routes/drivers/location.py, /home/user/spinrvm/driver-app/utils/tripLocationRecorder.ts, /home/user/spinrvm/driver-app/services/pendingRideOffer.ts, /home/user/spinrvm/driver-app/utils/backgroundAuth.ts, /home/user/spinrvm/backend/utils/refresh_tokens.py