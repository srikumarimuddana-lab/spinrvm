# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (spinr platform session) |
| Surface(s) | admin-dashboard, CI |
| Domain (Sentry tag) | admin |
| PR / commit link | (added on push) |
| Related issue or gap ID | Epic #2785 (Prerequisite), `ACTION_ITEMS.md` E11, backlog #2803 |

## 1. Issue / gap identified

`ACTION_ITEMS.md` E11: `@axe-core/playwright` is an `admin-dashboard` devDependency and already runs across all 41 dashboard routes in `e2e/crawl-audit.spec.ts`, but every test ends with `expect(true).toBe(true)` — axe violations are recorded as test annotations only, never fail the build. WCAG 2.1 AA is a stated regulatory mandate in `CLAUDE.md`, and this repo currently has no visual-regression tooling either (the other Phase 1 prerequisite in #2785), so a11y is the only automatable safety net available right now for the upcoming visual-refresh phases.

## 2. Root cause

The spec was written as an audit/discovery tool (its own comment says "this is an audit pass, not a gate") and never graduated to an enforced check.

## 3. Fix / remediation

- Ran the existing `crawl-audit.spec.ts` for real (Chromium, full 41-route matrix) and captured actual current violations — **64 instances across 41/41 routes** (9 critical, 43 serious, 12 moderate). Full breakdown filed as backlog issue #2803.
- Added `e2e/a11y-baseline.json`: per-route violation-count baseline, generated from that real run.
- Changed `crawl-audit.spec.ts`'s final assertion from `expect(true).toBe(true)` to `expect(axeViolations.length).toBeLessThanOrEqual(baseline[route] ?? 0)` — a **ratchet gate** matching this repo's existing "Lint warning trend check" pattern (`.github/workflows/ci-guardrails.yml`): existing debt is tolerated up to its recorded baseline, but no route may regress past it, and any route with no baseline entry defaults to zero tolerance (new pages must ship clean).
- Deliberately **not** a hard "zero violations" gate — see §4/§8 for why a big-bang fail-everything gate was rejected.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the `e2e-test` CI job.** Two files changed: `e2e/crawl-audit.spec.ts` (the assertion), `e2e/a11y-baseline.json` (new). No `src/` application code touched, no production build behavior affected — confirmed via `npm run build` (unaffected, e2e/ isn't bundled).
- **`e2e-test` in `.github/workflows/ci.yml` is currently non-blocking on PRs, blocking only on `main`** (per its own comment: "blocking on main, non-blocking on PRs while the suite stabilises ([17-1])"). Grepped `ci.yml` for job dependencies: `deploy-admin` depends on `[admin-test, deploy-backend]`, **not** `e2e-test` — a red `e2e-test` on `main` cannot block or delay a production deploy. This is why turning this into a real (but ratcheted, not absolute) gate is safe to ship directly rather than needing to be flagged.
- No other test file, route, or component reads `a11y-baseline.json` — new, single-purpose file.
- Verified the gate actually works both ways: reran the full 41-route suite against its own fresh baseline (42/42 passed — 41 audit tests + 1 auth setup), then artificially lowered one route's baseline to 0 and confirmed that route's test fails with a clear message (`New a11y violations on /dashboard/heatmap (baseline 0): [...]`) before restoring the real baseline.

## 5. User-experience effect

- **None, directly.** This is a CI-only change — no admin, rider, driver, or corporate-facing behavior changes. The 64 real violations it surfaces are pre-existing and unaffected by this PR; they're now visible/tracked (issue #2803) rather than fixed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/e2e/crawl-audit.spec.ts` | Replaced no-op `expect(true).toBe(true)` with a per-route baseline-ratchet assertion against `a11yBaseline` | Turns the existing axe run into a real, enforced (but non-absolute) gate |
| `admin-dashboard/e2e/a11y-baseline.json` | New file — per-route violation-count baseline (41 routes, 64 total instances) | Ratchet reference point; see §8 for how it's meant to be maintained |

## 7. Before / after

```ts
// Before
test.info().annotations.push(/* ... */);
// Don't fail the run — this is an audit pass, not a gate.
expect(true).toBe(true);

// After
test.info().annotations.push(/* ... */);
const allowed = (a11yBaseline as Record<string, number>)[route] ?? 0;
expect(
  axeViolations.length,
  `New a11y violations on ${route} (baseline ${allowed}): ${JSON.stringify(axeViolations.map(v => v.id))}`
).toBeLessThanOrEqual(allowed);
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure test-code and a new JSON data file, no data/migration/Stripe state involved. Reverting restores the prior always-passing behavior instantly.

**Deliberately not a hard zero-violations gate**: turning this into `expect(axeViolations.length).toBe(0)` directly would have instantly failed 41/41 routes on the very next `main` push, with zero of the 64 underlying issues actually fixed — a red CI status with no corresponding value delivered, and exactly the "gate that trains people to ignore red checks" failure mode this repo's own `CLAUDE.md` warns about elsewhere (re: the `review` check / #2503). The baseline-ratchet approach was chosen instead specifically to avoid that.

## 9. Verification performed

- [x] Automated tests run: the modified `e2e/crawl-audit.spec.ts` itself, executed locally end-to-end (not just `tsc`/lint) — full 41-route matrix, 42/42 passed against the real baseline. Also `npm run lint` (clean) and `npm run build` (compiles, all pages generate — confirms no impact on the production bundle from a test-only change).
- [x] Manual regression-catching check: temporarily zeroed one route's baseline and confirmed the assertion fails with the expected message, then restored it.
- [x] Blast-radius check: grepped `ci.yml` for what depends on `e2e-test` — nothing deploy-relevant does; confirmed non-blocking-on-PR status directly in the workflow file's own comment.
- [x] Reviewed against relevant CLAUDE.md conventions: WCAG 2.1 AA (this change is what starts actually enforcing it, incrementally), the "no permanently-red gate" principle from the `review`/#2503 precedent (informed the ratchet-not-absolute design choice), and the "Lint warning trend check" baseline pattern this mirrors.
- [ ] Feature-flagged: not applicable — this is a CI test-assertion change, not application code; there's no user-facing surface to flag.

## What was NOT verified

- Did not attempt to root-cause or fix any of the 64 underlying violations in this change — that's explicitly out of scope here and tracked separately in #2803, which includes a suggested `color-contrast`-first approach since it's 43/64 (67%) of the total and present on every route (likely 1-2 shared-component sources, not 41 independent bugs) — but that hypothesis itself is untested, not confirmed.
- Ran the suite locally against a pre-installed Chromium revision (1194) rather than the exact pinned revision CI downloads fresh (1217) — functionally equivalent for this purpose (headless axe/DOM analysis, not visual rendering), but not byte-identical to the CI browser build. CI's own `npx playwright install --with-deps chromium` step will use the real pinned revision.
- Did not verify behavior on the `rider-app-test`/`driver-app-test` E2E jobs (unaffected — this change is `admin-dashboard`-only) or confirm how the `main`-branch `e2e-test` run's red/green status is surfaced to anyone downstream (e.g., whether it's monitored/alerted on at all, given deploy doesn't depend on it) — worth confirming separately if this gate is meant to actually drive remediation urgency, not just exist.
