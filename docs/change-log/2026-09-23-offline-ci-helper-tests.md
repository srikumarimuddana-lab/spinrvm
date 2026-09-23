# Change Impact & Risk: offline CI helper tests

## Issue / root cause

The DAST target/report guard and payment-failure alert sample-floor checks had
local tests, but CI did not run them. The new load-test target-guard suite also
has no app-service dependency and belongs in the same fast assurance path.

## Fix

Add a standalone CI guardrails job that installs Python 3.12 with pinned pytest
and PyYAML versions, then runs the three pure helper suites. The job is included
in the PR guardrail summary and makes no application or service requests.

## Risk & impact

CI-only, isolated to workflow execution; no runtime, database, payment, or
user-facing behavior changes. The guardrail summary now waits for and reports
this job. A helper regression fails this check; dependency-install failures
also fail it rather than silently skipping coverage.

## User experience

No runtime effect. Contributors see a dedicated status for these offline tests.

## Files modified

| File path | Change | Purpose |
|---|---|---|
| `.github/workflows/ci-guardrails.yml` | Add the offline test job and summary row | Run and expose helper coverage on CI events |
| This record | Document scope and verification | Change impact record |

## Rollback

Revert this CI-only commit; no live state is changed.

## Verification performed / not verified

- [ ] Run all three suites locally after `loadtest/test_target_guard.py` is integrated.
- [ ] GitHub Actions execution; no workflow run was triggered here.
- [x] Workflow was checked to use the existing pinned checkout and setup-python actions.
