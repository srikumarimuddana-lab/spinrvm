# Sentry US-region ingestion accepted; cold-start region check relaxed

**Date:** 2026-08-05
**Surface:** admin-dashboard (Sentry init), backend/.env.example (docs only)

## Issue/gap identified

`admin-dashboard/sentry.server.config.ts` logged a `[spinr-admin][PIPEDA]` error on every
production cold start, because it asserted the DSN host contained `ingest.de.sentry.io`
(EU) and Spinr's actual Sentry DSN routes to `ingest.us.sentry.io` (US).

## Root cause

Not a code defect — a configuration/compliance mismatch. The Sentry organization
(`spinr-backend`, org id `o4511514049052672`) was created in Sentry's **US** region.
A Sentry org's data region is fixed at creation time and has no self-serve migration,
so reaching EU would require standing up a **new organization** and re-pointing every
surface's DSN. The check was written assuming an EU org would exist.

## Fix/remediation

Region check now accepts **both** `ingest.de.sentry.io` (EU, the standing target) and
`ingest.us.sentry.io` (US, accepted risk). An unrecognised ingest host still logs the
error, so the check retains its real value: catching a DSN repointed at an unreviewed
destination. Stale "use the EU DSN" comments in the client/server configs and in
`admin-dashboard/.env.example` were corrected so they no longer contradict the
deployed configuration. `backend/.env.example` guidance for `SENTRY_API_BASE_URL` was
corrected to key off the **org's** region (US → `https://sentry.io`, the existing default).

**This does not make US ingestion compliant.** It records it as a decision rather than
an unread log line. The DPA addendum (A-PE-P2-5, `docs/vendor-register.md § Sentry`)
is still outstanding and is now the tracking item.

## Risk & impact on existing functionality

Blast radius is small and non-runtime-behavioural:

- `_checkSentryRegion` is module-private to `sentry.server.config.ts`, called once at
  module load (line 20). No other importer — grepped, zero consumers.
- It only ever called `console.error`; it never threw and never touched `Sentry.init`
  options. Error capture, PII scrubbing (`beforeSend`), sampling, and tags are all
  untouched, so what is sent to Sentry — and what is scrubbed from it — is identical.
- The other three files changed are comments only (`.env.example` × 2,
  `sentry.client.config.ts` header comment). No executable change.

Real residual risk is **compliance, not code**: scrubbed stack traces, breadcrumbs, and
session replays are stored in the US, outside Canada. The `beforeSend` PII scrubbers and
`maskAllText`/`blockAllMedia` replay settings limit what crosses the border, but they are
a mitigation, not a residency control.

## User experience effect

None. No rider, driver, corporate-admin, or internal-admin surface changes. The only
observable difference is one fewer error line in Vercel Function logs at cold start.

## Files modified

| file path | what changed | why |
|---|---|---|
| `admin-dashboard/sentry.server.config.ts` | `ACCEPTED_INGEST_HOSTS` allows EU + US; message reworded; comment records accepted risk | Stop flagging a decision that has been made; still catch unreviewed hosts |
| `admin-dashboard/sentry.client.config.ts` | Header comment only | Comment claimed EU DSN was in use; it is not |
| `admin-dashboard/.env.example` | Sentry section comment only | Same stale EU-only instruction |
| `backend/.env.example` | `SENTRY_API_BASE_URL` comment only | Claimed frontend DSNs route to EU; region must match the **org**, which is US |

## Before/after

```ts
// before — any non-EU host is an error
if (!dsn.includes('ingest.de.sentry.io')) {
  console.error('[spinr-admin][PIPEDA] Sentry DSN routes to US ingestion. ...');
}

// after — EU and US both accepted; anything else still errors
const ACCEPTED_INGEST_HOSTS = ['ingest.de.sentry.io', 'ingest.us.sentry.io'];
if (!ACCEPTED_INGEST_HOSTS.some((host) => dsn.includes(host))) {
  console.error('[spinr-admin][PIPEDA] Sentry DSN routes to an unreviewed region. ...');
}
```

## Rollback plan

No deploy needed to undo the compliance posture: unset `NEXT_PUBLIC_SENTRY_DSN` and
`SENTRY_DSN` in Vercel. `enabled: !!dsn` (both configs) means the SDK stops initialising
and nothing leaves the browser or the server — this is the kill switch. Reverting the
code edit itself is a plain `git revert`; it is safe here because the change applied no
migration and wrote no data, only console output. Backend viewer config is independently
disabled by unsetting `SENTRY_API_TOKEN` (no redeploy).

## Verification performed

- `npx tsc --noEmit` on `admin-dashboard` — see session output.
- Region-check logic reasoned against the live DSN read from the Sentry MCP
  (`https://…@o4511514049052672.ingest.us.sentry.io/4511514049380352`): contains
  `ingest.us.sentry.io` → matches `ACCEPTED_INGEST_HOSTS` → no error logged.
- Blast radius established by grep: `_checkSentryRegion` has no callers outside its
  own module.

## What was NOT verified

- **No production build was run** (`npm run build` in `admin-dashboard`) — typecheck only.
- **Not verified end-to-end against live Sentry.** No event was actually emitted and
  confirmed received; the cold-start log line was not observed in Vercel Function logs.
- **The check only runs when `NODE_ENV === 'production'`**, so a local dev run exercises
  neither branch.
- **Unrelated standing gap, not introduced here:** `admin-dashboard` has no
  `instrumentation.ts` / `instrumentation-client.ts` and `next.config.ts` does not wrap
  with `withSentryConfig`. On `@sentry/nextjs` ^10 these config files are loaded via the
  instrumentation hook, so it is likely **neither config executes at all** and no events
  reach Sentry regardless of DSN. Not investigated or fixed in this change.
- No DPA addendum was filed. A-PE-P2-5 remains open.
