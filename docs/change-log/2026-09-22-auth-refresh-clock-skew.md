# Change Impact & Risk — Auth Refresh Clock Skew

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | GPT-6 Luna developer follow-up |
| Surface(s) | rider-app, driver-app, shared |
| Domain (Sentry tag) | auth |
| PR / commit link | PR #5716; `2e5e8c700fd9fbd1fc36df92af17c2589663064d`, `b2639e8edb946e9e9d72d509c333cbd930bd041e`, `7cbc71684b1429ef27d04e777655780a8a742bb6`, `87099c8d7d6c37a37639388f017b08344805fd3d` |
| Related issue or gap ID | PR #5716 verified device-clock-skew refresh bug |

## 1. Issue / gap identified

Foreground refresh in both apps and driver headless refresh rejected a valid rotated credential pair when the access token's absolute expiry looked past according to a skewed device clock. The backend now also provides `expires_in`, a relative lifetime that is independent of the device's absolute clock setting.

## 2. Root cause

Both paths parsed `access_expires_at` and compared it directly with `Date.now()`. This could reject a successful server refresh before persisting the successor refresh token, or store a lifetime anchored to a clock that disagreed with the server.

## 3. Fix / remediation

Both refresh paths now prefer a finite, positive `expires_in`, anchor it to device time, and reject a computed expiry that overflows. For legacy responses, the driver headless fetch path uses the HTTP `Date` header when available; foreground uses the device clock because `shared/api/client.ts` returns only `{data, status}` and drops response headers. A past legacy absolute expiry clamps to zero while a structurally valid rotated token pair is retained. Responses with malformed/missing token strings or with no usable expiry remain rejected. The background path still writes the successor refresh token before the access token and expiry.

## 4. Risk & impact on existing functionality

- Blast radius: cross-surface shared auth plus driver background location refresh.
- Shared `authStore` direct consumers checked: `driver-app/app/_layout.tsx`, `driver-app/app/become-driver.tsx`, `driver-app/app/documents.tsx`, `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/app/driver/(tabs)/profile.tsx`, `driver-app/app/driver/settings.tsx`, `driver-app/app/index.tsx`, `driver-app/app/otp.tsx`, `driver-app/app/profile-setup.tsx`, `driver-app/app/reactivate-account.tsx`, `driver-app/app/vehicle-info.tsx`, `driver-app/components/dashboard/DriverIdlePanel.tsx`, `driver-app/hooks/useAuth.ts`, `driver-app/hooks/useDriverDashboard.ts`, `driver-app/lib/androidAuto/carSession.ts`, `driver-app/utils/sessionTeardown.ts`; and `rider-app/app/(tabs)/account.tsx`, `rider-app/app/(tabs)/index.tsx`, `rider-app/app/_layout.tsx`, `rider-app/app/ai-assistant.tsx`, `rider-app/app/become-driver.tsx`, `rider-app/app/index.tsx`, `rider-app/app/login.tsx`, `rider-app/app/otp.tsx`, `rider-app/app/privacy-settings.tsx`, `rider-app/app/profile-setup.tsx`, `rider-app/app/reactivate-account.tsx`, `rider-app/app/ride-completed.tsx`, `rider-app/app/ride-details.tsx`, `rider-app/app/settings.tsx`, `rider-app/app/verify-email.tsx`, `rider-app/hooks/useAuth.ts`, `rider-app/hooks/useRiderSocket.ts`, `rider-app/store/aiChatStore.ts`, `rider-app/store/rideStore.ts`, and `rider-app/utils/apiClient.ts`.
- Headless background auth is consumed by `driver-app/utils/backgroundLocation.ts`; its regression suite is `driver-app/__tests__/utils/backgroundAuth.test.ts`.
- Main regression risk is an incorrectly long/short client-side access-token lifetime. TTL validation, timestamp fallback, overflow tests, and malformed-credential tests cover the relevant cases. Refresh token rotation, session-ended ownership fences, and serialized storage writes remain in place.
- Rider and driver auth screens/hooks share the same `shared/store/authStore.ts`; no user-visible copy or screen layout changes. A user with a skewed device clock may stay signed in where refresh previously failed; a legacy absolute timestamp behind the clock causes an immediate subsequent refresh.
- No ride state, payments, backend loops, or money paths are touched.

## 5. User-experience effect

- Riders and drivers may avoid an unnecessary refresh failure or sign-out when their device clock is ahead of the server. Driver background location can continue using the newly rotated credential.
- The change is visible mid-session only as continuity of an existing session; no copy or notification changes.
- rider-app and driver-app have no active automated visual regression tooling. This auth change has no intended visual effect; both app bundles were exported successfully.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | Prefer relative TTL, retain valid successor pair with zero legacy lifetime, guard overflow | Foreground shared refresh is used by rider and driver apps |
| `driver-app/utils/backgroundAuth.ts` | Apply the same TTL and legacy handling; persist normalized expiry | Headless driver location uses a separate refresh context |
| `driver-app/__tests__/store/authStore.refreshRace.test.ts` | Add ahead/behind clock, successor, legacy, overflow regressions | Pin foreground behavior and malformed-payload safety |
| `driver-app/__tests__/utils/backgroundAuth.test.ts` | Add ahead/behind clock, successor, legacy-Date, overflow regressions | Pin headless behavior and malformed-payload safety |
| `docs/change-log/2026-09-22-auth-refresh-clock-skew.md` | Record impact, risk, rollback, and verification | Required live-tested auth change log |

## 7. Before / after

```ts
// Before
const accessExpiresAtMs = Date.parse(access_expires_at);
if (!Number.isFinite(accessExpiresAtMs) || accessExpiresAtMs <= Date.now()) {
  throw new Error('Token refresh returned invalid credentials');
}
const expiresIn = (accessExpiresAtMs - Date.now()) / 1000;
```

```ts
// After
const expiresIn = validRelativeLifetime
  ?? Math.max(0, (accessExpiresAtMs - Date.now()) / 1000);
// Persist the valid rotated pair even if legacy expiry clamps to zero.
```

## 8. Rollback plan

No data migration or server-side state change is involved. Revert the four implementation/test commits and rebuild/release the affected app versions if a regression appears; already-rotated refresh tokens remain valid under the existing server rotation contract.

## 9. Verification performed

- [x] Automated tests: driver auth refresh race suite 20/20; driver background auth suite 18/18.
- [x] The clock-skew tests failed before their corresponding fixes; after the fixes both suites pass.
- [x] Shared foreground suite also passed using the rider app Jest configuration with the driver auth fixture explicitly included (20/20; Jest reports duplicate manual mocks because both app roots are included).
- [x] Android, iOS, and web Expo JS export completed for driver-app and rider-app after dependency installation/postinstall patches.
- [x] Blast-radius grep performed for shared auth-store imports and `renewBackgroundAuthToken` / `createBackgroundTokenProvider` consumers.
- [x] Reviewed the auth refresh and credential handling conventions in `CLAUDE.md` and `.claude/agents/spinr-security-auditor.md`; architect reviewed the final implementation diff with no blockers.
- [x] Feature flag not used: this repairs refresh expiry interpretation without introducing a new user-facing control or screen behavior.
- [ ] Manual staging repro not performed.
- [ ] Native iOS / Android binaries were not built. Expo JavaScript exports were run; no physical-device build or test was performed.
- [ ] No live backend/API call was made; API behavior was verified with mocked refresh payloads only.

## 10. Sign-off

- [x] Rollback is code-only and does not require data remediation.
- [x] Blast radius and directly importing consumers are stated above.
- [x] No screen, copy, or notification behavior changed.
