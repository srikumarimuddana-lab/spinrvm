# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (observability) |
| PR / commit link | (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Related issue or gap ID | Monitoring plan items 12 + 5a (`plans/monitoring-observability-implementation-plan.md`) |

## 1. Issue / gap identified

`/health` checks Postgres only. A Stripe, Twilio, Google Maps, Redis, or FCM
problem is invisible to operators until it surfaces as a failed ride, a failed
charge, or a support ticket. There is also nothing for an external uptime prober
to call that reflects more than database liveness — which matters because
deployment is Fly primary / Railway warm standby with *manual* Cloudflare DNS
failover, and per `ACTION_ITEMS.md` C5 the Railway standby is silently drifting.

## 2. Root cause

`/health` was built for one job — the platform liveness probe that Fly, Railway,
and the Docker `HEALTHCHECK` call — and correctly kept minimal for that job. No
second endpoint was ever added for the different question "are our dependencies
healthy", so that question had no answer anywhere.

## 3. Fix / remediation

Adds `GET /health/dependencies`, backed by the `utils/dependency_health` probe
module committed immediately prior. Also publishes `spinr_dependency_up
{dependency="<name>"}` gauges from the `/metrics` handler so the same cached
probe feeds both the prober and Grafana.

The `METRICS_AUTH_TOKEN` gate previously inline in `/metrics` is extracted to a
shared `_require_metrics_auth()` helper and used by both endpoints, so there is
one fail-closed implementation rather than two that can drift apart.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface, with one genuinely sensitive touch point.**

- **`/health` is unchanged** — this is the important one. It backs
  `backend/fly.toml:51`, `railway.json:9`, `Dockerfile:56`, and
  `backend/Dockerfile:106`. A shape or auth change there would fail liveness
  checks and abort rolling deploys on both providers. `/health/dependencies` is
  a distinct exact-match path and does not shadow it. Pinned by an explicit
  regression test asserting `/health` still needs no token, still returns
  `{"status": "healthy", "db": {...}}`, and gained no new keys.
- **`/metrics` auth was refactored, not rewritten.** The gate moved verbatim
  into a helper. Risk is that a subtle change silently weakens it, so a
  dedicated test re-pins all four cases (no header → 401, wrong bearer → 401,
  right bearer → 200, `?token=` query form → 200) on top of the pre-existing
  `test_metrics_auth.py`, which still passes unmodified.
- **`/metrics` gained an `await`.** The handler now awaits `probe_dependencies()`.
  That call is memoised for 30 s and never raises by contract, and its failure
  path is caught and logged so a broken probe degrades the scrape rather than
  emptying it (pinned by a test). Worst case on a cache miss is bounded by the
  module's 3 s per-probe timeout.

No interaction with the ride state machine, dispatch, money/wallet deltas, auth
flows, or any background loop. No DB writes. No migration.

**Security judgement — why the new endpoint is gated when `/health` is not:**
"which vendor is down" is operational intelligence. An open endpoint announcing
that Stripe is unreachable tells an attacker exactly when payment retries are
failing and when fraud controls may be degraded. Plain `/health` stays open
because platform probes need it and it reveals only "up or not".

## 5. User-experience effect

**Nobody.** Backend-only. No rider, driver, corporate-admin, or internal-admin
surface changes. Not visible mid-session. No copy or notification change. The
new endpoint is reachable only with the operational token (or, outside
production, by whoever can already reach the host).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/server.py` | Added `/health/dependencies`; extracted `_require_metrics_auth()`; publish `spinr_dependency_up` gauges in `/metrics` | Give probers and Grafana one dependency view without duplicating the fail-closed auth gate |
| `backend/tests/test_health_dependencies_endpoint.py` | New — 10 tests | Pin auth gate, status-code semantics, `/health` non-regression, and gauge exposition |

## 7. Before / after

`/metrics` auth, before — inline in the handler:

```python
@app.get("/metrics")
async def metrics(request: _Request) -> _MetricsResponse:
    from fastapi import HTTPException
    _token = _metrics_token()
    if _token:
        ...
    elif settings.ENV.lower() == "production":
        raise HTTPException(status_code=503, detail="Metrics endpoint not configured")
```

After — one shared helper, same logic, now reused by both endpoints:

```python
def _require_metrics_auth(request: _Request, endpoint: str) -> None:
    ...  # identical checks, endpoint name only used in the log line

@app.get("/metrics")
async def metrics(request: _Request) -> _MetricsResponse:
    _require_metrics_auth(request, "/metrics")
```

Status-code contract for the new endpoint (so a prober can alert on the code
alone, without parsing a body):

```
all ok / some degraded  -> 200   (degraded means still serving)
any dependency down     -> 503
```

## 8. Rollback plan

`git revert` is sufficient and the qualifying conditions hold: no durable
writes, no live state mutated, no migration. Reverting removes the new endpoint
and the gauges; `/health` is untouched either way, so **liveness probes cannot
be affected by the revert** any more than they were by the change.

Operationally there is also a no-deploy mitigation: because the endpoint is
gated by `METRICS_AUTH_TOKEN`, setting that secret to a fresh value immediately
locks out any prober while leaving `/health` and the app serving. That is a
containment lever, not a full rollback.

No feature flag: the endpoint is inert until something calls it, and the gauges
are inert until something scrapes `/metrics` — which is itself gated on operator
action (C11), so this ships dark by construction.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_health_dependencies_endpoint.py` →
      **10 passed**. Combined regression run of `test_metrics_auth.py`,
      `test_monitoring_health.py`, `test_server_coverage.py`,
      `test_metrics_histogram.py`, `test_loop_heartbeat_gauge.py`,
      `test_dependency_health.py` → **55 passed**. Unit tier.
- [x] Explicit non-regression test that `/health` keeps its exact shape and
      needs no token, because it backs four platform health checks.
- [x] Explicit test that the refactored `/metrics` gate still rejects all four
      auth cases correctly.
- [x] Explicit test that a raising dependency probe degrades rather than empties
      the scrape.
- [x] Blast-radius grep performed — searched `/health` across `fly.toml`,
      `railway.json`, `render.yaml`, and both Dockerfiles; results in §4.
- [x] Reviewed against `CLAUDE.md` PIPEDA rules — response carries status and
      short reason codes only; no credentials, hostnames, URLs, or upstream
      error text (asserted by the probe module's own leak tests).
- [x] `ruff check` and `ruff format --check` clean on both files.
- [ ] Not feature-flagged — justified above (ships dark by construction).

## 10. What was NOT verified

- **Not exercised against a real deployment.** All probes were mocked; the
  endpoint has never been called on Fly or Railway, and no external prober has
  been pointed at it. The 200/503 contract is verified in tests, not in the
  wild.
- **Not verified against live vendors.** Supabase and Redis probes were tested
  against mocks, never a real outage. The Stripe/Twilio/Maps/FCM entries are
  configuration-presence checks by design and therefore say nothing at all about
  whether those vendors are actually reachable — a `configured` Stripe with an
  expired key still reports `ok`. This is a deliberate scope boundary documented
  in the probe module, not an oversight.
- **`/metrics` added an `await` on a path that had none.** Reasoned about
  (memoised 30 s, 3 s per-probe timeout, never-raises contract) and covered by a
  failure-path test, but **scrape latency was not measured** under load or on a
  cold cache.
- **No load or concurrency testing** of the probe cache. The `asyncio.Lock`
  serialises concurrent refreshes by construction, but simultaneous scrape +
  prober traffic was not exercised.
- `ruff check .` remains red repo-wide (36 pre-existing errors) — unrelated to
  this diff, flagged as standing gate decay per pre-merge gate #8.
