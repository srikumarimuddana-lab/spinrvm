# Sentry never initialised in admin-dashboard — instrumentation hooks added

**Date:** 2026-08-05
**Surface:** admin-dashboard

## Issue/gap identified

`admin-dashboard` had `sentry.client.config.ts` and `sentry.server.config.ts`, a
`@sentry/nextjs` dependency, and a documented `NEXT_PUBLIC_SENTRY_DSN` env var — but
**Sentry never initialised**. No browser error, server-component error, route-handler
error, or middleware error has ever been reported from this surface, regardless of
whether a DSN was configured. The error-tracking for the admin dashboard was, in effect,
decorative.

## Root cause

`sentry.*.config.ts` are plain modules with no importer. Up to `@sentry/nextjs` v8 the
SDK auto-loaded them via its webpack plugin. From v9 onward (this repo is on **v10.51.0**,
Next.js **16.2**) they are loaded only by:

- `instrumentation.ts` → `register()`, which must `await import()` the server/edge configs
- `instrumentation-client.ts`, which Next.js loads on the client
- `withSentryConfig()` wrapping `next.config.ts` to wire the hooks into the build

None of the three existed. So `Sentry.init` was never called on any runtime. The config
files typechecked and looked correct in review, which is why this survived — nothing about
reading them reveals that they are dead code.

## Fix/remediation

Added the missing hooks and wrapped the Next config. Also extracted the PII scrubber,
which was duplicated byte-for-byte across the client and server configs, into
`sentry.scrub.ts` — the new edge config needed the same logic and a third hand-maintained
copy of a privacy control is how one copy silently drifts from the others.

Also added `Sentry.captureException` to `src/app/error.tsx`, the root error boundary,
which previously only `console.error`'d. Without it, React render errors are swallowed by
the boundary and never reach Sentry even once the SDK is live.

## Risk & impact on existing functionality

Blast radius is the whole admin surface, because `withSentryConfig` wraps the build and
`register()` runs on every server start. Specifically checked:

- **`next.config.ts`** — `withSentryConfig` returns the config with `headers()`,
  `redirects()`, and `rewrites()` intact. The security headers, the `/track/:path*` CSP,
  the four `/dashboard/*` → `/dashboard/records?tab=` redirects, and the backend `/api/:path*`
  proxy all still apply; verified by a passing production build that lists every route.
- **`tunnelRoute` deliberately NOT enabled.** It mounts a proxy route on the admin domain,
  which `src/middleware.ts` would subject to admin auth + the IP allowlist, breaking
  ingestion in a way that only shows up in production. Direct ingestion is already
  permitted by the CSP.
- **CSP** — checked `buildCsp()` in `src/middleware.ts`: `connect-src 'self' https: wss: ws:`
  permits Sentry ingestion and `worker-src blob: 'self'` covers the Session Replay worker.
  No CSP change needed; had `connect-src` been restrictive this would have failed silently
  in the browser only.
- **`sentry.scrub.ts` extraction is behaviour-preserving.** The two prior copies were
  compared and are identical: same header allowlist, same cookie/query-string handling,
  same `/[id]` URL regex, same `PII_KEYS` set, same recursive `scrubObj`, same user
  reduction to `{ id }`. Consumers: client, server, and the new edge config — no others.
- **`src/app/error.tsx`** is the root error boundary; the added call is inside the
  existing `useEffect` and does not change rendering or the `reset()` path.

The real new risk is **volume, not breakage**: this surface has reported nothing until now,
so enabling it will produce a first-time flood of previously-invisible errors, and
`tracesSampleRate: 1.0` plus 10% session replay now actually take effect. Watch the Sentry
quota for the first few days.

## User experience effect

None visible to riders, drivers, corporate admins, or internal admins. No UI, copy,
validation, or route behaviour changed. Second-order only: Session Replay now genuinely
records admin sessions (masked — `maskAllText: true`, `blockAllMedia: true`), which was
configured but inert before.

## Files modified

| file path | what changed | why |
|---|---|---|
| `admin-dashboard/src/instrumentation.ts` | **new** — `register()` imports server/edge config by runtime; exports `onRequestError` | The hook that makes server-side Sentry load at all. In `src/` because this project uses a src dir |
| `admin-dashboard/src/instrumentation-client.ts` | **new** — imports client config; exports `onRouterTransitionStart` | v9+ client entry point; replaces auto-loading of `sentry.client.config.ts` |
| `admin-dashboard/sentry.edge.config.ts` | **new** — edge-runtime init | `src/middleware.ts` (auth, IP allowlist, CSP nonce, tracking-host router) had no error reporting |
| `admin-dashboard/sentry.scrub.ts` | **new** — shared `scrubEvent` | Deduplicates the PII scrubber instead of adding a third copy |
| `admin-dashboard/sentry.client.config.ts` | Local `beforeSend` replaced with shared import | Same |
| `admin-dashboard/sentry.server.config.ts` | Local `beforeSend` replaced with shared import | Same |
| `admin-dashboard/next.config.ts` | Wrapped export in `withSentryConfig` | Required by v9+ to register the hooks |
| `admin-dashboard/src/app/error.tsx` | Added `Sentry.captureException(error)` | Root boundary swallowed render errors |

## Before/after

```ts
// next.config.ts — before: hooks never registered
export default nextConfig;

// after
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
  widenClientFileUpload: true,
  telemetry: false,
});
```

```ts
// src/instrumentation.ts — new; without this, sentry.server.config.ts is dead code
export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') await import('../sentry.server.config');
  if (process.env.NEXT_RUNTIME === 'edge') await import('../sentry.edge.config');
}
export const onRequestError = Sentry.captureRequestError;
```

## Rollback plan

No deploy required: unset `NEXT_PUBLIC_SENTRY_DSN` and `SENTRY_DSN` in Vercel. Every
config guards on `enabled: !!dsn`, so all four runtimes go inert and nothing is sent —
this returns behaviour to exactly the pre-change state (no events), which is the whole
point of the kill switch. `withSentryConfig` with no DSN and no auth token is a no-op
wrapper. Reverting the commit is safe and needs no data cleanup; nothing here writes to a
database, touches money, or changes ride state.

## Verification performed

- **Real production build run**: `npm run build` in `admin-dashboard` → `✓ Compiled
  successfully`, exit 0, full route list emitted. (Not just `tsc --noEmit`.)
- `npx tsc --noEmit` → 29 errors, all pre-existing and unrelated (missing test-runner
  types, `motion/react`, `qrcode.react`); **zero** in any changed file.
- **Runtime proof that the gap is closed** — started the built server with a deliberately
  unreviewed DSN host; `sentry.server.config.ts`'s module-scope region check printed
  `[spinr-admin][PIPEDA] Sentry DSN routes to an unreviewed region.` This line could not
  have printed before, since nothing imported the module. Server returned HTTP 200.
- **Positive path** — restarted with the real US DSN
  (`…@o4511514049052672.ingest.us.sentry.io/…`): zero PIPEDA warnings, HTTP 200. Confirms
  the relaxed region check accepts US and still rejects unknown hosts.
- Build artifacts inspected: `.next/server/instrumentation.js` emitted, and a
  `…sentry_server_config_ts…` chunk now exists in the server bundle.
- Fixed a deprecation surfaced by the first run (`disableLogger` is deprecated in
  @sentry/nextjs v10); removed it and confirmed the warning is gone on rebuild.
- CSP and `next.config` blast radius checked by reading `buildCsp()` and confirming the
  post-wrap build still emits all routes.

## What was NOT verified

- **No error was actually delivered to Sentry end-to-end.** Init is proven to run and the
  DSN is proven accepted, but no test exception was thrown and confirmed visible in the
  Sentry UI. That is the one remaining check, and it needs a deploy.
- **Edge/middleware capture is unproven at runtime.** `sentry.edge.config.ts` is wired
  into `register()` under `NEXT_RUNTIME === 'edge'`, but no middleware error was forced to
  confirm it reports. Static-only confidence there.
- **`onRequestError` and `onRouterTransitionStart` were not exercised** — no server-component
  throw and no client navigation were tested against a live DSN.
- **Session Replay was not observed.** Masking settings are unchanged from what was already
  configured, but since replay never actually ran before, its real-world output on admin
  screens (which render licences, payouts, Stripe IDs) has never been eyeballed. Worth one
  manual look after deploy.
- **No source maps uploaded** — `SENTRY_AUTH_TOKEN` was absent, so stack traces will be
  minified until it is set in Vercel.
- **No visual/snapshot regression tooling exists for this surface** (standing gap), so the
  "no UI change" claim is reasoned from the diff, not screenshotted.
- Local `node_modules` was stale (`motion`, `qrcode.react` declared but not installed);
  `npm install` was run to complete the build. This changed the local lockfile state —
  review `package-lock.json` before committing.
