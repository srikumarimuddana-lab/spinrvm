# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | agent |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | Driver-approval reviewer shows a blank viewer for PDF documents |

## 1. Issue / gap identified

In **Drivers → Approvals**, opening a driver's documents for review showed nothing for PDF uploads. The browser console showed: `Loading plugin data from 'https://<project>.supabase.co/storage/v1/object/sign/driver-documents/<id>.pdf?token=…' violates the following Content Security Policy directive: "default-src 'self'". Note that 'object-src' was not explicitly set`.

## 2. Root cause

`document-reviewer.tsx` renders PDFs with `<embed src={signed Supabase URL} type="application/pdf">`. CSP governs `<embed>` with `object-src`. `buildCsp()` in `src/middleware.ts` never set `object-src`, so it fell back to `default-src 'self'` and blocked the cross-origin Supabase storage URL. Images are unaffected: `img-src` already allows `https:`.

A HEAD request to the signed URL confirmed Supabase returns `Content-Type: application/pdf` with no `X-Frame-Options`, CSP, or `Content-Disposition`, so the CSP was the only blocker.

## 3. Fix / remediation

Added `object-src 'self' https://*.supabase.co` to `buildCsp()`. `'self'` keeps the previous effective value for same-origin content.

Alternatives considered:
- Exact project host only. Rejected: it hardcodes the production project ref, and the admin dashboard has no Supabase-URL env var, so staging's project would stay blocked.
- Replace the inline `<embed>` with an "Open PDF" link. Rejected by the user: reviewers would leave the page for every PDF.

## 4. Risk & impact on existing functionality

- **Blast radius:** `buildCsp()` is the CSP for every admin-dashboard and company-portal page (`passThroughWithNonce`). The tracking host uses its own `TRACK_CSP` and is untouched.
- The change only affects `<object>`/`<embed>` loads. The one such element in `src/` is `document-reviewer.tsx:331`.
- **Security:** plugin content from any `*.supabase.co` host becomes loadable, but only if something renders an `<embed>`/`<object>` pointing at it. Content loaded this way runs in the Supabase origin, not the admin origin, so it can't read admin cookies or DOM.
- `default-src`, `script-src`, and `frame-src` are unchanged.

## 5. User-experience effect

- Internal admin: PDF driver documents render inline in the approval reviewer instead of a blank panel. Visible on the next page load after the Vercel deploy.
- No rider/driver change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/middleware.ts` | Added `object-src 'self' https://*.supabase.co` | Unblock PDF `<embed>` |
| `admin-dashboard/src/__tests__/middleware-csp.test.ts` | New | Pins `object-src` and that `default-src` stays `'self'` |

## 7. Before / after

```
Before: default-src 'self'; … worker-src blob: 'self'; frame-ancestors 'none'; …
After:  default-src 'self'; … worker-src blob: 'self'; object-src 'self' https://*.supabase.co; frame-ancestors 'none'; …
```

## 8. Rollback plan

Vercel instant rollback to the previous deployment. Header-only change; no data or state is touched.

## 9. Verification performed

- [x] Root cause from the user's console CSP violation plus a HEAD check of the signed URL (see §2).
- [x] Vitest `src/__tests__/middleware-csp.test.ts`: 2 passed. It calls the real `middleware()` and parses the emitted header.
- [x] Red check: with the `object-src` line removed, the test fails (`expected undefined to be "'self' https://*.supabase.co"`).
- [x] `npm run check:middleware` passes. `tsc --noEmit` exit 0. eslint clean on `src/middleware.ts`.
- [ ] Production build: see the PR description.

## 10. What was NOT verified

- Not checked in a real browser against the deployed dashboard. PDF rendering inside `<embed>` also depends on the browser's built-in PDF viewer being enabled.
- Visual regression: the approvals reviewer is not among the 6 seeded baseline pages, and the CSP header does not change rendering of those pages.
