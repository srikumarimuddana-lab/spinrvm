# Change Impact & Risk Log — Push-triggered OTA updates silently went to `preview`, never `production`

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent session) |
| Surface(s) | CI/CD (`.github/workflows/eas-build.yml`) — affects rider-app and driver-app OTA delivery |
| Domain (Sentry tag) | n/a (pipeline config, no runtime code path) |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | Found during root-cause investigation of a user-reported live Google Play driver-app bug (green circle instead of car icon on the map; heat-pill/SOS button overlap) — see the same session's finding, no prior ACTION_ITEMS entry |

## 1. Issue / gap identified

`eas-build.yml`'s own header comment states: "On every push to main that touches rider-app / driver-app / shared: → Publishes an OTA JS-only update to the `production` channel for the affected app." The implementation did not do this. Both the `rider` and `driver` jobs computed:

```yaml
env:
  CHANNEL: ${{ github.event.inputs.profile || 'preview' }}
```

`github.event.inputs.*` is only populated when the workflow is triggered by `workflow_dispatch` (the manual dropdown run). On the `push` trigger — which fires on every merge to `main` touching `rider-app/**`, `driver-app/**`, or `shared/**` — `github.event.inputs.profile` is always empty/undefined, so `CHANNEL` always evaluated to the fallback, `'preview'`.

**Net effect: no push-triggered OTA update has ever reached the `production` channel.** Every JS-only fix merged to `main` — including this session's rider-app marker-parity fixes (route-continuity hint, GPS smoothing) and driver-app's own ring-freeze fix — was published only to `preview`, never to the channel that production-profile (Play Store / App Store) installs actually poll.

## 2. Root cause

The workflow was written assuming `github.event.inputs` degrades gracefully across trigger types. It does not — GitHub Actions only populates `event.inputs` for `workflow_dispatch` events; on every other trigger the field is simply absent, and the `||` fallback silently masks that instead of surfacing it. Nothing in the workflow validated that its own documented behavior (push → production) actually matched its implementation, and because the fallback always "succeeds" (never errors, never shows as a failed run), there was no CI signal that this was wrong — every run showed green.

## 3. Fix / remediation

Both jobs (`rider`, `driver`) now compute `CHANNEL` from a small shell conditional instead of a single GitHub Actions expression:

```bash
if [ -n "$INPUT_PROFILE" ]; then
  CHANNEL="$INPUT_PROFILE"        # workflow_dispatch: honor the operator's choice
elif [ "$EVENT_NAME" = "push" ]; then
  CHANNEL="production"            # push to main: matches the file's own documented intent
else
  CHANNEL="preview"                # defensive fallback for any other trigger
fi
```

`INPUT_PROFILE` and `EVENT_NAME` are passed in as separate `env:` entries (`${{ github.event.inputs.profile }}` and `${{ github.event_name }}`) rather than combined into one expression, so the branching logic is plain, testable shell rather than a nested GitHub Actions ternary.

## 4. Risk & impact on existing functionality

**Blast radius: CI/CD workflow file only — two `env:`/shell blocks in one workflow, no application code touched.**

- Grepped `eas-build.yml` for every other reader of `CHANNEL`/`EAS_ENV`: both are local to their own job step, consumed only by the immediately-following `eas update --branch "$CHANNEL" --environment "$EAS_ENV"` call. No other job or workflow reads this file's outputs.
- `eas-native-build.yml` (the separate native-binary build/submit workflow) is untouched — it already reads `github.event.inputs.profile` correctly because it is `workflow_dispatch`-only and never runs on `push`, so it never hit this bug.
- No change to `eas.json` (channel/branch/environment definitions), no change to the one-time `eas channel:edit <name> --branch <name>` linkage this file's own comment describes.
- No change to rider-app or driver-app source, no change to `runtimeVersion`, no native rebuild required or triggered.

Interactions considered:

- **OTA vs. native builds** — unaffected split. OTA remains JS-only; a native change still requires the separate `eas-native-build.yml` `workflow_dispatch` run, as documented at the top of this file.
- **`EXPO_PUBLIC_*` env inlining** — untouched. The existing `--environment` requirement and its `production`/`preview` mapping (`case "$CHANNEL" in production) EAS_ENV=production ;; *) EAS_ENV=preview ;; esac`) is preserved verbatim; only how `CHANNEL` itself is derived changed.
- **workflow_dispatch behavior** — unchanged. An operator-supplied `profile` input still wins outright, exactly as before; this fix only changes what happens when no input is present (the push case).

**The regression risk worth naming plainly: this is the first time push-triggered updates will actually reach the `production` channel.** Every future push to `main` touching `rider-app/**`, `driver-app/**`, or `shared/**` will now publish real JS to real production-channel installs on next app launch, where previously it silently did nothing to them. That is the fix working as intended, but it is a genuine behavior change on a path that, in practice, has never fired before — the first few production-channel publishes after this merges are the first real exercise of that code path and deserve a close watch (EAS dashboard `eas update:list --branch production`) rather than an assume-it-works merge.

## 5. User-experience effect

- **Rider/driver (indirect, no immediate visible change):** installs on `preview` (internal testers) see no change — they were already receiving these updates correctly. Installs on `production` (real Play Store / App Store users) will, starting with the next push to `main` after this merges, actually start receiving OTA JS updates for the first time via this pipeline. This is the intended fix for exactly the class of bug the user reported (a Play Store install stuck on old JS because the OTA channel was misrouted), but it does **not** retroactively patch an install already running an old **native** binary from before a given fix — see §8 for why a fresh native build is a separate, deliberately-not-bundled action here.
- **Internal admin/ops:** no dashboard-visible change; this is a CI pipeline behavior change only.
- No copy changes, no new notification.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-build.yml` | `rider` and `driver` jobs' "Publish OTA update" step: `CHANNEL` is now derived from `github.event.inputs.profile` (workflow_dispatch) → `production` (push) → `preview` (any other trigger, defensive) instead of `github.event.inputs.profile \|\| 'preview'` | The old expression always fell through to `preview` on `push`, contradicting the file's own documented behavior and meaning no push-triggered update ever reached `production` |
| `docs/change-log/2026-09-08-eas-ota-channel-push-defaults-to-preview.md` | New | Change Impact & Risk Log entry per CLAUDE.md (this is a bug fix, mandatory regardless of surface) |

## 7. Before / after

```yaml
# Before — both rider and driver jobs
env:
  CHANNEL: ${{ github.event.inputs.profile || 'preview' }}
  MSG: ${{ github.event.head_commit.message }}
run: |
  MESSAGE="${MSG:-manual update}"
  ...
```

```yaml
# After
env:
  INPUT_PROFILE: ${{ github.event.inputs.profile }}
  EVENT_NAME: ${{ github.event_name }}
  MSG: ${{ github.event.head_commit.message }}
run: |
  if [ -n "$INPUT_PROFILE" ]; then
    CHANNEL="$INPUT_PROFILE"
  elif [ "$EVENT_NAME" = "push" ]; then
    CHANNEL="production"
  else
    CHANNEL="preview"
  fi
  MESSAGE="${MSG:-manual update}"
  ...
```

Concrete scenario, taken from a real job log (run `34169054931`, our own driver-app ring-freeze-fix commit `b9e8d6a`, a `push` to `main`):

| | Before | After |
|---|---|---|
| Trigger | `push` to `main` (`driver-app/**` changed) | same |
| `github.event.inputs.profile` | (empty — push events have no `inputs`) | (empty — unchanged) |
| `CHANNEL` computed | `preview` | `production` |
| `eas update --branch` | `preview` | `production` |
| Who receives the update | Internal testers on the `preview` channel only | Real Play Store / App Store installs on the `production` channel |

## 8. Rollback plan

No migration, no schema change, no live-data mutation — this is a two-block edit to one workflow file's shell logic. A `git revert` is a complete rollback, restoring the (broken, but previously "working" in the sense of not erroring) always-`preview` behavior.

No feature flag: this is a CI workflow, not application code, so there is no `app_settings` equivalent to gate it behind. If the new push→production routing ever needs to be paused without a revert, the fastest lever is `workflow_dispatch`-only operation — an admin can temporarily disable the `push:` trigger block in this file, or simply not merge to `main` until confidence is established — but the straightforward rollback is the `git revert`.

**Deliberately out of scope for this change:**
- **No native build was triggered.** This fix only restores OTA (JS-only) delivery to the `production` channel; it does nothing for an install already running an old **native** binary whose JS bundle predates the OTA `runtimeVersion` the fix ships under, and nothing for the specific user report's Play Store install, whose provenance this session's investigation could not fully establish from CI (no `eas-native-build.yml` run in this repo's tracked history — 26 runs spanning 2026-07-15 through 2026-09-08 — was ever an Android + `production`-profile auto-submit; every Android run found was `preview`-profile with no `--auto-submit`). That means the live Google Play binary was almost certainly submitted manually, outside CI, at an unknown point in time, and this fix cannot patch it retroactively.
- **No fresh Android production build/submission was triggered**, per the user's explicit choice this turn to land only the CI fix now.

## 9. Verification performed

- [x] Blast-radius grep performed — confirmed `CHANNEL`/`EAS_ENV` are local to each job's own step; confirmed `eas-native-build.yml` is unaffected (`workflow_dispatch`-only, never hits the `push` path this bug lived in).
- [x] Root cause confirmed against a real job log, not just read from source: run `34169054931` (this session's own driver-app ring-freeze-fix push) shows `CHANNEL: preview` despite being a `push` trigger.
- [x] Cross-checked 26 `eas-native-build.yml` runs (2026-07-15 through 2026-09-08) via GitHub Actions job logs to establish that this repo's CI has never auto-submitted an Android production build — relevant context for why this fix alone does not resolve the user's live Play Store report, stated explicitly in §8 rather than implied.
- [x] `python3 -c "import yaml; yaml.safe_load(...)"` — YAML parses cleanly.
- [x] Reviewed against the file's own header comment — the fix now matches documented intent exactly.
- [ ] **Not exercised against a real GitHub Actions run** — see below.

## What was NOT verified

**This fix has not actually been exercised by a real push-triggered workflow run.** The shell conditional was reasoned through by hand and validated for YAML syntax only; the first real confirmation that `CHANNEL` resolves to `production` on an actual `push` event, and that `eas update --branch production --environment production` succeeds end-to-end (channel-to-branch linkage, EAS environment variables loading correctly), will be the next push to `main` that touches `rider-app/**`, `driver-app/**`, or `shared/**` after this merges. That run's log should be checked once it fires.

Also not verified: whether the `production` branch already has update history from any prior manual `workflow_dispatch` run (if so, this is additive; if the `production` channel has never been published to at all, this is the very first publish to it and is worth confirming the channel-to-branch link — `eas channel:edit production --branch production` — was actually set up, per this file's own comment describing it as a "one-time" manual step performed outside this repo).
