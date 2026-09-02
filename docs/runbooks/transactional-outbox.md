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

1. **Deploy the worker first.** APIs stay `SPINR_PROCESS_ROLE=all` so the three
   wave-1 loops still run on API replicas (temporary overlap is safe: push
   retry is claim-based, the worker initializes Firebase/Sentry/DB the same
   way the API does, Zoho upserts, onboarding reminders are per-driver/date).
2. Confirm worker `/health` is 200, authenticated `/metrics` scrapes, and
   `spinr_worker_task_healthy{task="outbox_poller"}` is 1. Confirm the worker
   claims test rows (or that `outbox_stats` stays empty while the producer is
   off).
3. **Only then** deploy APIs with `SPINR_PROCESS_ROLE=api` so those three loops
   stop on API replicas.
4. Drain old API replicas.
5. **Only then** set `settings.outbox_receipts_enabled = true`.
6. Do not claim Railway failover readiness until `ACTION_ITEMS.md` C5 is
   resolved and an equivalent one-process worker runs there.

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
- Keep at least one worker machine. CI scales
  `flyctl scale count app=8 worker=1` in `deploy-fly.yml` and
  `bootstrap-fly.yml`. A bare `flyctl scale count 8` (no process group) would
  also create 8 workers — never run that against this `fly.toml`.
- Manual resize of the API burst pool: `flyctl scale count app=N worker=1`.

## Local run

```powershell
cd backend
uvicorn worker:app --host 0.0.0.0 --port 8000 --workers 1
```
