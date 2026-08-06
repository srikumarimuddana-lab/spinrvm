# Change Impact & Risk Log — LogRocket was capturing credentials and PII in network bodies

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | `shared/`, driver-app, rider-app |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | PIPEDA audit of PII-in-logs; mitigation choice made by product owner |

## 1. Issue / gap identified

The audit that prompted this began as a narrow question — `shared/utils/pii.ts`
provides `redactPhone` / `redactEmail` / `redactCoords` and **nothing in production
calls them**, so either the app does not log those values or it logs them raw.

**The answer to that question is good news.** Console and server logging hygiene is
genuinely solid:

| Check | Result |
|---|---|
| `console.*` with coordinates, client-wide | 1 hit, and it logs `{ ride_id }` only (`useDriverDashboard.ts:1592`) |
| `logger.*` with coordinates or addresses, backend | none |
| `console.*` with phone / email / names | 1 hit, a dev script, already using `redactEmail` |
| Sentry identity | `setUser` sends `{ id }` only — with a "PIPEDA: id only" docstring and a test asserting a passed-in email is dropped |
| `LogRocket.identify` | id + role only |

The helpers are unused because the codebase avoids logging PII in the first place.

**The exposure was in a channel nobody was auditing: LogRocket session replay.**
`LogRocket.init('gfuign/spinr')` was called with **no options** in both apps, and
the SDK captures request *and response bodies* by default.

## 2. Root cause

Verified in the installed SDK (`@logrocket/react-native@2.3.3`), not assumed:

| Fact | Evidence |
|---|---|
| Network capture defaults to on | `dist/build.js` → `isEnabled:e=!0` |
| The JS interceptor is active with no options | `dist/build.js` init path (below) |
| It instruments `fetch` / `XmlHttpRequest` | vendor `README.md:54` |
| Sanitization is opt-in | `README.md:55`, `types.d.ts:37-40` |
| No sanitizers were passed | driver `_layout.tsx:352`, rider `_layout.tsx:277` |
| Enabled by default on iOS, both apps | identical `LOGROCKET_ENABLED = … : Platform.OS === 'ios'` |
| Our client uses `fetch` | `shared/api/client.ts` |

The decisive line from the SDK's own init path:

```js
isDisabled: false === options?.network?.isEnabled
         || ("ios" === Platform.OS && "native" === options?.network?.iosNetworkCaptureMode)
```

With no options object both terms evaluate false, so the interceptor runs. The same
line establishes the second thing that mattered: `iosNetworkCaptureMode` is not
`'native'`, so JS sanitizers **are** honoured — had it defaulted the other way, the
SDK would bypass them and this fix would have been useless.

Consequently, on iOS sessions the following were shipped whole to a US vendor:

- `POST /auth/verify-otp` — phone number and OTP code in; **access token, refresh
  token** and full profile (name, email, phone) out
- `POST /auth/refresh` — both tokens out
- `GET /auth/me` — name, email, phone
- ride booking — pickup/dropoff addresses and coordinates
- `POST /drivers/location-batch` — raw GPS traces
- `Authorization: Bearer …` on every authenticated request

CLAUDE.md's PIPEDA section forbids raw GPS, full phone numbers, full names, email
addresses and exact addresses in "logs, Sentry events, or analytics payloads";
session replay is squarely in that category. The bearer/refresh tokens are a
credential-exposure problem on top of the privacy one.

## 3. Fix / remediation

New `shared/services/logrocketSanitizer.ts`, passed as `{ network: … }` to both
`init` calls:

- **Bodies: default-deny.** Every request and response body is replaced with
  `[body redacted client-side — Spinr PIPEDA policy]`. A denylist of "sensitive
  endpoints" was considered and rejected — it fails open the moment someone adds an
  endpoint, which is exactly how this class of bug recurs. A test asserts a
  brand-new endpoint's body is redacted, to pin the policy rather than the list.
- **Credential headers redacted:** `authorization`, `cookie`, `set-cookie`,
  `x-csrf-token`, `x-firebase-appcheck`, `x-auth-token`, `refresh-token`, matched
  case-insensitively.
- **Sensitive query parameters masked** while keeping path and parameter names —
  `lat`, `lng`, `address`, `origin`, `destination`, `input`, `query`, `q`, `phone`,
  `email`, `token`. `input`/`query` matter because places-autocomplete puts
  **user-typed addresses** in the query string.
- **The envelope survives.** URL, method, status and non-credential headers are
  kept, so "the `/rides/estimate` call 500'd" is still debuggable. That was the
  point of choosing sanitizers over disabling network capture outright.
- One shared config object (`logRocketNetworkConfig`) so the two apps cannot drift.

URL parsing is deliberately string-based rather than using `URL`: React Native's
polyfill has historically been incomplete, and a throw inside a sanitizer is worse
than a crude parse — the SDK would fall back to the unsanitised value.

## 4. Risk & impact on existing functionality

**Blast radius: the two `init` call sites and one new shared module.** No API, no
backend, no data model.

- **Nothing in the apps consumes LogRocket data**, so no app behaviour depends on
  what is captured. The only consumer is the LogRocket dashboard.
- **The sanitizers are pure functions** with no imports and no native dependencies,
  so importing the module is safe in Expo Go and on web where LogRocket is not
  loaded at all.
- **They never return `null`.** Returning null tells the SDK to drop the record
  entirely; keeping the envelope is more useful and is asserted by a test.
- **Debugging capability is reduced.** Anyone who was reading request/response
  bodies in LogRocket loses that. That is the accepted cost of the chosen option,
  and it is the whole point of the change.
- **Android is unaffected** because LogRocket is disabled there
  (`LOGROCKET_ENABLED` defaults to iOS-only, due to an unrelated Android 16
  hidden-API crash). The exposure window was iOS builds only.
- **`EXPO_PUBLIC_ENABLE_LOGROCKET=true` on Android** would now also get sanitized
  capture — strictly better than before.

Not touched: ride state machine, money/wallet arithmetic, backend background loops,
RLS, migrations, WebSocket events, dispatch, auth logic itself.

## 5. User-experience effect

- **Rider / driver:** no visible change. No UI, copy, or notification difference.
  What changes is what leaves their device.
- **Internal:** anyone using LogRocket to inspect request or response payloads will
  now see a redaction marker instead. Deliberate, and the marker is explicit rather
  than an empty body so nobody mistakes it for "the request had no payload".
- **Visible mid-session:** no.
- **Compliance:** removes credentials and PII from a third-party session-replay
  feed. It does **not** by itself make the LogRocket disclosure accurate — see §9.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/services/logrocketSanitizer.ts` | New — `sanitizeRequest` / `sanitizeResponse` / `sanitizeHeaders` / `sanitizeUrl` and the shared `logRocketNetworkConfig`, with the SDK evidence recorded in the module docstring | Bodies and credentials must not reach the vendor; one definition so both apps match |
| `shared/services/__tests__/logrocketSanitizer.test.ts` | New — 17 cases using realistic Spinr payloads (OTP verify, `/auth/me`, location batch, places autocomplete) | A regression should read as "the phone number is visible", not as a shape mismatch |
| `driver-app/app/_layout.tsx` | `init` now passes `{ network: logRocketNetworkConfig }`; comment stating this is required, not optional hardening | The call site is where the mistake was made |
| `rider-app/app/_layout.tsx` | Same | Same |

**Natural commit boundaries:** (1) the shared module + its tests, (2) wiring both
apps. Splitting that way keeps the reviewable unit small; the second commit is where
behaviour changes.

## 7. Before / after

```tsx
// Before — both apps
LogRocket.init('gfuign/spinr');
// → JS network interceptor active, no sanitizers:
//   Authorization: Bearer …            captured
//   {"phone":"+13065551234","otp":"481920"}   captured
//   {"token":"…","refresh_token":"…"}         captured
```

```tsx
// After — both apps
LogRocket.init('gfuign/spinr', { network: logRocketNetworkConfig });
// → Authorization: "[redacted]"
//   body: "[body redacted client-side — Spinr PIPEDA policy]"
//   /drivers/nearby?lat=[redacted]&lng=[redacted]&radius=5
```

## 8. Rollback plan

Revert the `{ network: logRocketNetworkConfig }` argument in both `_layout.tsx`
files, then ship an app build. Client change, so **not instant** — OTA or EAS.

Nothing is written to any database; no migration, no `app_settings` value. Note the
asymmetry: rolling this back **restores the exposure**, so a revert should only ever
be a deliberate decision, not a reflex if something unrelated looks wrong. The
`EXPO_PUBLIC_ENABLE_LOGROCKET=false` env override is the safer emergency lever — it
disables LogRocket entirely at build time without touching this code.

**Not feature-flagged** (gate #3): flagging "should we redact credentials before
sending them to a third party?" is not a meaningful choice, and a flag read is an
authenticated HTTP call whose own request would be the thing needing redaction.

## 9. Verification performed

- [x] **Root cause established from the installed SDK**, not from vendor docs or
      memory: the `isDisabled` expression in `dist/build.js`, the `isEnabled:e=!0`
      defaults, the `IOptions.network` typings, and `README.md:54-55`.
- [x] **`iosNetworkCaptureMode` confirmed non-`native` by default**, which is what
      makes JS sanitizers effective. Had this gone the other way the fix would have
      been silently useless — checked before writing any code.
- [x] **`jest ../shared/services/__tests__/logrocketSanitizer.test.ts`** →
      **17/17 passed**. Cases assert the OTP body, the token pair, the `/auth/me`
      profile and a GPS batch are all unreadable, that a *new* endpoint's body is
      redacted (pinning default-deny), that the envelope survives, and that
      malformed query strings do not throw.
- [x] **Full rider-app + shared suite** — `yarn test --ci --coverage` →
      **60 suites, 547 tests passed**, coverage thresholds met (was 59/530 before
      this change).
- [x] **Full driver-app suite** — **46/46 suites, 346/346 tests passed**, fully
      green (the `onlineResync` CRLF fix earlier in this session cleared the last
      red one).
- [x] **`npx tsc --noEmit`** — driver-app exit 0, rider-app exit 0.
- [x] **Blast-radius grep performed** — `LogRocket` across all app/shared `.ts`/
      `.tsx` (init and identify call sites, gating constants); `setUser` call sites;
      `console.*` with coordinate / phone / email / name patterns across all four
      surfaces; `logger.*` equivalents across `backend/**/*.py`;
      `redactCoords|redactPhone|redactEmail|utils/pii` importers.
- [x] **Reviewed against `CLAUDE.md` conventions** — this change exists to enforce
      the PIPEDA "never in logs/analytics" list. No money, state machine, RLS, or
      migration.
- [x] **Escalated before acting** (gate #9): the mitigation choice — sanitize vs.
      disable network capture vs. disable LogRocket — was put to the product owner
      rather than decided unilaterally, because the options differ materially in
      debugging cost.

### What was NOT verified

- **No LogRocket payload was ever observed.** The exposure is established by
  inference — SDK defaults plus absent sanitizers plus our client using the
  instrumented API — not by looking at a captured session. **The remaining proof is
  to open a LogRocket session from an iOS build and confirm bodies show the
  redaction marker and `Authorization` shows `[redacted]`.** Until someone does
  that, both the severity claim and the fix are unconfirmed against the live vendor.
- **`textSanitizer` is left at its default, and this is arguably the LARGER
  remaining exposure.** Session replay records the UI itself, so addresses, names
  and phone numbers *visible on screen* are still captured as replay content —
  entirely separate from the network channel this commit closed. Setting
  `textSanitizer: 'excluded'` would redact it at the cost of replay legibility.
  That is a product/legal trade-off, not an engineering one, and is filed rather
  than decided here.
- **Console capture is also on by default** (`IOptions.console.isEnabled`). Our
  console hygiene is good (§1), so the risk is low, but it is another unsanitised
  channel that was not configured and not changed here.
- **The disclosure is still wrong and still pending.** `docs/legal/privacy-policy.md:62`
  says LogRocket is used "on our driver app" when the code enables it on both;
  the product owner's decision is that the **policy** should be corrected to cover
  both. The policy is also an unpublished draft whose own sign-off list (line 137)
  states that publishing it is what closes the "undisclosed-LogRocket gap", so
  LogRocket remains formally undisclosed until then. Filed; no doc edited here.
- **No subprocessor register file exists** in the repo despite
  `subprocessor-audit.yml` and `subprocessor-monitor.yml` workflows referencing that
  concept. Not investigated.
- **No native build.** `expo export --platform web` was not run for this commit
  because LogRocket is never loaded on web (`Platform.OS !== 'web'` gate), so a web
  bundle would not exercise the changed path at all. A real iOS build is the
  meaningful check and needs `[build]` in the commit message.
- **Retention at the vendor.** Data already captured before this fix is still in
  LogRocket. If that matters — and for tokens and OTPs it plausibly does — deleting
  historical sessions is a vendor-side action nobody has taken.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and explicitly notes that reverting
      restores the exposure, with the env override named as the safer lever
- [x] Blast radius is stated, not assumed — every claim about SDK behaviour traced
      to a line in the installed package, and the "no consumer depends on this" point
      established rather than asserted
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 records the loss of payload visibility for internal users, and
      §9 states plainly that the larger on-screen-text exposure is still open
