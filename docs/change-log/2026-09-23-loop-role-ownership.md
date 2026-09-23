# Change Impact & Risk: Background Loop Catalog Correction

## Issue/gap identified

The process-role catalog omitted two loops that `core/lifespan.py` already
starts and registers with the watchdog.

## Root cause

The ownership inventory drifted from lifespan's `_spawn()` calls, leaving
`route_deviation_alerter (30s)` and `insurance_period_reconciler (10min)`
without explicit placement.

## Fix/remediation

Classified both existing loops as API-owned and added a regression guard that
requires every spawned loop to be in the catalog. The H3 reconciler remains
catalogued as deferred and dormant.

## Risk & impact on existing functionality

Blast radius is limited to `background_loop_registry.py` consumers:
`core/lifespan.py` uses the placement map for role filtering and watchdog
selection; `backend/tests/test_lifespan_watchdog_coverage.py` verifies catalog
consistency. The classifications match current runtime placement, so default
`all` behavior and loop scheduling are unchanged. No database, network, or
deployment configuration is modified.

## User experience effect

No rider, driver, or admin UI change. This is internal ownership metadata;
there is no mid-session user-visible effect.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/background_loop_registry.py` | Added API placements for route-deviation and insurance-period loops | Match the existing lifespan spawns to process-role ownership |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Guard all spawned names are classified and H3 remains dormant | Prevent catalog drift and accidental H3 activation |
| `docs/change-log/2026-09-23-loop-role-ownership.md` | Recorded this impact assessment | Required change-impact record |

## Before/after snippet

```python
# Before
("route_gap_monitor (15s)", "deferred"),
("stale_intent_reconciler (15min)", "api"),

# After
("route_gap_monitor (15s)", "deferred"),
("route_deviation_alerter (30s)", "api"),
("stale_intent_reconciler (15min)", "api"),
```

## Rollback plan

Revert this catalog-and-test commit. It changes no live data or deployment
configuration, so no second deploy or data repair is required.

## Verification performed

- Regression was observed before the fix: the new spawned-name classification
  check found the two missing catalog entries.
- `/tmp/pr5725-venv/bin/python -m pytest tests/test_lifespan_watchdog_coverage.py -q --no-cov` — 8 passed.
- No frontend production build applies to this backend-only change.

## What was NOT verified

No staging or production process was started. This catalog-only slice does not
verify the later runtime role selection or dedicated-worker deployment; those
remain separate implementation and operational gates.
