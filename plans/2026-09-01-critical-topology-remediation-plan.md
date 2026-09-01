# Critical Topology Remediation Plan — 2026-09-01

> **Provenance note.** This plan was requested by filename but did not exist in the
> repository (not in the working tree, not in `git` history, not on
> `origin/claude/topology-remediation-plan-80gnou`, and unreferenced anywhere in the
> repo). It was authored from a fresh whole-codebase analysis on 2026-09-01 against
> `main` @ `9ad33f2`. Findings below are cited to file:line so every claim is checkable.

## Scope

"Topology" here means the runtime/deployment shape described in `CLAUDE.md` §System
Topology — the multi-replica FastAPI fleet, its shared state (Supabase, Redis), the
WebSocket fan-out, the 40 background loops, and the Fly-primary/Railway-standby
deployment pair. This plan covers defects in that layer only. Application-logic
defects (fare math, ride state machine, RLS) are out of scope and already tracked in
`ACTION_ITEMS.md`.

## Method

1. Enumerated all spawned background loops from `backend/core/lifespan.py` and
   mapped each to its implementing module programmatically.
2. Cross-checked each loop for a leader lock (`try_acquire_leader_lock` /
   `redis_set_nx`) and, where absent, read the module for an independent
   replay-safety guard.
3. Traced every Redis URL setting from `core/config.py` through its consumers to
   determine what actually degrades, and under which configurations.
4. Read the production startup guards (`core/config.py::_guard_production_secrets`,
   `core/middleware.py::_validate_production_config`, `core/lifespan.py`) to
   determine which misconfigurations are caught and which boot silently.
5. Reconciled findings against `ACTION_ITEMS.md` and
   `docs/audit/2026-08-26-db-query-optimization-recommendations.md` §4.3 so this plan
   adds to the backlog rather than duplicating it.

---

## Finding T1 — `REDIS_URL` can be unset in production and the fleet boots silently (CRITICAL, previously untracked)

### What is wrong

The backend runs on **2–8 Fly machines**, each with **2 uvicorn workers**
(`backend/fly.toml:43,55` — `UVICORN_WORKERS = "2"`, `min_machines_running = 2`, scaled
to 8 machines in `yyz`). The lifespan spawns its loops **per worker process**, which
`fly.toml:11-13` acknowledges ("replay-safe per process"), so the fleet is up to
**16 independent processes**, not 8. Shared state is therefore not optional. There are three Redis
settings (`backend/core/config.py:110-116`):

| Setting | Consumed by | Fallback |
|---|---|---|
| `REDIS_URL` | `utils/redis_client.py` — leader locks, OTP lockout, WS per-user rate limit | in-process dict |
| `RATE_LIMIT_REDIS_URL` | SlowAPI limiter | `REDIS_URL` |
| `WS_REDIS_URL` | `utils/ws_pubsub.py` | `RATE_LIMIT_REDIS_URL` |

Only `RATE_LIMIT_REDIS_URL` is enforced. The two startup guards are:

- `core/middleware.py:774-785` — **hard-fails** production if `RATE_LIMIT_REDIS_URL`
  is unset or not a `redis://`/`rediss://` URL. `REDIS_URL` is never checked.
- `core/lifespan.py:116-123` — logs an error only, and its condition is
  `not any([REDIS_URL, RATE_LIMIT_REDIS_URL, WS_REDIS_URL])`, so it fires **only when
  all three are unset**.

Therefore a production deploy that sets `RATE_LIMIT_REDIS_URL` and `WS_REDIS_URL` but
leaves `REDIS_URL` empty passes every guard, logs nothing, and reports healthy.

The degradation is silent by construction: `utils/redis_client.py::_get_redis` returns
`None` immediately when the URL is empty, with no log statement on that path — the
`logger.warning` there only fires on a connection *exception*, never on an unset URL.

### Blast radius (grep-verified consumers of `utils/redis_client.py`)

1. **OTP brute-force lockout — security.** `routes/auth.py:230-297`
   (`_check_otp_lockout`, `_record_otp_failure`) stores the failure counter and the
   24h lockout via `redis_get`/`redis_set`. Per-process state means the documented
   "5 failures/hour → 24h lockout" becomes effectively *5 × N processes* per hour, and
   resets on every deploy or machine suspend/resume. Note `fly.toml` uses
   `autostop`/suspend, so resumes are routine.

   **The existing fail-closed protection does not cover this case.**
   `_check_otp_lockout` deliberately raises 503 when Redis *errors*
   (`routes/auth.py:255-261`, "fail closed"). But an unset `REDIS_URL` is not an
   error — `_get_redis` returns `None` and `redis_get` reads the in-process dict and
   returns cleanly. So the one guard designed to stop exactly this degradation is
   bypassed, silently, by a missing env var rather than a broken one.
2. **All 28 leader-locked loops.** Every replica wins its own in-process lock, so the
   load-shedding purpose is fully defeated: 40 loops × up to 8 machines against
   Supabase, on a database the 2026-08-26 audit already found under sequential-scan
   pressure.
3. **WS per-user rate limit.** `socket_manager.py:203-224` falls back to the
   per-machine bucket (`_note_user_message_local`), silently undoing the fleet-wide
   cap that `ACTION_ITEMS.md` **B4** was closed on 2026-07-28 to deliver.

Correctness of ride/payment state is *not* at risk (see T4) — this is a security-
control and load defect, not a data-integrity one.

### Remediation

Extend `core/middleware.py::_validate_production_config` to require `REDIS_URL`
alongside `RATE_LIMIT_REDIS_URL`, with the same `redis://`/`rediss://` scheme check,
and add the missing observability line in `utils/redis_client.py::_get_redis` (log
once, at `error`, when `ENV=production` and the URL is empty) so the degradation is
never silent even if the guard is later relaxed.

> **⚠ Deployment risk — must be confirmed before implementing.** Adding a hard-fail
> guard means that if production *currently* runs without `REDIS_URL`, the next deploy
> refuses to boot — converting a silent degradation into an outage. **Verify the live
> Fly and Railway secret sets first** (`fly secrets list`), then choose:
> - **(a) Hard-fail** — consistent with the existing `RATE_LIMIT_REDIS_URL` precedent
>   and with `CLAUDE.md`'s "fails fast in production on weak secrets". Only safe once
>   `REDIS_URL` is confirmed present on both targets.
> - **(b) Log-loudly-then-hard-fail** — ship the `error` log + a startup metric now,
>   flip to hard-fail after one deploy cycle confirms the log is quiet. Safer;
>   matches the additive-over-destructive release gate.
>
> This choice is **not** ours to make unilaterally (release gate 9, "escalate, don't
> silently ship" — this touches auth brute-force protection).

### Verification

- Unit: extend `backend/tests/test_middleware_production_config_guard.py` — it already
  parametrises `RATE_LIMIT_REDIS_URL` empty/non-`redis://` cases at lines 67-68; add
  the `REDIS_URL` equivalents plus a case asserting a deploy with
  `RATE_LIMIT_REDIS_URL` + `WS_REDIS_URL` set but `REDIS_URL` empty is now rejected
  (this is the exact configuration that boots silently today).
- Manual: boot with `ENV=production` and that configuration; confirm the failure.

### Rollback

Config-only guard; revert is a single-commit revert with no data effect. Under option
(b) the log-only stage has no rollback surface at all.

---

## Finding T2 — Railway standby has been drifting from `main` (HIGH, tracked as `ACTION_ITEMS.md` C5)

`deploy-backend.yml` is blocked by a GitHub Environment protection rule with no expiry
or owner. ADR-007 designates Railway a *hot* standby; it is currently a paper one, so
a Fly outage would fail over to a stale and possibly schema-mismatched build. This is
a topology defect (the failover leg of the deployment pair is non-functional) but the
remediation is an infrastructure/permissions action, not a code change: confirm the
original pause reason is gone, verify Fly↔Railway secret parity (`JWT_SECRET`,
`SUPABASE_*`, `FIREBASE_*`, and — per T1 — all three Redis URLs), remove the rule,
then re-run the C1 failover drill.

**No code change is proposed here.** Left as a tracked ops item; flagged because T1's
secret-parity check and C5's are the same check and should be done in one pass.

---

## Finding T3 — Metrics are per-process, so no SLA in `CLAUDE.md` is actually measurable (HIGH, tracked as CR-2026-008 / ADR-010)

`backend/utils/metrics.py` aggregates per process only. With 8 machines × 2 uvicorn
workers, none of the P95 targets in `CLAUDE.md`'s Performance SLA table (dispatch
offer→accept < 2s, fare calc < 300ms, WS fan-out < 100ms) nor the KPI table can be
computed. The design exists (ADR-010) and the agent config is committed but inert;
completion is gated on 8 human infrastructure steps listed in
`metrics-agent/README.md`.

Relevant to this plan because **T1 and T2 are both undetectable without it** — a fleet
silently running per-replica Redis state, or a stale standby, produces no metric a
human would notice. **No code change proposed**; sequencing note only.

---

## Finding T4 — Loop replay-safety: verified sound, and the audit's headline is now stale (INFORMATIONAL — no action)

`docs/audit/2026-08-26-db-query-optimization-recommendations.md` §4.3 states "26 hold a
Redis leader lock; 14 do not" and frames fail-open locks as a risk. Re-measured against
`main` @ `9ad33f2`: **40 loops, 28 locked, 12 unlocked.**

The fail-open behaviour is correct by design, not a defect.
`utils/redis_client.py::try_acquire_leader_lock` documents the contract explicitly:
every caller must *already* be replay-safe by atomic claim or idempotency key, so the
lock only sheds duplicate DB load. Spot-checks confirm the contract holds on the
highest-risk unlocked loops:

- `corporate_autotopup` (off-session Stripe charges) — deterministic Stripe
  `idempotency_key` seeded from `(wallet, date, today_sum, topup_amount)`, so two
  replicas produce an identical key and Stripe collapses the duplicate
  (`utils/corporate_autotopup.py:148-182`).
- `allowance_reset` (zeroes employee allowance `used`) — compare-and-swap on
  `period_end` before zeroing; losers match zero rows
  (`utils/allowance_reset.py:121-131`).

**Recommendation: correct the audit's stale count rather than change any loop code.**
Changing loop locking here would be churn against a working design.

Two documentation drifts worth a one-line fix each (cosmetic, no runtime effect):
`backend/fly.toml:12` says "the 16 background loops" and `fly.toml:21-22` says
"18 loops"; the real count is 40. The cold-boot estimate at `fly.toml:21` is derived
from that stale number, so it understates resume cost.

---

## Proposed sequencing

| # | Item | Type | Risk | Gate |
|---|---|---|---|---|
| 1 | Verify `REDIS_URL` present on Fly **and** Railway (same pass as C5 secret parity) | Ops | none | prerequisite for 2 |
| 2 | T1 guard + non-silent degradation log (option a or b per your call) | Code | low, but boot-blocking under (a) | Change Impact Log; `test_middleware_production_config_guard.py` |
| 3 | T4 doc corrections (audit §4.3 count, `fly.toml` comments) | Docs | none | none |
| 4 | C5 Railway un-pause + C1 failover drill | Ops | medium | failover drill |
| 5 | ADR-010 metrics agent (CR-2026-008) | Ops | medium | out of scope here |

Items 2 and 3 are the only code/doc changes proposed. Each is a single logical commit
under the ≤200-line batch-size rule, and item 2 requires a Change Impact & Risk entry
per `CLAUDE.md` (it touches an auth-adjacent control on a live-tested surface).

## What was NOT verified

- **No live environment was inspected.** Whether `REDIS_URL` is actually set on Fly or
  Railway today is unknown from the repository alone — T1 describes a configuration
  that *can* boot silently, not a confirmed live misconfiguration. Step 1 exists to
  settle that before any guard lands.
- **No tests were run** and no production build was executed for this analysis; it is a
  read-only code audit.
- Replay-safety was **spot-checked** on 2 of the 12 unlocked loops (the two that move
  money or reset balances), not proven for all 12.
- Rider/driver/admin frontend surfaces were not analysed — this plan is backend
  topology only.
