# Change Impact & Risk Log — `/health` liveness split from `/ready` readiness

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend (+ deploy config: fly/railway/CI/monitoring) |
| Domain (Sentry tag) | admin (platform/infra) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.8 (critical #8) |

## 1. Issue / gap identified

`/health` ran a real DB ping and returned **503** when it failed. `fly.toml`'s
`[[http_service.checks]]` polls that endpoint every 30 s with a 5 s timeout, and
the DB circuit breaker opens after 5 transient failures in 30 s and stays open
for **60 s** (`repositories/_base.py`).

Because the DB is a **shared** dependency, one Supabase blip fails the probe on
**every machine at once**. Fly's proxy then stops routing all traffic for the
whole breaker window — including WebSocket keep-alives and cached reads that
would have worked fine. A degraded dependency became a total outage, and one
that also severed the WS connections riders and drivers rely on to see ride
state.

## 2. Root cause

One endpoint was doing two incompatible jobs.

F1 originally made `/health` DB-aware for a real reason, recorded in the code:
a replica whose Supabase connection is dead used to keep returning 200, stay in
rotation, and answer every request with a 503 — and a bad rolling deploy got
promoted. That reasoning is sound, but it is **readiness** reasoning: "should
this build be promoted / should this replica take DB-backed work?"

Fly's `http_service` check asks a different question — **liveness**: "is this
process serving; should I route to it?" Answering the readiness question there
is what makes a shared-dependency failure fleet-wide, because every replica
answers it identically and simultaneously.

## 3. Fix / remediation

Split the two probes; neither loses its protection.

- **`/health` — liveness.** 200 whenever the process is serving. The DB's real
  state is still reported as `db.status: "ok" | "degraded"` so dashboards and
  the external synthetic probe can see the outage — it just no longer removes
  the machine from rotation.
- **`/ready` — readiness.** 503 when the DB ping fails: byte-for-byte the old
  `/health` behaviour, F1's protection intact.

Consumers were then sorted by which question they are actually asking:

| Consumer | Probe | Why |
|---|---|---|
| `fly.toml` `[[http_service.checks]]` | `/health` (unchanged path) | runtime routing — must not fail fleet-wide |
| `backend/Dockerfile` + root `Dockerfile` HEALTHCHECK | `/health` (unchanged) | container restart policy — restarting the fleet on a Supabase blip is the same bug |
| `deploy-fly.yml` post-deploy poll | **→ `/ready`** | deploy gate — a dead-DB build must not be promoted |
| `ci.yml` post-deploy smoke test | **→ `/ready`** | same |
| `railway.json` `healthcheckPath` | **→ `/ready`** | deploy gate |
| `ci.yml` "Resolve live base URL" | `/health` (unchanged) | only asks "is something serving here"; using `/ready` would make it fail over to the legacy URL during a DB outage |
| `standby-parity-monitor.yml` | `/health` (unchanged) | up/down parity between providers |
| `monitoring/synthetic-checks.yaml` | `/health` (unchanged, notes rewritten) | external up/down probe |

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface — this is deployment and routing behaviour, the
widest-reaching change in this branch.** Every `/health` reference in the repo
was enumerated (grep over `*.toml`, `*.json`, `Dockerfile`, `*.yml`, `*.yaml`)
and classified in the table above; nothing was left unclassified.

Interactions considered:

- **`routes/main.py`'s `/health`** is a *different* endpoint at
  `/api/v1/health` and is untouched. Its docstring already warns that
  `server.py`'s is the live one; that remains accurate.
- **`_db_ready()`** is unchanged and still shared by both probes, so the 5 s
  cache and 3 s ping timeout still bound probe load and cannot hang.
- **Loop staleness** remains outside both probes, as before.

Regression risks, stated plainly:

1. **The failure mode F1 fixed is now only caught at deploy time.** A replica
   whose DB connection dies *while running* will stay in rotation and answer
   DB-backed requests with 503s, where before it would have been pulled out.
   That is a deliberate trade: pulling it out only helps when the failure is
   replica-local, and hurts badly when it is shared — which, for a single
   Supabase project, it almost always is. Per-replica DB failure is not
   currently alerted on separately; **`db.status: "degraded"` on `/health` is
   the signal to build that alert from, and no such alert exists yet.** This is
   the most important follow-up from this change.
2. **A wrong probe path in config silently disables a gate.** If
   `railway.json`/`deploy-fly.yml` had been left on `/health`, a dead-DB deploy
   would now pass. Every gate was moved in this same commit for that reason.
3. **`/ready` is a new unauthenticated endpoint.** It exposes the same body
   `/health` already did (`ping_ms`, `circuit_state`) — no new information is
   published.

## 5. User-experience effect

- **Rider / driver (visible, mid-session):** during a Supabase hiccup the app
  keeps its WebSocket connection and anything served from cache, instead of
  losing all connectivity for the 60 s breaker window. Requests that genuinely
  need the DB still fail — but they fail individually, with the app still
  connected, rather than as a total blackout.
- **Internal admin / on-call:** `/health` now reports
  `{"status": "healthy", "db": {"status": "degraded"}}` during a DB outage
  rather than a 503. Alerting that keys on the HTTP status alone will no longer
  fire for a DB outage — it must key on `db.status`, or probe `/ready`. This is
  a **breaking change for any existing alert rule**, called out in §8.
- No copy changes, no rider/driver UI change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/server.py` | `/health` becomes liveness (200 + `db.status: degraded`); new `/ready` keeps the 503 behaviour; the probe block comment rewritten to explain both | Stop a shared-dependency blip de-routing the fleet, without losing F1 |
| `.github/workflows/deploy-fly.yml` | Post-deploy poll → `/ready`, step renamed | Deploy gate must still catch a dead-DB build |
| `.github/workflows/ci.yml` | Smoke test → `/ready`; URL resolution deliberately left on `/health` | Same, without breaking failover resolution during a DB outage |
| `railway.json` | `healthcheckPath` → `/ready` | Deploy gate |
| `monitoring/synthetic-checks.yaml` | Notes rewritten: a non-200 now means "backend down", not "DB unreachable" | The alert-routing guidance was inverted by the split |
| `backend/tests/test_server_coverage.py` | `test_unhealthy_returns_503` → `test_db_down_still_returns_200_as_degraded`; adds `TestReadyEndpoint` | The old test pinned exactly the behaviour being removed |

## 7. Before / after

```python
# Before — backend/server.py
@app.get("/health")
async def health():
    ok, detail = await _db_ready()
    if ok:
        return {"status": "healthy", "db": {"status": "ok", **detail}}
    return JSONResponse(503, {"status": "unhealthy", "db": {"status": "error"}})
```

```python
# After
@app.get("/health")            # LIVENESS — never 503s on a DB fault
async def health():
    ok, detail = await _db_ready()
    if ok:
        return {"status": "healthy", "db": {"status": "ok", **detail}}
    return {"status": "healthy", "db": {"status": "degraded"}}

@app.get("/ready")             # READINESS — the original F1 behaviour
async def ready():
    ok, detail = await _db_ready()
    if ok:
        return {"status": "ready", "db": {"status": "ok", **detail}}
    return JSONResponse(503, {"status": "not_ready", "db": {"status": "error"}})
```

Concrete scenario — a 60 s Supabase hiccup opens the breaker on all replicas:

| | Before | After |
|---|---|---|
| `/health` on every machine | 503 | 200, `db.status="degraded"` |
| Fly routing | **all machines pulled; no traffic served** | unchanged; traffic keeps flowing |
| Rider/driver WebSockets | **severed fleet-wide** | stay connected |
| Cached / non-DB reads | **unreachable** | served normally |
| DB-backed requests | 503 | 503 (unchanged — they genuinely can't work) |
| Deploy of a dead-DB build | blocked | blocked (now via `/ready`) |

## 8. Rollback plan

No migration, no schema change, no data written. `git revert` restores the
previous coupling — but note it must revert **all six files together**: reverting
`server.py` alone while the gates point at `/ready` leaves the deploy gates
hitting a 404, and reverting the gates alone leaves a dead-DB build promotable.

Without a redeploy, the runtime behaviour can be restored by editing
`fly.toml`'s `[[http_service.checks]] path` to `/ready` and re-deploying —
which is itself a deploy, so there is genuinely **no zero-deploy rollback** for
the routing半 of this change. That is acceptable because the change only ever
makes the probe *more* permissive: it cannot cause an outage it would not have
had before, only fail to prevent one.

No feature flag — a probe path is not a runtime-flippable behaviour, and the
`app_settings` mechanism is not readable by Fly's proxy.

**Action required outside this change:** any alert rule keying on `/health`
returning non-200 to mean "Supabase down" must be re-pointed at `/ready` or at
this body's `db.status` field. `monitoring/synthetic-checks.yaml`'s notes were
updated to say so, but **the actual alerting backend was not touched** and its
rules are not in this repo's control.

## 9. Verification performed

- [x] Blast-radius grep performed — every `/health` reference across `*.toml`,
      `*.json`, `Dockerfile`, `*.yml`, `*.yaml` enumerated and classified (§3
      table). `routes/main.py`'s `/api/v1/health` confirmed to be a separate,
      unaffected endpoint.
- [x] `ci.yml`, `deploy-fly.yml` and `monitoring/synthetic-checks.yaml` parsed
      with `yaml.safe_load`; `railway.json` parsed with `json.load` — all valid.
- [x] The one existing test that pinned the removed behaviour was found and
      updated rather than left to fail silently.
- [x] `ruff check` and `ruff format --check` clean on `server.py` and the tests.
- [ ] **Automated tests NOT run** — see below.
- [ ] Not exercised against a real Fly deploy.

## What was NOT verified

**No tests were executed.** PyPI is blocked by this environment's network policy
(403), so backend dependencies could not be installed and `pytest` could not
run. The edited `test_server_coverage.py` cases and the new `TestReadyEndpoint`
**have never been run**.

**Nothing here was exercised against a real deployment.** Specifically unverified:
that Fly's proxy behaves as described when `[[http_service.checks]]` passes while
requests fail (the whole premise of the fix); that Railway accepts `/ready` as a
`healthcheckPath`; that the `deploy-fly.yml` and `ci.yml` edits work in an actual
workflow run — YAML validity was checked, semantics were not. The 60 s breaker
window and 30 s probe interval are read from `repositories/_base.py` and
`fly.toml`, not observed in an incident.

**Two known gaps left open deliberately:**
1. No alert exists on `db.status: "degraded"`, so a *per-replica* DB failure —
   the case F1 was written for — is now unmonitored at runtime. Building that
   alert is the necessary follow-up and is **not** done here.
2. `docs/runbooks/supabase-down.md:11` cites a `/healthz` endpoint that does not
   exist anywhere in this codebase — a pre-existing doc error, unrelated to this
   change and left alone rather than silently rewritten. It should be corrected
   to `/ready` when someone owns that runbook.
