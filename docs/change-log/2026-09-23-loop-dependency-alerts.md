# Financial loop dependency alerts

## Issue and root cause

The watchdog only alerted on `stale` heartbeats. Explicit `unhealthy` dependency
failures also need to reach the existing operator webhook. The monitor now
records a safe dependency-failure category separately from successful ticks,
returns unhealthy immediately (including before a first tick), and clears the
failure on the next healthy heartbeat.

## Fix / before and after

```text
Before: alert only when status == stale.
After: alert when status is stale or unhealthy; unhealthy uses dependency wording.
```

## Risk & impact

`core/lifespan.py`'s watchdog consumes this helper. The existing one-hour cooldown,
retry after failed delivery, optional webhook configuration, and stale-loop
message remain intact. Redis lock failures may now produce an operator alert
before the loop's first heartbeat. Only fixed diagnostic wording and loop names
are sent, never dependency exception text. The monitor is also consumed by
`routes/main.py` health status and worker readiness; these now reflect dependency
failure immediately rather than waiting for heartbeat staleness. Financial
loops retain their ownership gates; no financial execution changes here.

## User experience

Operators see required-dependency failures; no rider, driver, or corporate screen
changes. Delivery still depends on a configured and reachable webhook.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/loop_alert.py` | Handle unhealthy status | Surface dependency outages |
| `backend/tests/test_loop_alert.py` | Exercise early failure and repeated checks | Verify alert and cooldown |
| `backend/utils/loop_monitor.py` | Track dependency failure until recovery | Prevent false healthy status |
| `backend/tests/test_loop_monitor_dependency_failure.py` | Test failure before first tick and recovery | Verify monitor state |

## Rollback plan

Remove the configured loop-alert webhook to stop delivery without a deployment,
at the cost of also suppressing stale-loop alerts. Revert this isolated helper
change and redeploy to restore previous filtering. No data rollback is needed.

## Verification performed

The new regression failed before the helper change because no alert was sent.
After the change, all 11 focused loop-alert tests pass, including the existing
stale-loop delivery/retry cases. HTTP delivery is mocked.

## What was not verified

No live webhook message, provider receipt, deployment, or production build was run.
