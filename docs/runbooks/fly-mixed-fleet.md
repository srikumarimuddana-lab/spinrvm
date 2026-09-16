# Fly mixed fleet and burst protection

Status: PR preparation; no production changes. Grafana and email alerts are deferred at the user's request.

## Scope and implementation sequence

The September 15 incident included six confirmed Python OOM kills on 1 GB
Machines running two workers. More RAM provides headroom; it does not fix leaks,
unbounded database work, or prove that CPU is adequate under peak traffic.

Implement in separate commits, at most three files per task:

1. Record this rollout and acceptance plan (this file).
2. Add a read-only fleet preflight and its isolated regression tests.
3. Configure two process groups and update both deployment workflows together.
4. Defer Grafana and email delivery; retain controller logs and metrics for later integration.
5. Implement pure burst decisions and deterministic failure-matrix tests (2 files).
6. Implement bounded Fly/metrics clients and adapter tests (2 files).
7. Implement the external controller, durable action guard and tests (up to 3 files).
8. Add controller packaging/config, ownership policy and activation instructions
   (up to 3 files per commit); keep it observe-only until staging acceptance.
9. Check local overload bounds, add a focused fix/test if needed, and document
   gaps that scaling cannot address. Run review and verification before the PR.

| Group | Count | VM | Workers | Idle policy |
| --- | ---: | --- | ---: | --- |
| `app` | 2 | shared CPU, 2 vCPU, 4 GB | 2 | Always running |
| `burst` | 6 | shared CPU, 1 vCPU, 2 GB | 2 | Start on pressure/demand; retain while observing |

Both groups serve the same backend on ports 80/443. Sharing external ports is
intentional: Fly Proxy balances across both groups. Keep their connection limits
identical. These limits are inherited settings, not a measured rider capacity.
Fly permits up to 2 GB per shared CPU; performance CPUs are not required for 4 GB.
There is no separate performance RAM selection.

The workflows preserve this eight-Machine inventory. This is not a platform-wide
quota: manual CLI changes or a separate controller can still create Machines.
Deployments may start all eight temporarily. Proxy scaling uses connections,
**not memory pressure**. The expanded PR adds an external memory/CPU burst controller. Activation requires
turning off proxy autostop so it cannot undo a memory-driven start. The initial
controller only starts existing capacity; it never stops, destroys, creates or
resizes backend Machines. Automatic drain/scale-in remains a later gate.

## One-time migration (operator, before first deployment)

Do not deploy the new process definition directly over eight `app` Machines:
Fly would create the missing `burst` group in addition to those eight. The new
preflight rejects that layout before deployment or secret staging.

1. Schedule a quiet window. Pause both GitHub Fly workflows, wait for active runs
   to finish, and coordinate against manual deployments. Merge this PR while
   they are paused. Keep the previous image reference and an inventory of IDs,
   groups, states, regions, sizes and health checks for rollback.
2. Verify exactly eight backend Machines in `yyz`, with two healthy serving
   Machines and six idle ones. Use fresh `fly machine list -a spinr-backend-yyz`.
   Do not use historical Machine IDs or stop a Machine with live traffic.
3. For each of the six **currently stopped** idle Machines, run:
   `fly machine update <ID> -a spinr-backend-yyz --metadata fly_process_group=burst --skip-start --yes`.
   If an idle candidate is suspended or starts receiving traffic, stop here and
   arrange a verified drain/stop first. Do not force-stop an active trip.
4. Confirm the resulting inventory is `app=2`, `burst=6`, all in `yyz`.
   Re-enable the workflows and manually dispatch deployment from the merged
   commit. Do not run an old workflow/config: removing a declared process group
   can destroy its Machines.
5. Check every Machine's resulting size and services, both warm Machines'
   readiness, the deployed build SHA, and a rider/driver reconnect smoke test.
   Verify a stopped burst Machine starts under controlled staging pressure.
   Autostop is disabled during observation; all eight may remain running. Any
   later reduction requires a verified drain, not force-stopping live trips.

For a genuinely empty app, bootstrap permits an empty inventory. Deployment
seeds the declared groups without implicit HA extras; the following count step
fills them to `app=2 burst=6`. Unexpected groups, regions, duplicate IDs or
excess Machines fail closed. Nothing is automatically destroyed to fit the cap.

## Notifications deferred

Grafana configuration and email delivery are excluded at the user's request.
The earlier six-Machine / every-15-minute email requirement remains deferred.
No mailbox, contact point, alert rule, or email sender is created or activated.
Controller logs and metrics expose pressure, failure and capacity exhaustion;
they do not send notifications. Fly native metrics remain a controller input
and do not require installing Grafana.

## Verification and rollback

Run the fleet guard unit tests, strict Fly config validation, and
controller regression tests. Confirm both workflows run the guard before mutations,
serialize against each other, and use explicit group counts.

After rollout, observe per-Machine RAM, CPU throttling, DB queue wait, errors and
reconnects through a peak and a soak test. Escalate repeated OOM immediately;
adding capacity cannot free a leaking worker's memory or repair a saturated DB.

For application rollback, deploy the previous image with the **new mixed-group
config**, keeping RAM and group membership. Do not blindly revert `fly.toml` or
run the old bootstrap: removing `burst` can destroy six Machines and restoring
1 GB recreates the incident conditions. Preserve existing payment/dispatch alerts.

References: [Fly process groups](https://fly.io/docs/launch/processes/),
[Machine sizing](https://fly.io/docs/machines/guides-examples/machine-sizing/).

## Expanded burst protection acceptance matrix

Thresholds are provisional and must be calibrated in staging: per-Machine memory
>=70% for 30 seconds, >=85% immediately, or CPU pressure >=80% for 30 seconds.
Use fresh telemetry for every running Machine, not a fleet average. Start one
existing idle burst Machine, wait for readiness, and allow at least two minutes
between attempts. Connections can still trigger Fly proxy starts; only scale-in
is disabled. At eight, report capacity exhaustion in logs/metrics; never create a ninth backend Machine.

Cover normal/recovered pressure, short spikes, sudden critical memory, stale or
missing partial telemetry, NaN/invalid readings, duplicate time series, API
401/429/5xx/timeouts, uncertain start outcome, boot/readiness failure, concurrent
controllers, concurrent deployment, unexpected inventory/regions, repeated start
failures, all dependencies unready, controller restart and cap exhaustion. Shared dependency failure inhibits blind scale-out.
Do not claim an OOM is prevented when allocation growth beats metric collection
and boot time; heap leaks and already-running work need their own safeguards.

Implementation and controller activation: `infra/burst_controller/README.md`.
The controller remains observe-only by default; merging files does not provision
email delivery or turn on active pressure scaling. Proxy autostart continues to respond
to connections within the existing pool, independently of controller decisions.
