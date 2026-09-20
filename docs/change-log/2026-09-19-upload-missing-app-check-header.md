# Change Impact Log — Document uploads missing the App Check header

**Date:** 2026-09-19

## Issue/gap identified
Document uploads (driver license/vehicle registration/vehicle inspection re-uploads, and the driver-signup document upload flow in both apps) fail with `401 {"detail":"App Check token required"}` once `APP_CHECK_ENFORCEMENT` is on in production, while every other API call in the app works fine.

## Root cause
`shared/api/upload.ts`'s `uploadFile()`/`postMultipart()` sends the request via a hand-rolled `XMLHttpRequest` (deliberately — a documented workaround for an Expo SDK 54+ bug where `fetch()` rejects React Native's `{uri, name, type}` FormData part) instead of the shared `client` object in `shared/api/client.ts`. Every method on `client` (`get`/`post`/`put`/`patch`/`delete`) already attaches `X-Firebase-AppCheck` via an internal `appCheckHeader()` helper; the upload path never called it, so the header was silently never sent. This is a second, previously-unfixed instance of an anti-pattern `client.ts` already had a code comment warning about (citing an earlier, already-fixed instance in `report-safety.tsx`). It went unnoticed because `APP_CHECK_ENFORCEMENT` was off in production until this same debugging session re-enabled it.

## Fix/remediation
1. `shared/api/client.ts`: exported the existing `appCheckHeader()` helper (was module-private) — no behavior change to any existing caller.
2. `shared/api/upload.ts`: `doUpload()` now spreads `await appCheckHeader()` into the headers object passed to `postMultipart()`, alongside the existing `Authorization` header — mirroring exactly how `client.ts`'s own methods build their headers.

**Alternative considered and rejected:** migrating `postMultipart()` to use the shared `client` object (which uses `fetch()`) instead of raw XHR. Rejected because `upload.ts`'s own docstring documents that XHR is required to work around the Expo SDK 54+ FormData/`fetch()` incompatibility — switching back would re-break uploads a different way. Adding just the missing header is the minimal fix that doesn't touch the (working, necessary) transport mechanism.

## Risk & impact on existing functionality
`shared/api/upload.ts`'s `uploadFile`/`postMultipart` are used by (confirmed via repo-wide grep): `driver-app/app/documents.tsx`, `rider-app/app/become-driver.tsx`, `driver-app/app/become-driver.tsx`. All three get this fix simultaneously since it's centralized in `shared/`. `frontend/api/upload.ts` has its own separate copy but `frontend/` is explicitly deprecated dead code per `CLAUDE.md` and was not touched. `admin-dashboard` has an unrelated, separate upload implementation (Next.js web, not this RN module) — not affected. The change is purely additive (spreads one more header key into an object that's always been spread the same way for other headers); nothing depends on the header's prior absence, since its absence was the bug, not intended behavior.

## User experience effect
Rider/driver-facing. Before this fix, once App Check enforcement is on, a driver could not re-upload license/registration/inspection documents (silently blocked with a "401 App Check token required" error surfaced as "Upload Failed" in the UI), and a rider or driver going through the driver-signup flow could not submit their onboarding documents either. Not a mid-session-visible behavior change to an already-working flow — it repairs a flow that was actively broken the moment enforcement was turned on.

## Files modified
| File | What changed | Why |
|---|---|---|
| `shared/api/client.ts` | `appCheckHeader()` changed from module-private to exported | So `upload.ts` (and any future hand-rolled request code) can reuse the same App Check token logic instead of re-implementing or omitting it |
| `shared/api/upload.ts` | `doUpload()`'s headers object now includes `await appCheckHeader()` | The actual fix — attaches the missing `X-Firebase-AppCheck` header to upload requests |
| `driver-app/__tests__/lib/uploadTransport.test.ts` | Added `appCheckHeader` to the `shared/api/client` mock; added two new tests (header attached when a token is available, header omitted when it isn't) | Existing tests would otherwise break on the new import; new tests pin this exact fix as a regression guard, matching this file's own stated purpose of pinning transport-layer bugs |

## Before/after snippet
```diff
  const doUpload = async (token: string | null): Promise<MultipartResponse> =>
      postMultipart(
          `${SpinrConfig.backendUrl}/api/v1/upload`,
          buildFormData(),
-         token ? { Authorization: `Bearer ${token}` } : {},
+         {
+             ...(token ? { Authorization: `Bearer ${token}` } : {}),
+             ...(await appCheckHeader()),
+         },
      );
```

## Rollback plan
`git revert` is a complete rollback here — this only adds an HTTP header to outgoing requests; no data was written, no migration, no config/secret changed. Reverting restores the pre-fix (broken-under-enforcement) behavior with no cleanup needed.

## Verification performed
- `npx jest __tests__/lib/uploadTransport.test.ts __tests__/lib/uploadMimeType.test.ts` (driver-app): 21/21 passed, including the 2 new regression tests.
- `npx jest __tests__/app/documentsScreen.test.tsx` (driver-app): 15/15 passed.
- `npx jest __tests__/app/becomeDriverScreen.test.tsx` (driver-app): 42/42 passed.
- `npx jest __tests__/becomeDriverScreen.test.tsx` (rider-app): 15/15 passed.
- `npx tsc --noEmit` clean on both `driver-app` and `rider-app`.
- Ran each apps' full test suite; driver-app showed 35 failing suites and rider-app showed 1 (46 tests) — confirmed via `git stash`/re-run against unmodified `main` that these are pre-existing sandbox environment issues (a `react-test-renderer` peer-dependency version mismatch, and an unrelated `act()`/React 19 test-renderer issue in `rideInProgressScreen.test.tsx`), reproducing identically with or without this diff. None of the failing suites touch upload, client, documents, or become-driver code.
- This is a `driver-app`/`rider-app` change (both in the `shared/` package they both consume) but not `admin-dashboard`, so the "real production build" requirement in `CLAUDE.md`'s release gates (npm run build for admin-dashboard/rider-app/driver-app changes) — I did not run a full EAS/production native build; `tsc --noEmit` + the full JS test suite is what's feasible in this environment, consistent with this repo's own documented limitation on native build verification in this environment (see the PR template's native-build note).

## What was NOT verified
- Not tested against a real device — the actual bug report came from a real device, but I don't have one attached to this session. The fix is verified via unit tests against a mocked XHR and `appCheckHeader()`, not an end-to-end real upload against production. The user who reported the original bug is best positioned to confirm the fix live once this merges and ships.
- Not verified against `frontend/api/upload.ts` (the deprecated app's separate copy of this same code) — deliberately out of scope per `CLAUDE.md`'s explicit instruction not to fix code in `frontend/`.
- No real native build (EAS) was triggered for either app; this environment cannot authenticate `eas-cli` per this repo's own documented limitation.
