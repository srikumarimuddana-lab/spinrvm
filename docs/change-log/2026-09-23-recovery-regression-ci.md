# Recovery and deployment regression CI

## Issue and root cause
The isolated Fly checks ran fleet tests, but newly added recovery and deployment-evidence tests were only executed locally. Helper-only changes could miss their safety regressions in CI.

## Fix and before/after
The existing Fly resilience workflow now watches both helper/test pairs and the staging deployment workflow and runs their credential-free unittest suites alongside fleet checks. Before, only local runs established these checks; after, PR and main changes to the helpers trigger them automatically.

## Risk and UX impact
CI only; no provider request, credentials, live staging failure injection, or customer behavior changes. Runtime failure states remain enforced by their respective deployment workflows.

## Files
- `.github/workflows/fly-resilience-checks.yml`: trigger paths and two test commands.
- `docs/change-log/2026-09-23-recovery-regression-ci.md`: scope and verification.

## Verification
The exact unittest commands pass locally (8 recovery and 14 deployment tests); workflow YAML parses and diff whitespace checks pass. Hosted CI execution is separate evidence.

## Rollback
Revert the added paths/commands if this standalone test harness changes. Do not weaken deployment or recovery runtime checks.
