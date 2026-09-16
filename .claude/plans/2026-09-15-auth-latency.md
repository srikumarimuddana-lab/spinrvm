# Authentication Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Reduce rider and driver OTP-screen transition latency and perceived logout latency by measuring every phase, removing unnecessary pre-auth work, and preserving all authentication and driver-location safety guarantees.

**Architecture:** First add correlated client, FastAPI, and Supabase-boundary timing so one request ID explains the complete path. Then make the two public OTP endpoints bypass client-side SecureStore and Firebase App Check work that the backend does not require. Finally make logout publish the local signed-out state immediately while the existing ordered remote revocation and privacy cleanup continue safely.

**Tech Stack:** Expo React Native, TypeScript, Jest, FastAPI, Python 3.12, pytest, Supabase Postgres/PostgREST, Firebase App Check, Fly.io, Prometheus-style metrics.

**Spec:** .claude/plans/2026-09-15-auth-latency.md, sections “Evidence”, “Decisions”, “Security invariants”, and “Acceptance criteria”.

## Global Constraints

- Never log a full phone number, OTP, access token, refresh token, raw GPS coordinate, name, or email.
- Preserve OTP hashing, expiry, rate limits, Redis lockout, reviewer-account behavior, and the non-production fixed code 1234.
- Do not change Twilio behavior in this work.
- Keep Firebase App Check on every endpoint except the already backend-exempt exact paths /auth/send-otp and /auth/verify-otp.
- Keep /auth/refresh protected by App Check.
- Preserve driver logout ordering: mark the driver offline before server session revocation; stop location producers before deleting queued coordinates.
- A database migration or new index is out of scope unless new measurements contradict the current query-plan evidence.
- Every behavior-changing PR must include the repository Change Impact & Risk Log.
- Each implementation commit must touch no more than three files and represent one independently testable change.

---

## Evidence

The September 15 investigation found:

| Check | Result |
|---|---|
| Supabase project | ACTIVE_HEALTHY in ca-central-1 |
| Connections | Approximately 6–7 of 60, no blocked or waiting query |
| Cache | Table cache 100%; index cache 99.97% |
| users.phone direct plan | Existing index used; approximately 1.324 ms execution |
| Exact users-by-phone PostgREST statement | 424 calls; approximately 24.025 ms weighted mean |
| OTP lookup | Approximately 0.102 ms on a 23-row table |
| OTP delete / insert | Approximately 1.009 ms / 4.515 ms mean |
| Twilio in non-production | No external call; fixed OTP 1234 and local fallback |
| Send OTP user lookup | None; users.phone is queried during verification, not send |
| Client pre-auth path | Waits for getAuthHeader and Firebase App Check before fetch |
| Backend App Check policy | send-otp and verify-otp are already exempt |
| Logout | Remote requests, secure storage deletion, cache clearing, and callbacks are awaited sequentially before logout resolves |

These facts rule out a missing users.phone index as the current explanation. They do not yet identify how much of the observed three-to-four seconds belongs to client preparation, device-to-Fly network, Fly processing, backend-to-Supabase calls, response parsing, or navigation.

## Decisions

### Selected approach: instrument, then make two bounded optimizations

1. Add timing with one request ID across mobile and backend.
2. Exempt only the two public OTP paths from client authentication-token and App Check preparation.
3. Publish logged-out Zustand state immediately after capturing the credentials required for ordered cleanup.
4. Keep database structure unchanged.
5. Compare cold and warm builds on both iOS and Android before and after each change.

### Rejected alternatives

- Add another users.phone index: rejected because the existing index is used and send-otp does not query users.
- Disable Firebase globally: rejected because protected mobile endpoints rely on App Check.
- Fire-and-forget all logout work: rejected because session revocation and driver-location shutdown are security and privacy controls.
- Move Twilio delivery to a background task in this change: rejected because it changes production delivery semantics and is unrelated to the current no-Twilio delay.

## Security invariants

- Public-path matching must be exact; substring matching such as url.includes("/auth") is forbidden.
- No timing label may contain a phone number, URL query value, user ID, OTP, or token.
- Client timing storage is bounded and in-memory; it must not persist request bodies.
- Driver state becomes locally signed out before UI navigation, but the captured credentials remain available only inside the active logout closure until server revocation finishes.
- Failure of remote cleanup remains visible through existing non-fatal reporting and must never restore the local session.
- The driver session-ended marker and producer-stop steps remain mandatory.

## Acceptance criteria

| Flow | Target |
|---|---|
| Non-production send-otp backend, warm | P95 below 500 ms |
| Tap to OTP screen, warm Canadian device/network | P95 below 1,000 ms |
| Tap to OTP screen, cold App Check state | P95 below 1,500 ms because public OTP no longer waits for attestation |
| Logout state transition visible to UI | P95 below 300 ms |
| Full logout cleanup | P95 below 2,000 ms on a healthy network; phase timeout/failure is reported |
| Functional regression | Zero change to OTP response shape, rate limiting, verification, token rotation, or driver location teardown |
| Privacy | Zero PII or raw coordinates in new metrics, logs, breadcrumbs, or test snapshots |

The device targets are release gates measured from at least 20 successful samples per app/platform combination. Rate-limited 429 responses are counted separately and never mixed into success latency.

---

### Task 1: Backend total request timing

**Files:**
- Modify: backend/core/middleware.py
- Create: backend/tests/test_request_timing_middleware.py

**Interfaces:**
- Consumes: existing X-Request-ID request correlation.
- Produces: X-Response-Time-Ms and Server-Timing: app;dur=N response headers plus spinr_http_request_duration_ms histogram observations.

- [ ] **Step 1: Write failing middleware tests**

Create focused ASGI tests that assert successful and failing responses both contain the same request ID plus finite, non-negative timing headers. The test must also assert that paths are represented by a bounded route-family label rather than raw URLs.

~~~python
def test_request_timing_headers_are_emitted(client):
    response = client.get("/health", headers={"X-Request-ID": "latency-test-id"})
    assert response.headers["X-Request-ID"] == "latency-test-id"
    assert float(response.headers["X-Response-Time-Ms"]) >= 0
    assert response.headers["Server-Timing"].startswith("app;dur=")
~~~

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run:

~~~bash
cd backend
pytest tests/test_request_timing_middleware.py -q
~~~

Expected: failure because X-Response-Time-Ms and Server-Timing are absent.

- [ ] **Step 3: Add monotonic timing around call_next**

Use time.perf_counter, emit the two headers, and observe spinr_http_request_duration_ms with method, route_family, and status labels. Do not use a raw path with IDs or query strings as a metric label.

~~~python
started = time.perf_counter()
try:
    response = await call_next(request)
finally:
    duration_ms = (time.perf_counter() - started) * 1000.0

response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"
_metric_observe(
    "spinr_http_request_duration_ms",
    duration_ms,
    {"method": request.method, "route_family": route_family, "status": str(response.status_code)},
)
~~~

- [ ] **Step 4: Verify targeted and existing middleware tests**

Run:

~~~bash
cd backend
pytest tests/test_request_timing_middleware.py tests/test_csrf_middleware.py -q
ruff check core/middleware.py tests/test_request_timing_middleware.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add backend/core/middleware.py backend/tests/test_request_timing_middleware.py
git commit -m "obs: measure backend request latency"
~~~

---

### Task 2: OTP backend phase timing

**Files:**
- Modify: backend/routes/auth.py
- Modify: backend/tests/test_auth_send_otp.py
- Modify: backend/tests/perf_baseline.py

**Interfaces:**
- Consumes: backend.utils.metrics.time_ms and the existing send_otp phases.
- Produces: spinr_auth_send_otp_phase_duration_ms with phase labels settings, otp_delete, otp_insert, and sms; plus an in-process performance baseline.

- [ ] **Step 1: Add failing metric tests**

Patch backend.routes.auth._metric_time_ms and assert one timing context is entered for every executed phase. In the no-Twilio non-production case, the sms phase must still be measured but the Twilio client must not be constructed.

~~~python
expected_phases = {"settings", "otp_delete", "otp_insert", "sms"}
observed_phases = {
    call.args[1]["phase"]
    for call in mock_time_ms.call_args_list
}
assert observed_phases == expected_phases
mock_twilio_client.assert_not_called()
~~~

- [ ] **Step 2: Run the targeted auth test and confirm failure**

Run:

~~~bash
cd backend
pytest tests/test_auth_send_otp.py -q
~~~

Expected: failure because phase timers are not yet present.

- [ ] **Step 3: Wrap each await boundary with the existing metrics context manager**

Use one metric name with a bounded phase label. Record failures as well as successes; time_ms already observes from its finally block.

~~~python
with _metric_time_ms("spinr_auth_send_otp_phase_duration_ms", {"phase": "settings"}):
    app_settings = await get_app_settings()

with _metric_time_ms("spinr_auth_send_otp_phase_duration_ms", {"phase": "otp_delete"}):
    await db_supabase.delete_many("otp_records", {"phone": phone})

with _metric_time_ms("spinr_auth_send_otp_phase_duration_ms", {"phase": "otp_insert"}):
    await db_supabase.insert_otp_record(otp_record.dict())
~~~

Do not attach phone, OTP, environment secret, or request body as a label.

- [ ] **Step 4: Add send-otp to the in-process performance baseline**

Mock settings, Supabase, and SMS boundaries so the baseline detects Python/FastAPI regressions without pretending to measure Fly or Supabase network latency. Report P50/P95/P99 under a distinct auth_send_otp key.

- [ ] **Step 5: Verify auth behavior and baseline**

Run:

~~~bash
cd backend
pytest tests/test_auth_send_otp.py tests/test_auth.py -q
python tests/perf_baseline.py
ruff check routes/auth.py tests/test_auth_send_otp.py tests/perf_baseline.py
~~~

Expected: existing OTP behavior remains unchanged and the new baseline is emitted.

- [ ] **Step 6: Commit**

~~~bash
git add backend/routes/auth.py backend/tests/test_auth_send_otp.py backend/tests/perf_baseline.py
git commit -m "obs: measure send otp phases"
~~~

---

### Task 3: Exact public-auth client policy and timing

**Files:**
- Modify: shared/api/client.ts
- Create: shared/api/__tests__/client.public-auth.test.ts

**Interfaces:**
- Consumes: getAuthHeader, appCheckHeader, generateRequestId, and fetchWithTimeout.
- Produces: isPublicPreAuthPath and bounded ApiPerformanceLogEntry records retrievable through getApiPerformanceLog.

- [ ] **Step 1: Write failing request-policy tests**

The tests must prove exact behavior, including negative cases.

~~~typescript
it.each(['/auth/send-otp', '/auth/verify-otp'])(
  'does not prepare stored auth or App Check for %s',
  async (path) => {
    await client.post(path, { phone: '+13065550100' });
    expect(mockGetAuthHeader).not.toHaveBeenCalled();
    expect(mockAppCheckProvider).not.toHaveBeenCalled();
  },
);

it.each(['/auth/refresh', '/auth/logout', '/users/profile'])(
  'keeps protected request preparation for %s',
  async (path) => {
    await client.post(path, {});
    expect(mockGetAuthHeader).toHaveBeenCalled();
    expect(mockAppCheckProvider).toHaveBeenCalled();
  },
);
~~~

Use a synthetic test phone only in request setup; never copy it into timing logs or snapshots.

- [ ] **Step 2: Run the client test and confirm failure**

Run from both app environments because shared code is bundled by both:

~~~bash
cd rider-app
yarn test ../shared/api/__tests__/client.public-auth.test.ts --runInBand
~~~

Expected: public OTP calls currently invoke both providers.

- [ ] **Step 3: Implement exact public-path matching**

~~~typescript
const PUBLIC_PRE_AUTH_PATHS = new Set([
  '/auth/send-otp',
  '/auth/verify-otp',
]);

const isPublicPreAuthPath = (url: string): boolean =>
  PUBLIC_PRE_AUTH_PATHS.has(url);
~~~

In client.post, skip getAuthHeader and appCheckHeader only when this function returns true. Keep request ID, deadline, app identity, content type, error handling, and retry behavior unchanged.

- [ ] **Step 4: Record bounded client phases**

Record auth_header_ms, app_check_ms, network_ms, parse_ms, total_ms, status, method, redacted path, request_id, surface, and a cold_or_warm marker. Keep at most 100 entries in memory. Do not store headers, request bodies, response bodies, phone numbers, or tokens.

~~~typescript
export interface ApiPerformanceLogEntry {
  ts: string;
  method: string;
  path: string;
  status: number;
  request_id: string;
  auth_header_ms: number;
  app_check_ms: number;
  network_ms: number;
  parse_ms: number;
  total_ms: number;
  surface?: 'rider-app' | 'driver-app';
}
~~~

- [ ] **Step 5: Verify exact exemptions and protected-path regression coverage**

Run:

~~~bash
cd rider-app
yarn test shared/api/__tests__/client.public-auth.test.ts __tests__/api-client-401-refresh.test.ts --runInBand
yarn lint
cd ../driver-app
yarn test shared/api/__tests__/client.public-auth.test.ts __tests__/store/authStore.refreshRace.test.ts --runInBand
yarn lint
~~~

Expected: OTP endpoints skip the two unnecessary providers; refresh and all protected endpoints retain them.

- [ ] **Step 6: Commit**

~~~bash
git add shared/api/client.ts shared/api/__tests__/client.public-auth.test.ts
git commit -m "perf: remove pre-auth request setup"
~~~

---

### Task 4: Rider tap-to-OTP measurement

**Files:**
- Modify: rider-app/app/login.tsx
- Modify: rider-app/app/otp.tsx
- Create: rider-app/__tests__/app/loginLatency.test.tsx

**Interfaces:**
- Consumes: API request performance entry and Expo Router params.
- Produces: non-PII rider auth_tap_to_response_ms and auth_tap_to_otp_mount_ms breadcrumbs.

- [ ] **Step 1: Write a failing screen test**

Use fake timers and a mocked API response. Assert that navigation occurs immediately after the API resolves and that the timing payload contains durations and surface only, never the phone.

- [ ] **Step 2: Add a monotonic start timestamp to the send action**

Pass an opaque numeric authStartMs route parameter to the OTP screen. On OTP mount, calculate the complete transition duration and add a non-sensitive breadcrumb tagged surface=rider-app and flow=send_otp.

- [ ] **Step 3: Verify rider tests and production export**

Run:

~~~bash
cd rider-app
yarn test __tests__/app/loginLatency.test.tsx --runInBand
yarn lint
npx expo export --platform ios
npx expo export --platform android
~~~

Expected: tests pass; both platform exports succeed; there is no visual change.

- [ ] **Step 4: Commit**

~~~bash
git add rider-app/app/login.tsx rider-app/app/otp.tsx rider-app/__tests__/app/loginLatency.test.tsx
git commit -m "obs: measure rider otp transition"
~~~

---

### Task 5: Driver tap-to-OTP measurement

**Files:**
- Modify: driver-app/app/login.tsx
- Modify: driver-app/app/otp.tsx
- Create: driver-app/__tests__/app/loginLatency.test.tsx

**Interfaces:**
- Consumes: the same route parameter contract as the rider task.
- Produces: non-PII driver auth_tap_to_response_ms and auth_tap_to_otp_mount_ms breadcrumbs.

- [ ] **Step 1: Mirror the rider failing test for the driver surface**

Assert the driver screen reports surface=driver-app and never includes the submitted phone.

- [ ] **Step 2: Implement the identical timing contract in the driver screens**

Do not copy Firebase logic into either screen. The shared client remains the sole owner of request-header policy.

- [ ] **Step 3: Verify driver tests and production export**

Run:

~~~bash
cd driver-app
yarn test __tests__/app/loginLatency.test.tsx __tests__/app/otpScreen.test.tsx --runInBand
yarn lint
npx expo export --platform ios
npx expo export --platform android
~~~

Expected: tests pass; both platform exports succeed; there is no visual change.

- [ ] **Step 4: Commit**

~~~bash
git add driver-app/app/login.tsx driver-app/app/otp.tsx driver-app/__tests__/app/loginLatency.test.tsx
git commit -m "obs: measure driver otp transition"
~~~

---

### Task 6: Immediate local logout state with ordered cleanup

**Files:**
- Modify: shared/store/authStore.ts
- Modify: driver-app/__tests__/store/authStore.initialize.test.ts
- Create: rider-app/__tests__/store/authStore.logoutLatency.test.ts

**Interfaces:**
- Consumes: existing storage adapter, api client, appCache, session marker, and registered logout callbacks.
- Produces: logout that makes the Zustand session locally unauthenticated before awaiting network cleanup, while returning a promise that settles only after cleanup completes.

- [ ] **Step 1: Write failing ordering tests**

Use deferred promises for driver-offline and server-logout calls. Immediately after invoking logout, assert the store has no user, driver, token, or refreshToken even though neither network promise has resolved. Then resolve each promise and assert the existing safety order.

~~~typescript
const logoutPromise = useAuthStore.getState().logout();
expect(useAuthStore.getState()).toMatchObject({
  user: null,
  driver: null,
  token: null,
  refreshToken: null,
});
expect(mockPut).toHaveBeenCalledBefore(mockPost);

resolveDriverOffline();
resolveServerLogout();
await expect(logoutPromise).resolves.toBeUndefined();
~~~

Also assert a remote failure cannot restore the cleared local session.

- [ ] **Step 2: Capture the immutable cleanup snapshot before changing state**

Capture driver ID, access-token presence, refresh token, and revokeServerSession option in local variables. Publish the signed-out Zustand state synchronously. Retain the in-memory access token only for the ordered remote calls, then clear it in a finally block.

- [ ] **Step 3: Preserve privacy-critical cleanup**

Keep these guarantees unchanged:

1. Driver offline request precedes server logout.
2. Server logout receives the captured refresh token.
3. Local token storage is deleted.
4. SESSION_ENDED_KEY is written.
5. Driver location producers stop before the coordinate outbox is purged.
6. All registered callbacks run even when cache clearing fails.
7. logout never rejects solely because a best-effort remote or marker operation failed.

- [ ] **Step 4: Add phase duration breadcrumbs without sensitive values**

Measure local_state, driver_offline, server_revoke, credential_delete, cache_clear, and callbacks. Report only phase, duration, outcome, and surface.

- [ ] **Step 5: Verify rider and driver logout behavior**

Run:

~~~bash
cd driver-app
yarn test __tests__/store/authStore.initialize.test.ts utils/__tests__/sessionTeardown.test.ts --runInBand
yarn lint
cd ../rider-app
yarn test __tests__/store/authStore.logoutLatency.test.ts __tests__/accountScreen.test.tsx --runInBand
yarn lint
~~~

Expected: local signed-out state is synchronous, remote order is preserved, and teardown tests remain green.

- [ ] **Step 6: Commit**

~~~bash
git add shared/store/authStore.ts driver-app/__tests__/store/authStore.initialize.test.ts rider-app/__tests__/store/authStore.logoutLatency.test.ts
git commit -m "perf: publish logout state before cleanup"
~~~

---

### Task 7: Staging measurement and decision gate

**Files:**
- Create: docs/change-log/2026-09-15-auth-latency.md
- Modify: docs/runbooks/MOBILE_SMOKE.md

**Interfaces:**
- Consumes: client timing log, request IDs, response timing headers, backend histograms, Fly logs, and Supabase pg_stat_statements.
- Produces: reproducible baseline/after table and a release decision.

- [ ] **Step 1: Test the exact build target**

Record the resolved API hostname from a development/preview build without logging credentials. Run 20 successful cold and 20 successful warm samples for each of rider iOS, rider Android, driver iOS, and driver Android.

- [ ] **Step 2: Correlate every slow sample**

For any sample over 1,000 ms, record the non-sensitive request ID and compare:

- tap-to-request preparation;
- auth header and App Check;
- device-to-Fly network;
- X-Response-Time-Ms;
- send-otp backend phase metrics;
- response-to-OTP-screen mount.

Do not infer a Fly cold start without a machine-start event and matching request timestamp.

- [ ] **Step 3: Validate Supabase under the same window**

Capture pg_stat_statements aggregates and EXPLAIN ANALYZE for the exact phone and OTP access patterns using synthetic values. Do not add an index when execution remains below 10 ms or when the planner correctly selects a sequential scan for a tiny table.

- [ ] **Step 4: Apply the release gates**

Approve rollout only when:

- all functional and security tests pass;
- protected endpoints still send App Check;
- successful warm tap-to-screen P95 is below 1,000 ms;
- logout local-state P95 is below 300 ms;
- no sample contains PII in telemetry;
- Fly and Supabase timings reconcile with the client total within expected network overhead.

If backend P95 remains above 500 ms and otp_delete plus otp_insert dominate, open a separate design for a single atomic Supabase RPC/upsert. That change requires its own migration, RLS/security review, rollback SQL, and Supabase advisor run; it is not authorized by this plan alone.

- [ ] **Step 5: Complete the Change Impact & Risk Log**

Document measured before/after values, blast radius, exact builds, platform matrix, rollback, what was not verified, and whether real production exports were run.

- [ ] **Step 6: Commit**

~~~bash
git add docs/change-log/2026-09-15-auth-latency.md docs/runbooks/MOBILE_SMOKE.md
git commit -m "docs: record auth latency verification"
~~~

---

## PR sequence

1. Observability: Tasks 1–2. Safe additive backend metrics and timing headers.
2. OTP optimization: Tasks 3–5. Exact public-route policy plus rider/driver end-to-end measurements.
3. Logout responsiveness: Task 6. Immediate local state with unchanged cleanup guarantees.
4. Evidence and rollout: Task 7. Staging matrix and release record.

Do not combine all behavior changes into one implementation PR. Each PR must be independently reversible and must include its own Change Impact & Risk entry.

## Rollback

- Timing headers and metrics: disable emission with a small code revert; they do not alter stored data.
- Public OTP policy: remove the two exact-path exemptions; requests return to attaching App Check and consulting stored auth.
- Logout state timing: restore the previous point at which Zustand state is cleared; no database rollback is required.
- No migration, index, table, token format, API response, or persisted user data changes are included.

## Plan self-review

- Spec coverage: client, Fly/backend, Supabase boundary, navigation, and logout phases are each measurable.
- Placeholder scan: no unresolved implementation choice remains; the RPC path is explicitly a separate, evidence-gated design.
- Type consistency: PUBLIC_PRE_AUTH_PATHS, ApiPerformanceLogEntry, getApiPerformanceLog, and metric names are defined before use.
- Scope: no Twilio redesign, global Firebase change, database migration, or unrelated refactor.
