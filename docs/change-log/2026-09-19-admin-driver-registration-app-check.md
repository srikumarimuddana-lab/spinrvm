# Change Impact Log — App Check for the public web driver-registration upload

**Date:** 2026-09-19

## Issue/gap identified
`admin-dashboard/src/app/register/driver/page.tsx` — the public, unauthenticated web driver self-registration flow (`/register/driver`, explicitly listed as a public path in `src/middleware.ts`) — uploads documents via a hand-rolled `fetch("/api/v1/upload", ...)` that never attaches an App Check header. With `FirebaseAppCheckMiddleware` enforcing in production, this 401s at the "Docs" step of registration, blocking web driver signups entirely.

## Root cause
Same class of bug as `shared/api/upload.ts` (already fixed for mobile in PR #5506, same session): a hand-rolled HTTP request bypassing the App Check attestation every other request path already carries. Discovered by a `spinr-security-auditor` review of the mobile fix, which flagged this as a third, still-unfixed instance while confirming the mobile diff itself was clean. Distinct fix shape from mobile: web App Check needs a reCAPTCHA v3 attestation provider (Play Integrity/DeviceCheck don't apply to browsers), which didn't exist anywhere in `admin-dashboard` before this change — no prior Firebase SDK usage of any kind in this app.

## Fix/remediation
1. Added the `firebase` npm package (v12.19.0) to `admin-dashboard`.
2. New `admin-dashboard/src/lib/firebase-app-check.ts`: lazily initializes a Firebase app + App Check instance (`ReCaptchaV3Provider`) from `NEXT_PUBLIC_FIREBASE_*`/`NEXT_PUBLIC_RECAPTCHA_SITE_KEY` env vars, exposing an async `appCheckHeader()` that mirrors `shared/api/client.ts`'s mobile equivalent exactly: fails open (`{}`) whenever config is missing or a token can't be fetched, never blocks the request client-side. `getToken()` is wrapped in a 4s timeout (`withTimeout()`) that also fails open — added after a `spinr-security-auditor` review flagged that an unbounded `getToken()` call could otherwise hang a document upload indefinitely on a stalled reCAPTCHA network round-trip.
3. `register/driver/page.tsx`'s upload header construction was extracted into a standalone, exported `buildUploadHeaders(token)` function that spreads `await appCheckHeader()` alongside the existing `Authorization` header — same review flagged the original inline version had no test proving the two headers merge correctly; the extraction makes that mergeable/testable without rendering the full multi-step wizard.
4. `admin-dashboard/src/middleware.ts`'s CSP gained a `frame-src https://www.google.com https://recaptcha.google.com` directive. **This was a blocker found by the same review**: reCAPTCHA v3 runs its attestation in an invisible iframe from those origins, and this app's CSP had no `frame-src` at all, so it fell back to `default-src 'self'` — which silently blocks the iframe. Without this, the whole fix above would still 401 every upload once the Firebase Console setup is done, with no CSP-violation telemetry to explain why.
5. `.env.example` documents the five new (all public, client-embedded) env vars and the exact Firebase Console steps to obtain them.
6. **Provider swap: reCAPTCHA v3 → reCAPTCHA Enterprise.** While completing the Firebase Console setup (a separate follow-up, not part of this code review), the user found Firebase Console's App Check screen has classic reCAPTCHA's registration field disabled/uneditable — Firebase has stopped accepting new classic-reCAPTCHA App Check registrations for this project, even though the classic key pair itself still mints fine on Google's reCAPTCHA admin console. Enterprise is the only provider Firebase will register. `firebase-app-check.ts` now uses `ReCaptchaEnterpriseProvider` instead of `ReCaptchaV3Provider` — confirmed via the installed SDK's type definitions (`node_modules/@firebase/app-check/dist/src/providers.d.ts`) that both take an identical `(siteKey: string)` constructor, so this was a drop-in swap with no other code shape changes. Enterprise needs no separate secret key wired into Firebase (unlike classic v3, which required one) — it verifies tokens via the reCAPTCHA Enterprise API using the same GCP project's own credentials.

**Alternative considered and rejected:** exempt `/api/v1/upload` from `FirebaseAppCheckMiddleware` for this specific traffic. Rejected because that endpoint is shared with the mobile apps' now-fixed upload path — exempting it by prefix would undo the mobile fix's protection for everyone, not just this page.

**Note on `package-lock.json`:** the diff also touches `@spinr/shared`'s recorded `@types/node`/`@types/react` dev-dependency versions (`^26.4.1`→`^26.5.1`, `^19.0.0`→`^19.3.0`). Investigated and confirmed **not** scope creep: `shared/package.json` on `main` already declares `^26.5.1`/`^19.3.0` — the lockfile on `main` was already stale against that manifest before this PR touched anything. Running `npm install firebase` made npm reconcile the lockfile to the versions the manifest already committed to; no new package.json range was introduced by this change. Confirmed via `git diff origin/main...HEAD -- shared/package.json` (no diff) and reading both versions of `admin-dashboard/package-lock.json`'s recorded range for the `@spinr/shared` entry directly.

## Risk & impact on existing functionality
Isolated to this one page and the new `firebase-app-check.ts` module — grepped `admin-dashboard` for any other caller of `firebase`/`appCheckHeader`; none exist yet. `npm install firebase` added zero new vulnerabilities: `npm audit` after install shows the same 3 pre-existing findings (`maplibre-gl` critical, `hono` moderate, `joi` low) that were already in `package-lock.json` before this change — confirmed via `git diff package-lock.json`, none of those three packages appear in the diff's added lines. Until the five env vars are actually set (Firebase Console work still pending — see "What was NOT verified"), `appCheckHeader()` returns `{}` and this page's behavior is byte-for-byte unchanged from before the fix — additive and inert until configured, not a behavior change on its own.

The `frame-src` CSP addition is scoped to two specific Google origins, additive to every other directive `buildCsp()` already sets, and applies to every admin-dashboard page (the middleware builds one CSP for the whole non-tracking domain) — grepped `middleware.ts` for existing `frame-src`/iframe usage first; there was none, so this cannot narrow or conflict with an existing directive. No other page in this app uses an iframe today, so this only newly *permits* iframes from these two origins; it does not change behavior for any page besides `/register/driver`. Verified the same two origins (`https://www.google.com`, `https://recaptcha.google.com`) cover reCAPTCHA Enterprise's iframe requirement too, not just classic v3 — Google's own documented CSP guidance for both providers scopes `frame-src` to `https://www.google.com/recaptcha/` and `https://recaptcha.google.com/recaptcha/`; the bare origins already in this CSP (no path restriction) are a superset of that, so no CSP change was needed for the provider swap.

Confirmed the `firebase` package addition has zero blast radius on `rider-app`/`driver-app`: this repo has no root-level npm/yarn workspaces (`package.json` at the repo root declares no `workspaces` field), and `rider-app`/`driver-app` each manage their own dependencies via `yarn.lock`, entirely separate from `admin-dashboard/package-lock.json`. There is no hoisting mechanism connecting them, so this change cannot affect either mobile app's install or build.

## User experience effect
Public, driver-facing (web signup flow only — no rider/admin/corporate surface touched). No visible change until the Firebase Console values are set; once set, this repairs a currently-broken step (document upload) in an otherwise-working multi-step form, rather than changing an already-working flow's behavior.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/package.json` / `package-lock.json` | Added `firebase` dependency | Needed for the web App Check SDK |
| `admin-dashboard/src/lib/firebase-app-check.ts` | New file — `appCheckHeader()`, `getToken()` wrapped in a 4s timeout, uses `ReCaptchaEnterpriseProvider` (swapped from `ReCaptchaV3Provider` — see "Fix/remediation" item 6) | The actual fix: mints and returns the App Check header, fail-open; timeout prevents an indefinite hang on a stalled reCAPTCHA round-trip; Enterprise is the only provider Firebase Console still registers |
| `admin-dashboard/src/app/register/driver/page.tsx` | Upload header logic extracted into exported `buildUploadHeaders(token)`; `uploadFile()` now calls it | Wires the fix into the one broken upload call, in a form that's testable without rendering the full wizard |
| `admin-dashboard/src/middleware.ts` | Added `frame-src https://www.google.com https://recaptcha.google.com` to `buildCsp()` | Blocker fix: without it, the CSP's `default-src 'self'` fallback silently blocks reCAPTCHA's required iframe and App Check can never mint a token |
| `admin-dashboard/src/lib/__tests__/firebase-app-check.test.ts` | Regression tests (6 cases, was 5) | Pins fail-open behavior, token attachment, app-instance reuse, and the new getToken() timeout |
| `admin-dashboard/src/app/register/driver/page.test.ts` | New file — 4 regression tests for `buildUploadHeaders()` | Pins that Authorization and X-Firebase-AppCheck merge correctly and neither clobbers the other |
| `admin-dashboard/.env.example` | Documented 5 new `NEXT_PUBLIC_*` vars + setup steps | So the Firebase Console values have a documented home once obtained |

## Before/after snippet
```diff
  const res = await fetch("/api/v1/upload", {
      method: "POST",
      body: data,
-     headers: token ? {
-         'Authorization': `Bearer ${token}`,
-     } : {},
      // Note: Content-Type header is auto-set by browser with boundary for FormData
+     headers: {
+         ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
+         ...(await appCheckHeader()),
+     },
  });
```

## Rollback plan
`git revert` is a full rollback — additive-only change, no data written, no migration. If the reCAPTCHA setup itself needs backing out after being configured, unset the five `NEXT_PUBLIC_FIREBASE_*`/`NEXT_PUBLIC_RECAPTCHA_SITE_KEY` env vars in Vercel and redeploy; `appCheckHeader()` immediately reverts to its fail-open `{}` behavior with no code change needed.

## Verification performed
- `spinr-security-auditor` review of this diff — found one BLOCKER (missing CSP `frame-src`) and several warnings; all addressed below, then re-verified.
- `npx vitest run src/lib/__tests__/firebase-app-check.test.ts src/app/register/driver/page.test.ts`: 10/10 passed.
- `npx vitest run` (full admin-dashboard suite, post-fix): 81/81 suites, 744/744 tests passed (was 80/739 pre-fix; +1 suite, +5 tests from this round).
- `npx tsc --noEmit`: clean.
- `npx eslint` on all touched/new source files (`middleware.ts`, `firebase-app-check.ts`, `page.tsx`, both test files): 0 errors.
- **Real production build**: `npm run build` — exit code 0, "✓ Compiled successfully", `/register/driver` present in the route output. Re-run after the CSP fix, not just before it, and again after the Enterprise provider swap. Not just a dev-server or `tsc --noEmit` check, per this repo's explicit requirement for admin-dashboard changes.
- `npm audit` diff-checked against `package-lock.json`'s git diff to confirm the `firebase` install introduced zero new vulnerabilities.
- CSP `frame-src` fix: read `middleware.ts`'s full `buildCsp()` directly to confirm the gap before fixing (did not take the review's claim on faith), confirmed no existing `frame-src` directive would be narrowed or conflicted with.
- Lockfile devDependency question: resolved by direct comparison of `shared/package.json` across `main` and this branch (no diff) and reading the recorded range in both versions of `package-lock.json` — see "Fix/remediation" note above.
- reCAPTCHA Enterprise provider swap: verified the constructor signature via the installed SDK's own `.d.ts` file before writing the code (`new ReCaptchaEnterpriseProvider(siteKey: string)`, identical shape to `ReCaptchaV3Provider`) rather than assuming; verified via web search that Google's documented CSP requirement is identical between classic v3 and Enterprise, so the already-fixed `frame-src` directive needed no further change; re-ran the full test suite, typecheck, lint, and production build after the swap — all clean (results above already reflect the post-swap state).

## What was NOT verified
- **The actual Firebase Console setup (registering a Web app + enabling App Check) is in progress but not complete as of this writing** — the user has registered the Web app and hit the classic-reCAPTCHA-disabled wall documented in "Fix/remediation" item 6, which is what triggered the Enterprise provider swap. This PR ships the code path fully wired but inert (fails open) until the Enterprise key registration and the five env vars in Vercel are both finished. Until then, this page's behavior is unchanged from before this fix (still missing the header) — it does not resolve the live 401 by itself.
- The reCAPTCHA Enterprise integration has not been exercised end-to-end against a live Enterprise key yet (none was available at the time of this code change) — only unit-tested against a mocked `firebase/app-check` module. Once the user finishes Console setup and env vars are set, a real-device/browser smoke test of the `/register/driver` upload step is still needed to confirm the live integration actually mints a valid token.
- Not tested against a real reCAPTCHA challenge or real browser bot-detection behavior — the test suite mocks the entire `firebase/app-check` module; no live integration test exists for this page in this repo.
- No visual regression coverage exists or was run for `/register/driver` specifically (not one of the 6 seeded admin-dashboard visual-regression pages) — this change has no visible UI difference regardless (header-only), so this was reasoned about, not screenshotted.
