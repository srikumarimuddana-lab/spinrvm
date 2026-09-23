# Payment alert sample guard impact

## Issue/gap identified

The payment alert title and ADR-010 require at least 20 settlement samples, but the provisioned expression only compared a failure rate. At low volume, one failure could breach the displayed 1% rule.

## Root cause

The minimum-sample requirement existed only in alert annotations; no PromQL predicate enforced it.

## Fix/remediation

Compute the 10-minute failure ratio from counter increases and require at least 20 total Fly settlement samples before evaluating the 1% threshold. Lower-volume windows return no data for this rule; query errors alert.

## Risk & impact on existing functionality

Blast radius: isolated to the checked-in Grafana alert source file. Backend settlement metrics, money movement, and alert thresholds are unchanged. The file is not automatically loaded into Grafana, so the live UI rule remains unchanged until an operator applies this update. A quiet window is `NoData` for this rule, not proof of healthy telemetry; the operational acceptance gate must separately confirm scrape and remote-write health.

## User-experience effect

No rider, driver, or admin behavior changes. Once an operator applies the rule update, on-call should receive fewer low-volume false breaches; missing metrics must be detected by the separate telemetry-health check.

## Files modified

| File | Change | Reason |
|---|---|---|
| `metrics-agent/grafana/alert-rules.yaml` | Add 20-sample PromQL gate and explicit NoData/Error handling | Make the stated minimum-sample policy executable |
| `scripts/test_payment_failure_alert.py` | Parse rule YAML and pin expression plus sample-gate cases | Catch drift between alert prose and expression |
| `docs/change-log/2026-09-23-payment-alert-sample-guard.md` | Record impact and verification boundary | Preserve required operational evidence |

## Before/after

```promql
sum(rate(failed[10m])) / sum(rate(total[10m]))
```

```promql
(sum(increase(failed[10m])) / sum(increase(total[10m])))
and on() (sum(increase(total[10m])) >= 20)
```

## Rollback plan

Revert the alert-rule file if evaluation behaves unexpectedly, then re-import/update the Grafana-managed rule from the corrected provisioning file. No application data or payment state changes.

## Verification performed

`/tmp/pr5725-venv/bin/python -m unittest scripts.test_payment_failure_alert -v` parses the YAML, pins the exact expression and state settings, and checks the sample-count truth table. `promtool` is not installed, so no PromQL parser/evaluator validation was available. `git diff --check` is required before commit.

## What was NOT verified

The Alloy metrics-agent app is marked not deployed and no live Grafana data source, alert rule, series, or notification channel was accessed. No Prometheus `promtool` parser/evaluation or live low-volume/no-data drill was run. The test checks the exact expression fixture and corresponding threshold cases, not a PromQL engine. Before relying on the rule, an operator must apply it in Grafana, verify a live settlement series has `provider="fly"`, confirm Alloy scrape/remote-write health and Grafana NoData/Error states, then trigger a controlled alert test and obtain an operator receipt.
