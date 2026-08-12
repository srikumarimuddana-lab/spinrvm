# Capacity & scaling runbook

How Spinr's backend scales under load, what the limits are at each layer, and
what to do when a capacity alert fires.

Related: [`railway-fly-failover.md`](railway-fly-failover.md) ·
[ADR-001 (Supabase)](../adr/001-supabase-postgres.md) ·
[ADR-007 (Fly primary)](../adr/007-fly-primary-railway-standby.md) ·
[ADR-010 (metrics)](../adr/010-metrics-aggregation-and-alerting.md)

---

## 1. The four capacity layers

Load passes through four ceilings. The **lowest one binds** — raising any other
does nothing until you raise that one. In rough order of what users hit first:

| # | Layer | Current ceiling | Scales how |
|---|---|---|---|
| 1 | Per-user rate limits | Per authenticated user, per route | Code change (limits in `backend/utils/rate_limiter.py`) |
| 2 | Fly connections | 8 machines × 750 soft / 1000 hard | `flyctl scale count` — live, no deploy |
| 3 | Backend DB thread pool | running machines × 2 workers × 64 threads | `DB_THREAD_POOL_SIZE` env (§4) |
| 4 | Supabase compute | Fixed by tier | Dashboard tier upgrade (§5) — **does not autoscale** |

Layers 1–3 are elastic or config-tunable. **Layer 4 is not** — Supabase compute
is a dedicated always-on instance, so it must be *pre-sized* ahead of a burst,
not scaled during one. That asymmetry is the reason for the alerting in §6:
the alert exists to buy you the ~2 minutes it takes to upgrade a tier.

---

## 2. Fly: how the burst pool actually works

`backend/fly.toml` + `.github/workflows/bootstrap-fly.yml`:

```
pool size            8 machines in yyz     (flyctl scale count 8)
always running       2                     (min_machines_running = 2)
suspended            6                     (auto_stop_machines = "suspend")
per machine          750 soft / 1000 hard  (type = "connections")
```

Fly's proxy resumes a suspended machine when running machines exceed
`soft_limit`. Concurrency is measured in **connections** because riders and
drivers each hold a long-lived WebSocket — connection count ≈ active users.

**Pool size and concurrency limits ship together, automatically.**
`deploy-fly.yml` runs `flyctl scale count 8` immediately after every deploy, so
`fly.toml`'s limits can never take effect without the machines to absorb them.
This is deliberate: the two are one capacity decision, and separating them once
meant a merge would have raised limits 4× on an unchanged 2-machine fleet —
strictly worse than the limits it replaced. `scale count` is idempotent, so the
step is a no-op once the pool exists. You do **not** need to run
`bootstrap-fly.yml` to get the pool; that workflow remains for first-time app
creation only.

- **Capacity:** 8 × 750 = **6,000** concurrent users before the last machine is
  woken; 8 × 1000 = **8,000** absolute before Fly rejects.
- **Before this pool existed:** a fixed 2 machines × 250 hard = **~500**, at
  which point new users were refused outright.

**Suspend vs stop.** Suspend restores from a memory snapshot in under a second;
a cold boot is 5–15 s (VM + uvicorn + lifespan DB probe + 18 background loops)
plus the 30 s health-check grace period. Suspend supports machines ≤ 2 GB, so
the 1 GB VM qualifies. If resumed machines show stale-connection errors beyond
what `run_sync`'s `httpx.NetworkError` retry absorbs
(`backend/repositories/_base.py:311-317`), set `auto_stop_machines = "stop"`.

**Checking state:**

```bash
flyctl status -a spinr-backend-yyz     # expect 2 started + 6 suspended at rest
flyctl scale count 8 --region yyz -a spinr-backend-yyz --yes   # resize the pool (live)
```

All 8 `started` outside a burst means autostop is not taking effect — check
`auto_stop_machines` in `backend/fly.toml`.

**Do not raise per-machine limits further without loadtest evidence.** CPU on
`shared-cpu-1x`, not memory, is the suspected binding constraint at 750+
connections, and it has never been measured. Add machines instead — that scales
CPU linearly, which raising `soft_limit` does not.

---

## 3. Rate limits: per-user, not per-IP

The burst-sensitive limiters key on **authenticated user id**, falling back to
client IP only for anonymous traffic (`get_user_or_ip_key` in
`backend/utils/rate_limiter.py`).

This matters because mobile carriers use carrier-grade NAT: hundreds of riders
share one egress IP. Under IP keying, one carrier's users shared a single
bucket — 5 bookings/minute across every Rogers customer in Saskatoon, and an
SOS that could 429 because strangers on the same carrier IP tapped ride actions.

Kill switch, if user keying itself misbehaves:

```bash
fly secrets set RATE_LIMIT_USER_KEYING=off -a spinr-backend-yyz
```

This reverts to IP keying by rolling machines with the new env value — a config
revert, no code deploy. Expect the CGNAT collisions to return.

OTP, login, and admin limiters deliberately stay IP-keyed: they guard
*unauthenticated* surfaces where there is no trustworthy user id yet.

---

## 4. Backend DB thread pool

Every Supabase call occupies one thread from `_DB_EXECUTOR`
(`backend/repositories/_base.py`), sized by `DB_THREAD_POOL_SIZE` (default 64)
**per uvicorn worker process**.

**Fleet ceiling = running machines × `UVICORN_WORKERS` × `DB_THREAD_POOL_SIZE`**

| Running machines | Workers | Pool size | Concurrent PostgREST calls |
|---|---|---|---|
| 2 (the floor, normal) | 2 | 64 | 256 |
| 4 (partial burst) | 2 | 64 | 512 |
| 8 (full burst) | 2 | 64 | **1,024** |
| 8 (full burst) | 2 | 32 | 512 |

Guidance:

- **Keep 64 while `min_machines_running = 2`.** The steady-state ceiling stays
  256 — identical to pre-pool behavior — and bursts are transient.
- **Drop to 32** (set `DB_THREAD_POOL_SIZE = "32"` in `fly.toml`'s `[env]`) when
  either: `min_machines_running` is raised to 4+, or the capacity alert shows
  repeated full-fleet wakes accompanied by Supabase-side latency.

Rationale: an oversized pool during a full wake can push 1,024 simultaneous
calls at a small Supabase tier, and the resulting failures trip the circuit
breaker (5 failures / 30 s) **fleet-wide**, converting a slow database into a
hard outage. A smaller pool queues in-process instead — requests wait rather
than fail. The app-side queue plus the breaker are the buffer; sizing the pool
below the DB's capacity is what keeps the buffer in front of the database
rather than inside it.

`UVICORN_WORKERS` stays at **2**: 4 workers on `shared-cpu-1x`/1 GB is
memory-tight (see the `fly.toml` header comment).

---

## 5. Supabase: pre-size, because it cannot autoscale

Supabase compute is a dedicated instance billed 24/7. There is no autoscaling —
**the tier you are on when a burst arrives is the tier you handle it with.**

### Tier guidance

| Situation | Tier | Net cost/mo |
|---|---|---|
| Steady state, 2 warm machines | **Small** ← pre-sized here | +$5 (after Pro's $10 compute credit) |
| Sustained 8-machine operation, or repeated DB-saturation alerts | **Medium** | +$50 |
| Beyond that | Large+ / read replicas | See Supabase pricing |

Each tier raises CPU, RAM, `max_connections`, and pooler pool size. Confirm the
current per-tier numbers in **Dashboard → Settings → Database → Connection
pooling** — they change, so do not plan capacity from memory.

### Upgrading (≈ 2 minutes, brief restart)

1. Supabase Dashboard → **Settings → Compute and Disk**
2. Select the tier → confirm. The database restarts (tens of seconds).
3. Watch `/health` and the backend logs — the circuit breaker may open briefly
   during the restart and will close on its own once the DB answers.

Schedule off-peak when possible. Compute bills hourly, so a **temporary** bump
to Medium for a known event (concert, holiday surge) and back down afterwards
costs only the hours used — this is a legitimate and cheap tactic.

### Which Supabase add-ons scale automatically (almost none)

This is the question that decides how much manual vigilance you need:

| Add-on | Automatic? | Notes |
|---|---|---|
| **Disk size** | **Yes** — grows automatically | The only thing that self-scales. Costs money as it grows; it will not stall you |
| **Compute** (Micro → Small → …) | **No — always manual** | The one that actually binds. Dashboard action, ~2 min, brief restart |
| **Read replicas** | No — manual | Requires Small compute minimum |
| **PITR** | No — manual, **$100/mo per 7 days** | Also requires Small compute minimum. **Not included in Pro** — see the warning below |
| **IPv4 address / custom domain / log drains** | No — manual | Not capacity-related |

**So: nothing about your database capacity scales on its own.** That asymmetry
against Fly (which now does scale itself) is the entire reason the
`capacity_watchdog` exists — the alert is what converts "the DB silently got
slow" into "someone upgrades the tier."

### Reading the billing page correctly

The Supabase **billing/usage page is not a capacity dashboard** and will look
healthy right up until the moment compute saturates. Quotas there (egress,
storage, MAU, Realtime connections, Edge Function invocations) are *plan limits*;
the thing that actually binds this project is *compute CPU*, which is not a
quota and does not appear as a percentage anywhere on that page.

For real capacity signals use **Dashboard → Database Health / Observability**
(CPU, memory, disk IO), not the usage summary.

Two Spinr-specific quirks worth knowing when reading that page:

- **Monthly Active Users will read 0** even with real users. Spinr does not use
  Supabase Auth — it issues its own JWTs (ADR-001). That zero is correct, not a
  reporting bug.
- **Realtime Concurrent Peak Connections will read 0**, and its 500 limit does
  **not** cap your users. Spinr's WebSockets terminate at FastAPI on Fly and
  fan out over Redis pub/sub; Supabase Realtime is not in the path.

The plan quota most likely to bind *eventually* is **egress**, since it scales
with API traffic. Project it from the current cycle rather than assuming
headroom.

> **Warning — verify before relying on the PITR runbook.**
> `docs/runbooks/pitr-restore.md` says *"Supabase plan includes PITR (confirm:
> Pro tier minimum with 7-day window)"*. That "confirm" was never resolved, and
> the assumption appears to be **wrong**: PITR is a separate **$100/mo** add-on
> (per 7 days of retention) that additionally requires at least **Small**
> compute. On Pro with Micro compute you have **daily backups with 7-day
> retention, not PITR** — so a restore is to the last daily snapshot, and the
> disaster-recovery runbook's recovery-point assumptions do not hold. Confirm in
> Dashboard → Database → Backups before an incident, not during one.

### Read replicas

Available on Pro+, billed as an additional compute instance each. The natural
first candidate is admin-dashboard analytics traffic, which ADR-001 notes
already queries Supabase directly and which competes with rider/driver traffic
on the primary today. Not provisioned yet.

---

## 6. Alert thresholds

The `capacity_watchdog` loop (`backend/utils/capacity_watchdog.py`, 60 s tick)
alerts when any signal trips. Alerts are per-machine by design — saturation is a
per-process condition — and each signal has a 30-minute cooldown so a sustained
burst does not spam the channel.

### Turning alerts on (required — they are silent by default)

Both settings default to unset, which means **the watchdog runs but reports
nothing**. Set at least one:

```bash
# Slack (or any Slack-compatible incoming webhook)
fly secrets set ALERT_WEBHOOK_URL=https://hooks.slack.com/services/... -a spinr-backend-yyz

# Email — comma-separated; uses the same SES/Resend path as receipts
fly secrets set ALERT_EMAIL_TO=ops@spinr.ca,oncall@spinr.ca -a spinr-backend-yyz
```

Verify with `fly secrets list -a spinr-backend-yyz` (values are hidden; you are
checking the names are present).

Notes that matter:

- **The channels are independent.** Both are attempted for every alert, so a
  dead Slack workspace does not cost you the email. The cooldown is shared per
  signal and is only stamped once at least one channel delivered — if nothing
  got through, the alert retries on the next tick.
- **Use a shared inbox or distribution list for `ALERT_EMAIL_TO`, not one
  person.** The email provider's suppression list means a single address that
  hard-bounces gets capacity alerts silently dropped from then on.
- **`ALERT_WEBHOOK_URL` is shared with `loop_watchdog`**, so setting it also
  turns on background-loop staleness alerts. Expect real findings on first
  deploy: those alerts were suppressed for the first hour of every process's
  life until the cooldown fix in this branch.

| Signal | Metric | Threshold | Means |
|---|---|---|---|
| DB pool saturation | `spinr_db_thread_pool_queue_depth` | > 50 for 3 consecutive ticks | Requests are queueing for DB threads — layer 3 or 4 is the bottleneck |
| DB call rejection | `spinr_db_calls_rejected_total` delta | any increase; immediate when `reason=circuit_open` | Requests are being refused outright — circuit open means the DB is already failing |
| Rate-limit pressure | `spinr_rate_limit_violation_total` delta | > 120/min for 3 ticks | Users are being 429'd in volume — either a real burst or a limit set too low |

The queue-depth threshold of 50 comes from the recorded breaking point in
`loadtest/README.md`.

Metrics are also scrapeable per machine:

```bash
curl -H "Authorization: Bearer $METRICS_AUTH_TOKEN" https://<machine>/metrics
```

`/metrics` **fails closed in production** if `METRICS_AUTH_TOKEN` is unset.

---

## 7. Playbook: a capacity alert fired

**1. Classify it.** Read the alert payload — it names the signal and the
machine (`FLY_MACHINE_ID`). Then check fleet state:

```bash
flyctl status -a spinr-backend-yyz          # how many machines are awake?
curl -H "Authorization: Bearer $METRICS_AUTH_TOKEN" https://<machine>/metrics \
  | grep -E 'spinr_db_thread_pool|spinr_db_calls_rejected|spinr_db_circuit_state|spinr_rate_limit_violation'
```

**2. Match the symptom to the fix:**

| Symptom | Bottleneck | Action |
|---|---|---|
| All 8 machines awake, connections near `soft_limit`, DB queue normal | Layer 2 (Fly) | `flyctl scale count 12 --region yyz` — live, takes effect immediately |
| DB queue depth high, `spinr_db_circuit_state` closed, all machines awake | Layer 4 (Supabase) | Upgrade the tier (§5). Consider `DB_THREAD_POOL_SIZE=32` after |
| `spinr_db_calls_rejected_total{reason=circuit_open}` climbing | Layer 4, already failing | Upgrade tier **now**; check Supabase status page for an incident |
| `spinr_rate_limit_violation_total` high, DB and connections healthy | Layer 1 | Identify the path from the metric label. A legitimately-too-low limit is a code change; do **not** reach for `RATE_LIMIT_USER_KEYING=off` — that makes CGNAT collisions worse, not better |
| Queue depth high on **one** machine only | Not capacity | Likely a stuck loop or a slow query on that machine. Check its logs; `flyctl machine restart <id>` |

**3. `RATE_LIMIT_USER_KEYING=off` is only for a bug in the keying itself** —
e.g. every request collapsing into one bucket. It is not a capacity lever.

**4. After the incident:** record what bound first and at what load in
`loadtest/README.md`'s breaking-point table. That table is the only empirical
capacity data this project has.

---

## 8. What is not measured

Stated plainly so nobody plans against numbers that were never observed:

- **No load test has ever run against this configuration.**
  `loadtest/locustfile.py` exists but needs a staging environment
  (ACTION_ITEMS E1); its dev-OTP interlock refuses to run against production.
- **Suspend/resume latency, proxy wake timing, and whether Fly suspends a
  machine holding live WebSockets** are all unverified platform behavior.
  The last one matters most: suspending a machine with live rider/driver
  sockets would drop them.
- **Real RSS and CPU at 750–1,000 connections per machine** are unprofiled.
  The memory figure (tens of KB per WS) is arithmetic; the CPU figure does not
  exist.
- **The 6,000-user capacity figure is arithmetic** (8 × 750), not a measured
  throughput. It assumes connections spread evenly and CPU is not the binding
  constraint — the second assumption is exactly the untested one.
- **Supabase per-tier connection numbers change**; confirm in the dashboard.

Treat every number here as a planning estimate until the loadtest harness has
somewhere to run.
