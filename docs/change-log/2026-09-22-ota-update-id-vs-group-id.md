# Change Impact & Risk Log — the id Expo reports is an update id, not a group id

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (user: "Rollout failed check it") |
| Surface(s) | rider-app / driver-app (release pipeline) |
| Domain (Sentry tag) | rides / drivers |
| Related | fixes a defect shipped in PR #5704 and PR #5705 |

## 1. Issue / gap identified

The first real run of `EAS Rollout Control` (run 1, job `rollout (rider-app)`) failed:

```
Promoting 01a0c190-41d0-718e-ad47-a84349da13fa to 100% on branch production
Could not find any updates with group ID: "01a0c190-41d0-718e-ad47-a84349da13fa"
    Error: update:edit command failed.
```

The id came straight from the publish failure's own remediation text, so the workflow was telling operators to use an id that the command it named cannot accept.

## 2. Root cause

**An Expo update id and an update *group* id are different ids.** A rollout-refused publish reports `Update <id> is currently rolling out to N%` — a *platform-specific update* id. But `eas update:edit` takes an update GROUP id, and `eas update:revert-update-rollout --group` likewise. Passing the reported id to either fails.

Two compounding mistakes, both mine:

1. **PR #5704** parsed the id out of Expo's message and printed it as a ready-to-run `eas update:edit <id>` argument. That command could never work.
2. **PR #5705** carried the same assumption into `EAS Rollout Control`, and its guard *hid* the error rather than catching it: the guard used `eas update:view`, whose documented argument is "the ID of an update group, **or the ID of a platform-specific update**". It accepts both, so it validated an id that `update:edit` then rejected. The check could not have caught this class of bug — it was the wrong check.

The stub tests for both PRs asserted the id was *extracted* correctly, never that the resulting command was *accepted by eas*. Correct extraction of the wrong kind of id looks identical to success at that level.

## 3. Fix / remediation

- `eas-rollout-control.yml` now **resolves** whatever id it is given to a group id before mutating: it reads `eas update:view <id> --json` and walks the document for a `group` field, then passes that to `update:edit` / `revert-update-rollout`. When the resolved group differs from the input it says so in a `::notice::`. If no group can be read it fails with a message pointing at `action=list`, rather than guessing.
- `eas-ota-publish/action.yml`'s remediation text no longer prints a command that cannot work. It now states that Expo named an UPDATE id, that the commands need that update's GROUP id, and gives both routes: the `EAS Rollout Control` workflow (which resolves it), or `eas update:view <id> --json` locally to read `.group` first.
- The `group_id` input description now says either kind of id is accepted.

## 4. Risk & impact on existing functionality

- **Blast radius:** the same two files as before; `eas-ota-publish` is consumed only by the four jobs in `eas-build.yml`, and `eas-rollout-control.yml` has no consumers at all.
- **The publish success path is untouched** — the change is confined to the `grep -q 'rollout is in progress'` branch, which only runs after a publish has already failed. Re-verified by stub run: happy path still performs one source-map upload per platform and exits 0.
- **A near-miss worth recording:** the first draft of the corrected remediation text used `'"'"'` single-quote escaping *inside a double-quoted* shell string, where a single quote is already literal. That broke the string and left `(action=ramp-to-100 or revert)` unquoted — a bash syntax error at `(`. Had it shipped, **every production publish would have died with a syntax error instead of publishing**, a far worse outcome than the bug being fixed. Caught by `bash -n` on the extracted body before commit, not by reading.
- `update:view` is read-only, so the added resolution step cannot itself change Expo state.

## 5. User-experience effect

None for riders or drivers. For operators: the printed commands now work, and `EAS Rollout Control` accepts the id from the error message directly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-rollout-control.yml` | resolve input id → group id via `update:view` before `update:edit`/`revert`; input description accepts both id kinds | the mutation was being given an id its command rejects |
| `.github/actions/eas-ota-publish/action.yml` | remediation text states which id kind Expo reported and how to get the group id | it printed a command that could not work |
| `docs/change-log/2026-09-22-ota-update-id-vs-group-id.md` | this file | |

## 7. Before / after

```bash
# Before — the reported update id handed straight to update:edit
eas update:edit "$GROUP_ID" --rollout-percentage 100 --non-interactive
#   → Could not find any updates with group ID: "01a0c190-…"
```

```bash
# After — resolve to the group first
VIEW_JSON=$(eas update:view "$ID_IN" --json)
RESOLVED=$(… walk the JSON for a UUID-shaped "group" …)
eas update:edit "$RESOLVED" --rollout-percentage 100 --non-interactive
```

## 8. Rollback plan

`git revert`. Both files are CI configuration; no deploy, migration, or live data. Reverting restores the broken remediation text and the unresolved id, so revert only if this fix itself misbehaves.

## 9. Verification performed

- YAML parses for both files; `actionlint` v1.7.7 clean on the workflow.
- `bash -n` on both extracted shell bodies — this is what caught the quoting defect in §4.
- The embedded Python's indentation was verified to survive YAML block-scalar de-indentation by **executing** it, not by reading it.
- `eas-rollout-control` step body executed against a stubbed `eas` whose `update:view` returns a `group` **different** from the id passed in (the exact real-world condition), for three cases: `ramp-to-100` → resolved, notice printed, `update:edit <GROUP>` called; `revert` → same resolution, `revert-update-rollout --group <GROUP>`; app does not own the id → notice, exit 0.
- `eas-ota-publish` body re-executed for all three publish outcomes: success → 2 publishes, 2 per-platform source-map uploads, exit 0; rollout-refused → corrected six-line remediation, exit 1; unrelated failure → exit 1 with no rollout remediation.

## 10. What was NOT verified

- **No workflow run was executed** (no Actions-dispatch access — confirmed again this session by a `403 Resource not accessible by integration` on `run_workflow`), and no Expo credential exists here.
- **`eas update:view --json`'s exact shape is still unverified.** The resolver walks the whole document for a UUID-shaped `group` rather than indexing a fixed path, and fails loudly pointing at `action=list` if it finds none — but that a `group` field exists in that output at all is an assumption this fix rests on. The next real run is what proves it.
- Whether the Android rollouts (rider 2.2.0, driver 2.8.0) are also open remains unobserved; the publish loop still aborts on iOS before reaching Android.
