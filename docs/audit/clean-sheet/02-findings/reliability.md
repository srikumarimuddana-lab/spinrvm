# R13 — Reliability / SRE & Observability — Findings

Scope: deploy topology (Fly primary / Railway standby), failover, all 44 background
loops + watchdog, Redis fallback behaviour, WebSocket cross-replica fan-out, SLA/KPI
measurement, alerting, incident MTTD/MTTR, DR/backup, deploy safety, load shedding.
Repo `/home/user/spinrvm`. Report-and-recommend only.

---

## 0. Method

Read `CLAUDE.md` (Deployment, Background task safety, Redis transparency, WebSocket
auth, Observability Conventions, Performance SLAs), the master audit prompt §4–§7,
`greenfield-extensions.md` §4/§7/§9, `roles.md` §3, and `00-history.md` /
`W0-SUMMARY.md`. Read `backend/core/background_loop_registry.py` in full (150 lines —
the current, live 45-entry `LOOP_CATALOG`, not any count quoted elsewhere) and
`backend/core/lifespan.py` in full (975 lines — every `_spawn()` call site and its
surrounding doc-comment, the watchdog registration self-check, and the WS pub/sub
startup block). For every loop's replay-safety mechanism, grepped and read the
actual lock/claim primitive it calls (`utils/redis_client.py`'s `redis_set_nx`,
`redis_set_nx_strict`, and `try_acquire_leader_lock`), then cross-checked each loop's
real heartbeat cadence against `utils/loop_monitor.py`'s `LOOP_THRESHOLDS` table by
reading each loop's own `while True` / `asyncio.sleep` structure — not just the
interval implied by its display name. Read `docs/runbooks/railway-fly-failover.md`,
`docs/adr/007-fly-primary-railway-standby.md`, `docs/runbooks/pitr-restore.md`,
`docs/runbooks/redis-down.md`, `backend/fly.toml`, `backend/fly.worker-canary.toml`,
`docs/incidents/*.md`, and `backend/utils/ws_pubsub.py` / `backend/socket_manager.py`
(WS fan-out call sites, not full files — targeted grep + read of the send/broadcast
methods). Grepped `ACTION_ITEMS.md` for C1, C11, C5, C129, C12 before filing any
finding as new. Did not use the Railway MCP tools (session had no confirmed
`cooperative-harmony`/`spinrvm` binding at hand-off time — see §12); did not touch
Fly per policy; did not use Sentry/Stripe/Redis MCP (down, per brief).

---

## 1. Steelman

Contrary to what a naive read of CLAUDE.md's own (admittedly stale — "42 loops")
prose would suggest, the background-loop layer is **materially more mature than the
2026-08-26 DB-optimization audit or the 2026-09-24 rapid baseline's A3 lane fully
credit**, and several things are worth stating plainly before the findings below:

- **The watchdog now self-verifies its own registration**, not just the loops it
  watches. `core/lifespan.py:831-861` computes `_WATCHDOG_LOOP_NAMES` from the same
  registry the spawner used, then raises `RuntimeError` in production if any spawned
  loop is unwatched, watched-but-never-spawned, or double-registered. This is
  *exactly* the class of defect ("silently unregistered loop defeats the watchdog")
  ranked as blocker #27 in an earlier audit — it is now a boot-time hard gate, not a
  standing risk. VERIFIED (`lifespan.py:830-861`).
- **The leader-lock primitive itself is honest about its own limits.**
  `redis_set_nx`'s docstring (`utils/redis_client.py:229-264`) explicitly documents
  the exact silent-degradation failure mode ("every replica proceeds independently")
  that used to be baked in, and the 2026-08 fix made it raise instead of silently
  falling back — callers now decide explicitly, per-loop, whether to fail open
  (`try_acquire_leader_lock`, load-shedding only) or fail closed
  (`redis_set_nx_strict`, money loops). This is the right shape for a distributed-
  systems primitive and better than "one lock helper, one behaviour" would be.
- **WS cross-replica fan-out (P0-B3) is real, not aspirational.** Every
  `send_personal_message` call publishes through `utils/ws_pubsub.py`'s Redis
  channel before any local delivery, `broadcast()` (the one method that is genuinely
  local-only) has zero live callers (confirmed by grep, not just the docstring's own
  claim), and `broadcast_to_admins` correctly fans out via `publish_broadcast`. This
  answers a chunk of this lane's own charter ("did the right replica's connection
  actually receive it") with a clean VERIFIED yes for the unicast path, which is the
  one that matters for dispatch/ride-state events.
- **Money loops fail closed, not open, on a real Redis outage.** `preauth_capture`,
  `payment_retry`, `orphaned_hold_reconciler`, `corporate_autotopup`, and
  `auto_payout` all use `redis_set_nx_strict`, which raises (never silently grants a
  process-local lock) when Redis is unconfigured or down — the tick is skipped
  loudly (ERROR log + `spinr_loop_lock_unavailable_total` metric +
  `record_dependency_failure`) rather than every replica racing to capture/charge in
  parallel. This is the correct trade for a payment path: a missed tick, not a
  double-charge.
- **Fly's own health check cannot be tripped by a stale background loop.**
  `server.py`'s live `/health` (`:260-274`) deliberately returns `200` even when the
  DB is degraded, and does not surface `loop_monitor` status at all — the endpoint
  that *does* embed loop staleness (`routes/main.py`) is dead code, explicitly
  documented as such in its own module docstring. This means the loop-threshold
  bugs in §2/§3 below are an **alerting blind spot**, not a **mass-restart risk** —
  worth being precise about, since the two would call for very different urgency.
- The failover and PITR-restore runbooks are unusually candid about their own
  unverified assumptions (the PITR runbook literally opens with "UNCONFIRMED AND
  LIKELY FALSE — verify before relying on this runbook") rather than presenting a
  paper procedure as settled. That is the right posture for a runbook nobody has
  exercised.

---

## 2. Findings

### REL-001 — Loop-watchdog staleness thresholds are wrong in both directions for ~30 of 44 loops, and one loop can never be flagged at all
- Hierarchy: L2 Platform Foundation & Schema › L3 Background jobs › L4 Loop
  observability › L5 `utils/loop_monitor.py` staleness detection
- Severity: **HIGH**   Priority score: S×B×L = 4×4×4 = 64 (the only live signal
  for 44 loops is degraded/absent for most of them)
- Status: VERIFIED   Existing item: related to, not a duplicate of, the fix
  already recorded in `ACTION_ITEMS.md:23361-23371` (the 4 worker-wave loops were
  found missing from `LOOP_THRESHOLDS` and added) — this finding is the much
  larger remainder that fix did not cover, plus a distinct failure mode (a loop
  that never heartbeats at all) that fix's pattern did not catch.
- Adversary: on-call engineer at 3 a.m. trusting the loop-watchdog Slack channel;
  regulator/auditor asking whether the T4A annual tax-slip job is monitored
- Evidence:
  - `backend/utils/loop_monitor.py:18-46` — `LOOP_THRESHOLDS` only tunes 20 of the
    45 `LOOP_CATALOG` names (`background_loop_registry.py:31-77`); every other
    loop falls back to `_DEFAULT_THRESHOLD = 7200` (2 h, line 21).
  - **Direction A — threshold too loose for sub-minute safety/dispatch loops**,
    all on the 2 h default: `safety_checkin (30s)` (`utils/safety_checkin_loop.py`
    — sends the SOS-adjacent "are you OK" check-in and opens a safety incident on
    no response), `route_deviation_alerter (30s)`, `offer_expiry_reaper (10s)`
    (durable backstop for the `<2s` P95 dispatch-offer SLA),
    `driver_readiness_reconciler (20s)`, `stuck_ride_sweeper (60s)`,
    `driver_claim_reaper (60s)`. A hung or silently-no-op instance of any of
    these could run undetected for up to 2 hours before the watchdog even
    *could* alert (subject to §3 below) — a safety check-in loop and a dispatch
    backstop loop are disproportionate candidates for a generic 2 h floor.
  - **Direction B — threshold too tight relative to actual heartbeat cadence,
    producing a permanent false "stale" once `check_and_alert` fires** (verified
    by reading each loop's own sleep structure, not its display-name interval):
    `kyb_reverification (24h)` — single heartbeat per `asyncio.sleep(86400 * (0.9
    + random.random()*0.2))` tick (`utils/kyb_reverification.py:149-150`), so
    `elapsed > threshold` (2h) is true for ~22 of every 24 hours;
    `dispute_evidence_reminder (6h)` — single heartbeat per
    `_TICK_INTERVAL_SECONDS` (`utils/dispute_evidence_reminder.py:167-186`), false
    "stale" ~4 of every 6 hours; `subscription_expiry (6h)` — single heartbeat per
    `await asyncio.sleep(6 * 3600)` (`routes/drivers/subscriptions.py:2023-2029`),
    same ~67%-of-the-time false stale; `document_expiry (12h)` — single
    heartbeat per ~12h tick (`utils/document_expiry.py:441-453`), false stale
    ~83% of the time; `retention_guard_monitor (6h)` — single heartbeat per
    `CHECK_INTERVAL_SECONDS` (`utils/retention_guard_monitor.py:278-288`), same
    ~67%. Compare to `reconciliation (daily 02:00 UTC)` and `t4a_annual_job
    (yearly Feb 28)`, both of which correctly heartbeat on a fast **inner** poll
    (`utils/reconciliation.py:63-69`, `_LOOP_POLL_SECONDS=60`) independent of how
    rarely the real work fires — proving the fast-inner-poll pattern is already
    known and used elsewhere in this codebase, just not applied consistently.
  - **Direction C — no heartbeat at all, ever, by design gap**:
    `t4a_annual_job (yearly Feb 28)` polls every 60s
    (`utils/t4a_annual_job.py:38,81-86`) but **calls `record_heartbeat` nowhere in
    the file** (grepped, zero hits). It therefore sits permanently in
    `get_loop_status`'s `"never_ticked"` state (`loop_monitor.py:106-116`), which
    the function explicitly treats as *not unhealthy* ("startup wait is
    expected"). A CRA-regulatory annual tax-slip issuance job (T4A, ≥$500 driver
    earnings) can crash-loop or silently no-op for a full year and the
    loop-watchdog — the only live alerting path for any of these 44 loops — has
    no mechanism that could ever flag it.
- What happens (plain language): for the five Direction-B loops, if
  `ALERT_WEBHOOK_URL` is ever set, on-call gets an hourly Slack "loop stale:
  kyb_reverification" (or the other four) message for most of every day/week —
  the kind of chronic false alarm that trains a team to ignore the channel, so a
  *real* stale-loop alert on `payment_retry` or `safety_checkin` arrives into a
  muted channel. For the six Direction-A loops, a real hang in the safety
  check-in or dispatch-offer backstop path is invisible for up to 2 hours. For
  `t4a_annual_job`, there is no scenario in which the watchdog alerts on it at
  all, regardless of `ALERT_WEBHOOK_URL`.
- Root cause: `LOOP_THRESHOLDS` was hand-populated per loop as each was added or
  fixed (see the ACTION_ITEMS precedent above), with no CI check tying every
  `LOOP_CATALOG` entry to a threshold entry and no check that a threshold is
  proportionate to the loop's *actual* heartbeat cadence (only its cosmetic
  display-name suffix, which for 3 of the 5 Direction-B loops does match the real
  cadence — the bug is threshold-vs-cadence, not naming).
- Recommendation: (1) add a test asserting every non-watchdog `LOOP_CATALOG` name
  has an explicit `LOOP_THRESHOLDS` entry (extends the pattern that already
  caught the 4 worker-wave gaps) so this can't silently regress again; (2) size
  each new/fixed threshold to the loop's actual heartbeat cadence, not its
  interval label — for the five Direction-B loops, either add a fast inner poll
  (cheapest, matches the `reconciliation`/`t4a_annual_job` precedent) or set the
  threshold to ≥ 2× the real per-tick sleep; (3) add `record_heartbeat` to
  `t4a_annual_job.py`'s inner 60s poll loop (it already has the fast poll — it
  just never calls the function) and give it its own threshold (~10 min, matching
  `capacity_watchdog`'s pattern for a frequently-polling-but-rarely-acting loop);
  (4) tighten the six Direction-A loops to cadence-proportionate thresholds (e.g.
  `safety_checkin`: 3–5 missed ticks ≈ 2–3 min, not 2 h).
  Alternative considered: leave the 2 h default as a deliberately generous
  catch-all and rely on `_restartable`'s crash-log + Sentry for fast detection of
  hard crashes — rejected as the sole answer because it does nothing for a
  *hang* (awaiting a stuck call, not raising), which is exactly the failure mode
  the watchdog exists to catch and `_restartable` cannot.
- Blast radius: `loop_monitor.py`/`loop_alert.py` are shared by every one of the
  44 loops plus the dedicated `worker.py` process's 3 (which are already fully
  tuned — no change needed there). No loop *behaviour* changes; this is purely a
  monitoring-threshold fix.
- Rollout: additive (new test + threshold values); no migration, no flag needed.
- Verification to close: the new coverage test passes; a manual `time.monotonic()`
  fault-injection test (patch `_heartbeats` to simulate each Direction-B loop's
  real cadence) confirms `get_loop_status` no longer reports "stale" mid-cycle
  for a healthy loop, and confirms `t4a_annual_job` transitions out of
  `"never_ticked"` after its first heartbeat.

### REL-002 — All 44-loop alerting collapses to one channel (`ALERT_WEBHOOK_URL`), whose configured status is UNKNOWN, with no fallback surface
- Hierarchy: L2 Observability & Monitoring › L3 Alerting delivery
- Severity: **HIGH**   Priority score: 4×5×3 = 60
- Status: VERIFIED (mechanism) / UNKNOWN (whether the one channel is even
  configured — carried forward from W0, not re-resolved here)   Existing item:
  extends **C11** (metrics aggregation/alerting not live — ADR-010 scaffolding
  merged, not deployed) and confirms the rapid baseline's A6 "0 of 8 alerts" with
  the loop-specific mechanism A6 didn't trace.
- Adversary: on-call engineer; auditor asking "show me the alert that would fire
  if `payment_retry` stopped ticking"
- Evidence: `utils/loop_alert.py:34-47` — `check_and_alert` no-ops entirely
  (`if not webhook_url: return`) when `ALERT_WEBHOOK_URL` is unset;
  `core/lifespan.py:820-823` passes `settings.ALERT_WEBHOOK_URL` straight through
  with no fallback. The loop-status JSON that *would* show staleness without the
  webhook (`routes/main.py`'s `health_check`) is confirmed dead code (module
  docstring, `routes/main.py:1-13`, not mounted in `server.py`). `utils/metrics.py`
  has no loop-status gauge (grepped, zero hits) — so `/metrics` (already gated
  behind C11's "nothing scrapes production metrics" gap) wouldn't help either
  even if it were being scraped. `W0-SUMMARY.md` line 23 lists
  `ALERT_WEBHOOK_URL` set? as a still-open human-only question.
- What happens (plain language): every one of the 44 loops' liveness signal —
  including money loops (`payment_retry`, `auto_payout`, `preauth_capture`,
  `orphaned_hold_reconciler`), safety loops (`safety_checkin`,
  `route_deviation_alerter`), and the dispatch backstop (`offer_expiry_reaper`,
  `stuck_ride_sweeper`) — depends on exactly one env var being set to exactly
  one Slack-compatible webhook. If it is unset (status genuinely unknown to this
  session), the *only* detection path for a stalled loop is a human noticing
  the downstream symptom (rides stuck in `searching`, a driver's card hold never
  released, a T4A never issued) or reading Railway/Fly logs directly for the
  `_restartable` crash-restart ERROR line — neither of which is proactive.
- Root cause: the metrics-aggregation/alerting design (ADR-010) that was meant to
  give this a second, dashboard-based surface is scaffolded but not deployed
  (C11); the loop-specific webhook was built as a lighter-weight stopgap and
  became, in practice, the only channel actually wired to something a human
  would see.
- Recommendation: (1) resolve the human-only question — confirm
  `ALERT_WEBHOOK_URL` is set in both Fly and Railway production secrets, and add
  it to `deploy/backend-required-env.txt` (the single source of truth
  `railway-fly-failover.md` already uses for parity checks) so the standby-parity
  monitor would flag a one-sided miss; (2) treat C11 (Grafana Cloud + Alloy
  deploy) as the priority unblock, since it is the only path to a
  non-single-point-of-failure alert surface; (3) short-term, mount
  `routes/main.py`'s `api_router` (or port its loop-status JSON into
  `server.py`'s live `/health`, additively — e.g. under a `background_loops` key
  that never affects the 200-status contract already covered by the steelman
  above) so a synthetic-monitoring probe (`monitoring/synthetic-checks.yaml`
  already exists) could observe loop staleness without depending on the webhook
  at all.
  Alternative considered: page directly from Sentry using the loguru→Sentry
  bridge on the existing `logger.opt(exception=True).error` crash line in
  `_restartable` — rejected as a full replacement because it only fires on a
  raised exception, not a hang, which is the gap this alerting layer specifically
  exists to cover.
- Blast radius: none from the recommended fix itself (additive JSON field / env
  var documentation); the underlying single-point-of-failure risk already exists
  today across all 44 loops.
- Rollout: additive; no migration.
- Verification to close: confirm `ALERT_WEBHOOK_URL` presence via the standby-
  parity monitor once added to the required-env list; a synthetic probe (or
  manual curl) against the additive `/health` field once shipped.

### REL-003 — `SPINR_PROCESS_ROLE` is unset in the real Fly/Railway configs, so every warm and burst machine (and the Railway standby) runs all 45 loops — confirms and sharpens A3-001
- Hierarchy: L2 Platform Foundation & Schema › L3 Deploy topology › L4 Process
  role placement
- Severity: HIGH (carried at A3-001's rating; this pass adds the canary-config
  evidence that the fix already exists and is dark)   Priority score: 4×4×5 = 80
- Status: VERIFIED   Existing item: **A3-001** (`rapid-baseline-2026-09-24/
  A3-transmission-capacity.md:13-21`) — not re-filed; this section adds the
  concrete proof that the split mechanism is built, tested via a canary config,
  and simply never adopted in the files that actually deploy.
- Adversary: cost auditor; flaky network (DB saturation from redundant loop
  polling compounds a real outage)
- Evidence: `backend/fly.toml` `[env]` (`:26-31`) sets `ENV`, `PORT`,
  `SUPABASE_REGION`, `UVICORN_WORKERS`, `PYTHONUNBUFFERED` — no
  `SPINR_PROCESS_ROLE`. `resolve_process_role(None, env="production")` therefore
  returns `"all"` (`background_loop_registry.py:84-91`), and
  `should_spawn_on_api(..., "all", ...)` returns `True` unconditionally
  (`:110-114`) — every loop runs on every one of Fly's 8 machines (2 warm +
  6 burst, `fly.toml:46,68`) **and** on Railway. Contrast with
  `backend/fly.worker-canary.toml:26-28`, which does set
  `SPINR_PROCESS_ROLE`/`SPINR_WORKER_LOOP_ALLOWLIST` per process group — proving
  the split is implemented, wired through `worker.py`, and validated well enough
  to have its own canary config, but that config is not `fly.toml` (the one
  `deploy-fly.yml` actually deploys) or `railway.json`.
- What happens (plain language): DB load from background loops has a floor that
  scales with *machine count*, not user count — a burst machine that Fly wakes
  purely to absorb a traffic spike immediately adds a full copy of every 15s/30s/
  60s loop's polling load to the same Supabase tier the traffic spike is already
  stressing. ADR-007 itself calls this out as an accepted trade-off ("Dual hot
  deploys run the loops twice over... safe only because both share the same
  Supabase and aliased Redis") but that ADR's own text still says "16 background
  loops" — the actual number is 44, nearly 3× what the accepted-risk decision was
  made against.
- Root cause: the process-role registry and `worker.py` were built (dated
  comments reference "T9"/"T12"/"T13" phases and a dispatch-pool rollout still
  gated behind an unset DSN) as a staged migration, but the final step — flipping
  `fly.toml`/`railway.json` to use it — was never taken.
- Recommendation: set `SPINR_PROCESS_ROLE=api` on the `app`/`burst` process
  groups in the real `fly.toml` (and the Railway equivalent) and stand up one
  dedicated `worker` process/machine for the `worker_wave1` set (currently just
  `push_retry`, `zoho_desk_sync`, `driver_onboarding_reminders` — all already
  fully threshold-tuned per REL-001). This is a capacity/cost fix, not a
  correctness one — every loop is already documented as replay-safe across
  concurrent replicas (per §3 below), so the risk of the change is
  narrow (verify the `worker` process actually starts and its 3 loops
  keep ticking under the new topology).
  Alternative considered: add leader locks to the ~14 currently-unlocked loops
  instead of splitting processes — rejected (matches A3-001's own reasoning):
  locks fail open on a Redis error, so they reduce load on the happy path without
  removing the worst-case multiplier, and don't address the DB-connection-floor
  problem at all (every machine still opens a `DB_THREAD_POOL_SIZE`-sized pool
  regardless of whether its loops are doing useful work).
- Blast radius: touches only deploy config (`fly.toml`, `railway.json`) plus
  standing up `worker.py` as a real running process — no application code change.
  `docs/runbooks/capacity-scaling.md` and `docs/adr/007` both need a text update
  once this ships (both currently cite stale loop/machine counts — A3-005).
- Rollout: additive/flag-shaped by construction (`SPINR_PROCESS_ROLE` already
  defaults to `all`, i.e. today's behaviour, if unset) — can be rolled out
  machine-group by machine-group.
- Verification to close: after the split, confirm via `/deploy-info` or logs
  that `app`/`burst` machines log "Skipped background task for process role api:
  push_retry (30s)" etc., and that a standalone `worker` machine's logs show
  those 3 loops ticking and their heartbeats reaching the watchdog.

### REL-004 — Both cross-provider failover (C1) and the backup-restore path (E7/PITR) have never been exercised, and PITR's own assumption is self-flagged as likely wrong
- Hierarchy: L2 Platform Foundation & Schema › L3 DR/backup › L4 Failover drill,
  PITR restore drill
- Severity: **HIGH**   Priority score: 4×5×3 = 60
- Status: VERIFIED   Existing item: **C1** (failover drill, `ACTION_ITEMS.md:
  13567-13570`, status "never exercised") and **E7** (backup-restore drill,
  `:20222-20251`, status "scaffolding done, drill itself still not run") — both
  already tracked; this finding is the composite observation that Spinr's two
  primary DR mechanisms (provider failover and data restore) are *both*
  simultaneously unverified, which is the scenario a real regional-outage drill
  in this audit's adversary list (§5 "a regional Fly outage") would actually hit.
- Adversary: regulator/auditor asking "prove your RTO"; a real Fly `yyz` regional
  outage on a Friday at 5 p.m.
- Evidence: `docs/runbooks/railway-fly-failover.md:308-331` — the drill checklist
  exists, is detailed, and ends with `Drill log: _(none yet)_`. Same file's
  "Capacity asymmetry" callout (`:200-213`) notes Railway runs
  `numReplicas: 1` with no autoscaling against Fly's burst-provisioned 8-machine
  pool — so an untested failover would also be a *capacity* surprise, not just a
  DNS-cutover surprise, if it were exercised today. `docs/runbooks/
  pitr-restore.md:22-31` opens with "UNCONFIRMED AND LIKELY FALSE — verify before
  relying on this runbook" regarding whether Supabase's plan even includes true
  PITR (vs. daily-snapshot-only on Pro + Micro compute) — i.e., the **stated RTO
  target (4h) and RPO target (5 min)** on that same runbook's header
  (`pitr-restore.md:3`) may not be achievable on the actual provisioned tier, and
  nobody has restored a branch to find out either way (`ACTION_ITEMS.md:
  20245-20251`).
- What happens (plain language): if Fly's Toronto region degrades hard enough to
  need a real cutover, the first time anyone learns whether the Cloudflare CNAME
  swap, the shared-Redis alias, the JWT_SECRET parity, and the Railway capacity
  headroom all actually work together is *during* the incident, at 5 p.m. on a
  Friday, with real riders and drivers on active trips. Same for data recovery:
  if a bad migration or bug corrupts rows, the runbook's own RPO/RTO numbers are
  unverified against the real Supabase tier.
- Root cause: both drills require access (Supabase org/billing for a scratch
  project + real PITR restore; a live low-traffic production window + Cloudflare
  DNS access) that no session running inside this repo has ever had — both are
  genuinely human/ops actions, not engineering gaps that more code closes.
- Recommendation: schedule both drills in the same maintenance window (a failover
  drill is a good time to also prove the standby's Redis/DB dependencies are
  independently healthy, which is also a precondition PITR's runbook needs
  verified). Confirm the actual Supabase backup tier (Dashboard → Database →
  Backups) *before* the drill, not during, so the PITR runbook's RTO/RPO header
  can be corrected to match reality either way.
  Alternative considered: skip the live drill and instead formally verify each
  precondition in isolation (Redis alias reachable from both providers, secret
  parity via `/deploy-info` fingerprints, Supabase backup tier confirmed) —
  useful and already partly automated (`standby-parity-monitor.yml`), but
  explicitly not a substitute per this audit's own framing: "a deploying,
  healthy standby is necessary but not sufficient evidence the failover path
  itself works end to end" (`CLAUDE.md` Deployment section, on C1 specifically).
- Blast radius: a live drill touches production DNS and (for the PITR half) a
  scratch Supabase project only — the runbooks already scope both drills to be
  non-destructive to the live system when followed as written.
- Rollout: N/A (operational activity, not a code change).
- Verification to close: `railway-fly-failover.md`'s "Drill log" section is filled
  in with real timings; `verify_restore.py` is run against a real restored
  branch and its RTO output is recorded in `pitr-restore.md`.

### REL-005 — WS Redis outage silently drops cross-replica delivery by design, with no metric distinguishing "local delivery" from "cross-replica delivery lost"
- Hierarchy: L2 Observability & Monitoring › L3 WS fan-out › L4 Redis pub/sub
  degraded mode
- Severity: MEDIUM   Priority score: 3×4×2 = 24
- Status: VERIFIED (mechanism)   Existing item: new — this is a sharper version
  of the documented, accepted degradation in `utils/ws_pubsub.py`'s own module
  docstring, not a contradiction of it.
- Adversary: flaky network / a Redis blip during a dispatch-heavy period
- Evidence: `utils/ws_pubsub.py:22-27` documents the fallback explicitly: "Redis
  outage ⇒ `publish()` / `publish_broadcast()` return False and the caller falls
  back to the original local-only path... silent miss for the others [replicas]
  rather than bringing WS traffic down entirely." `socket_manager.py:373-376`
  confirms: on a `False` return from `pubsub.publish`, `send_personal_message`
  calls `_deliver_local` directly — which only reaches the target if it happens
  to be connected to *this* machine. The `spinr_ws_fanout_duration_ms` metric
  (`socket_manager.py:369-376`) is labelled `path=pubsub` or `path=local` but
  does not distinguish "local because the client actually is local" from "local
  because Redis was down and this may have missed the real client" — both look
  identical in the metric.
- What happens (plain language): during a WS-Redis outage, a rider and driver on
  different Fly machines (which — per REL-003 and Fly's proxy behaviour — is a
  routine occurrence, not an edge case) silently stop receiving each other's
  ride-state events. `redis-down.md`'s own runbook already rates this
  "**Critical** for multi-replica WS fan-out" and its only mitigation is "scale
  backend to 1 replica temporarily" — a manual, minutes-scale response to an
  outage with sub-second user impact (a rider not seeing "driver arrived").
- Root cause: the degrade-to-local-only design is a deliberate, documented choice
  (keep the socket working rather than closing it), but nothing distinguishes the
  degraded state in telemetry, so detecting "we are currently dropping
  cross-replica WS events" depends on someone noticing the Redis-down alert
  (§4 below) and independently remembering this consequence, rather than a
  direct signal.
- Recommendation: emit a distinct counter (e.g.
  `spinr_ws_fanout_pubsub_unavailable_total`) at `socket_manager.py:373`'s
  `if await pubsub.publish(...)` `False` branch, and surface it on the same
  dashboard/alert path as the general Redis-down alert once C11 is live. This is
  a pure-addition, low-risk change.
  Alternative considered: buffer undelivered messages and replay on Redis
  recovery — rejected as a bigger change than the gap warrants; `ws_pubsub.py`
  already has a durable per-client outbox/seq-replay mechanism
  (`OUTBOX_MAXLEN`/`?last_seq` reconnect recovery cited in `socket_manager.py`'s
  own comments) for the *client-reconnect* case, which is a different failure
  mode than *cross-replica* Redis unavailability and would need Redis to exist
  in the first place to replay from.
- Blast radius: additive metric only; no behavioural change.
- Rollout: additive.
- Verification to close: a test that forces `pubsub.publish` to return `False`
  and asserts the new counter increments exactly once per fallback delivery.

---

## 3. The 44-loop table

Every name in `LOOP_CATALOG` (`backend/core/background_loop_registry.py:31-77`),
in spawn order, 44 loops + the watchdog (45 registry entries total — this is the
live count as of this pass; do not hardcode it elsewhere per `W0-SUMMARY.md`'s own
warning). **Placement** is the registry's `api`/`worker_wave1`/`deferred` tag —
today (REL-003) every placement still runs on every API process regardless, since
`SPINR_PROCESS_ROLE` is unset. **Mechanism** cites the actual primitive read in
source, not the display-name interval. **2-replica** describes the effect of two
processes ticking the same loop concurrently, assuming Redis is healthy.
**Redis-down** describes behaviour with `REDIS_URL`/the relevant URL unset or
erroring. **Crash mid-tick** describes recovery on the next tick/restart.
**Watchdog** flags whether `LOOP_THRESHOLDS` has a tuned entry (see REL-001 for
the loops marked ⚠).

Legend for Mechanism: **DB-claim** = atomic conditional `UPDATE ... WHERE`
(ride-acceptance-style CAS) or unique-constraint insert; **flag** = a
`*_sent_at`/`*_warned`/`*_processed`-style idempotency column checked
`IS NULL`; **lock(strict)** = `redis_set_nx_strict`, fails closed (raises, no
in-process fallback, tick explicitly skipped); **lock(open)** = `redis_set_nx`
or `try_acquire_leader_lock`, fails open (Redis error/absence ⇒ every replica
proceeds, each winning its own in-process lock); **read-only** = no writes, so
concurrency is inherently safe.

| # | Loop | Placement | Mechanism (path) | 2-replica effect | Redis-down effect | Crash mid-tick | Watchdog |
|---|---|---|---|---|---|---|---|
| 1 | subscription_expiry (6h) | deferred | flag (`expiry_warned_3d`/`expiry_warned`) for warn + lock(open) for enforce (`routes/drivers/subscriptions.py:1643-1672`) | Warn: safe (flag). Enforce: could double-send the offline-kick push once (explicitly accepted, `:1661-1670`) | Falls open — all replicas enforce; warn flags still prevent duplicate warn pushes | Safe — flags are per-window, re-checked next tick | ⚠ default 2h; real cadence 6h → false-stale ~67% of cycle if alerted (REL-001 Direction B) |
| 2 | surge_engine (2min) | deferred | lock(open), per-service-area bucket (`utils/surge_engine.py:479`) | Idempotent recompute — redundant identical writes, not corruption; fare-calc always re-clamps to 2.5× cap regardless | Falls open — every replica recomputes independently; values converge (deterministic function of current ratio) | Safe — recompute is stateless per tick | tuned (8min) |
| 3 | scheduled_dispatcher (60s) | api | lock(open) (`utils/scheduled_rides.py:86`) | Safe — dispatch itself is DB-claim-guarded downstream (dispatch charter, R8 territory) | Falls open; downstream claim is the real guard | Safe | ⚠ default 2h (60s cadence — loose but not broken) |
| 4 | payment_retry (5min) | deferred | lock(strict) (`utils/payment_retry.py:58-69`) | N/A — second replica can't acquire; DB status filter is the documented backstop if it ever did | **Fails closed** — tick skipped, ERROR + metric + dependency-failure recorded (heartbeat suppressed) | Safe (INFERRED — not fully read this pass; DB status-filtered retry is the standard pattern in every other money loop read) | tuned (20min) |
| 5 | preauth_capture (5min) | deferred | lock(strict); DB claim is "the real double-capture guard" per its own comment (`utils/preauth_capture.py:206-231`) | N/A — fails closed on lock contention; DB claim backstops a race | **Fails closed** — verified in full: `lock_failed=True` path skips `_capture_tick()`, suppresses heartbeat (`:210-226`) | Safe — `settle_card` writes `paid`/`failed`; a ride stuck `processing` is picked up by `payment_retry` after 30 min (`:111-114`) | ⚠ default 2h — money loop, 5min cadence, generous but not broken |
| 6 | referral_payout (5min) | deferred | lock(open) bucket + `referral_payouts` UNIQUE constraint (`utils/referral_payout.py:250`, `lifespan.py:366-367`) | Safe — UNIQUE claim is the real guard even if the lock races | Falls open; UNIQUE constraint prevents double-pay | Safe | ⚠ default 2h |
| 7 | driver_claim_reaper (60s) | api | lock(open); releases orphaned claims (idempotent — releasing an already-released driver is a no-op) | Safe by construction | Falls open; harmless duplicate release attempts | Safe | ⚠ default 2h (60s cadence — loose) |
| 8 | document_expiry (12h) | deferred | flag (shares claimed block with suspension CAS, `utils/document_expiry.py:66-69,452-453`) | Safe (flag) | INFERRED safe (flag-based, not lock-dependent) | Safe | ⚠ default 2h; real cadence ~12h → false-stale ~83% of cycle (REL-001 Direction B) |
| 9 | driver_onboarding_reminders (15min) | worker_wave1 | DB claim log, idempotent per driver/reminder/date (`utils/driver_onboarding_reminders.py:73-93`) | Safe | Safe (no Redis dependency) | Safe | tuned (45min) |
| 10 | corporate_autotopup (10min) | deferred | lock(strict) — off-session Stripe charge (`utils/corporate_autotopup.py:70-75,219`) | N/A — fails closed | **Fails closed** | Safe (INFERRED, same pattern as preauth_capture) | tuned (40min) |
| 11 | corporate_low_balance (1h) | deferred | flag (`claim_low_balance_notification`) | Safe | INFERRED safe (flag-based) | Safe | tuned (4h) |
| 12 | allowance_reset (1h) | deferred | atomic CAS per period roll-forward (F8); `apply_reset` idempotent | Safe | INFERRED safe | Safe | tuned (4h) |
| 13 | kyb_reverification (24h) | deferred | flag (`kyb_reverify_flagged_at`) | Safe | INFERRED safe | Safe | ⚠ default 2h; real cadence ~24h → false-stale ~92% of cycle (REL-001 Direction B, worst case) |
| 14 | route_finalizer (15s) | deferred | lock(open) (`utils/route_finalizer.py`) | Safe — claims one pending route per tick | Falls open | Safe | tuned (15min) |
| 15 | route_gap_monitor (15s) | deferred | lock(open) (`utils/route_gap_monitor.py`) | Safe — idempotent audit events only, never touches ride/fare | Falls open | Safe | tuned (15min) |
| 16 | route_deviation_alerter (30s) | api | `SET NX` dedupe keys per ride (`utils/route_deviation_alerter.py:39-84`), alert-only (no ride/driver mutation) | Safe — worst case is a duplicate alert, gated by dedupe key | Falls open — dedupe key unavailable could allow duplicate escalations | Safe | ⚠ default 2h — **safety loop, 30s cadence** (REL-001 Direction A) |
| 17 | stale_intent_reconciler (15min) | api | durable `updated_at` signal + Redis-healthy gate + presence double-check | Safe (INFERRED — not fully read) | Gated off when Redis unhealthy per its own design (per lifespan comment `:470-480`) | Safe | ⚠ default 2h |
| 18 | safety_checkin (30s) | api | atomic `SET ... NX` (`utils/safety_checkin_loop.py:15,37-39`) | Safe — dedupe by construction | Falls open | Safe | ⚠ default 2h — **SOS-adjacent safety loop, 30s cadence** (REL-001 Direction A, highest-priority instance) |
| 19 | retention_purge (24h) | deferred | lock(open) belt-and-braces over a naturally-idempotent SECURITY DEFINER Postgres function (`lifespan.py:504-515`) | Safe — Postgres function itself is idempotent | Falls open; Postgres function is the real guard | Safe | tuned (48h) |
| 20 | data_export_purge (1h) | deferred | idempotent-per-object Storage delete; unclaimed row retried next tick | Safe | INFERRED safe (no Redis dependency documented) | Safe | ⚠ default 2h |
| 21 | reconciliation (daily 02:00 UTC) | deferred | `SET NX EX` daily window claim; fast 60s inner poll (`utils/reconciliation.py:63-69`) | Safe — one replica per day wins | Falls open (INFERRED on exact behaviour; not fully read) | Safe | ⚠ default 2h — but fast inner poll means no false-stale (REL-001 positive control) |
| 22 | stripe_reconcile (24h) | deferred | `SET NX EX` leader lock, 24h window (`utils/stripe_reconcile.py:31,98-105`) | Safe | Falls open (INFERRED) | Safe | tuned (48h) |
| 23 | dispute_evidence_reminder (6h) | deferred | flag (`evidence_reminder_sent_at IS NULL` atomic UPDATE, `utils/dispute_evidence_reminder.py:10-16`) | Safe (flag) | Safe (flag, no Redis dependency in the claim itself) | Safe | ⚠ default 2h; real cadence 6h, no inner poll → false-stale ~67% of cycle (REL-001 Direction B) |
| 24 | ledger_projection (15min) | deferred | UNIQUE(event_id, account, side) constraint + whole-batch insert; Redis lock is throttle-only (`lifespan.py:561-566`) | Safe — UNIQUE constraint is the real guard | Falls open; constraint still holds | Safe | ⚠ default 2h |
| 25 | distance_reconciliation (daily 04:00 UTC) | deferred | `SET NX` leader lock (`utils/distance_reconciliation.py:33,205`) | Safe | Falls open (INFERRED) | Safe | tuned (48h) |
| 26 | period1_distance_finalizer (5min) | api | lock(open) + claim-before-write (`lifespan.py:584-593`) | Safe | Falls open; claim-before-write is the real guard | Safe | tuned (20min) |
| 27 | driver_daily_rollup (30min) | deferred | `SET NX` leader lock; idempotent upsert per driver-day (`utils/driver_daily_rollup.py:40-45,338`) | Safe — upsert is idempotent even if the lock races | Falls open; upsert still safe | Safe | tuned (90min) |
| 28 | stale_p3_closer (15min) | api | `SET NX` leader lock; touches only the open span's `ended_at`, alert-first (auto-close gated off by default) | Safe | Falls open | Safe | tuned (45min) |
| 29 | insurance_period_reconciler (10min) | api | `SET NX` leader lock; only opens new correct rows via existing RPC (append-only preserved) | Safe | Falls open | Safe | ⚠ default 2h |
| 30 | h3_index_reconciler (2min) | deferred | lock(open) (`try_acquire_leader_lock`, per test file `test_h3_index_reconciler.py`) | Safe | Falls open (documented as pure load-shedding, correctness guard elsewhere — matches `try_acquire_leader_lock`'s own contract) | Safe | ⚠ default 2h |
| 31 | t4a_annual_job (yearly Feb 28) | deferred | flag (`spinr:t4a:issued:{prior_year}` `SET NX EX`, `utils/t4a_annual_job.py:10,47-49`) | Safe (flag) | Falls open (INFERRED) | Safe | **NONE — never calls `record_heartbeat` at all (REL-001 Direction C)** |
| 32 | driver_statements (30min) | deferred | insert-claim on `driver_statements` UNIQUE index (migration 272) | Safe | INFERRED safe (UNIQUE constraint) | Safe | ⚠ default 2h |
| 33 | stuck_ride_sweeper (60s) | api | try_acquire_leader_lock (open) + atomic per-ride DB claim (`lifespan.py:655-658`) | Safe — atomic claim is the real guard | Falls open; atomic claim still holds | Safe | ⚠ default 2h — **dispatch-recovery loop, 60s cadence** (REL-001 Direction A) |
| 34 | stale_in_progress_ride_alerter (5min) | api | flag/dedupe via `SET NX`, alert-only (no ride mutation, cited in `rider-journey.md` RIDERJ-002) | Safe — worst case is a duplicate alert | Falls open | Safe | ⚠ default 2h |
| 35 | retention_guard_monitor (6h) | api | `SET NX` dedupe per (table, trigger) pair, read-only RPC | Safe (read-only + dedupe) | Falls open | Safe | ⚠ default 2h; real cadence 6h, no inner poll → false-stale ~67% of cycle (REL-001 Direction B) — notable because this is the loop that detects tampering with the append-only audit triggers, i.e. a security control |
| 36 | orphaned_hold_reconciler (15m) | deferred | lock(strict) + `updated_at` CAS + Stripe idempotency key (`lifespan.py:692-699`) | N/A — fails closed | **Fails closed** | Safe — next tick re-finds the ride | ⚠ default 2h |
| 37 | offer_expiry_reaper (10s) | api | try_acquire_leader_lock (open) + per-offer atomic claim, restart-safe backstop for the `<2s` dispatch SLA (`lifespan.py:707-712`) | Safe — atomic claim is the real guard | Falls open; atomic claim still holds | Safe | ⚠ default 2h — **dispatch-SLA backstop, 10s cadence, tightest case of Direction A** |
| 38 | driver_readiness_reconciler (20s) | api | try_acquire_leader_lock (open); pauses fenced by online epoch under `SKIP LOCKED` — safe to overlap by design (`lifespan.py:719-721`) | Safe by construction (`SKIP LOCKED`) | Falls open; `SKIP LOCKED` fencing still holds | Safe | ⚠ default 2h |
| 39 | suspension_reactivation (10min) | deferred | atomic conditional update (`utils/suspension_reactivation.py:60-64,193`) | Safe | INFERRED safe | Safe | ⚠ default 2h |
| 40 | push_retry (30s) | worker_wave1 | try_acquire_leader_lock (open); exponential backoff up to 5 attempts | Safe (open lock, load-shed only) | Falls open | Safe | tuned (2min) |
| 41 | zoho_desk_sync (10min) | worker_wave1 | `SET NX` lock; upsert (replay-safe) | Safe | Falls open; upsert idempotent | Safe | tuned (40min) |
| 42 | support_sla_breach_sweep (5min) | deferred | flag (`sla_breach_alerted_at IS NULL` atomic UPDATE, migration 377, `utils/support_sla.py:31-33`) | Safe | Safe (flag, no Redis dependency) | Safe | ⚠ default 2h |
| 43 | capacity_watchdog (60s) | api | read-only — no DB/Redis writes, replay-safe by construction; per-replica alerting intentional (`lifespan.py:774-779`) | N/A — read-only | N/A — read-only | N/A | tuned (4min) |
| 44 | auto_payout (1h, Sundays) | deferred | lock(strict) + `week_key` UNIQUE + per-driver idempotency keys (`lifespan.py:787-791`) | N/A — fails closed | **Fails closed** | Safe (INFERRED, same pattern family) | ⚠ default 2h — 1h real cadence, so not falsely stale (only loop in the "long interval, single heartbeat" set that happens to land under the 2h floor) |
| — | loop_watchdog (5min) | api | N/A — the watcher, not watched (excluded from its own list by design, `lifespan.py:832-833`) | N/A | N/A (its own tick degrades gracefully — `check_and_alert` no-ops without a webhook regardless of Redis) | Safe — `_restartable` covers it like every other loop | N/A (self) |

**Note on "Redis-down" for the fail-open (`lock(open)`) rows**: with `REDIS_URL`
genuinely unset (not just erroring), `redis_set_nx`'s in-process fallback means
*every replica* independently "wins" its own local lock (`redis_client.py:661-663`'s
own docstring: "every replica wins its own local lock, and the loop behaves exactly
as it did before the lock existed"). For every loop marked "Safe" in the table
above, this is fine because the *real* correctness guard is the DB claim/flag/
constraint next to the lock, not the lock itself — this is `try_acquire_leader_lock`'s
documented contract (`redis_client.py:643-651`) and was spot-checked, not
individually re-derived per row.

---

## 4. Redis-down matrix

| Feature | Redis key(s) | Behaviour when Redis unavailable | Fails open or closed | Evidence |
|---|---|---|---|---|
| OTP codes + lockout counters | `otp:*` | In-process dict fallback, per-replica | **Open** (weaker, not broken) — lockout resets on restart/differs per replica | `CLAUDE.md` Redis transparency; `docs/runbooks/redis-down.md:12-13` |
| Rate-limit counters (SlowAPI) | `ratelimit:*` | In-process dict, per-replica — effective limit becomes `N × limit` across N replicas | **Open** | `redis-down.md:14,45-51` |
| WS pub/sub cross-replica fan-out | `spinr:ws:dispatch` channel | Falls back to local-only delivery; cross-replica messages silently dropped | **Open at the transport level, but functionally a silent drop for the wrong-replica client** (see REL-005) | `ws_pubsub.py:22-27`; `redis-down.md:15,53-58` |
| Background-loop leader locks (majority — `lock(open)` rows in §3) | `spinr:<loop>:lock` | Every replica proceeds independently each tick | **Open** — correctness relies on the DB claim/flag next to the lock, per row in §3 | `redis_client.py:643-663` |
| Money-loop leader locks (`payment_retry`, `preauth_capture`, `corporate_autotopup`, `orphaned_hold_reconciler`, `auto_payout`) | same, via `redis_set_nx_strict` | Tick explicitly skipped, ERROR logged, heartbeat suppressed | **Closed** — no replica proceeds until Redis recovers | `redis_client.py:267-277`; verified in full for `preauth_capture.py:206-231` |
| Driver marker write-gate (`spinr:locwrite:*`) | 3s coalescing TTL | Falls back to an in-process 3s floor per replica; write volume never exceeds pre-gate rates | **Open** (degrades to pre-optimization behaviour, not broken) | `redis-down.md:18` |
| Driver presence (`is_online` effective-online read) | Redis presence key | Composed at read time with the durable `is_online` flag; a Redis gap no longer corrupts the persistent flag (presence-sweeper was retired specifically to fix this dual-source-of-truth class) | **Open, by deliberate design** — degrades to "can't confirm live reachability," not to a wrong persisted state | `lifespan.py:461-469` |
| Offer/dispatch state (30s TTL) | active-offer keys | Dispatch retries; the durable `offer_expiry_reaper` (§3 row 37) is the DB-level backstop regardless of Redis state | **Open**, backstopped by DB | `redis-down.md:16` |
| Session/refresh-token cache | session cache keys | Forces a Supabase read instead (slower, still correct) | **Open**, correctness preserved | `redis-down.md:17` |

Redis-down behaviour is documented per-feature in `redis-down.md`, but that
runbook's own "Known Gaps" section (`:97-101`) still lists "no SRE alert fires
when rate-limit Redis falls back — fix blocks launch" as open — worth
re-confirming against the current `capacity_watchdog`/loop-watchdog state (REL-002)
rather than assuming it's stale; this pass did not find a rate-limit-specific
Redis-down alert distinct from the general loop-watchdog path.

---

## 5. Failover & DR

Covered in depth in REL-004. Summary against the charter's specific question:

- **Has failover been drilled under traffic?** No. `docs/runbooks/
  railway-fly-failover.md`'s drill log is empty (`:331`). `ACTION_ITEMS.md` C1 is
  open, status "never exercised."
- **What IS proven**: the standby-parity automation (`.github/workflows/
  standby-parity-monitor.yml` + `scripts/standby_parity.py`) verifies, on a daily
  schedule, that both providers serve the same build SHA, have the same required
  env vars, and (via `/deploy-info` HMAC fingerprints keyed by `JWT_SECRET`) the
  same secret values — without ever reading a secret's actual value
  (`railway-fly-failover.md:243-257`). This closes the *"is the standby a faithful
  replica right now"* question but explicitly not the *"does the cutover
  mechanism itself work under load"* question — CLAUDE.md's own Deployment
  section makes this distinction in almost these words.
- **What is NOT proven**: DNS TTL-bounded cutover latency in practice, whether
  Railway's non-autoscaling single-replica capacity actually survives a
  burst-sized failover load, whether the shared-Redis-alias TLS-hostname setup
  (`railway-fly-failover.md:158-172`'s own caveat about a plain CNAME failing
  cert verification against a managed Redis provider) is actually configured
  correctly today — this is a documented footgun, not a confirmed-working
  setup.
- **Backup/PITR**: Supabase plan-tier PITR availability is explicitly
  self-flagged as unconfirmed in the runbook (§REL-004). No restore drill has
  ever run against a real restored branch; `verify_restore.py` exists and is
  unit-tested against mocks only (`ACTION_ITEMS.md:20245-20251`).
- **What state is lost on each component's loss** (derived from §4 plus this
  section): Redis loss → degrades many features gracefully, closes money loops,
  drops cross-replica WS (REL-005) — no data loss, availability/consistency
  hit only. Supabase loss → full outage for anything DB-backed (`/health`
  reports `db.status: degraded` but stays 200 — see Steelman — so the process
  keeps serving cached/WS traffic); recovery is PITR/daily-backup, RPO/RTO
  unverified. A single Fly machine loss → absorbed by the 8-machine pool +
  rolling deploy health checks (`fly.toml:37-59`), no drill needed at that
  granularity. A full Fly-region loss → the untested cross-provider cutover
  above.

---

## 6. SLA → metric → alert → delivery

Extends `A6-observability.md`'s OBS-002 (3 of 8 CLAUDE.md Performance-SLA rows
have zero measuring metric) with the alert-delivery question this lane owns:

| CLAUDE.md SLA row | Metric exists? | Scraped in production? | Alert rule exists? | Delivery channel live? |
|---|---|---|---|---|
| Dispatch offer→accept < 2s | Yes (`spinr_dispatch_offer_to_accept_duration_ms`) | **No** — C11 (nothing scrapes production metrics; per-process, resets on deploy) | Config drafted, not imported (ADR-010 §3, `metrics-agent/grafana/*`) | **No** — Grafana Cloud account/deploy steps not done (C11) |
| Fare estimate < 300ms | Yes (`spinr_fare_calc_duration_ms`) | No (C11) | No | No |
| Fare settlement < 1s | Not verified this pass (R9 territory) | No (C11) | No | No |
| WS fan-out < 100ms | Yes (`spinr_ws_fanout_duration_ms`, verified `socket_manager.py:369-376`) | No (C11) | No | No |
| Driver location write < 150ms | **No** — only a failure counter (`spinr_live_marker_write_failures_total`), no latency histogram (OBS-002) | N/A | No | No |
| Auth token refresh < 200ms | **No** — only event counters, no duration histogram (OBS-002) | N/A | No | No |
| Stripe webhook processing < 500ms | **No** metric of any kind found (OBS-002) | N/A | No | No |
| Migration apply < 30s | N/A — applied by hand (HIST-006), not instrumented | N/A | No | No |

**Every row's alert-delivery answer is "no" for the same two root causes**: (1)
C11 — the metrics-aggregation layer (Grafana Alloy + Cloud) is fully scaffolded
in-repo but never deployed, so nothing scrapes `/metrics` in production even for
the SLAs that *do* emit a metric; (2) even once scraped, the 2 drafted alert
rules (dispatch-latency breach, payment-failure-rate breach — ADR-010 §3, the
only 2 of 8 rows with a drafted rule at all) are **also** wired to the same
`ALERT_WEBHOOK_URL` this lane's REL-002 finding shows is a single, unverified,
single-point-of-failure channel. Resolving C11 without also confirming
`ALERT_WEBHOOK_URL` closes half the gap; resolving REL-002 without C11 closes
the other half. Neither alone gives a working SLA alert.

---

## 7. Incident MTTD/MTTR

`docs/incidents/` contains 3 documents; only one (the Supabase service-role-key
exposure) has enough dated detail to reason about detection timing, and it is a
security/PIPEDA incident (R10 territory), not an infra/loop/deploy incident — cited
here only for its detection-mechanism pattern, not duplicated as a finding.

- **2026-07-30 Supabase key exposure**: detected **not** by any alerting system,
  but by an engineer manually verifying a CI secret-scanning gate — the
  credential was live and publicly readable for ~3.5 months before that manual
  check (`docs/incidents/2026-07-30-supabase-service-role-key-public-exposure.md:
  1-3,168-185`). MTTD here is best read as "undetectable by any automated system
  that existed at the time," since the gate that eventually caught it
  (`.gitleaks.toml`) had been silently loading zero rules the whole window.
- **2026-09-10 driver-app crash**: detected via a human PR report claiming a
  "100% fleet-wide P0," acted on and shipped a mitigation *before* anyone pulled
  the actual Sentry data — which, once pulled after the fix, showed 3 events / 1
  user (`docs/incidents/2026-09-10-driver-app-skia-heatmap-turbomodule-crash.md:
  1-17,50-57`). This is not an infra-loop incident, but it is directly relevant
  evidence for this lane's MTTD/MTTR question: **detection and severity
  assessment both ran through human/social channels (a PR comment) rather than
  a paged alert with real data attached**, and the real data was only checked
  *after* the fix shipped, not during triage.
- **No incident in `docs/incidents/` involves a background-loop stall, a WS
  fan-out gap, a Redis outage, or a failover event.** Combined with REL-002's
  finding that the loop-watchdog's alert-delivery status is unknown, this session
  cannot compute a real MTTD/MTTR number for the failure modes this lane's
  charter is most directly about (a stalled loop, a Redis blip, a bad deploy) —
  there is no recorded instance of one happening and being caught. This is
  itself worth stating plainly: **the absence of loop/deploy incident postmortems
  is not evidence such incidents haven't happened; `record_dependency_failure`'s
  own state is lost on restart** (`loop_monitor.py:30-31`, `loop_alert.py`'s
  `_last_alerted` dict, same note) **and the only alert channel (REL-002) may
  never have been exercised, let alone logged.** UNKNOWN, not zero.
- `docs/runbooks/on-call.md` exists and defines an on-call process (not read in
  full this pass — out of the loop/deploy/Redis/WS scope this lane prioritized
  in the time budget); whether it has ever been invoked for an infra incident is
  UNKNOWN.

**MTTD/MTTR verdict: UNKNOWN for the loop/deploy/Redis/WS failure classes this
lane owns.** No postmortem exists to compute from, and the one alerting channel
that would generate one (loop-watchdog → `ALERT_WEBHOOK_URL`) has unverified
configuration status. This is itself the headline finding for §7, not a gap in
this audit pass.

---

## 8. Deploy safety

- **Health checks**: Fly (`fly.toml:54-59,84-89`) — 30s grace, 30s interval, 5s
  timeout, `GET /health`, rolling strategy with `max_unavailable=1`
  (`:37-39`); a bad deploy's new machines must pass `/health` before old ones are
  replaced. `/health` itself is deliberately liveness-only (returns 200 even on a
  degraded DB — see Steelman), so this gate catches "the process didn't start"
  but not "the process started but a background loop is broken" (REL-001/002) or
  "the process started but is serving wrong data."
- **Rollback path — admin-dashboard**: `docs/runbooks/admin-rollback.md` gives a
  clean, tested-shape Vercel "Promote to Production" path (<2 min).
- **Rollback path — backend (Fly/Railway)**: **no dedicated backend rollback
  runbook was found** in `docs/runbooks/` (grepped the full 70-file listing for
  a Fly/Railway equivalent of `admin-rollback.md` — none exists). Fly's rolling
  deploy + health-check gate prevents a *crash-looping* bad deploy from taking
  traffic, but there is no documented procedure for "the new version passed
  `/health` but is behaviorally wrong" (e.g., a logic regression that doesn't
  crash) beyond the general `git revert` + redeploy path, which CLAUDE.md itself
  flags is not a rollback plan for anything already applied to live data. This
  is a gap: `admin-rollback.md` exists and is good; its backend counterpart does
  not.
- **Migrations applied by hand**: confirmed via HIST-006
  (`00-history.md:136-182,375,509`) — `apply-supabase-schema.yml` is
  `workflow_dispatch`-only (a human triggers it), 116 migration files are
  untracked by any apply record (G2), 8 are pending today (C125) including the
  RLS-enable for the `settings` table that holds live Stripe/Twilio/Maps keys
  (deferred since 2026-08-25, C43). The apply step needs a `DATABASE_URL` no
  session in this audit programme has had. This means a deploy's application
  code and its schema can be out of sync for an unbounded window between merge
  and whenever a human runs the workflow — a real deploy-safety gap distinct
  from anything CI gates.
- **Feature-flag rollback**: the `app_settings`-in-DB pattern (CLAUDE.md Critical
  Conventions) is used consistently across the loops read this pass
  (`stale_p3_closer`'s auto-close gate, `insurance_period_reconciler`'s downgrade
  gate, `route_deviation_alerter`'s enable gate, `dispatch_direct_pool_enabled`
  in `lifespan.py:88-140`) — this is a real, working no-redeploy rollback
  mechanism for the behaviors it covers, and is used well.
- **Secret drift risk**: `railway-fly-failover.md`'s own safety checklist
  (`:223-241`) is explicit that a `JWT_SECRET` mismatch between providers logs
  users out at random — this is caught today only by the daily standby-parity
  monitor's fingerprint check, not by any deploy-time gate that would block a
  bad secret push before it reaches production.

---

## 9. Scenario cards

Per greenfield-extensions §4's chain (`TRIGGER → DETECTION → SYSTEM STATE → USER
EXPERIENCE → BUSINESS RULE → RECOVERY → ESCALATION → AUDIT RECORD → TEST`) plus
the three distributed-step questions, applied to this lane's two highest-value
scenarios not already fully chained in dispatch.md/rider-journey.md:

### Scenario A — a background loop hangs (not crashes) mid-tick on the only replica watching it

| Step | Detail |
|---|---|
| TRIGGER | A loop's tick awaits a call that never returns (e.g. a Supabase/Stripe call with no timeout — not verified whether every loop's downstream calls are bounded; `run_sync`'s deadline propagation is the general mechanism, per `init_database`'s own comment about it being "inactive" at boot) |
| DETECTION | `_restartable`'s crash-restart (`lifespan.py:271-295`) does **not** trigger — a hang is not an exception. Only `loop_monitor`'s staleness check can catch this, and only if (a) the loop has a tuned threshold (REL-001) and (b) `ALERT_WEBHOOK_URL` is set (REL-002) |
| SYSTEM STATE | The loop's asyncio task sits alive but idle forever; `background_tasks` still lists it as running |
| USER EXPERIENCE | Depends entirely on which loop — for `offer_expiry_reaper`/`stuck_ride_sweeper`, riders/drivers experience the exact symptom already documented in RIDERJ-002/DRIVER-005 (stuck `in_progress`/`searching` rides with no automated resolution) |
| BUSINESS RULE | None enforces a maximum silent-hang duration |
| RECOVERY | Manual — restart the process (which also silently "fixes" the symptom without anyone diagnosing the hang's root cause, unless logs are read) |
| ESCALATION | Only the (possibly unconfigured) loop-watchdog webhook, and only after the (possibly mistuned, per REL-001) threshold elapses |
| AUDIT RECORD | None automatic |
| TEST | Not located — `test_lifespan_watchdog_coverage.py` tests the *registration* self-check, not a live hang scenario |
| Status | **Unhandled** for the safety/dispatch loops in Direction A; **partially handled** (alert exists but is potentially mistuned or unwired) for everything else |

### Scenario B — Fly and Railway are both live and both take traffic briefly (mixed-fleet window during a DNS TTL propagation)

| Step | Detail |
|---|---|
| TRIGGER | A DNS cutover during a real incident, or the TTL propagation window of any routine CNAME change |
| Can this run twice? | Yes, deliberately — ADR-007 accepts "dual hot deploys run the loops twice over" as safe *because* both share Supabase + aliased Redis. §3's table confirms this holds for every loop read this pass (DB claim/flag/constraint is the real guard in every "Safe" row) |
| What if the network fails after a write succeeds? | Not scenario-specific — covered by each loop's own atomic-claim/idempotency mechanism per §3; no evidence found of a loop that would double-act on a network partition after a successful DB write |
| Duplicated/out-of-order events? | WS events fan out via Redis pub/sub identically regardless of which provider's replica handled the original write (REL-005's caveat about Redis-down aside) — no provider-specific WS split-brain risk found |
| DETECTION | The standby-parity monitor checks *configuration* parity daily, not *live dual-serving* — nothing detects "both providers are currently receiving user traffic" as an event in itself |
| SYSTEM STATE | Correct, per the above, contingent entirely on the shared-Redis-alias TLS/DNS setup actually being live and correctly aliased on both sides (REL-004's caveat) |
| Status | **Handled** for the loop-safety half (well-evidenced); **Partial** for the "is Redis genuinely shared right now" precondition, which is asserted but not live-verified outside the drill this lane recommends running |

---

## 10. Rebuild Delta cards

## Epic: Reliability Platform (background loops, deploy topology, alerting)
- Verdict per inherited pattern: **MODIFY** — the primitives (atomic DB claims,
  idempotency flags, fail-open vs fail-closed Redis locks, a self-verifying
  watchdog registry) are already the right shape; what's missing is consistent
  application (REL-001) and a real second alert surface (REL-002/C11), not a new
  architecture.
- Keep (already best-in-class): the `redis_set_nx` vs `redis_set_nx_strict`
  split with callers deciding open/closed per correctness requirement
  (`redis_client.py:229-277`); the watchdog's boot-time self-check
  (`lifespan.py:830-861`); the `app_settings`-in-DB flag pattern for dark-launch
  rollback.
- Uber/Lyft do: run dedicated worker fleets separated from API-serving fleets by
  default, with per-queue autoscaling and a paging system independent of any
  single webhook (industry-standard SRE practice, not a specific public source).
  Spinr today: the worker-role split exists in code and has a working canary
  config but was never adopted in the real deploy configs (REL-003); alerting is
  a single webhook (REL-002).
- Clean-sheet Spinr would: ship `SPINR_PROCESS_ROLE=api` + a real `worker`
  process on day one of any capacity-sensitive rollout, and treat "alert-as-code"
  (every loop's threshold and its alert route defined next to the loop, checked
  in CI against the registry) as a merge gate rather than a hand-maintained
  dict. Why (the edge it creates): a burst machine that wakes for *traffic*
  should not also be the thing that saturates the DB tier that traffic is
  already stressing; an alert channel that's provably wired (CI-checked) can't
  silently rot the way `LOOP_THRESHOLDS` did for 25 of 44 loops.
- How (architecture/pattern): a `LOOP_CATALOG`-driven CI check
  (`test_every_loop_has_a_tuned_threshold`) that fails a PR adding a loop
  without a threshold sized to its real heartbeat cadence, plus the C11
  Grafana pipeline as the primary alert path with the webhook as a documented
  secondary, not the only path. Who: whoever owns `core/background_loop_registry.py`
  (per CODEOWNERS, currently placeholder team handles — E8). When: **Now** for
  the CI check (cheap, no infra dependency); **Next** for the process-role split
  and C11 completion (both need real infra access this repo can't grant itself).
- Incremental path from today (no big-bang): step 1 — add the threshold-coverage
  CI check (pure test, zero risk) → step 2 — fix the 6 Direction-A / 5
  Direction-B / 1 Direction-C loops identified in REL-001 (each a small,
  independently-shippable diff) → step 3 — flip `SPINR_PROCESS_ROLE` on one Fly
  machine group first, watch for a deploy cycle, then the rest → step 4 —
  complete C11's remaining human-only steps (Grafana account, `fly deploy` for
  the metrics agent).
- Cost/effort: S for step 1–2, M for step 3, M (mostly ops time, not code) for
  step 4. Risk: low throughout — every change is additive or config-only.
  Reversibility: high (env var flip, threshold values, CI check can all be
  reverted in one commit each).
- Advantage type: operational (fewer 3 a.m. false alarms, faster real-incident
  detection) — not a market-facing feature, so not directly defensible, but a
  direct input to the "Driver cancellation rate" and "Match rate" KPIs if a
  dispatch-backstop loop's silent hang is ever the root cause of a bad week.
- "Why not?": the process-role split and watchdog registry were clearly built
  with intent (staged "T9/T12/T13" comments, a working canary config) — the
  most likely reason the final adoption step never happened is that it's an
  infra/ops action (editing `fly.toml`, provisioning a worker machine) rather
  than a code change, and this audit programme's own W0 summary notes exactly
  this pattern repeatedly (built, not finished, because the last step needs
  access no session has).

## Epic: Distributed Tracing (ADR-014 trigger)
- Verdict per inherited pattern: **PROPOSED / Later**, not evaluated as urgent —
  this pass found no `ADR-014` file in `docs/adr/` (grepped; does not exist yet),
  so this card records the trigger condition rather than a build recommendation.
- Keep: N/A — nothing exists yet to keep.
- Uber/Lyft do: distributed tracing (span-per-request across services) is
  standard at their scale, correlating a single ride's dispatch → fare → payment
  → WS-fan-out path across every hop.
- Spinr today: Sentry performance tracing exists at a 0.1 sample rate
  (noted in `A6-observability.md`'s OBS-002 correction) — sampled *transaction*
  latency, not full distributed tracing with span propagation across the loop/
  WS/DB boundaries this lane covers. `/metrics`'s counters are per-process,
  reset on deploy (C11) — there is no trace ID that would let an engineer follow
  one ride's dispatch-offer through the WS fan-out to the driver's accept and
  back, across replicas.
- Clean-sheet Spinr would: **not** build full distributed tracing until C11's
  much cheaper metrics-aggregation gap is closed and actually used — tracing
  is a bigger investment (instrumentation across every hop, a trace-storage
  backend, sampling policy) than the current gap (no cross-replica metrics at
  all) justifies solving first. The trigger for revisiting: once C11 is live and
  the team is actively debugging cross-replica latency issues using aggregated
  metrics and still can't isolate a specific hop, that's the signal tracing is
  worth the investment, not before.
- How/Who/When: When = **Later**, explicitly gated behind C11 landing and a
  demonstrated need (not before). Who: whoever inherits the Observability &
  Monitoring epic once C11 ships.
- Incremental path: N/A until triggered.
- Cost/effort: L (new backend, new instrumentation across every service
  boundary). Risk: low to implement additively (OpenTelemetry-style tracing can
  be added without touching business logic), but effort is not justified yet.
- Advantage type: operational only; not a rider/driver-facing differentiator.
- "Why not [build it now]?": the cheaper fix (C11) isn't even deployed yet, and
  this pass found no incident or debugging session in `docs/incidents/` that
  tracing would have made faster to resolve that aggregated metrics wouldn't
  also have solved — building the expensive tool before the cheap one is live
  and proven insufficient would be solving a problem that hasn't been
  demonstrated yet.

---

## 11. Top 5

1. **REL-001** — ~30 of 44 loops have an untuned or entirely absent staleness
   threshold, in three distinct failure directions (too loose for safety/
   dispatch, too tight causing chronic false alarms, or never-heartbeats-at-all
   for the CRA-regulatory T4A job). The single highest-leverage, lowest-risk fix
   in this report — a CI check plus per-loop threshold values.
2. **REL-002** — every one of the 44 loops' only live alert path is one env var
   (`ALERT_WEBHOOK_URL`) whose configured status is unknown, with the dashboard
   fallback confirmed to be dead/unmounted code.
3. **REL-003** — `SPINR_PROCESS_ROLE` is unset in the real Fly/Railway configs
   despite a working, canary-tested split already existing in code — confirms
   and sharpens A3-001 with the concrete evidence that the fix is built and
   simply unshipped.
4. **REL-004** — cross-provider failover (C1) and PITR restore (E7) are both
   never-drilled, and the PITR runbook's own RTO/RPO header may not match the
   actually-provisioned Supabase backup tier.
5. **§7 MTTD/MTTR is UNKNOWN, not measurable**, for every failure class this
   lane owns (loop stall, Redis outage, bad deploy, WS fan-out gap) — no
   postmortem exists for any of them, and the one alert channel that would
   generate one has unverified configuration.

---

## 12. What was NOT verified

- Whether `ALERT_WEBHOOK_URL` is actually set in Fly/Railway production
  secrets (REL-002/§6/§7) — human-only, carried from W0.
- Whether the shared-Redis-alias TLS/hostname setup described in
  `railway-fly-failover.md:158-172` is correctly configured today (vs. the
  documented footgun of a plain CNAME failing cert verification) — this session
  did not and should not probe live Redis.
- The full internal logic of `insurance_period_reconciler`, `stale_p3_closer`,
  and `route_deviation_alerter` beyond their flag/lock naming and lifespan
  doc-comments — read enough to classify their replay-safety mechanism for §3,
  not their full escalation/business logic (R8/R11 territory).
- Whether `payment_retry`'s, `corporate_autotopup`'s, and `auto_payout`'s
  per-row DB claim (the backstop cited in each's `lifespan.py` comment) is
  actually atomic at the SQL level — marked INFERRED in §3 based on the
  documented pattern and the fully-verified `preauth_capture` sibling, not
  independently re-read line-by-line for each file given the time budget.
- `docs/runbooks/on-call.md` — not read in full; whether it has ever been
  invoked for an infra incident is UNKNOWN.
- Live Railway/Fly state via MCP tools — not used this pass (see §0); every
  finding above is a static-code/doc read, not a live-system probe. Nothing in
  this report is labelled VERIFIED-LIVE.
- `docs/runbooks/fly-mixed-fleet.md`, `capacity-scaling.md` in full detail
  (skimmed for the A3-005 cross-reference only, not independently re-verified
  this pass) — treat A3-005's own findings as current rather than re-deriving
  them here.
- Whether a rate-limit-specific Redis-down alert exists distinct from the
  general loop-watchdog path (§4's closing note) — `redis-down.md`'s "Known
  Gaps" section claims it doesn't, not independently re-confirmed against the
  current codebase this pass.
- The traceability.csv rows for `Observability & Monitoring` (34 rows) and
  `Platform Foundation & Schema` (88 rows) were consulted for row counts only,
  not individually walked given the CSV's malformed-for-`awk` embedded commas
  and this lane's time budget — treat the loop-registry/lifespan.py direct read
  as the authoritative source for this report's loop-specific claims, not the
  CSV.

---

## 13. Human-only questions

1. Is `ALERT_WEBHOOK_URL` set in Fly and Railway production secrets today? (This
   single answer changes REL-002/§6/§7's severity materially.)
2. What Supabase backup tier is actually provisioned — daily-snapshot-only or
   true PITR add-on? (`pitr-restore.md`'s own header is unconfirmed.)
3. When can a low-traffic window + Supabase org/billing access + Cloudflare DNS
   access be scheduled for the combined failover + PITR-restore drill (REL-004)?
4. Is there appetite to stand up a real dedicated `worker` process/machine
   (REL-003), given it needs a genuine infra change (new Fly machine group or
   Railway service), not just a code change?
5. Has an infra incident (loop stall, Redis outage, bad deploy) ever actually
   happened in production and simply gone undocumented, or has none occurred?
   (§7's MTTD/MTTR verdict depends on which.)

---

## 14. Escalations

- **REL-002 / §6 (alert-delivery single point of failure)** → engineering
  leadership + whoever owns Fly/Railway secrets: this is the kind of "in doubt,
  escalate" item CLAUDE.md's gate 9 describes — the blast radius (44 loops, all
  of CLAUDE.md's Performance SLA table) is large and this session cannot verify
  the one fact (`ALERT_WEBHOOK_URL` set?) that determines whether it's a paper
  gap or a live one.
- **REL-004 (both DR mechanisms never drilled)** → same escalation as C1/E7
  already carry in ACTION_ITEMS; this pass adds no new access, just the
  composite framing that both are open at once.
- **REL-003 (process-role split unshipped)** → this is a cost/capacity decision
  (new Fly machine group or Railway service, ongoing infra spend) as much as an
  engineering one — appropriate for whoever owns infra budget, not a unilateral
  engineering call.
