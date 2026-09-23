# Transactional outbox and dedicated worker

Automatic ride receipts are produced by a Postgres trigger on paid+completed
rides, then delivered by a one-process worker that claims `outbox_messages`
with expiring leases. Delivery is **at least once**: a crash after the email
provider accepts a message and before acknowledgement can send the same
receipt twice. There is no historical backfill.

## Components

| Piece | Where |
|---|---|
| Table + trigger + lease RPCs | `backend/migrations/399_transactional_outbox.sql` |
| Producer flag | `settings.outbox_receipts_enabled` (default `false`) |
| Worker app | `backend/worker.py` — `uvicorn worker:app --workers 1` |
| Poller | `backend/utils/outbox_worker.py` |
| Wave-1 loops on the worker | `push_retry (30s)`, `zoho_desk_sync (10min)`, `driver_onboarding_reminders (15min)` |
| API process role | `SPINR_PROCESS_ROLE=all` (default) or `api` |
| Operator CLI | `python -m scripts.outbox_admin` from `backend/` |

Payloads store `{"ride_id": "<id>"}` only. Never log email, phone, or GPS.

## Staged rollout (do not skip)

The production Fly app does not currently activate the dedicated worker
process group. The sequence below is a future rollout procedure, not an
instruction to change the live topology as part of this code change. First
prove the candidate inventory, process configuration, private health, and
authenticated metrics checks described below.

`backend/fly.worker-canary.toml` is an inactive candidate for moving only
`push_retry (30s)`. Before any operator uses it, validate from `backend/` with
`fly config validate -c fly.worker-canary.toml --strict`, then review the
resulting process/service/check behavior. This candidate does not declare
Machine counts; it must be paired with the opt-in eight-Machine preflight.
Never point an active deploy workflow at this file as part of this change.

1. Set `SPINR_API_ROLE="all"` in the inactive candidate and deploy that
   candidate as a staged overlap. Fly deploy does not guarantee that the worker
   starts before API Machines; this phase keeps the API as owner while worker
   startup and health are validated.
2. Confirm worker `/health` is 200, authenticated `/metrics` scrapes, and
   `spinr_worker_task_healthy{task="outbox_poller"}` is 1. Confirm the worker
   claims test rows (or that `outbox_stats` stays empty while the producer is
   off).
3. Before moving any loop, verify its complete-tick replay behavior and
   external side effects; claim safety or upsert behavior alone does not prove
   that a repeated provider call is harmless. Wait through at least one full
   loop cadence (and any claim/lease window) while observing task health and
   duplicate-side-effect signals.
4. **Only after those checks pass**, set `SPINR_API_ROLE="api"` and redeploy the
   candidate. This makes API Machines select the exact loop also selected on
   the worker. Verify API watchdog ownership and worker health again.
5. Drain old API replicas.
6. **Only then** set `settings.outbox_receipts_enabled = true`.
7. Do not claim Railway failover readiness until `ACTION_ITEMS.md` C5 is
   resolved and an equivalent one-process worker runs there.

### Single-loop worker canary

The worker loop selector supports a bounded canary while the rest of wave 1
stays on the API. Use the exact same selector on every API and worker Machine:

| Process group | `SPINR_PROCESS_ROLE` | `SPINR_WORKER_LOOP_ALLOWLIST` | Loops owned |
|---|---|---|---|
| API | `api` (via candidate `SPINR_API_ROLE`) | One exact catalog name, for example `push_retry (30s)` | All current API loops and the two unselected wave-1 loops |
| Worker | `worker` | The same exact catalog name | Outbox poller and the selected wave-1 loop |

The candidate's app/burst wrappers copy `SPINR_API_ROLE` into
`SPINR_PROCESS_ROLE`; the worker wrapper sets `SPINR_PROCESS_ROLE=worker`.
Set `SPINR_API_ROLE="all"` for the worker-first stage and set it to `api` only
after the worker is proven healthy and the overlap/replay gate passes. Do not
assume Fly deploy orders worker before API Machines.

The selector value is a comma-separated list of exact loop names. Empty, unknown, or
duplicate names fail startup. An unset value preserves the existing full-wave
role behavior: API role moves all three wave-1 loops, and worker role starts
all three. `SPINR_PROCESS_ROLE=all` ignores the selector and preserves the
legacy all-loops behavior.

Before deploying a canary:

1. Run `flyctl machine list --json -a <app-name> | python scripts/fly_fleet_preflight.py --workers 1`
   against the current inventory. This only validates
   `app<=2, burst<=5, worker<=1, total<=8`; it does not prove loop ownership,
   health, or metrics.
2. Run `fly config validate -c fly.worker-canary.toml --strict` from `backend/`
   and review provider validation. This validation has not been run for this
   repository candidate yet.
3. Verify the candidate's `SPINR_API_ROLE` and selector wrapper match on every
   API/burst Machine, and the worker wrapper sets role `worker` with the same
   selector. Confirm effective `SPINR_PROCESS_ROLE` and
   `SPINR_WORKER_LOOP_ALLOWLIST` in each live process. Any mismatch can
   duplicate or omit work; do not continue until all Machines agree.
4. Verify the worker is private and `/health` returns 200 with exactly the
   outbox poller and selected loop. An authenticated private `/metrics` scrape
   must return 200 and show `spinr_worker_task_healthy=1` for both tasks.
5. Verify API watchdog status still includes the two unselected wave loops.
   Observe worker heartbeats, outbox claim/release, oldest pending age, and
   task errors before moving another loop.
6. Roll back by changing candidate `SPINR_API_ROLE` to `all` and redeploying
   the compatible process commands. Verify API logs show role `all` and the
   watchdog monitors the full wave, then scale the worker down after committed
   outbox rows drain. Setting `SPINR_PROCESS_ROLE` alone does not override the
   candidate wrapper, which derives it from `SPINR_API_ROLE`; redeploy the
   changed candidate and confirm effective API role `all` before scaling down.

These external checks are required because unit tests cannot prove Fly Machine
environment parity, private network reachability, deployed restart behavior,
or metrics credentials. Do not enable the outbox producer as part of a loop
ownership canary.

Do not enable the producer if the worker is not healthy in every environment
that can take payment traffic.

## Alerts

- Worker `/health` 503 or `spinr_worker_task_healthy == 0` for any task.
- `spinr_outbox_oldest_pending_age_seconds > 300` (5 minutes).
- Any increase in `spinr_outbox_dead_lettered_total`.

Sentry dead-letter events are tagged `domain=payments`, `surface=backend`,
`ride_id`.

## Dead-letter triage and redrive

```powershell
cd backend
python -m scripts.outbox_admin list-dead
python -m scripts.outbox_admin show <id>
python -m scripts.outbox_admin redrive <id> --actor-id <ops-user-id>
```

- `list-dead` / `show` print id, topic, ride_id, attempts, timestamps, and the
  allow-listed `last_error_code` only.
- `redrive` accepts **dead-lettered rows only**. It resets lease/attempt state,
  increments `redrive_count`, and appends `audit_logs.action=outbox_redrive`.
- After redrive the worker claims the row again. Duplicate receipts are
  possible (at-least-once).

## Cleanup retention

The idle poller calls `services.outbox.cleanup()` every 60s: published/discarded
rows after 30 days; dead letters after 90 days (`dead_lettered_at`, or
`updated_at` if that timestamp is null). Retention is Python `delete_many`, not
a SQL `DELETE FROM` in the migration. Dead letters must alert before cleanup.

## Rollback (incident order)

1. `UPDATE public.settings SET outbox_receipts_enabled = false WHERE id = 'app_settings';`
   New paid transitions stop producing rows. API code sees no row and uses the
   existing direct receipt path.
2. Leave the worker running long enough to drain already-committed rows.
3. Redeploy API with `SPINR_PROCESS_ROLE=all` and confirm the three moved loops
   are healthy on the API.
4. Scale the Fly `worker` process group to zero.
5. **Preserve `outbox_messages`.** Never drop it during an incident.

## Fly notes

- `[http_service]` stays on `app` only. Worker machines are not in the public
  service; they expose `/health` and Bearer `/metrics` on port 8000 for Fly
  checks and internal scrape.
- The current production `fly.toml` and deployment workflows do not configure
  this worker group. Do not run worker scale commands against the live app
  until a separate deployment change declares the process group, private
  checks, and matching process-role/selector environments.
- The opt-in fleet preflight validates only candidate counts. It does not
  activate, resize, or verify Fly process configuration.

## Local run

```powershell
cd backend
uvicorn worker:app --host 0.0.0.0 --port 8000 --workers 1
```
