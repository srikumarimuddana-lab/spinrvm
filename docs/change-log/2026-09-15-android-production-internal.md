# Production Android builds to Google Play internal testing

## Run
After this PR is merged, open **Actions → Android Production → Play Internal Testing → Run workflow**.
Select branch **main**, then **driver-app**, **rider-app**, or **both** (default).
Each selected app builds a signed production AAB with automatic remote version-code increment, then submits that exact build ID to the internal testing track. This is a real production backend/environment and production OTA channel; test rides may have real operational/payment effects. Public Play release is a separate operation.

## Prerequisites
- Existing repository secrets: `EXPO_TOKEN` and `PLAY_SERVICE_ACCOUNT_JSON` (base64 of the Google service-account JSON, not raw JSON).
- EAS Android signing credentials and production environment variables configured for both projects.
- Service account has release access to each selected app in Play Console; app entries and the required first manual upload already exist.
- Internal testers are configured in Play Console and have accepted that app's opt-in link.
- Normal PR/CI release checks must be reviewed before dispatch; this workflow builds/submits and does not replace application tests.

## Failure and retry
Build and submission are separate blocking steps. A failed build never submits; submission failure makes the job fail. When both apps are selected, one failure does not cancel the other's build.
The job summary records the completed build ID before submission. If only submission failed, inspect EAS/Play status and retry that specific submission through EAS rather than automatically rebuilding or choosing an unrelated latest build.
A GitHub cancellation/timeout may leave the remote EAS job running. Check/cancel it in Expo before retrying. Per-app concurrency serializes GitHub runner jobs for this workflow and the Android-only EAS Native Build workflow. The latter uses --no-wait and releases its lock while its remote EAS build can still run; check EAS before dispatching and avoid simultaneous submissions through other workflows or local CLI.
Successful EAS submission is not proof of immediate availability: check Play processing/review and install from the internal testing link.

## Change impact and risk
| Field | Detail |
|---|---|
| Issue/root cause | Production profiles already target internal testing, but the general native workflow defaults to iOS/preview and returns after queueing; the dedicated driver testing workflow uses the preview-channel android-auto profile. |
| Fix | Add one manual production Android workflow supporting either app or both, with credential/profile checks and exact-build submission that waits for completion. |
| Alternative | Reusing the general native workflow requires several correct selections and only reports queue success. A dedicated entry point fixes profile/track choices and exposes submission failures. |
| Consumers/blast radius | New workflow reads rider-app/eas.json and driver-app/eas.json and existing secrets. Existing eas-native-build.yml, deploy-driver-play-testing.yml, eas-build.yml, ci.yml and test-env.yml flows are not edited. No app, backend, DB, ride-state or payment code changes. |
| UX/risk | Only internal testers receive the binary; production services and OTA channel are used. Builds consume EAS capacity and increment version codes. Existing app defects can still cause build/test failures. No automatic public-track promotion. |
| Files | .github/workflows/android-production-internal.yml: new manual pipeline. This document: prerequisites, operations and impact record. |
| Before/after | Before: select Android + production + auto-submit in the generic queue-only workflow. After: select app; wait for production build and exact-ID internal submission. |
| Rollback | Disable the new workflow for future runs. For an already distributed bad build, stop further distribution in Play Console and publish a corrected internal build with a higher version code; reverting workflow code cannot downgrade installed apps. |
| Verification | Both eas.json files parsed and production/internal settings inspected; CLI flags checked against Expo CLI documentation/source; static contract checks and independent CI/CD review found no blockers. These checks are not a YAML parser, actionlint or execution test. |
| Not verified | Local command execution is unavailable in this session. actionlint/YAML parsing, GitHub runner, EAS production build, signing, repository secret availability/Play permissions and actual tester delivery have not been executed or verified. No app visual changes; mobile visual regression tooling is absent. |

References: [Expo CLI build/submit flags](https://docs.expo.dev/eas/cli/), [EAS CI prerequisites](https://docs.expo.dev/build/building-on-ci/).
