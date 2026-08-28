# Change Impact & Risk Log — jest exact pin (both apps) + mobile CI cache hardening

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | vikas@ngitservices.com (via Claude Code session) |
| Surface(s) | rider-app, driver-app, CI (workflows) |
| Domain (Sentry tag) | admin (CI infra) |
| PR / commit link | branch `claude/map-vehicle-tracking-animation-3e85y2` (PR #4652) |
| Related issue or gap ID | rider-app-test red repo-wide 2026-08-28 (main runs 33143074542, PR run 33143399069) |

## 1. Issue / gap identified

Every `rider-app-test` run — on `main` too — failed instantly with `this._moduleMocker.clearMocksOnScope is not a function` across all 124 suites, blocking every PR that touches rider/shared code. The same latent exposure existed for driver-app.

## 2. Root cause

Two layers:

1. **Version float**: `"jest": "^30.3.0"` permits jest 30.4.x, which is incompatible with `jest-expo ~57.0.3` (its environment supplies jest-mock 30.3.0, which lacks `clearMocksOnScope`; jest-runtime 30.4.x calls it). Reproduced locally both ways: clean `--frozen-lockfile` install (30.3.0 everywhere) is green; `yarn upgrade jest` → 30.4.2 reproduces the exact CI error.
2. **Cache poisoning vector**: the `…-rider-node-…` node_modules cache key (`hashFiles(yarn.lock, patches/**)`) is shared by four workflows. The combined `actions/cache` action saves in a POST step — i.e. after `mobile-dep-check.yml`'s `npx expo install --check`, which can mutate node_modules (only `jest` itself is in `expo.install.exclude`). After the previous good entry was evicted, a mutated (floated-jest) tree was saved under the pristine-lockfile key; cache entries are immutable, so every subsequent job restored the poisoned tree.

## 3. Fix / remediation

- `"jest": "30.3.0"` exact in **rider-app** (earlier commit) and **driver-app** — the float is impossible now; the lockfile change also rotated the poisoned cache key.
- `mobile-dep-check.yml`: cache split into `actions/cache/restore` + explicit `actions/cache/save` immediately after the clean frozen install (gated on cache miss), for both jobs — a later mutating step can no longer be captured in the shared cache.
- The six other jobs sharing these keys (`ci.yml` rider/driver test + e2e, `mobile-bundle-smoke.yml` both jobs) run no post-install mutators today; instead of churning them, each cache step now carries a SHARED-KEY INVARIANT comment stating the mutation-free requirement and pointing at the restore/save split. (Reviewed by `spinr-cicd-infra-reviewer`: change verdict "safe to merge"; extending the split everywhere noted as optional defense-in-depth.)

## 4. Risk & impact on existing functionality

- **Blast radius**: CI-only plus two dependency pins. No app runtime code changes. The pins forbid only jest 30.4.x, which was never validly installed (lockfiles always resolved 30.3.0).
- Cache behavior: first run after merge takes a cold install per key (slow once, correct always — same trade the existing "no restore-keys" comment already accepts). `actions/cache/restore|save` subactions use the same pinned SHA already trusted repo-wide.
- Watch item: if a future job adds a node_modules-mutating step after install, the invariant comment is the guard; the durable fix is switching that job to the restore/save split.

## 5. User-experience effect

None — CI and devDependency pins only. Unblocks merging for every rider/shared-touching PR.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/package.json` + `yarn.lock` | jest `^30.3.0` → `30.3.0` (earlier commit) | forbid incompatible float; rotate poisoned cache key |
| `driver-app/package.json` + `yarn.lock` | jest `^30.3.0` → `30.3.0` | same latent exposure |
| `.github/workflows/mobile-dep-check.yml` | restore/save cache split, save pre-mutation | close the poisoning vector at the known mutator |
| `.github/workflows/ci.yml`, `mobile-bundle-smoke.yml` | invariant comments on shared-key cache steps | documented decision for the six mutation-free jobs |

## 7. Before/after snippet

```yaml
# Before — one action; node_modules saved in POST, after expo install --check ran
- uses: actions/cache@55cc83… 
  with: { path: rider-app/node_modules, key: …-rider-node-… }
- run: yarn install --frozen-lockfile
- run: npx expo install --check   # can mutate node_modules → mutated tree got saved

# After — explicit save right after the clean install; the mutator runs later
- uses: actions/cache/restore@55cc83…
  id: rider-node-cache
- run: yarn install --frozen-lockfile
- uses: actions/cache/save@55cc83…
  if: steps.rider-node-cache.outputs.cache-hit != 'true'
- run: npx expo install --check
```

## 8. Rollback plan

`git revert` of the workflow/pin commits — CI-only configuration, no live data touched. Reverting the pins alone re-opens the float; reverting the workflow split alone re-opens the poisoning vector; each is independently revertible.

## 9. Verification performed

- rider-app full suite on pinned tree: 124 suites / 1,762 tests green (`yarn test --ci --coverage --forceExit`), `tsc --noEmit` clean.
- driver-app full suite on pinned tree: 1,311 tests green, `tsc --noEmit` clean (unchanged code).
- Both workflow files YAML-parse; `spinr-cicd-infra-reviewer` audit: SHA pinning, `cache-hit` wiring, key parity, and step ordering verified correct; no blockers.
- Poisoning mechanism reproduced/refuted empirically: clean frozen install healthy; in-range jest float reproduces the exact CI failure signature.

## 10. What was NOT verified

- The restore/save subactions were not executed against real GitHub infrastructure from here — first CI run on this branch is the live confirmation (reviewer noted the same).
- The precise job that saved the poisoned entry was not forensically identified (Actions cache history isn't inspectable retroactively); `expo install --check` is the strongest suspect and the only known mutator in the key-sharing set.
- No load/timing measurement of the extra explicit save step (expected ≈ the old POST save, just earlier).
