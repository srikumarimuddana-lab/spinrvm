# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W7 |

## 1. Issue / gap identified

`sentry.scrub.ts`'s `scrubEvent` (wired as `beforeSend` in all 3 Sentry
config files) scrubs `event.request.{headers,cookies,query_string,url}`,
`event.extra`, `event.contexts`, and `event.user` — but never touches
`event.breadcrumbs`. Sentry's default integrations (console, fetch, xhr,
dom/click) are all active in this app (each config's `integrations: [...]`
array *adds to*, not replaces, the SDK defaults — none of the 3 files sets
`defaultIntegrations: false`), and those integrations can attach a full
request URL (with query string) or a raw `console.log` call's arguments to
a breadcrumb, bypassing `beforeSend`'s request-level scrubbing entirely.
The existing `event.request.query_string`/`url` scrubbing in `scrubEvent`
already shows the team is aware query strings/paths can carry PII — that
same class of data reaches Sentry unredacted via breadcrumbs. No confirmed
live violating `console.log`/PII-in-query-string call site was found by
the original audit — a structural gap, not a confirmed active leak.

## 2. Root cause

`beforeSend` was written to scrub the top-level event shape (request,
extra, contexts, user) but breadcrumbs — which Sentry's own default
integrations populate automatically, outside any code this repo wrote —
were never brought into scope.

## 3. Fix / remediation

- `sentry.scrub.ts`: added `scrubBreadcrumb(breadcrumb)`, exported
  alongside `scrubEvent` (same "one copy, imported by all three" file this
  repo already uses for `beforeSend`, per its own header comment):
  - Strips the query string from `breadcrumb.data.url` when present (same
    reasoning as `event.request.query_string`).
  - Recursively scrubs `breadcrumb.data` via the existing `scrubObj` helper
    (drops any `PII_KEYS`-named field anywhere in structured breadcrumb
    data).
  - For `category === 'console'` breadcrumbs specifically, filters
    `message` entirely — a console breadcrumb's text is whatever arguments
    the code happened to log, arbitrary and untrusted by construction
    (unlike a navigation/click/fetch breadcrumb's `message`, which is a
    short SDK-generated description, not app data, and is left alone so
    breadcrumb trails stay useful for debugging).
- Wired `beforeBreadcrumb: scrubBreadcrumb` into `sentry.client.config.ts`,
  `sentry.server.config.ts`, and `sentry.edge.config.ts` — identical
  3-file pattern `beforeSend` already uses.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the shared Sentry init.** All three config
  files already imported `sentry.scrub.ts` for `beforeSend`; this adds one
  more named import and one more init option to each — no other file
  imports or calls `scrubEvent`/`scrubBreadcrumb`.
- **What could regress:** Sentry breadcrumb trails for non-console
  categories (fetch, xhr, navigation, ui.click) are unaffected in content,
  only `data.url` loses its query string and any PII-keyed sub-field.
  Console-category breadcrumbs lose their message text entirely (by
  design — see §3) but keep their `category`/`level`/`timestamp`, so the
  trail still shows *that* a console call happened and its severity, just
  not its content.
- No runtime behavior change besides what data leaves the process — this
  is a data-egress change, not an application logic change.

## 5. User-experience effect

None — this only affects what Sentry (an internal observability tool)
receives; no rider/driver/corporate-admin/internal-admin-facing behavior
changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/sentry.scrub.ts` | Added `scrubBreadcrumb()`, exported; `Breadcrumb` type added to the existing `@sentry/nextjs` import | The actual fix — closes the breadcrumb-scrubbing gap |
| `admin-dashboard/sentry.client.config.ts` | Imports `scrubBreadcrumb as beforeBreadcrumb`; added to `Sentry.init()` | Wire the fix into the browser runtime |
| `admin-dashboard/sentry.server.config.ts` | Same | Wire the fix into the Node runtime |
| `admin-dashboard/sentry.edge.config.ts` | Same | Wire the fix into the edge runtime (middleware) |
| `admin-dashboard/src/__tests__/sentry-scrub-breadcrumb.test.ts` (new) | 8 tests: query-string stripping, PII-key redaction in nested breadcrumb data, console-message filtering, non-console messages left alone (fetch/click/navigation), no-data/no-message and non-string-url edge cases | `sentry.scrub.ts` had zero test coverage before this fix |

## 7. Before / after

```typescript
// Before (sentry.client.config.ts, and server/edge identically)
import { scrubEvent as beforeSend } from './sentry.scrub';
Sentry.init({
  ...
  beforeSend,
  // breadcrumbs: never scrubbed
});

// After
import { scrubBreadcrumb as beforeBreadcrumb, scrubEvent as beforeSend } from './sentry.scrub';
Sentry.init({
  ...
  beforeSend,
  beforeBreadcrumb,
});
```

```typescript
// New in sentry.scrub.ts
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  if (breadcrumb.data) {
    if (typeof breadcrumb.data.url === 'string') {
      breadcrumb.data.url = breadcrumb.data.url.split('?')[0];
    }
    scrubObj(breadcrumb.data as Record<string, unknown>);
  }
  if (breadcrumb.category === 'console' && typeof breadcrumb.message === 'string') {
    breadcrumb.message = '[Filtered]';
  }
  return breadcrumb;
}
```

## 8. Rollback plan

`git revert`-safe. No data, schema, or Stripe/wallet state involved —
reverting removes the `beforeBreadcrumb` scrubbing (re-opens the gap), no
data-level remediation needed since this only affects future Sentry
ingestion, not stored application data.

## 9. Verification performed

- [x] Full test suite: `npx vitest run` — **640 passed, 0 failed**, 66 test files (includes the 8-test `sentry-scrub-breadcrumb.test.ts`).
- [x] `npx eslint` on all 5 touched files — 0 errors, 0 warnings.
- [x] `npx tsc --noEmit` — 0 errors.
- [x] **Real production build**: `npm run build` — "Compiled successfully" (per CLAUDE.md's Change Impact Log requirement — the actual build, not just dev-server/tsc).
- [x] Confirmed (by reading, not assuming) that `integrations: [...]` in all 3 config files does not set `defaultIntegrations: false`, meaning Sentry's default console/fetch/xhr/dom integrations really are active and really do need this scrubbing — the premise of the original audit finding.

## What was NOT verified

- No live Sentry ingestion test (no DSN configured in this sandboxed
  session) — verified by unit test + type-check + build only. The actual
  runtime effect (a real breadcrumb reaching Sentry with `message:
  '[Filtered]'` for a console call, or a stripped query string on a fetch
  breadcrumb) was not observed against a real Sentry project.
- No confirmed *active* PII leak via breadcrumbs was found before this fix
  (the original audit explicitly called this a structural gap, not a
  confirmed leak) — this closes a defense-in-depth gap, not a demonstrated
  incident.
- This is the last of the 9 fixes in this audit batch (2 blockers + 7
  hardening items, W1 through W7); the final combined verification pass
  (re-running every touched test suite together) follows this commit.
