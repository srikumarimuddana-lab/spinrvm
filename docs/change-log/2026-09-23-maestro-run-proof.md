# Change Impact & Risk Log: Maestro execution proof

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | none; CI workflow assurance |
| PR / commit link | pending |
| Related issue or gap ID | A3 Maestro assurance |

## 1. Issue / gap identified

The opt-in Android Cloud and iOS Simulator Maestro lanes could complete without retaining local run evidence or checking that any flow actually executed. A green job therefore lacked archived proof of a non-empty completed flow set.

## 2. Root cause

Both jobs ran Maestro directly and relied on its process result. The Android lane archived only EAS build metadata on failure; the iOS lane archived no Maestro output. Neither workflow inspected a report for executed cases.

## 3. Fix / remediation

Both lanes now request a JUnit report, require at least one non-skipped testcase, and always upload their report and run output as a 14-day artifact. Maestro command failures still fail the job. Existing workflow-dispatch and opt-in label triggers are unchanged.

Maestro's official CLI documentation describes JUnit output for local test runs and cloud runs and cloud command exit codes of 0 on success and 1 when a flow fails. The repository pins CLI 1.39.0; its release history predates `--test-output-dir`, so the iOS lane uses the compatible `--debug-output` option introduced in CLI 1.31. Sources: [Maestro test reports and artifacts](https://docs.maestro.dev/maestro-flows/workspace-management/test-reports-and-artifacts), [Generic CI platform](https://docs.maestro.dev/maestro-cloud/ci-cd-integration/generic-ci-platform), and [Maestro CLI changelog](https://github.com/mobile-dev-inc/maestro/blob/main/CHANGELOG.md). The existing install step's `MAESTRO_VERSION=1.39.0` pin is preserved.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to two manually/label-triggered mobile CI jobs.
- Flow files, app builds, backend behavior, device selection, and labels are unchanged.
- If the pinned CLI does not emit its documented JUnit report on a particular failure mode, the explicit report guard also fails and the artifact step warns when no files exist; this may reduce diagnostics but cannot produce a false green run.
- No changes to background loops, ride state, payment, or wallet behavior.

## 5. User-experience effect

- No in-app effect for riders, drivers, or admins. CI operators can inspect retained Maestro reports and session output for 14 days.
- No mid-session or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/maestro-e2e.yml` | Request JUnit, reject zero executed cases, always upload cloud run report | Retain and gate on Android flow execution evidence |
| `.github/workflows/maestro-ios-macos-runner.yml` | Request JUnit and session output, reject zero executed cases, always upload run output | Retain and gate on iOS Simulator flow execution evidence |
| `docs/change-log/2026-09-23-maestro-run-proof.md` | Record scope, risks, and verification boundary | Required impact record |

## 7. Before / after

```yaml
# Before
run: maestro test "../${{ matrix.app.flows }}"
```

```yaml
# After
run: |
  mkdir -p maestro-results
  maestro test --debug-output=maestro-results/session --format junit --output maestro-results/report.xml "../${{ matrix.app.flows }}"
```

## 8. Rollback plan

Revert the workflow and log changes to restore the prior opt-in lane behavior. No live data or deployed application behavior is affected.

## 9. Verification performed

- [x] Official Maestro documentation checked for report flags, run artifacts, and cloud exit semantics.
- [ ] Automated workflow run on GitHub Actions (unavailable from this environment).
- [x] Confirmed the existing `run-maestro` and `run-maestro-ios` label gates and `workflow_dispatch` triggers remain unchanged.
- [x] Parsed both workflow YAML files and exercised the report guard with executed and all-skipped JUnit fixtures.
- [x] No production build or vendor device-farm execution was performed.

## 10. Sign-off

- Rollback is a workflow revert; no live data remediation is needed.
- No visual tooling is relevant to CI workflow changes.
- Actual Maestro Cloud execution on a vendor-hosted device and iOS Simulator execution on a GitHub macOS runner remain unverified here; this change must not be described as proof that those vendor/runner executions occurred.
