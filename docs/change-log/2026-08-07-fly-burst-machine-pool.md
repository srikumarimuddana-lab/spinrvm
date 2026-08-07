# Change Impact & Risk Log — Fly burst machine pool + connection-limit raise

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend (deployment configuration only — no application code) |
| Domain (Sentry tag) | rides (capacity affects all traffic; no domain logic changed) |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Burst-tolerance request; relates to ACTION_ITEMS C5 (Railway drift) and ADR-010's inaccurate "autoscaled" claim |

## 1. Issue / gap identified

The Fly deployment had **no working elasticity**: 2 machines with `hard_limit = 250`
connections each. Riders and drivers each hold a long-lived WebSocket, so
connection count is effectively the active-user count — the fleet hard-rejected
new users at roughly 500 concurrent. `auto_start_machines = true` was set, but
because `bootstrap-fly.yml` scaled the app to exactly 2 machines, there were no
stopped machines for Fly's proxy to start. The autoscaling knob was on with
nothing to scale into.

## 2. Root cause

Two settings were correct in isolation but never combined into a working
mechanism:

- `auto_stop_machines = false` meant idle machines were never suspended, so the
  fleet size was static at whatever `fly scale count` last set.
- `fly scale count 2` sized the *pool* to the *floor*, leaving `auto_start_machines`
  with an empty pool to draw from.

The `soft_limit`/`hard_limit` values (200/250) were also sized for a
request-oriented workload rather than a WebSocket-per-user one, where every
active app user permanently occupies one connection slot.

## 3. Fix / remediation

Provision a pool of 8 machines in `yyz`, keep 2 running at all times, and let
Fly suspend the other 6. Fly's proxy resumes suspended machines when running
machines exceed `soft_limit`. Raise per-machine limits to WebSocket-appropriate
values (750 soft / 1000 hard).

Resulting capacity: 8 × 750 = 6,000 concurrent users before the last machine is
woken; 8 × 1000 = 8,000 absolute ceiling. Previously ~500.

`"suspend"` rather than `"stop"`: suspend resumes from a memory snapshot in
under a second, versus a 5–15 s cold boot (VM start + uvicorn + lifespan DB
health probe + 18 background loops) plus the 30 s health-check grace period.
During a burst that difference is the whole point of the change.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but configuration-only.** No application code is
touched by this commit. Every rider, driver, corporate, and admin request path
runs on these machines, so a mistake here affects everything — which is why the
rollback is a single `flyctl` command (§8).

What this interacts with:

- **The 18 background loops** (`backend/core/lifespan.py`) now run on up to 8
  machines instead of 2 during a burst. This is explicitly safe per the existing
  `fly.toml` header comment and ADR-007 §L83-88: every loop is replay-safe via
  atomic DB claims, idempotency keys, or Redis leader locks, and worker/machine
  count is documented as "a capacity knob only". No loop was modified.
- **Shared Redis** (`redis.spinr.ca`) carries rate-limit counters, OTP lockouts,
  driver presence, WS pub/sub, and loop leader locks. More machines means more
  contention on the same keys. Rate-limit *correctness* is unaffected (counters
  are shared, so limits do not multiply per machine — `core/middleware.py:633-648`
  enforces a real Redis URL in production for exactly this reason), but Redis
  load scales with fleet size. `spinr_redis_used_memory_bytes` is already on
  `/metrics`; it is a candidate signal for the capacity watchdog (Commit F).
- **Supabase connection pressure.** Fleet DB-call ceiling is
  `running machines × UVICORN_WORKERS × DB_THREAD_POOL_SIZE`. At the 2-machine
  floor that is 2 × 2 × 64 = 256, unchanged from today. During a full 8-machine
  wake it becomes 1,024 potential concurrent PostgREST calls. `DB_THREAD_POOL_SIZE`
  is deliberately **not** changed in this commit (that would bundle a second
  live-behavior change); the guidance to drop it to 32 when the floor rises is
  in `docs/runbooks/capacity-scaling.md`, and the app-side queue plus the
  existing circuit breaker are the buffer in the meantime.
- **WebSocket connection counts in the admin dashboard** are per-replica
  (`socket_manager.py:115-122` documents this). With more machines awake, the
  WS-health card undercounts by a larger factor. Pre-existing behavior, now more
  visible — no code change here, flagged for awareness.
- **Failover posture.** Raising Fly capacity to ~6,000 users widens the gap with
  the Railway standby, which is drifting from `main` (ACTION_ITEMS C5, deploy
  blocked by an environment protection rule) and sits at 1 replica. A Fly-region
  failure during a burst would land that traffic on a stale, much smaller
  deployment. This commit does not make that worse in absolute terms, but it
  does make the asymmetry larger. Called out in the failover runbook (Commit B).
  Fixing C5 is out of scope here.

**Could this regress a flow that currently works?** The realistic failure mode
is a resumed machine serving a request with a stale upstream TLS connection.
`run_sync` already retries `httpx.NetworkError` and `RemoteProtocolError`
(`repositories/_base.py:311-317`), which is the same class of failure the
existing HTTP/2 GOAWAY retry was built for. If resume-related anomalies appear
anyway, the documented fallback is `auto_stop_machines = "stop"`, which trades
resume latency for a guaranteed-cold process.

## 5. User-experience effect

- **Riders and drivers:** strictly positive and invisible when it works — during
  a burst, users who would previously have been refused a connection now get
  one. No copy change, no new screen, no new error state.
- **Visible mid-session?** No. Existing connections are unaffected; this only
  changes whether *new* connections are accepted and on which machine they land.
- **Internal admin:** the WS-health card's per-replica counts become a smaller
  fraction of the fleet total during bursts (see §4). No functional change.
- **No notification or copy changes.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/fly.toml` | `auto_stop_machines` false → `"suspend"`; `soft_limit` 200 → 750; `hard_limit` 250 → 1000; added a burst-capacity comment block documenting the mechanism, the suspend-vs-stop rationale, and the do-not-raise-further warning | Creates the suspend/resume elasticity and sizes per-machine limits for a WebSocket-per-user workload |
| `.github/workflows/bootstrap-fly.yml` | `flyctl scale count 2` → `8`; step renamed to say "2 warm + 6 suspended burst pool"; added a comment explaining pool ≠ running count | Provisions the machines that `auto_start_machines` needs in order to have anything to start |
| `docs/change-log/2026-08-07-fly-burst-machine-pool.md` | This log | CLAUDE.md mandate for live-tested surfaces |

## 7. Before / after

```toml
# Before — fixed 2-machine fleet, hard ceiling ~500 concurrent users
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 2

  [http_service.concurrency]
    type = "connections"
    soft_limit = 200
    hard_limit = 250
```

```toml
# After — 2 warm + 6 suspended, proxy-woken at soft_limit, ~6000 concurrent
  auto_stop_machines = "suspend"
  auto_start_machines = true
  min_machines_running = 2

  [http_service.concurrency]
    type = "connections"
    soft_limit = 750
    hard_limit = 1000
```

```yaml
# Before (bootstrap-fly.yml)
      - name: Scale to 2 machines in yyz
        run: flyctl scale count 2 --region yyz -a "${FLY_APP}" --yes
```

```yaml
# After
      - name: Scale to 8 machines in yyz (2 warm + 6 suspended burst pool)
        run: flyctl scale count 8 --region yyz -a "${FLY_APP}" --yes
```

## 8. Rollback plan

**No redeploy required.** Both halves revert with `flyctl` against the live app:

```bash
flyctl scale count 2 --region yyz -a spinr-backend-yyz --yes   # shrink pool to the old fixed fleet
```

To revert the concurrency limits without waiting for a code deploy, the machine
config can be set directly (`flyctl machine update --metadata` / a `fly.toml`
revert on the next deploy). The `fly.toml` values are picked up on deploy; the
machine count is live-mutable at any time, which is the faster lever and the one
that actually governs cost.

Nothing in this change touches live data — no Stripe charges, wallet deltas,
ride state, or insurance-period rows are affected — so a config revert is a
complete rollback, not a partial one.

Documented fallback for resume anomalies specifically:
`auto_stop_machines = "stop"` in `fly.toml` (one-line change, next deploy).

## 9. Verification performed

- [x] Blast-radius grep performed — searched for `fly-autoscaler`, `autoscal`,
      `scale count`, `machines api`, `FLY_API_TOKEN` repo-wide: the only machine-count
      call sites are `bootstrap-fly.yml:114` (changed here) and
      `docs/runbooks/railway-fly-failover.md:114` (documentation, updated in Commit B).
      `deploy-fly.yml` never sets machine count, so the routine deploy path needs
      no change. Confirmed `UVICORN_WORKERS` is consumed only in `backend/Dockerfile:108`.
- [x] Reviewed against relevant CLAUDE.md conventions — background-loop replay
      safety (loops unchanged and documented replay-safe), deployment section,
      and the failover-parity requirement (secrets unchanged; machine counts are
      explicitly not required to match across providers per the failover runbook).
- [x] Config-only change — no automated tests exercise `fly.toml`; the Python
      test suite is unaffected by this commit. Test runs are reported in the
      commits that touch application code (C–G).
- [ ] Manual repro in staging — **not possible**: no staging environment exists
      (ACTION_ITEMS E1).
- [x] Feature-flag consideration — not applicable; this is infrastructure
      configuration whose "flag" is the `flyctl scale count` command itself.

## What was NOT verified

Stated explicitly per CLAUDE.md rather than letting silence imply coverage:

- **No production build or deploy was run.** This is a config change validated by
  reading, not by applying. Everything below needs a real Fly deploy to confirm:
- **Suspend/resume latency was not measured.** The sub-second claim is Fly
  platform behavior, not something observed for this app's 1 GB image.
- **Proxy wake timing at `soft_limit` was not observed** — specifically whether
  machines wake fast enough that a burst arriving faster than resume time still
  sees brief `hard_limit` rejections on the 2 warm machines.
- **Whether Fly suspends a machine still holding live WebSocket connections is
  unconfirmed.** Expected behavior is that it does not suspend until connections
  drain, but this was not verified and matters: suspending a machine with live
  rider/driver sockets would drop them.
- **Real RSS and CPU at 750–1,000 connections were not measured.** The memory
  estimate (tens of KB per WS ⇒ 40–80 MB) is arithmetic, not a profile. CPU on
  `shared-cpu-1x` is the suspected real bound and is entirely unmeasured — this
  is why the config comment forbids raising per-machine limits further without
  loadtest evidence.
- **Fly's suspend semantics were taken as platform knowledge**, including the
  ≤2 GB machine support, the `"suspend"` enum spelling, and rootfs-only billing
  for suspended machines. Re-confirm against Fly's current docs before deploying.
- **The load-test harness could not be run** — `loadtest/locustfile.py` requires
  a staging target (ACTION_ITEMS E1) and its dev-OTP interlock refuses to run
  against production.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`flyctl scale count 2`, no deploy)
- [x] Blast radius is stated, not assumed (loops, Redis, Supabase, failover, WS counts)
- [x] No silent behavior change to an already-shipped flow — the UX field is
      filled in; the only user-facing delta is fewer rejections under load
