# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | admin-dashboard, CI |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B37 (step 1 of 4 — item stays open overall) |

## 1. Issue / gap identified

`admin-dashboard/vitest.config.ts` has a real `coverage.thresholds` block
(`branches: 50, functions: 50, lines: 60, statements: 60`), but
`.github/workflows/ci.yml`'s admin-dashboard test step ran plain `npm test`
(`vitest run`, no `--coverage` flag) — Vitest only evaluates coverage
thresholds when `--coverage` is passed, so the threshold block was
unreachable dead code as far as CI was concerned. A live `vitest run
--coverage` measured the real numbers at 19.94% statements / 13.56%
branches / 11.88% functions / 21.80% lines — 25-45 points below the
configured (but never-enforced) floor.

## 2. Root cause

`test` and `test:coverage` npm scripts both already existed
(`package.json`), presumably so local/dev runs stay fast by default — but
CI was never pointed at the coverage variant, so the gate has been a no-op
since it was added.

## 3. Fix / remediation

- `ci.yml`'s admin-dashboard test step now runs `npm run test:coverage`
  instead of `npm test`.
- `vitest.config.ts`'s thresholds dropped to `branches: 10, functions: 10,
  lines: 18, statements: 15` — a few points below the measured real
  numbers, so this lands green immediately instead of turning `main` red
  the moment the gate starts actually running. This is a ratchet floor
  (mirrors the backend's `pytest.ini` coverage-ratchet history: 6%→40%→
  50%→60%, ceiling 80), not a target — raise it in small steps as coverage
  genuinely improves.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI configuration and one vitest config file.**
  No application code changed.
- **What could regress:** none directly — this only makes an existing,
  already-written test step slower (coverage instrumentation overhead) and
  makes a previously-silent gate start actually gating. The one real risk
  is exactly what this item's own text warned about: if a future PR raises
  these thresholds without re-measuring first, or if someone widens
  `coverage.include` without lowering the threshold to match, CI could go
  red for an unrelated PR — that's a process risk for the *next* change to
  this file, not something this PR does.
- **Other consumers of `vitest.config.ts`/this CI step:** none beyond the
  one `admin-test` job — grepped `ci.yml` for other references to
  `test:coverage` or this config file; none found.
- No interaction with the ride state machine, money/wallet deltas, RLS, or
  any background loop — this is test/CI tooling only.

## 5. User-experience effect

None — internal CI/tooling change only, invisible to riders, drivers,
corporate admins, and internal admins alike. Slightly longer CI run time
for the `admin-test` job (coverage instrumentation), not a functional
change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | admin-dashboard test step: `npm test` → `npm run test:coverage` | Actually run the coverage plugin so `thresholds` stops being dead code |
| `admin-dashboard/vitest.config.ts` | Thresholds dropped from `50/50/60/60` to `10/10/18/15` | Match the real measured baseline so the now-enforced gate lands green, not newly red |
| `ACTION_ITEMS.md` | B37 step 1 marked done; item stays open overall (steps 2-4 remain) | Backlog hygiene |

## 7. Before / after

```yaml
# Before (.github/workflows/ci.yml)
      - name: Run admin dashboard tests
        working-directory: admin-dashboard
        run: npm test
```

```yaml
# After
      - name: Run admin dashboard tests
        working-directory: admin-dashboard
        run: npm run test:coverage
```

```ts
// Before (vitest.config.ts)
thresholds: {
  branches: 50,
  functions: 50,
  lines: 60,
  statements: 60,
},
```

```ts
// After
thresholds: {
  branches: 10,
  functions: 10,
  lines: 18,
  statements: 15,
},
```

## 8. Rollback plan

**`git-revert-safe`** — pure CI-config and test-config change, no data, no
migration, no application code. A plain `git revert` fully restores the old
(non-enforcing) state.

## 9. Verification performed

- [x] Automated tests run: `npm run test:coverage` (locally, `admin-dashboard/`) → exits `0` against the new thresholds; coverage summary matches this item's own recorded numbers exactly (19.94% / 13.56% / 11.88% / 21.80%), confirming no drift since the finding was written earlier the same day.
- [ ] Manual repro steps followed in staging — n/a, CI/tooling-only change, no staging surface to repro against.
- [x] Blast-radius grep performed: searched `ci.yml` for other references to `test:coverage`/this vitest config — none found beyond the one `admin-test` job step.
- [x] Reviewed against relevant CLAUDE.md convention(s): mirrors the backend's existing coverage-ratchet pattern (`pytest.ini`'s documented history) — drop-then-ratchet, never silently raise a bar and break `main`.
- [ ] Feature-flagged — n/a, CI configuration has no flag mechanism and this isn't user-visible.

## What was NOT verified

- Steps 2-4 of B37's recommended fix (widening `collectCoverageFrom` on rider-app/driver-app, a milestone-ratchet doc, working toward the user's stated 100% ceiling) are explicitly out of scope for this pass — a materially larger, separate piece of work per app.
- Whether CI's actual runner environment produces byte-identical coverage numbers to this local run (different Node/npm cache state) was not verified — the margin between the new threshold (10/10/18/15) and the measured baseline (11.88/13.56/19.94/21.80) is deliberately generous enough to absorb minor environment-to-environment variance.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to one CI step + one config file, grep-verified)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — no user-facing flow touched, filled in above
