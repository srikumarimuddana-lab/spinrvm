# Change Impact Log — App Check for the public web driver-registration upload

**Date:** 2026-09-19

## Issue/gap identified
`admin-dashboard/src/app/register/driver/page.tsx` — the public, unauthenticated web driver self-registration flow (`/register/driver`, explicitly listed as a public path in `src/middleware.ts`) — uploads documents via a hand-rolled `fetch("/api/v1/upload", ...)` that never attaches an App Check header. With `FirebaseAppCheckMiddleware` enforcing in production, this 401s at the "Docs" step of registration, blocking web driver signups entirely.

## Root cause
Same class of bug as `shared/api/upload.ts` (already fixed for mobile in PR #5506, same session): a hand-rolled HTTP request bypassing the App Check attestation every other request path already carries. Discovered by a `spinr-security-auditor` review of the mobile fix, which flagged this as a third, still-unfixed instance while confirming the mobile diff itself was clean. Distinct fix shape from mobile: web App Check needs a reCAPTCHA v3 attestation provider (Play Integrity/DeviceCheck don't apply to browsers), which didn't exist anywhere in `admin-dashboard` before this change — no prior Firebase SDK usage of any kind in this app.

## Fix/remediation
1. Added the `firebase` npm package (v12.19.0) to `admin-dashboard`.
2. New `admin-dashboard/src/lib/firebase-app-check.ts`: lazily initializes a Firebase app + App Check instance (`ReCaptchaV3Provider`) from `NEXT_PUBLIC_FIREBASE_*`/`NEXT_PUBLIC_RECAPTCHA_SITE_KEY` env vars, exposing an async `appCheckHeader()` that mirrors `shared/api/client.ts`'s mobile equivalent exactly: fails open (`{}`) whenever config is missing or a token can't be fetched, never blocks the request client-side.
3. `register/driver/page.tsx`'s `uploadFile()` now spreads `await appCheckHeader()` into the upload request's headers alongside the existing `Authorization` header.
4. `.env.example` documents the five new (all public, client-embedded) env vars and the exact Firebase Console steps to obtain them.

**Alternative considered and rejected:** exempt `/api/v1/upload` from `FirebaseAppCheckMiddleware` for this specific traffic. Rejected because that endpoint is shared with the mobile apps' now-fixed upload path — exempting it by prefix would undo the mobile fix's protection for everyone, not just this page.

## Risk & impact on existing functionality
Isolated to this one page and the new `firebase-app-check.ts` module — grepped `admin-dashboard` for any other caller of `firebase`/`appCheckHeader`; none exist yet. `npm install firebase` added zero new vulnerabilities: `npm audit` after install shows the same 3 pre-existing findings (`maplibre-gl` critical, `hono` moderate, `joi` low) that were already in `package-lock.json` before this change — confirmed via `git diff package-lock.json`, none of those three packages appear in the diff's added lines. Until the five env vars are actually set (Firebase Console work still pending — see "What was NOT verified"), `appCheckHeader()` returns `{}` and this page's behavior is byte-for-byte unchanged from before the fix — additive and inert until configured, not a behavior change on its own.

## User experience effect
Public, driver-facing (web signup flow only — no rider/admin/corporate surface touched). No visible change until the Firebase Console values are set; once set, this repairs a currently-broken step (document upload) in an otherwise-working multi-step form, rather than changing an already-working flow's behavior.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/package.json` / `package-lock.json` | Added `firebase` dependency | Needed for the web App Check SDK |
| `admin-dashboard/src/lib/firebase-app-check.ts` | New file — `appCheckHeader()` | The actual fix: mints and returns the App Check header, fail-open |
| `admin-dashboard/src/app/register/driver/page.tsx` | `uploadFile()` now attaches `await appCheckHeader()` | Wires the fix into the one broken upload call |
| `admin-dashboard/src/lib/__tests__/firebase-app-check.test.ts` | New regression tests (5 cases) | Pins fail-open behavior, token attachment, and app-instance reuse |
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
- `npx vitest run src/lib/__tests__/firebase-app-check.test.ts`: 5/5 passed.
- `npx vitest run` (full admin-dashboard suite): 80/80 suites, 739/739 tests passed.
- `npx tsc --noEmit`: clean.
- `npx eslint` on all three touched/new source files: 0 errors.
- **Real production build**: `npm run build` — exit code 0, "✓ Compiled successfully", `/register/driver` present in the route output. Not just a dev-server or `tsc --noEmit` check, per this repo's explicit requirement for admin-dashboard changes.
- `npm audit` diff-checked against `package-lock.json`'s git diff to confirm the `firebase` install introduced zero new vulnerabilities.

## What was NOT verified
- **The actual Firebase Console setup (registering a Web app + enabling App Check with reCAPTCHA v3) has not been done yet** — this PR ships the code path fully wired but inert (fails open) until a human completes that Console work and sets the five env vars in Vercel. Until then, this page's behavior is unchanged from before this fix (still missing the header) — it does not resolve the live 401 by itself.
- Not tested against a real reCAPTCHA challenge or real browser bot-detection behavior — the test suite mocks the entire `firebase/app-check` module; no live integration test exists for this page in this repo.
- No visual regression coverage exists or was run for `/register/driver` specifically (not one of the 6 seeded admin-dashboard visual-regression pages) — this change has no visible UI difference regardless (header-only), so this was reasoned about, not screenshotted.
