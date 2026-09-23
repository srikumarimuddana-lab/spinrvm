# Change Impact & Risk: Opt-in Worker Fleet Preflight

Date: 2026-09-23 · Surface: deployment preflight · Domain: worker operations · PR 5725

## Issue / gap

The read-only Fly inventory check only understood the existing `app=2,
burst=6` fleet. It could not validate the bounded one-worker canary that keeps
the total Machine budget at eight.

## Root cause

`scripts/fly_fleet_preflight.py` allowed only `app` and `burst` process groups
and enforced their existing independent limits. It had no explicit profile
for a worker group or aggregate budget.

## Fix / remediation

Keep the current profile as the default. An operator may explicitly pass
`--workers 1` to validate `app<=2`, `burst<=5`, `worker<=1`, and total
Machines `<=8`. Unknown groups, wrong regions, duplicate/missing IDs, and
over-budget inventories remain rejected. This changes only a read-only
preflight; it does not create, scale, or deploy Machines.

## Risk & impact on existing functionality

The existing app/burst command and its accepted inventory remain unchanged.
The new profile can admit a worker only when its opt-in flag is supplied, and
cannot accept a ninth Machine. Deployment workflows and `fly.toml` are not
modified, so the canary remains inactive unless a separate deployment change
is approved and made.

## User experience effect

Operators validating the future worker canary can request its budget explicitly
and get a profile-specific success message. Existing deploy preflights continue
to display the current app/burst budget.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `scripts/fly_fleet_preflight.py` | Add opt-in worker profile and aggregate eight-Machine cap | Validate the bounded canary without changing defaults |
| `scripts/test_fly_fleet_preflight.py` | Cover default compatibility, opt-in admission, and budget rejection | Guard profile selection and capacity limits |
| `docs/change-log/2026-09-23-worker-fleet-preflight.md` | Record impact, rollback, and validation limits | Required change-impact record |

## Before / after

```text
# Before: only app/burst groups accepted
python scripts/fly_fleet_preflight.py < inventory.json

# After: current profile remains the default; worker capacity is explicit
python scripts/fly_fleet_preflight.py < inventory.json
python scripts/fly_fleet_preflight.py --workers 1 < inventory.json
```

## Rollback plan

Remove the `--workers` option and worker profile validation. Current callers
need no change because they continue to use the default profile. No Fly config
or running Machine needs rollback.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m unittest discover -s scripts -p test_fly_fleet_preflight.py` — 7 passed.
- `git diff --check` clean.
- Tests verify the existing `app=2, burst=6` default, opt-in `app=2, burst=5, worker=1`, rejection without opt-in, and rejection over the total budget.

## What was NOT verified

No live Fly inventory was queried or changed. This does not validate process
environment variables, worker health/metrics reachability, database/Redis
readiness, or restart behavior. Production topology activation remains separate.
