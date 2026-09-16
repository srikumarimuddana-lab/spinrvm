# Spinr burst controller

Status: implementation for review. Not deployed or activated by this PR. Starts
existing backend Machines only. It never creates, destroys, stops, resizes,
cordons, or restarts a running Machine.

## Policy

- Exactly two `app` and six `burst` Machines in Toronto, eight backend Machines
  total. Restore a stopped warm Machine before adding burst capacity.
- Per-Machine memory >=70% or CPU >=80% of the shared-CPU baseline for 30 seconds
  of **source metric timestamps** triggers one start. Memory >=85% bypasses the
  sustained threshold. These are provisional thresholds, not measured capacity.
- Fresh telemetry is required for every started Machine, including the least
  healthy one. Samples older than 90 seconds, future timestamps, incomplete
  series, invalid numbers, and duplicate instance series are rejected.
- Require at least one running Machine's own `/ready` to report DB readiness.
  All unready means inhibit blind scale-out and report action-required metrics. This cannot distinguish
  every shared dependency outage from a completely overloaded application.
- Minimum 120 seconds between controller attempts; at most eight attempts/hour.
  An attempted start is durably recorded **before** sending the request. Do not
  automatically retry uncertain API outcomes. Await readiness for 180 seconds;
  a failed candidate is recorded, then a different idle candidate is preferred.
- A fixed warm anchor's Fly lease serializes controllers. Its metadata stores
  `spinr_burst_journal`; a separate target lease fences the actual start. Require
  at least 20 seconds of lease lifetime before each mutation, recheck inventory
  and metric freshness, and fail closed when the anchor disappears.
- Fly Proxy can independently start peers for connection demand. Autostop is
  disabled so it cannot undo memory-driven scale-out. There is **no automated
  scale-in** in this observation phase: added capacity remains running, potentially
  all eight after a deployment. No active trip is stopped to save compute.

This controller is one additional 256 MB management Machine, separate from the
user-approved eight backend Machines. Compute, storage and monitoring charges
must be included in the rollout budget. Provision it once, not per backend VM.

## Provision and activate

1. Complete `docs/runbooks/fly-mixed-fleet.md` migration. Confirm fresh inventory,
   sizes, services, per-Machine readiness and Redis connectivity. Never point this
   controller at the old eight-`app` layout.
2. Create `spinr-burst-controller-yyz` in `spinr_backend`. Set `BURST_ANCHOR_ID`
   to a verified warm Machine ID; all controller instances must use the same ID.
   Do not automatically replace it if that Machine is removed.
3. In Fly secret storage, set `FLY_API_TOKEN` to a backend-app-scoped deploy token
   and `PROMETHEUS_TOKEN` to an organization read-only metrics token. Both values
   include their `FlyV1 ` Authorization prefix. Never copy a personal token into
   this configuration or log secret values.
4. From this directory deploy with `fly deploy --ha=false`. Keep exactly **one**
   controller Machine. `BURST_DRY_RUN=true` is the committed default: it reads
   inventory, metrics, readiness and the journal, but acquires no leases and
   writes nothing. It must remain observe-only until the gates below pass.
5. Email and Grafana are deferred at the user's request. Logs and private metrics
   remain available, but no six-Machine or failure email is sent. Native Fly
   platform metrics used by the controller do not require a Grafana installation.
6. Verify the scenarios below in staging, including lease enforcement on metadata
   and start endpoints, retention of `spinr_burst_journal` across a deployment,
   and controller metrics ingestion. Observe at least one peak and a six-hour
   soak. Record boot-to-ready time and database impact before tuning thresholds.
7. Enable by changing the reviewed `BURST_DRY_RUN` configuration to `false` and
   deploying. Keep the repository configuration in sync so the next controller
   deployment cannot silently revert the mode. Disabling uses `true` and retains
   the current running backend capacity; it does not shut down trips.

Pause the controller around backend maintenance or anchor replacement; wait for
its leases to expire and retain the journal. Never delete the journal just to
bypass a cooldown or attempt budget.

## Failure matrix and limits

| Scenario | Expected result |
| --- | --- |
| One VM hot, fleet average low | Start from the worst individual pressure signal |
| Brief spike / repeated or delayed samples | No false sustained-pressure decision |
| Critical memory | Immediate decision once fresh telemetry arrives; boot still takes time |
| Partial/stale/NaN metrics or API errors | No speculative start; action-required metric |
| Dependency outage on every running VM | Inhibit scale-out; retain serving processes |
| Start reply lost / controller restarted | Reconcile durable pending; no immediate duplicate |
| Competing controller / deploy | Lease or changed-inventory failure; no unfenced start |
| Lease or telemetry expires mid-operation | No later start; retain pending if already journaled |
| Failed boot / target not ready | Record failure after 180s; prefer another idle candidate |
| Wrong group/region / anchor missing | Fail closed; never elect a new lock anchor silently |
| All eight running / budget exhausted | Action-required metric; no ninth backend Machine |
| Controller fails | Pressure starts pause; external notification deferred |
| Six or more running | Running count metric available; email deferred |
| Pressure falls | Retain capacity during observation; no unsafe trip draining |

Not guaranteed: an allocation spike faster than metric delivery/startup, a heap
leak in existing work, regional/provider failure, or recovery of every live ride
from process loss. Scaling cannot move existing WebSockets or cure a saturated
shared DB. Local bounded work and client reconnect/reconciliation remain required.

## Local verification

`python -m unittest discover -s infra/burst_controller -p 'test_*.py'` from repo root.
Tests use fake time, fake transport and in-memory Fly state; they make no production
writes. Strict Fly config validation is read-only. The Docker image uses the
backend's existing digest-pinned Python base and no third-party Python packages.
