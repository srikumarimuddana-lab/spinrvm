# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Found while investigating a11y rule aggregation (Workstream C of the admin-portal UX audit) — the same crash was also independently noted in the earlier audit session as "Records & Corporate crashed under mocked sparse data" |

## 1. Issue / gap identified

Three admin-dashboard pages throw `TypeError: Cannot read properties of
undefined (reading 'length')` and fall back to the shared "Something went
wrong" error boundary whenever their fetch resolves with a 200 response
that is missing an expected array/object field, rather than showing an
empty state:

- `/dashboard/audit-logs` — `getAuditLogTopActors()` response missing `actors`
- `/dashboard/corporate-accounts` — `getWalletRiskPortfolio()` response missing `wallets`; `getKybReverificationDue()` response missing `companies`
- `/dashboard/heatmap` — `heatMapData.stats` present but missing individual numeric fields (e.g. `total_rides`), so `stats.total_rides.toLocaleString()` throws

## 2. Root cause

All four call sites assign the raw field straight out of the API response
into state (`setTopActors(res.actors)`, `setKybDue(res.companies)`,
`setFlaggedWallets(res.wallets.filter(...))`) or read a nested numeric
field directly (`stats.total_rides`) with no fallback for that field being
absent. Each site already has a `.catch()` for a *rejected* promise (and
initializes state to `[]`), but none of them guard a *resolved* response
that is valid JSON yet missing the specific key the page expects — a
partial/malformed 200 (stale cache, a backend field renamed or omitted, a
transient empty payload) reaches the render path as `undefined` instead of
falling back to the already-established empty-array/zero default.

This surfaced now because Workstream C's axe-aggregation run mocked all
`/api/**` calls with a single generic empty-but-valid response shape
(`{ data: [], items: [], results: [], stats: {}, ... }`) that does not
include page-specific keys like `actors`/`wallets`/`companies` — the same
shape the existing `crawl-audit.spec.ts` e2e mock already uses. That
aggregation run's "shared component" cross-reference showed the resulting
error-boundary render (`<h2>Something went wrong</h2>`, `<pre>` stack
trace) is itself a repeat `color-contrast` violation across the affected
routes, so this crash was also inflating the a11y violation count, not
just breaking those pages' own content.

## 3. Fix / remediation

Defaulted each field at the point it enters state / is read, using the
same fallback values each page already uses elsewhere for the "empty"
case:

- `audit-logs/page.tsx`: `setTopActors(res.actors ?? [])`
- `corporate-accounts/page.tsx`: `setFlaggedWallets((res.wallets ?? []).filter((w) => (w.risk_flags?.length ?? 0) > 0))`, `setTotalWallets(res.total_wallets ?? 0)`, `setKybDue(res.companies ?? [])`
- `heatmap/page.tsx`: `stats` is now built by spreading `heatMapData?.stats` over an explicit `{ total_rides: 0, corporate_rides: 0, regular_rides: 0 }` default object (field-level fallback), instead of `heatMapData?.stats || { ... }` (object-level fallback, which only covers `stats` itself being null/undefined — not `stats` being present but incomplete)

No API contracts, types, or other logic changed. Each fix is a one- or
two-line null-coalescing guard at an existing state-assignment/read site.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the four call sites above, in three files.**
  Grepped both files for every other reader of `topActors`, `flaggedWallets`,
  `kybDue`, and `stats` — all are used only for rendering (`.length`,
  `.map`, `.toLocaleString()`) within the same page; nothing else imports
  or reads this local state.
- On a normal (complete) API response, behavior is byte-for-byte
  unchanged — `res.actors ?? []` returns `res.actors` whenever it's
  present, same for the other three guards.
- The corporate-accounts wallet-risk filter additionally guards
  `w.risk_flags?.length` per-row (previously `w.risk_flags.length`, which
  would have thrown on any individual wallet row missing that field even
  if the top-level `wallets` array was present) — same reasoning, applied
  one level deeper since it's the same response shape.

## 5. User-experience effect

- **Internal admin only.** Before this fix, an admin hitting a
  degraded/partial API response on these three pages saw a full-page
  "Something went wrong" crash screen instead of the page's existing empty
  state (e.g. "No activity in this window."). After this fix, that same
  degraded response renders the intended empty state instead — no new UI,
  just the existing empty-state path now reachable via a fallback that
  previously threw before reaching it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | `setTopActors(res.actors ?? [])` | Response missing `actors` crashed the page instead of showing the empty state |
| `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` | Null-coalesce `res.wallets`, `w.risk_flags`, `res.total_wallets`, `res.companies` | Same class of bug, three call sites in this file |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | `stats` built via field-level defaults instead of object-level `||` fallback | A `stats` object present but missing one numeric field still crashed `.toLocaleString()` |

## 7. Before / after

```tsx
// audit-logs/page.tsx — before
.then((res) => {
    if (!cancelled) setTopActors(res.actors);
})
// after
.then((res) => {
    if (!cancelled) setTopActors(res.actors ?? []);
})

// corporate-accounts/page.tsx — before
setFlaggedWallets(res.wallets.filter((w) => w.risk_flags.length > 0));
setTotalWallets(res.total_wallets);
...
setKybDue(res.companies);
// after
setFlaggedWallets((res.wallets ?? []).filter((w) => (w.risk_flags?.length ?? 0) > 0));
setTotalWallets(res.total_wallets ?? 0);
...
setKybDue(res.companies ?? []);

// heatmap/page.tsx — before
const stats = heatMapData?.stats || { total_rides: 0, corporate_rides: 0, regular_rides: 0 };
// after
const stats = {
    total_rides: 0,
    corporate_rides: 0,
    regular_rides: 0,
    ...(heatMapData?.stats || {}),
};
```

## 8. Rollback plan

`git-revert-safe` — three files, no data/API/schema change, no feature
flag involved.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, all three routes compiled clean.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] **Live browser reproduction and re-verification**: ran a Playwright script against a live `next dev` server, mocking `/api/**` with the exact sparse-but-valid response shape that originally crashed these pages (no `actors`/`wallets`/`companies` keys, `stats: {}`). Before the fix, `/dashboard/corporate-accounts` threw `TypeError: Cannot read properties of undefined (reading 'length')` and rendered the error boundary (confirmed via `pageerror`/console listeners and body-text check for "Something went wrong"); `/dashboard/audit-logs` and `/dashboard/heatmap` were fixed on the first pass, `/dashboard/corporate-accounts` needed a second fix (`kybDue`) found by the same live repro before all three passed clean.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — grepped every reader of the touched state in each file.
- [x] No silent behavior change on the happy path — only the previously-crashing sparse-response path changes, to the page's own existing empty state.
