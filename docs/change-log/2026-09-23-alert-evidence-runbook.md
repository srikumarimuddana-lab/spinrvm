# Alert evidence and operator gates impact

## Issue/gap identified

Thresholds, delivery behavior, payment-identity repair signals, and production acceptance boundaries were split across code and several runbooks. Tests could be mistaken for proof that Grafana and notification channels were live.

## Root cause

The Alloy app is not deployed according to its checked-in Fly config, alert YAML is not automatically applied, and backend unit tests mock metrics and notification delivery. Existing runbooks did not collect these evidence limits and response steps in one operator-facing place.

## Fix/remediation

Document repository thresholds, observed emitters, repair-log coverage gaps, Grafana no-data interpretation, and the required scrape/data-source/notification/receipt gates in the on-call runbook.

## Risk & impact on existing functionality

Blast radius: documentation only (`docs/runbooks/on-call.md`). No code, alert state, metrics, payment identity, DB, or notification configuration changes. Instructions explicitly prevent interpreting missing samples or mocked delivery as production health.

## User-experience effect

No rider, driver, or admin UX change. On-call receives a clearer verification and repair triage procedure.

## Files modified

| File | Change | Reason |
|---|---|---|
| `docs/runbooks/on-call.md` | Add alert thresholds, repair signals, acceptance and receipt gates | Give operators one place to check evidence and boundaries |
| `docs/change-log/2026-09-23-alert-evidence-runbook.md` | Record impact, source review and verification limits | Preserve the required impact record |

## Before/after

```text
# Before
Alert thresholds and runtime status were split across Grafana YAML,
capacity-scaling, payment cutover notes, and unit tests.
```

```text
# After
The on-call runbook states each threshold, identifies emitted rider repair
metrics vs. driver/corporate logs, and requires live scrape and operator
receipt evidence before calling the signal production coverage.
```

## Rollback plan

Revert the two documentation edits. There is no runtime behavior to roll back.

## Verification performed

Inspected `metrics-agent/grafana/alert-rules.yaml`, `metrics-agent/config.alloy`, `metrics-agent/fly.toml`, backend emitters, `capacity_watchdog`, `loop_alert`, their focused tests, and the capacity and Stripe cutover runbooks. `git diff --check` is required before commit.

## What was NOT verified

No Grafana Cloud or Fly app was queried, no remote-write sample or live alert state was measured, and no external notification was sent. Unit tests use mocked data and channels. A live channel receipt, Prometheus target-health check, threshold preview, and alert-rule comparison remain operator gates.
