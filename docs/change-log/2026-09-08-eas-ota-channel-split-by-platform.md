# Change Impact & Risk Log — OTA push publishes routed both platforms to the same channel, but Android's real store builds are never on that channel

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent session) |
| Surface(s) | CI/CD (`.github/workflows/eas-build.yml`) — affects rider-app and driver-app OTA delivery |
| Domain (Sentry tag) | n/a (pipeline config, no runtime code path) |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | Correction to the same-day fix in `docs/change-log/2026-09-08-eas-ota-channel-push-defaults-to-preview.md` / merged as `47f136f` (#5126) — found to be incomplete after the user supplied real Expo dashboard build-history screenshots |

## 1. Issue / gap identified

Earlier the same day, `#5126` fixed `eas-build.yml` so a `push` to `main` publishes OTA updates to the `production` channel (previously it silently always went to `preview`) — for **both** platforms, via one unscoped `eas update --branch "$CHANNEL" ...` call.

That fix assumed "production channel" is where real store installs are, for both platforms uniformly. The user then supplied two real Expo dashboard screenshots of this project's own build history, which show that assumption is wrong for Android:

- **driver-app**: "Android Play Store build 2.0.0 (24)", submitted ~9h before the screenshot, built via the `android-auto` profile, **channel `preview`**.
- **rider-app**: "Android Play Store build 2.0.0 (14)" and "(13)", both built via the `preview` profile, **channel `preview`** — even though `rider-app/eas.json` also defines a `production` profile with `channel: production`, no real Android submission has ever actually used it.
- Both apps' real **iOS** App Store submissions ("iOS App Store build 2.0.0 (29)" driver, "(25)" rider) *do* use the `production` profile/channel.

So `#5126`'s fix routed push-triggered updates correctly for iOS, but for Android it routed them to a channel (`production`) that no real Android install has ever been on — meaning Android testers, who are genuinely on the `preview` channel, would have stopped receiving push-triggered OTA updates entirely once `#5126` merged, the opposite of that fix's intent.

## 2. Root cause

`eas update` (no `--platform` flag) publishes to one branch/channel for all platforms in a single call. The workflow treated "channel" as one value per run, but this repo's real build history shows the two platforms are **not** on the same channel: driver-app's Android submissions use a distinct `android-auto` build profile (`driver-app/eas.json`) that is hardcoded to `channel: preview`, separate from the `production` profile iOS uses; rider-app's Android submissions have, in practice, always used the `preview` profile even though a `production` profile exists. Neither of these was verified against the actual Expo dashboard before `#5126` shipped — it was inferred from `eas.json`'s profile *names* ("production" implies production-channel installs) rather than confirmed against which profile real submissions actually used.

## 3. Fix / remediation

Both `rider` and `driver` jobs now compute **two** channels, `CHANNEL_IOS` and `CHANNEL_ANDROID`, and publish with two separate `--platform`-scoped `eas update` calls instead of one unscoped call:

- `workflow_dispatch`: both platforms keep using the operator's chosen `profile` input, unchanged — a manual dispatch is a deliberate, explicit choice, not the automatic default this bug is about.
- `push`: `CHANNEL_IOS=production` (matches real iOS submissions), `CHANNEL_ANDROID=preview` (matches real Android submissions for both apps).
- Any other trigger: both default to `preview`, unchanged from before.

## 4. Risk & impact on existing functionality

**Blast radius: same as `#5126` — two workflow-file steps, no application code.** `CHANNEL_IOS`/`CHANNEL_ANDROID`/`EAS_ENV` remain local to their own job step. `eas-native-build.yml` is untouched.

Interactions considered:

- **iOS behavior is unchanged from `#5126`** — still routes to `production` on push. This commit only changes Android's push-triggered destination, correcting it back to `preview` (matching where real Android testers actually are) instead of leaving it wrongly pointed at `production` (where no real Android install is).
- **`--environment`** is still always explicit per the existing hazard documented in the file (`EXPO_PUBLIC_*` inlining) — unchanged, just now resolved once per platform instead of once per run.
- **Two publishes instead of one** doubles the `eas update` invocation count per job run (one per platform instead of one for both). Each is independent and non-interactive; a failure in one does not block the other since they're sequential shell commands in the same step — if the iOS publish fails, the script would still attempt Android (bash does not `set -e` here, matching the pre-existing script's own lack of `set -e`). This is a deliberate carry-over of the existing script's behavior, not a new gap introduced here — worth revisiting separately if partial-publish failures become an actual production problem.

**Regression risk, stated plainly**: if this repo's real build practice changes — e.g. Android production-track submissions start using the `production` profile instead of `android-auto`/`preview` — this hardcoded split will go stale the same way the original single-channel assumption did. Nothing in this workflow verifies the split against the live Expo dashboard at run time; it's a snapshot of what the build history showed today. Whoever changes which build profile ships to Android production next should double check this file.

## 5. User-experience effect

- **Android testers (rider + driver)**: unaffected relative to before `#5126` ever merged — they keep receiving push-triggered OTA updates on the `preview` channel, as they always have. Without this correction, they would have silently stopped receiving any push-triggered update the moment `#5126`'s Android-to-`production` routing took effect.
- **iOS testers**: unaffected by this commit — the `#5126` behavior (push → `production`) is preserved.
- No copy changes, no new notification.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-build.yml` | `rider` and `driver` jobs' "Publish OTA update" step: computes `CHANNEL_IOS`/`CHANNEL_ANDROID` separately and publishes with two `--platform`-scoped `eas update` calls instead of one unscoped call | `#5126`'s single-channel-for-both-platforms fix wrongly routed Android's push-triggered updates to `production`, a channel no real Android install is actually on |
| `docs/change-log/2026-09-08-eas-ota-channel-split-by-platform.md` | New | Change Impact & Risk Log entry — this is a correction to an already-merged fix, still mandatory per CLAUDE.md |

## 7. Before / after

```bash
# Before (#5126, both jobs)
if [ -n "$INPUT_PROFILE" ]; then
  CHANNEL="$INPUT_PROFILE"
elif [ "$EVENT_NAME" = "push" ]; then
  CHANNEL="production"
else
  CHANNEL="preview"
fi
...
eas update --branch "$CHANNEL" --environment "$EAS_ENV" --non-interactive --message "$MESSAGE"
```

```bash
# After
if [ -n "$INPUT_PROFILE" ]; then
  CHANNEL_IOS="$INPUT_PROFILE"; CHANNEL_ANDROID="$INPUT_PROFILE"
elif [ "$EVENT_NAME" = "push" ]; then
  CHANNEL_IOS="production"; CHANNEL_ANDROID="preview"
else
  CHANNEL_IOS="preview"; CHANNEL_ANDROID="preview"
fi
...
for pair in "ios:$CHANNEL_IOS" "android:$CHANNEL_ANDROID"; do
  platform="${pair%%:*}"; channel="${pair#*:}"
  ...
  eas update --platform "$platform" --branch "$channel" --environment "$env" --non-interactive --message "$MESSAGE"
done
```

Concrete scenario, using the real evidence that prompted this fix:

| | `#5126` (before this fix) | After this fix |
|---|---|---|
| Trigger | `push` to `main` (`driver-app/**` changed) | same |
| iOS destination | `production` channel | `production` channel (unchanged) |
| Android destination | `production` channel — **no real Android install is on this channel** | `preview` channel — matches driver-app's real `android-auto` build (channel `preview`, per the Expo dashboard) |
| Android testers receive this push's update? | **No** (published to a channel nothing polls) | **Yes** |

## 8. Rollback plan

No migration, no schema change, no live-data mutation — same as `#5126`, this is a shell-logic edit to one workflow file. A `git revert` restores `#5126`'s single-channel behavior (which itself is wrong for Android, per this entry — reverting is not recommended, just stated for completeness per the mandatory rollback field).

No feature flag, for the same reason `#5126` had none: this is CI/CD config, not application code.

## 9. Verification performed

- [x] Root cause verified against real evidence, not inferred from `eas.json` alone: the user supplied two Expo dashboard screenshots of this project's actual build history (driver-app and rider-app), showing real channel/profile values per platform.
- [x] Cross-checked one of the Android build git refs (`90e7d2b`, rider-app build 13) against this session's earlier independent GitHub Actions job-log audit of `eas-native-build.yml` — same commit, same "preview profile, no auto-submit" finding, corroborating both sources.
- [x] Checked the other two build git refs (`103f2f7` driver build 24, `6d6e527` rider build 14) against this repo's git history: `103f2f7` is a real commit on `main` ("docs: correct production identity... (#5106)", merged 2026-09-08 00:34 local time — ~9h before the screenshot, consistent with the dashboard's "about 9 hours ago"), and it is **not** among the 26 `eas-native-build.yml` runs audited earlier this session — confirming this specific Android build was triggered outside this repo's tracked CI (manual `eas build` CLI, most likely), which is itself useful corroboration of the earlier session's "manually submitted outside CI" hypothesis. `6d6e527` is not resolvable in this shallow clone (older commit, not fetched) — not independently verified beyond the dashboard screenshot itself.
- [x] Confirmed `103f2f7` (the commit driver-app's live Android build 24 was built from) contains the driver-app ring-freeze marker fix (`b9e8d6a` is an ancestor) — meaning, contrary to what I told the user earlier this session, the currently-live driver-app Android Play Store build **does** already include that fix. That earlier statement is corrected here rather than left standing.
- [x] Bash parameter-expansion syntax (`${pair%%:*}` / `${pair#*:}`) tested standalone against both platform/channel pairs — resolves correctly.
- [x] `python3 -c "import yaml; yaml.safe_load(...)"` — YAML parses cleanly.
- [ ] **Not exercised against a real GitHub Actions run** — see below.

## What was NOT verified

**This fix has not been exercised by a real workflow run.** Same boundary as `#5126`: the first real confirmation is the next qualifying push to `main`, and that run's log should be checked for both `CHANNEL_IOS`/`CHANNEL_ANDROID` resolving as expected and both `eas update` calls succeeding.

Also not verified:
- Whether `6d6e527` (rider-app build 14's git ref) is on `main` at all, or on some other branch/tag not fetched in this shallow clone — taken on the dashboard screenshot's word, not independently confirmed via `git`.
- Whether there are **other** manual/out-of-band EAS builds beyond the four visible in the two screenshots the user shared — the screenshots are a snapshot of recent history, not the full build list for either project.
- Whether this session's earlier claim to the user ("a fresh Android production build+submission is still needed") should now be walked back entirely, or whether the *currently* Play-Store-live build for real end users (as opposed to the most recent *dashboard* build) is actually `103f2f7` — Play Store staged rollouts and review timing mean "submitted" is not always synonymous with "what a given user's phone has already downloaded." That distinction is called out to the user directly, not silently assumed either way.
