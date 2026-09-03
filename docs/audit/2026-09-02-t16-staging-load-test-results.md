# T16 — Staging Load Test Results (Scenario A, steady-state)

**Date:** 2026-09-02
**Item:** ACTION_ITEMS.md C50, Phase 3 T16 ("staging validation")
**Plan doc:** `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md` (rev 2)
**Run owner:** Ravi (Engineering Manager), executed via `feat/c50-phase0-dispatch-metrics`
**Target:** `https://spinr-backend-staging.fly.dev` (Fly app `spinr-backend-staging`)

## Headline finding

**This run establishes a pre-C50 BASELINE, not a C50 validation.** Staging's
currently-deployed backend predates all of C50 Phase 0–2 by several days —
verified below, not assumed. T16 as literally written in the plan ("flag on
in staging; re-run T5; compare against the T5 baseline row") cannot happen
yet because the flag and the dispatch-pool code it gates do not exist on
staging's deployed image. What *could* happen this session — seeding,
harness repair, and a first real Scenario A run — is done, and the results
below are the "before" side of the eventual A/B comparison once the C50
branch is deployed to staging (a separate, human decision — see
Recommendation).

---

## (a) Staging's deployed commit/version and C50 presence

Verified via `flyctl status`/`flyctl releases`/`flyctl image show` plus a
direct file inventory over `flyctl ssh console` (read-only — no writes to
the running container beyond uploading the seed script under
`/app/scripts/`, which is not part of the deployed application code).

- **Fly release:** `v1`, status **`failed`** per `flyctl releases` (a
  release-workflow anomaly, not a runtime one — the app has been serving
  healthy traffic on this same release throughout, confirmed by `/health`
  returning `{"status":"healthy","db":{"status":"ok"}}` before, during, and
  after this session's load test). Deployed 2026-08-29 03:48 by
  `spinryoutube@gmail.com`. It is the **only release this app has ever had**
  — staging has never been redeployed since initial setup. The Fly image
  itself carries no embedded git SHA, so the commit below is inferred from
  file-inventory evidence, not read directly off the image.
- **Migrations present on the image:** `/app/migrations/` tops out at
  `371_route_gap_latest_captures_fn.sql`. Migration `372_...` (commit
  `a724f5557`, 2026-08-29 16:45 UTC) is **absent** — it landed on `main`
  *after* the v1 image was built (the image's own
  `370_location_marker_write_gate_flag.sql` inclusion matches the local
  repo's Aug-27/28 state).
- **No C50 code present, confirmed three independent ways:**
  1. `grep dispatch_direct_pool /app/schemas.py` — no match (C50 T10 added
     this field; absent means Phase 1 never landed here).
  2. `/app/requirements.txt` pins `psycopg2-binary==2.9.12` only — no
     `psycopg[binary,pool]` (C50 T8's new dependency).
  3. `/app/migrations/` has no `401_settings_dispatch_direct_pool_enabled.sql`
     or `402_dispatch_claim_batch.sql` (C50 T10/T12).
- **Conclusion:** staging is running a build from **~2026-08-29**, roughly
  **5 days and the entire C50 Phase 0–2 commit range (all dated
  2026-09-02) behind** `feat/c50-phase0-dispatch-metrics`. This matches
  the plan doc's own framing (§4 G2/T4 assumed staging didn't exist yet;
  it does, but was clearly stood up and left on its initial build).

---

## (b) What got seeded — staging-only, confirmed

Ran `backend/scripts/seed_loadtest_bots.py` (new, this session) directly on
the staging Fly machine (`flyctl ssh console`), so `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` came from the machine's own env — the same
staging-only credentials `flyctl secrets list --app spinr-backend-staging`
showed earlier (digests distinct from production's, confirmed prior
session). The script's own printed banner confirmed `ENV=staging` and the
staging Supabase URL (`https://mvmyygoinicjdpqprizr.supabase.co`) at seed
time.

| Seeded | Count | Detail |
|---|---|---|
| Rider bot accounts | 45 | `users` rows, phones `+13065550002`..`+13065550090` (even suffixes) |
| Driver bot accounts | 15 | `users` + `drivers` rows, phones `+13065550003`..`+13065550031` (odd suffixes), each `is_verified=true`, `status='active'`, `vehicle_type_id` set, no expiry fields set (unset ⇒ "no doc on file", not "expired" — passes `go_online`'s gate) |
| Service area | 1 (new) | "Saskatoon Downtown (loadtest seed)" — **staging's `service_areas` table was completely empty** before this run, a prerequisite gap the plan/README didn't anticipate (they assumed an area already existed, only a polygon needed adding) |
| Vehicle type | 1 (new) | "Standard (loadtest seed)" — `vehicle_types` was also empty |
| Fare config | 1 (new) | Standard default rates for the seeded area/vehicle-type pair |

**Production was never touched by seeding.** The script hard-refuses to
run when `ENV=production` (checked via `os.environ["ENV"]`, mirroring
`settings.ENV`'s own source — `backend/core/config.py:203`) and additionally
requires `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` to be explicitly set,
same pattern as the existing `seed_corporate_test_data.py`. It was invoked
only via `flyctl ssh console --app spinr-backend-staging`, never against
any production host or credential.

---

## (c) Scenario A results — steady-state, 60 users, 10 minutes

Command: `locust -f locustfile.py --headless -u 60 -r 2 -t 10m --host https://spinr-backend-staging.fly.dev --csv results/steady`
Weighting: 3:1 rider:driver (locustfile default) → ~45 rider bots, ~15 driver bots at full ramp.

### Harness bugs found and fixed before this run could produce real numbers

The harness (`loadtest/locustfile.py`) had **never been executed** before
this session (self-disclosed in its own README/module docstring). Running
it for real surfaced concrete bugs, fixed in commit `9d2bfc77b` on
`feat/c50-phase0-dispatch-metrics`:

1. `VerifyOTPRequest`'s real field is `code`, not `otp`; new signups also
   need `consent_accepted: true` (PIPEDA gate) or the request 400s.
2. `AuthResponse`'s token field is `token`, not `access_token`.
3. Default `LOADTEST_PHONE_PREFIX = "+1****55"` contained literal `*`
   characters and failed the `^\+1\d{10}$` E.164 validator outright —
   every login would have 422'd. Replaced with `+1306555` (real Saskatoon
   area code + the NANP fictional-555 exchange).
4. `RiderBot`'s vehicle-type lookup read a non-existent top-level
   `vehicle_type_id`/`id` field; the real shape is
   `estimates[i]["vehicle_type"]["id"]` — every `POST /rides` was 422'ing.

A pre-existing prerequisite gap (not a harness bug) also had to be closed:
staging's `service_areas`/`vehicle_types` tables were empty, and the first
seeded polygon was 0.0032° too narrow for the harness's own 0.02° jitter
radius, causing intermittent "outside service area" 400s — widened with
margin (see seed script).

### Real numbers, against the CLAUDE.md SLA gates

| Gate | Source | Observed | Result |
|---|---|---|---|
| Fare estimate P95 < 300ms | CLAUDE.md SLA table | **910ms** (P95, `rides:estimate`, 100 requests, 0 failures) | **FAILED** |
| Dispatch offer→accept P95 < 2s | CLAUDE.md SLA table | 594ms (n=1 successful accept — see caveat below) | Not statistically meaningful |

Locust's own `assert_slas` hook confirmed: `SLA GATES FAILED: fare
estimate P95 < 300ms: observed P95=910ms` at test end.

### Full request breakdown (final CSV, `results/steady_stats.csv`)

| Endpoint | Requests | Failures | Median | P95 | Max |
|---|---|---|---|---|---|
| `auth:send-otp` | 535 | 529 (98.9%) | 750ms | 1600ms | 2017ms |
| `auth:verify-otp` | 535 | 530 (99.1%) | 450ms | 1000ms | 1272ms |
| `rides:estimate` | 100 | 0 (0%) | 320ms | 910ms | 1987ms |
| `rides:create` | 100 | 98 (98%) | 110ms | 280ms | 1496ms |
| `rides:poll` | 131 | 0 (0%) | 180ms | 300ms | 1458ms |
| `drivers:accept` | 1 | 0 | 594ms | — | 594ms |
| `market:offer-to-accept` | 1 | 0 | 594ms | — | 594ms |
| **Aggregated** | **1414** | **1162 (82.2%)** | 450ms | 1200ms | 2017ms |

### Root cause of the 82% failure rate: THIS IS A HARNESS ARTIFACT, not a backend finding

`loadtest/README.md` explicitly predicted this exact failure mode:

> "Rate limits binding during a ramp is most likely a **harness**
> artifact: bot users that share one token key to one bucket."

Confirmed: `POST /auth/send-otp` and `POST /auth/verify-otp` are both
**per-client-IP** rate limited (`backend/routes/auth.py:396`
`@limiter.limit("6/minute")` for send-otp, `:900` `@limiter.limit("5/minute")`
for verify-otp — via `slowapi`'s `get_remote_address` key function, per-IP
by design). All 60 Locust bots ran from **one process on one machine**,
sharing one egress IP against staging — so the entire bot pool collectively
gets 6 send-otp + 5 verify-otp requests per minute, not 6/5 *each*. 529 of
535 send-otp attempts and 530 of 535 verify-otp attempts hit `429 Too Many
Requests` as a direct, expected consequence — every one of the 96
`rides:create` `409 Conflict`s is the same root cause one layer downstream
(a bot that never got a valid session retrying against a stale/duplicate
ride). This is not a backend capacity or dispatch problem; it is 60
simulated *browsers* correctly being treated as **one client** by an IP-keyed
limiter that was never designed to have 60 independent users share an
egress IP. Real bot-distinct rate limiting (per-user, not per-IP) would
require running Locust with `--processes` across multiple egress IPs, or a
harness-side per-bot proxy — out of scope for this session, flagged as a
follow-up for whoever runs Scenario B.

**What this means for the SLA numbers above:** the 82% *failure* rate is a
harness ceiling, not a platform ceiling — discard it as a capacity signal.
The **P95=910ms fare-estimate breach is real and independent of the rate
limit** (all 100 `rides:estimate` calls succeeded — the SLA breach is on
response *time*, not the failure count). It reflects staging's real,
pre-C50 latency under a light but real concurrent load (100 estimate calls
over 10 minutes with a handful of concurrent bots at any moment) on the
smallest possible Fly tier (`shared-cpu-1x`, 512MB, 1 machine, scale-to-zero
— staging is deliberately NOT sized like production's 8-machine burst
pool). A cold `/health` ping mid-run showed 30–67ms DB ping times — the DB
itself was not the bottleneck; likely candidates are cold-start / single
small machine CPU contention and Google Maps/Directions round-trip time in
the estimate path, neither of which C50 (a dispatch-claim-path change) would
touch.

### Platform health during the run

- `flyctl status` before/during/after: 1 machine, `started`, health check
  passing throughout. Never scaled up (staging's `min_machines_running=0` /
  `auto_stop_machines` config never triggered a second machine — expected,
  this load level doesn't need one).
- `/metrics` before/after: `spinr_db_thread_pool_queue_depth=0` both times
  — no thread-pool saturation. Only 2 `spinr_dispatch_offer_sent_total` /
  `spinr_dispatch_offer_accepted_total` events fired total (direct
  consequence of the rate-limit ceiling — almost no bots got far enough to
  reach dispatch). `spinr_dispatch_offer_to_accept_duration_ms` recorded
  exactly 2 samples, sum 1382ms — not a usable sample size for the
  offer→accept SLA gate this run.

---

## (d) `/metrics` auth finding — root cause identified in code

**Finding: this is a real, unresolved gap on staging, not an intentional
design choice.** Read `backend/server.py:228-259` (the `/metrics` handler)
directly:

```python
def _metrics_token() -> str:
    return os.getenv("METRICS_AUTH_TOKEN", "").strip()

@app.get("/metrics")
async def metrics(request: _Request) -> _MetricsResponse:
    _token = _metrics_token()
    if _token:
        # ... Bearer-token check, 401 if it doesn't match
    elif settings.ENV.lower() == "production":
        # FAIL CLOSED: refuse the scrape (503) if no token is set on prod
        raise HTTPException(status_code=503, detail="Metrics endpoint not configured")
    # else: non-production with no token set → serve unauthenticated
```

The code's own comment is explicit about the intent: *"Non-production is
unauthenticated by design for local Prometheus."* So the **fail-open
behavior on staging (ENV=staging) is intentional** — it is not a bug in
the sense of violating what the code was written to do. But:

- `flyctl secrets list --app spinr-backend-staging` (prior session,
  confirmed unchanged) shows **no `METRICS_AUTH_TOKEN` secret set** —
  confirmed directly, not inferred.
- This means anyone on the internet can currently scrape
  `spinr_db_calls_total`, `spinr_redis_*`, error-rate counters, and traffic
  volume off `https://spinr-backend-staging.fly.dev/metrics` with no auth
  at all — real operational signal, even if not customer PII.
- **The gap is real but scoped**: the code's own docstring frames the
  non-production carve-out as "for local Prometheus" — i.e. a developer
  laptop, not a public internet-facing Fly hostname. Staging is
  internet-reachable at a stable public URL, which the "local Prometheus"
  framing did not anticipate. This is a legitimate finding worth flagging
  to Kiran/ops: **set `METRICS_AUTH_TOKEN` on staging** (`flyctl secrets
  set METRICS_AUTH_TOKEN=<value> --app spinr-backend-staging` — a
  staging-only action, not touched this session per the production
  off-limits constraint) so `/metrics` requires the same Bearer auth in
  staging as the loadtest README documents and expects.

**Not filing this as a fresh ACTION_ITEMS entry pending Kiran/Pandi's call
on whether it warrants one** — surfaced here as directed.

---

## (e) Commits pushed to `feat/c50-phase0-dispatch-metrics`

```
9d2bfc77b feat(loadtest): add staging bot-seed script + fix never-run harness bugs (C50 T16)
```

Pushed via `git push origin feat/c50-phase0-dispatch-metrics`:
`ae00e2b19..9d2bfc77b`. Files touched: `loadtest/locustfile.py` (fixes),
`backend/scripts/seed_loadtest_bots.py` (new).

**Confirmed nothing went to `main` or `staging` (git branch):**
```
$ git status --short --branch
## feat/c50-phase0-dispatch-metrics...origin/feat/c50-phase0-dispatch-metrics
```
No `git merge`, `git checkout main`, or `git push origin main`/`staging`
commands were run this session. `main`'s HEAD (`c12009287`) and the
`staging` git branch (if it exists locally) were not touched.

---

## (f) Production confirmation

**Production (`spinr-backend-yyz`) was never touched this session.** Every
`flyctl` command targeting infrastructure used `--app spinr-backend-staging`
explicitly; grep of this session's command history for `spinr-backend-yyz`
or any bare `flyctl` command without an explicit `--app spinr-backend-staging`
flag returns none against a live app. The seed script itself refuses
`ENV=production` in code (§b) as a second, independent interlock. No
`flyctl secrets`, `flyctl deploy`, or `flyctl scale` command was run against
any app other than staging.

---

## (g) Recommendation (Ravi's recommendation — deploy/merge decisions are Kiran's)

1. **Fix the harness's rate-limit-vs-bot-pool mismatch before the next
   run.** Either run Locust with `--processes` spread across distinct
   egress IPs, or add a harness-side note that Scenario A/B results below
   ~400 concurrent users are structurally capped by the per-IP OTP limiter,
   not platform capacity. Without this, every future run on a single
   egress IP will show the same ~85% synthetic failure rate regardless of
   backend performance — that's not a useful signal to compare against.
2. **Deploy the C50 branch to staging next, then re-run Scenario A for a
   real A/B.** This session's numbers (P95 estimate 910ms) are a
   pre-C50 baseline on old code — genuinely useful as a comparison point,
   but T16's actual purpose (validating the direct-pool dispatch claim
   path under load) needs staging running Phase 2's code with the flag on.
   That's a deploy decision, explicitly reserved for Kiran per this
   session's constraints — not something to do proactively.
3. **Set `METRICS_AUTH_TOKEN` on staging** (§d) before relying further on
   `/metrics` data from it, independent of the C50 timeline.
4. **Do not run Scenario B (600-user ramp) until #1 is addressed.** Running
   it now would multiply the same rate-limit artifact, not produce a
   meaningful breaking-point number — the exact caveat the README already
   warns about, now confirmed in practice rather than theory.
5. Once #1 and #2 land, T7's Go/No-Go (ADR-011) can finally be written with
   real G3/G4 evidence instead of the "not run" placeholder currently in
   the plan doc.
