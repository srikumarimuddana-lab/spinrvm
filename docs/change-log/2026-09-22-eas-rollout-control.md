# Change Impact & Risk Log — dispatchable control for stuck Expo rollouts

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (user: "i want production OTA updates") |
| Surface(s) | rider-app / driver-app (release pipeline) |
| Domain (Sentry tag) | rides / drivers |
| PR / commit link | branch `claude/brave-clarke-wo8pv7` |
| Related issue or gap ID | follow-on to `docs/change-log/2026-09-22-ota-production-dispatch-only.md` (PR #5704) |

## 1. Issue / gap identified

PR #5704 stopped production OTA publishing from failing on every push, but it did not make production OTA *work*. Production publishing is still blocked by Expo rollouts that have been open at 10% since 2026-09-20, and nothing in the repo can end one — `eas update:edit` / `eas update:revert-update-rollout` need an interactive session or an explicit `--group`, and the only path to either was a local `eas` login.

## 2. Root cause

Ending a rollout is a human decision (promote the canary to everyone, or roll it back), so it was never automated. That is correct — but it left the repo with no operator path at all, so the decision could only be made by someone with eas CLI access on their own machine.

**A second finding, corrected here:** the blocking rollouts are *four*, not two. Both apps set a per-platform `runtimeVersion` — top-level is iOS, the `android:` block overrides it:

| App | iOS | Android |
|---|---|---|
| rider-app | 2.1.0 | 2.2.0 |
| driver-app | 2.7.0 | 2.8.0 |

Expo scopes a rollout to (branch, **platform**, runtime version), so ios and android hold separate slots on the same branch. Every failure log names only 2.1.0 / 2.7.0 — the iOS values — because the publish loop runs `ios:production` first and `set -e` aborts the job there, so `android:production` has not been attempted since the failures began. The Android rollouts created by the one successful post-C7 run are therefore almost certainly still open and unreported. This is inferred from the loop order and the runtime-version config, **not** observed — see §10.

## 3. Fix / remediation

New `workflow_dispatch` workflow `.github/workflows/eas-rollout-control.yml`:

- `action=list` — read-only. Prints raw `eas update:list --json` plus a summary of any rollout below 100%.
- `action=ramp-to-100` — sets one named group's rollout to 100%.
- `action=revert` — reverts one named group's rollout (Expo republishes the previous update).

Both mutating actions **require an explicit `group_id`** and refuse to run without one. The workflow never selects a group itself: eas-cli's `--json` layout carries no compatibility promise, and picking a group by parsing it would risk mutating the wrong update on real handsets. `group_id` is validated against a UUID pattern before it reaches `eas`, and a group the app does not own exits 0 with a notice (expected under `app=both`, where the sibling job owns it) rather than failing the run.

The summary parser walks the whole JSON document instead of indexing a fixed path, so an eas-cli shape change degrades it to "could not parse" rather than silently reporting nothing. Its negative result is deliberately **not** phrased as "you are not blocked": absence of a `rolloutPercentage` field is indistinguishable from a real 100%, and a false all-clear would send an operator straight back into a failing production publish.

**Alternatives considered:** (a) automating rollout-ending inside `eas-ota-publish` so production self-unblocks — rejected, it is the auto-promote behaviour explicitly declined when the dispatch-only option was chosen, and it would promote an unverified canary to every handset without a human. (b) Pointing the operator at expo.dev's dashboard — still valid and mentioned to the user, but it leaves no audit trail in the repo and does not enumerate all four groups in one place. (c) Doing nothing and requiring a local eas login — the status quo that left this stuck for two days.

## 4. Risk & impact on existing functionality

- **Blast radius: additive.** A new workflow file with a `workflow_dispatch`-only trigger. It shares no code with `eas-build.yml` or `.github/actions/eas-ota-publish` — it does not import, call, or modify them, and nothing calls it. Removing the file restores the prior state exactly.
- **It can change what production devices run** — that is its purpose. Both mutations are gated behind a manual dispatch plus an explicit UUID the operator must copy from a prior `list` run. There is no path where a dispatch mutates a group the operator did not name.
- **`concurrency: eas-rollout-control` with `cancel-in-progress: false`** so two rollout mutations can never race.
- **No new secret.** Uses the existing `EXPO_TOKEN`, already consumed by `eas-build.yml`, `ci.yml`, and `android-production-internal.yml`.
- **`EXPO_TOKEN` reach is unchanged but worth stating:** this workflow can mutate Expo rollout state, which previously required a local login. Anyone who can dispatch a workflow in this repo can now promote or revert an update. That is the same population that can already dispatch `EAS Mobile Update` with `profile=production`, so it grants no new capability to a new group of people.
- **Not a required check, not on any critical path.** It cannot make CI red.

## 5. User-experience effect

- **Riders / drivers:** none from merging this. When an operator *runs* it, `ramp-to-100` promotes that update to every device on the branch and runtime version; `revert` returns them to the previous update. Both are stated in the step output before the command runs.
- **Internal:** ending a stuck rollout no longer needs a local eas login.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-rollout-control.yml` | new dispatch-only workflow: list / ramp-to-100 / revert | no operator path existed to clear the state blocking production OTA |
| `docs/change-log/2026-09-22-eas-rollout-control.md` | this file | |

## 7. Before / after

Before — the only way to clear a stuck rollout:

```sh
# on an operator's own machine, with a local Expo login
cd rider-app && eas update:list --branch production   # find the group
eas update:edit <GROUP_ID> --rollout-percentage 100 --non-interactive
```

After — same operations, from Actions, with the group ID still supplied by a human:

```
Actions → EAS Rollout Control → Run workflow
  app=both  branch=production  action=list            # read the group IDs
  app=both  branch=production  action=ramp-to-100  group_id=<paste>
```

## 8. Rollback plan

Delete the file (or `git revert`). It is dispatch-only and nothing references it, so removal is immediate and total — no deploy, no migration, no live data. Note that an Expo rollout **already ended** by a run of this workflow is not undone by reverting the workflow: promoting to 100% is undone with `eas update:revert-update-rollout`, and a revert is undone by republishing. Those are Expo-side operations, which is exactly why both actions print what they are about to do before doing it.

## 9. Verification performed

- YAML parses (`yaml.safe_load`); `actionlint` v1.7.7 **clean**.
- Nested heredoc terminator confirmed to land at column 0 after YAML block-scalar de-indentation (a `PY` left indented would have silently swallowed the rest of the script); `bash -n` clean.
- The step's shell body was extracted and **executed** against a stubbed `eas` across all eight paths:

  | Scenario | Result |
  |---|---|
  | `list`, stuck rollout nested two levels deep | found and reported; exit 0 |
  | `list`, everything at 100% | prints the qualified "not proof you are unblocked" text; exit 0 |
  | `list`, unparseable JSON | `::warning::`, raw output still shown; exit 0 |
  | `ramp-to-100`, no `group_id` | `::error::`, **exit 1** |
  | `ramp-to-100`, `group_id="10%; rm -rf /"` | rejected by the UUID guard, **exit 1** — never reaches `eas` |
  | `ramp-to-100`, group owned by the sibling app | `::notice::` and skip; exit 0 |
  | `ramp-to-100`, valid and owned | `eas update:edit <id> --rollout-percentage 100 --non-interactive` |
  | `revert`, valid and owned | `eas update:revert-update-rollout --group <id> --non-interactive --message …` |

- `eas update:list` / `update:edit` / `update:revert-update-rollout` / `update:view` flag spellings re-checked against the EAS CLI reference for the pinned 24.7.0.

## 10. What was NOT verified

- **No workflow run was executed** — no Actions-dispatch access in this session, and no Expo credential. The `eas` stub is not Expo.
- **The `eas update:list --json` shape is unverified.** This is why the raw output is printed first and the summary is best-effort; the mutating actions do not depend on parsing it at all.
- **The four-stuck-rollouts claim is inference, not observation.** Only the two iOS rollouts (rider 2.1.0, driver 2.7.0) appear in any log. The Android ones (2.2.0, 2.8.0) are deduced from the per-platform `runtimeVersion` config plus the loop aborting on the iOS pair before reaching Android. An `action=list` run is what will confirm or refute it.
- **The matrix ternary** (`app == 'both' && fromJSON(...) || fromJSON(format(...))`) is a standard GitHub Actions idiom and passes `actionlint`, but was not observed expanding on a real runner. If it ever misbehaved, `working-directory` would fail with a missing-directory error — loud, not silent.
- No app code changed; no build, device, or visual verification applies.
