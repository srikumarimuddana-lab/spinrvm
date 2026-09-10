# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (agent session) |
| Surface(s) | CI/CD (infra) — `.github/workflows/ci.yml`, `test-env.yml`, `eas-build.yml`, `eas-native-build.yml`, `submit-play-store.yml`, `maestro-e2e.yml`, `deploy-driver-play-testing.yml` |
| Domain (Sentry tag) | n/a (infra/CI, not application code) |
| PR / commit link | commit `968a02a`, branch `claude/bump-expo-github-action-v9` |
| Related issue or gap ID | Spotted as a non-blocking warning in a successful build log ("Node.js 20 is deprecated... forced to run on Node.js 24"); not previously tracked in `ACTION_ITEMS.md` |
| Review | `spinr-cicd-infra-reviewer` — verdict SAFE TO MERGE, no blockers (see §9) |

## 1. Issue / gap identified

Every workflow step using `expo/expo-github-action` (pinned at v8.2.1) logs a GitHub-generated warning: its `action.yml` still declares `using: node20`, and GitHub is deprecating Node 20 as an Actions runtime. GitHub currently auto-upgrades the runtime and the build still completes, but the underlying runtime is being fully retired.

## 2. Root cause

Not a bug — the pinned action version simply predates the ecosystem-wide Node 20→24 migration. Confirmed by diffing the action's own `action.yml` between the pinned tag (`v8.2.1`) and the latest (`v9.0.0`): the only difference is `using: node20` → `using: node24`.

## 3. Fix / remediation

Bumped the SHA pin from `c7b66a9c327a43a8fa7c0158e7f30d6040d2481e` (v8.2.1) to `eab7a230208c952974db8c3245cfd78402c7b385` (v9.0.0) at all 8 call sites across the 7 files listed above, in one commit (a single indivisible version bump — splitting it per-file would leave some workflows on v8 and others on v9 with no benefit).

Diligence performed before bumping a release the vendor itself flags as containing a breaking change:
- `git log --oneline v8.2.1..v9.0.0` on the upstream repo (a ~2-year commit range) — the *only* conventional-commit-tagged breaking change (`chore!:`) in the entire range is `94ad6f36 chore!: update tooling and actions to Node 24 (#354)`, i.e. exactly this bump. Every other commit in the range is the `continuous-deploy-fingerprint` sub-action, which nothing in this repo uses.
- Diffed `action.yml` directly between the two tags: one line changed. The `eas-version` and `token` inputs — the only two inputs any workflow in this repo passes — are present, unchanged, and non-deprecated in v9.0.0.

## 4. Risk & impact on existing functionality

- **Blast radius: 7 CI/CD workflow files, 8 call sites, confirmed exhaustive.** `grep -rn "expo-github-action"` across `.github/workflows/` before and after the change confirms no leftover reference to the old SHA anywhere in a live workflow file (the SHA does still appear in 3 historical `docs/change-log/*.md` entries — correctly untouched, they're point-in-time records). No application code (`backend`/`rider-app`/`driver-app`/`admin-dashboard`/`shared`) touched.
- No interaction with the separate, earlier `docs/change-log/2026-08-28-eas-mobile-update-node-version-fix.md` fix (which bumped `actions/setup-node`'s `node-version` to 22 ahead of these same steps, for a different reason — the Node version on `PATH` for the shell's own `eas-cli`/`yarn` install). That fix controls the shell environment later steps run in; this bump controls only the runtime GitHub Actions uses to execute the action's own bundled JS. Independently reviewed and confirmed non-conflicting.
- Worst case if v9.0.0 has an undiscovered incompatibility: the same class of failure already described in the deprecation warning (a tooling-install step fails), not a new failure mode, and not something that can reach a live rider/driver.

## 5. User-experience effect

None. Pure CI/CD tooling change — no rider, driver, corporate-admin, or internal-admin facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | `expo-github-action` pin v8.2.1→v9.0.0 (1 call site) | Clear Node 20 deprecation warning |
| `.github/workflows/test-env.yml` | Same (2 call sites) | Same |
| `.github/workflows/eas-build.yml` | Same (2 call sites) | Same |
| `.github/workflows/eas-native-build.yml` | Same (1 call site) | Same |
| `.github/workflows/submit-play-store.yml` | Same (1 call site) | Same |
| `.github/workflows/maestro-e2e.yml` | Same (1 call site) | Same |
| `.github/workflows/deploy-driver-play-testing.yml` | Same (1 call site) | Same |

## 7. Before / after

```yaml
# Before
- uses: expo/expo-github-action@c7b66a9c327a43a8fa7c0158e7f30d6040d2481e # v8
  with:
    eas-version: latest
    token: ${{ secrets.EXPO_TOKEN }}

# After
- uses: expo/expo-github-action@eab7a230208c952974db8c3245cfd78402c7b385 # v9
  with:
    eas-version: latest
    token: ${{ secrets.EXPO_TOKEN }}
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure CI config change, no data, migration, or runtime component. Reverting restores the old (still-functional-today) pin.

## 9. Verification performed

- [x] Diffed `action.yml` between the pinned tag and the target tag directly (via a shallow clone of the upstream repo) — confirmed the only change is the runtime declaration line.
- [x] Confirmed via `git log --oneline v8.2.1..v9.0.0` that the Node 24 bump is the only breaking-change-flagged commit in the whole range.
- [x] `grep -rn` confirmed all 8 call sites updated and zero stray references to the old SHA remain in any live workflow file.
- [x] `python3 -c "import yaml; yaml.safe_load(...)"` on all 7 edited files — valid YAML.
- [x] Independent adversarial review (`spinr-cicd-infra-reviewer`) — verdict SAFE TO MERGE, no blockers; confirmed no interaction with the earlier Node-22 `setup-node` fix, confirmed exhaustive blast radius, confirmed all files still parse as valid YAML.

## What was NOT verified

- **No real GitHub Actions run of the 4 gated files** (`eas-build.yml`, `eas-native-build.yml`, `submit-play-store.yml`, `deploy-driver-play-testing.yml`) — all are `workflow_dispatch`-only or push-path/commit-message-gated, so opening this as a PR against `main` does not exercise them. This is the identical disclosure `docs/change-log/2026-08-28-eas-mobile-update-node-version-fix.md` §9 made for the same four files under a different fix — this session has no GitHub Actions dispatch permission (confirmed via a 403 on an unrelated attempt this session). A human with dispatch access running one of these post-merge is the real confirmation.
- `ci.yml`'s `mobile-build` job and `maestro-e2e.yml` are gated behind a `[build]` commit-message flag / PR label respectively, so they are also unlikely to run automatically from this PR alone — only `pull_request`-triggered jobs elsewhere in `ci.yml` exercise anything.
- Did not independently re-verify the SHA↔tag mapping a second time beyond the initial `git ls-remote` — a single clean source, not cross-checked against a second registry.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — grepped for every call site before and after.
- [x] No silent behavior change — this changes only which runtime executes an existing, unchanged CI step; the two inputs every workflow passes are unchanged.
