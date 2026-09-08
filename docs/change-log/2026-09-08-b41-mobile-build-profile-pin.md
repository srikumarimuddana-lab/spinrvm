# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — `.github/workflows/ci.yml`, `rider-app/eas.json`, `driver-app/eas.json`) |
| Domain (Sentry tag) | admin (closest fit — this is a deploy-pipeline/CI change, not an application-code domain) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` B41 |

## 1. Issue / gap identified

`ci.yml`'s `mobile-build` job (rider+driver-app native EAS builds, gated on `[build]` in a `main`-branch commit message) omitted `--profile` on all 4 `eas build` calls. Which profile actually shipped from a real trigger was previously unverified — the item was deliberately held back pending confirmation, not shipped on a guess.

## 2. Root cause

The job was written without an explicit `--profile` flag. Nobody had confirmed what EAS CLI actually resolves to when `--profile` is omitted, so pinning one (even the seemingly-obvious `production`) was correctly held back until that was verified rather than assumed.

## 3. Fix / remediation

1. **Confirmed EAS CLI's own documented default**: when `--profile` is omitted, it resolves to the profile literally named `production`, if one exists (both apps have one). This means every prior successful run of this job was already building `production` — the original "held back" fix's guess was correct, just previously unconfirmed. Source: [Configure EAS Build with eas.json](https://docs.expo.dev/build/eas-json/) and a corroborating community write-up, both retrieved via WebSearch (direct `docs.expo.dev` fetch is blocked by this session's network proxy). Pinned `--profile production` explicitly on all 4 `eas build` calls, replacing the implicit default.
2. **Found and fixed a real latent bug in `eas.json` along the way**: both apps' `preview` build profile had no explicit `distribution` field. EAS defaults `distribution` to `"store"` when omitted (same source) — meaning any build using `preview` (this job never used it, but `test-env.yml`'s `build-mobile-test` job already does, unconditionally, for `staging`) would have silently produced store-distribution-shaped artifacts instead of an internal one. Added `"distribution": "internal"` explicitly to both apps' `preview` profile.
3. **A first draft of this change also tried to expand `mobile-build` to run on `staging`** (`main` → `production`, `staging` → `preview`, per the product-owner-directed target design in `ACTION_ITEMS.md` B41's 2026-09-07 interview) via a branch-conditional profile-resolution step. A `spinr-cicd-infra-reviewer` pass caught a real blocker before this was committed: **`test-env.yml`'s `build-mobile-test` job already triggers on the identical condition** (`github.ref == 'refs/heads/staging' && contains(commit message, '[build]')`) and already builds all 4 artifacts with `--profile preview`. Adding the same trigger to `mobile-build` would have doubled every staging build — 8 real, billed EAS builds instead of 4 per push — a genuine cost bug, not a style nit. **Reverted the `staging` expansion entirely.** `mobile-build` stays `main`-only, exactly as before, just with `--profile production` now explicit instead of implicit; `staging`'s preview builds continue to be owned solely by `test-env.yml`, which was already correct and is untouched by this change (it benefits from the `distribution: internal` fix in item 2 above, though — see Risk section).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `mobile-build`'s 4 build steps and two `eas.json` files' `preview` profile.** Grepped every `eas build --platform` call site in `.github/workflows/`: `eas-native-build.yml` (manual `workflow_dispatch`, profile is a user-supplied input — untouched), `maestro-e2e.yml` (uses its own `test` profile — untouched), `deploy-driver-play-testing.yml` (explicitly pins `--profile android-auto` — untouched), `test-env.yml`'s `build-mobile-test`/`ota-update-test` jobs (already explicitly use `--profile preview` — trigger and profile choice untouched by this change, but see next bullet).
- **`test-env.yml`'s existing `--profile preview` builds are an indirect consumer of the `eas.json` fix**: those staging builds will now resolve `distribution: internal` explicitly instead of implicitly defaulting to `store`. This is the correct, intended outcome — nothing on `staging` should ever silently resolve to a store-distribution build — but it's worth stating plainly since it's a behavior change to an *already-shipped, already-running* job, not just this PR's own new code.
- **Could this regress a working flow?** `mobile-build`'s `main` behavior is unchanged in practice — it was already resolving to `production` by CLI default, this just makes it explicit. `test-env.yml`'s `staging` builds keep building `preview` exactly as before; only the distribution shape of that already-existing artifact changes (store → internal), which is a fix, not a new risk.
- No interaction with `backend/core/lifespan.py` background loops, the ride state machine, or money/wallet deltas — this is CI/CD config only.

## 5. User-experience effect

None directly — this is an internal build pipeline, no rider/driver/admin-facing surface. Indirect effect: `staging` `[build]` pushes' existing EAS artifacts change from (previously-silent) store distribution to internal distribution — relevant only to whoever installs/tests those builds, not to any live app user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | `mobile-build` job: added `--profile production` to all 4 `eas build` calls; trigger condition (`main`-only) left unchanged after a reviewer-caught staging-duplication issue was reverted | Close B41 — pin an explicit, verified profile instead of relying on an unstated implicit default |
| `rider-app/eas.json` | Added `"distribution": "internal"` to the `preview` profile | Without it, `preview` defaults to `store` distribution — wrong for a staging/internal build, and already in live use by `test-env.yml` |
| `driver-app/eas.json` | Same as above | Same reason |

## 7. Before / after

```yaml
# Before
- name: Build Android (Rider)
  working-directory: rider-app
  run: eas build --platform android --non-interactive
```

```yaml
# After
- name: Build Android (Rider)
  working-directory: rider-app
  run: eas build --platform android --profile production --non-interactive
```

(The job's `if:` trigger condition is unchanged — `main`-only, same as before this PR.)

## 8. Rollback plan

`git-revert`-safe. No live data or already-triggered build is affected by this change itself — it only changes what a *future* `[build]`-tagged push resolves to, and only makes explicit what was already happening implicitly. Reverting the commit restores the prior (implicit-`production`) behavior exactly. If a bad build is somehow triggered before a revert lands, the remediation is deleting/ignoring that EAS build artifact directly in the EAS dashboard — not a data-level concern, no Stripe/wallet/ride-state involved.

## 9. Verification performed

- [x] YAML syntax validated (`yaml.safe_load` against the full file after every edit, including the post-review revert; no `actionlint` binary available in this session — see "What was NOT verified" below)
- [x] Both `eas.json` files validated as well-formed JSON after the edit
- [x] Blast-radius grep performed: all `eas build --platform` call sites across `.github/workflows/*.yml`, and all references to the `preview` profile — this is exactly what surfaced the `test-env.yml` collision before merge
- [x] `spinr-cicd-infra-reviewer` review requested for the diff — found one real BLOCKER (staging-trigger collision with `test-env.yml`, described above) and one WARNING (an `else`-branch fragility in the profile-resolution step, which was made moot by removing that step entirely once `staging` was dropped from scope). Blocker fixed, re-validated.
- [ ] Manual repro steps followed in staging — not possible from this session (no EAS/Expo credentials or dashboard access)
- [ ] Feature-flagged — not applicable; this is CI config, not a runtime code path with a flag mechanism

## What was NOT verified

- **No real EAS build was triggered to confirm the fix end-to-end.** This session has no `EXPO_TOKEN` value, no `eas` CLI, and no Expo/EAS connector access (confirmed earlier — see `ACTION_ITEMS.md` B41's history). Everything here is verified via documentation (EAS's own default-profile and default-distribution behavior), static analysis (grep, YAML/JSON parsing), and a dedicated CI/CD-focused subagent review — not a live run.
- **`actionlint` (the stricter GitHub-Actions-specific linter this repo used to catch a real bug in C76) was not available in this session** — only generic YAML parsing was performed.
- **The `eas.json` `distribution: internal` addition was not independently confirmed against a real EAS build** — only against EAS's documented default-resolution behavior for an *omitted* field, cited above.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data entanglement)
- [x] Blast radius is stated, not assumed (grepped every other EAS-build call site and every `preview`-profile reference — this is what caught the real collision before merge)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (User-experience effect section states the one indirect effect on `test-env.yml`'s already-running builds plainly)
