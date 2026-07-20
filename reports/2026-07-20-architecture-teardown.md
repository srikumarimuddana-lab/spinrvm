# Spinr — Engineering Director & Chief Architect Teardown

**Date:** 2026-07-20 · **Mode:** Read-only review (no code modified) · **Scope:** backend (FastAPI/Python 3.12), rider-app + driver-app + shared (React Native / Expo SDK 55), admin-dashboard (Next.js 16), CI/CD + infra.

**Method:** Three parallel deep-reads (backend / mobile / admin-infra). Every finding below is grounded in a specific `file:line` that was actually read, not inferred. Findings already marked resolved in `sprint-current.md` were excluded — this is what remains latent *after* the P0/P1/P2 sweep.

**Context of maturity:** This is a genuinely above-average pre-launch codebase. The security *substance* is strong — HttpOnly refresh cookies with rotation + reuse detection, server-authoritative fare charging, Stripe idempotency via `claim_stripe_event`, OTP SHA-256-at-rest with fail-closed lockout ordering, Decimal money discipline, PII scrubbing from Sentry. The residual risk is concentrated in **(a) a small number of silent security-control downgrades under degraded infra, (b) concurrency/replay-safety of the 19 background loops, and (c) an infrastructure/scaling layer that has not yet been built out to ride-share scale.** The teardown reflects that: fewer "the code is broken" findings, more "this will bite you under load or under a plausible prod misconfiguration."

---

## 🚨 Critical Issues & Security Flaws

Ranked by blast radius. The top three are the ones I would gate launch on.

### C1 — `REDIS_URL` unvalidated in prod → silent downgrade of OTP lockout, driver presence, and every loop leader-lock to per-replica dicts
`backend/core/config.py:526` · `backend/utils/redis_client.py:80` · `backend/core/lifespan.py:116`

`_validate_production_config` hard-requires `RATE_LIMIT_REDIS_URL` but **never** `REDIS_URL`. Yet `redis_client._get_redis()` reads *only* `os.environ["REDIS_URL"]` with no fallback to the rate-limit URL. Lifespan logs an error only when *all three* Redis URLs are unset.

- **Why it's critical:** A plausible prod config (operator sets `RATE_LIMIT_REDIS_URL` only) passes startup validation clean, yet `_check_otp_lockout`, `present_driver_ids`, `spinr:offer_skip:*`, and the surge/reconciliation `redis_set_nx` leader locks all silently fall to per-process dicts. The documented OTP 5-fail/hour lockout resets on every restart and isn't shared across replicas — brute-force protection quietly evaporates. This *is* a live security control turning off with no alarm, and it violates CLAUDE.md's own "never warn-and-continue on an auth failure" rule.
- **Root cause:** Validation and the client read different env vars; the transparent in-process fallback (a good dev-ergonomics feature) has no production tripwire.
- **Fix (conceptual):** Require `REDIS_URL` in `_validate_production_config`, *and* have `_get_redis()` fall back to `RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL` the way `ws_pubsub` already does. On fallback activation in prod, emit a security-domain Sentry event + audit row — never a bare `warning`.

### C2 — Surge loop fails **open** to `is_leader = True` on any Redis error → every replica recomputes & writes a regulated price
`backend/utils/surge_engine.py:372-379`

When `redis_set_nx` raises, the code sets `is_leader = True` and proceeds. Compounded by C1 (in-process fallback = every replica "acquires" its own lock).

- **Why it's critical:** A Redis blip during a 2-minute tick on a 2-replica deploy → both replicas run `recalculate_all_surges`, each inserting a `surge_pricing` history row (`:289`) and each writing `service_areas.surge_multiplier` (`:273`). Duplicated audit history and racing writes on a **rider-facing, regulated** price (the 2.5× cap is provincial). This is exactly the "loops run on every replica" hazard the Background Loop Recipe warns against.
- **Fix:** On lock error, **skip the tick** (fail closed). Treat the in-process path as non-authoritative for cross-replica election.

### C3 — Accept-race loser recorded into insurance Period 1 unconditionally → false commercial-coverage window in an append-only regulatory table
`backend/routes/drivers/ride_flow.py:344-346`

`_release_loser` calls `set_driver_available(lid, True)` then `record_period_transition(lid, 1)` with **no check the release took effect**. The parallel offer-timeout paths (`matching.py:1022`, `:1182`) deliberately guard this — they only open Period 1 when `released.get("is_available")` is true.

- **Why it's critical:** Driver B is offered a ride, goes offline, driver A accepts. B's cleanup clamps `is_available=False` (B is offline) but *still* appends a Period-1 (TNC commercial insurance) row to `driver_insurance_periods`. That is a **misclassified insurance window in an append-only, cannot-be-mutated regulatory audit table** — direct SGI/regulatory exposure. Per CLAUDE.md, period must be derived from ride state, and Period rows are append-only.
- **Fix:** Gate `record_period_transition(lid, 1)` on the release result, exactly as the timeout handlers already do.

### C4 — Admin IP allowlist trusts the spoofable left-most `X-Forwarded-For`
`admin-dashboard/src/middleware.ts` (`isIpAllowed()` → `forwarded.split(",")[0]`)

Uses the client-controlled left-most XFF entry. An attacker sends `X-Forwarded-For: <allowed-ip>, …` and bypasses `ADMIN_ALLOWED_IPS` entirely.

- **Why it's critical:** This IP allowlist is effectively the *only real* edge control (see C5) — and it's bypassable with a header. On Vercel the trustworthy value is the platform-set right-most hop / `x-real-ip`.
- **Fix:** Use the right-most trusted hop (or Vercel's `request.ip`), never `[0]`.

### C5 — Edge admin gate is cosmetic: `set-cookie` accepts any client string; middleware never verifies the signature
`admin-dashboard/src/app/api/auth/set-cookie/route.ts` · `admin-dashboard/src/middleware.ts`

The HttpOnly `admin_token` cookie is set from an unvalidated `body.token`; middleware only decodes + checks `exp`. A self-minted unsigned JWT with a future `exp` satisfies the entire edge gate (data stays safe because the backend verifies signatures). Defensible defense-in-depth — but it means C4's allowlist is the only real edge control, and three cookie lifetimes disagree (8h here, 7-day RT, 12h documented access). **Document the edge gate as cosmetic; do not let anyone "simplify away" the backend verification, and reconcile the TTLs.**

### C6 — Production deploys to Fly + Railway are **not gated by tests**
`.github/workflows/deploy-fly.yml` · `deploy-backend.yml`

Both trigger on `push: branches:[main], paths:[backend/**]` with no `needs:`/`workflow_run:` on `ci.yml`. They run concurrently with CI, so a green build is never a precondition — a merge that races CI deploys a red build to **both** primary and standby. (Ironically the *Render* deploy inside `ci.yml` *is* correctly gated.) **Fix:** gate Fly/Railway on `ci.yml` success via `workflow_run`, or fold them into `ci.yml` with `needs: [backend-test]`.

### C7 — CORS hard-codes `localhost:3000/3001` in every environment, reflected with `Allow-Credentials: true`
`backend/core/middleware.py:571-581` — low real-world risk, but an unnecessary credentialed production origin. Gate the localhost entries behind non-production `ENV`.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

The stated bar — *no raw errors leak to users; every failure logged with admin-actionable context* — is **met on the primary paths but broken on secondary/degraded paths.**

**User-facing (mobile) — one real leak, one landmine:**
- **E1 [HIGH] Transient refresh failure force-logs-out the user and wipes the refresh token.** `shared/api/client.ts:794-806, 909-928` vs `authStore.ts:284-290`. `refreshTokens()` deliberately returns `false` on a transient blip *to preserve the session* — but the 401 interceptor has no `return`, so it falls through to the generic handler that calls `logout()` and deletes `refresh_token`. A 2-second cellular hiccup on `/auth/refresh` bounces the rider to the OTP screen mid-booking, refresh token gone — the exact opposite of the documented intent. **Fix:** `return Promise.reject(...)` on transient-refresh-false before the session-clearing block; only hard-logout on a definitive 401 from the refresh call itself.
- **E2 [MED] `shared/api/cachedClient.ts` is a diverged parallel client — broken auth, no silent refresh, raw `detail`/`"Request failed"` leaks, no timeout, no CSRF, no PII redaction, and `console.log`s full URLs.** Reads token from the `auth_token` key that hardened `client.ts` no longer persists. Currently effectively dead code, but exported as `default` + `cachedClient` — the moment any authed endpoint is wired to it, it leaks. **Fix:** delete it, or make it delegate token + error extraction to `client.ts`.

**Admin logging / telemetry — errors swallowed exactly where CLAUDE.md forbids it:**
- **E3 [MED] WebSocket message handlers swallow all errors silently.** `driver-app/hooks/useDriverDashboard.ts:1067` (`catch {}`), `:1070-1071` (empty `onerror`), `rider-app/hooks/useRiderSocket.ts:294` (`catch {}`). A malformed/partially-actionable `ride_cancelled`/`tip_received` frame vanishes with zero trail — undebuggable live WS issues, violating "never silently swallow dispatch errors." **Fix:** `console.error` + Sentry breadcrumb (domain `dispatch`), keeping the parse guard.
- **E4 [MED] `asyncio.create_task` fire-and-forget with no retained reference.** `backend/routes/webhooks.py:1288-1306`. The subscription-invoice email task's handle is discarded; Python can GC it mid-flight — the exact hazard `lifespan.py:525` explicitly guards against. Renewal invoices silently dropped under load with no error. **Fix:** use the project's `spawn`/registry that retains the handle.
- **E5 [Infra] Redis outage is logged at `warning` and continues** (`utils/redis_client.py`), silently disabling OTP lockout + rate limiting (ties to C1). This is a security-control degradation that may never page. **Fix:** Sentry security event on fallback + fail-closed OTP lockout.

**Structural telemetry blind spots (see also Tech Stack):**
- **E6 Metrics are in-process only.** `utils/metrics.py` states it: "Per-process only… we do NOT aggregate across replicas." A `/metrics` scrape hits one random worker and counters reset every deploy — so the KPI histogram `spinr_dispatch_offer_to_accept_duration_ms` (P95 < 2s target) is effectively **unmeasurable in aggregate**. You cannot prove your headline SLA today.
- **E7 No distributed tracing.** Sentry `traces_sample_rate=0.1` is error-APM sampling, not span-level tracing across rider→Vercel-rewrite→FastAPI→Redis→Supabase→Stripe. When dispatch misses 2s you can't see which hop cost it.

---

## 🐢 Performance Bottlenecks & Optimizations

All grounded, all on hot or regulated paths.

- **P1 [SLA] N+1 `quest_progress` query per claimed driver on the dispatch hot path.** `backend/routes/rides/matching.py:795-822`. Rider/incentive enrichment is correctly batched via `asyncio.gather` (`:781`), but the per-driver notify loop issues one `quest_progress` select per driver, serially — up to 10 sequential Supabase round-trips *inside* the <2s "offer → driver phone" SLA before any push is sent, worst exactly when supply is deep. **Fix:** one `.in_(driver_uids)` batch before the loop.
- **P2 [SLA + egress] Surge supply count is a full-fleet table scan per service area — twice.** `backend/utils/surge_engine.py:161-167`, called from `:213` (2-min loop) and `:326` (`get_surge_status`, synchronous on the admin request path, uncached). `_count_supply_in_area` pulls up to 5000 online drivers with *no area predicate* and polygon-filters in Python, once per area, on each tick and each admin page load. With A areas that's A full scans every 120s + A on every dashboard hit. (Latent: `:193` treats coordinate `0.0` as missing.) **Fix:** fetch the online set once per tick and bucket by polygon in memory, or use the PostGIS `drivers_available_in_polygon` RPC; cache `get_surge_status`.
- **P3 [dispatch correctness under load] `update_acceptance_rate` is a non-atomic read-modify-write.** `backend/repositories/driver_repo.py:274-295`. Reads `acceptance_rate`, computes the EWMA in Python, writes back — two concurrent updates for the same driver (accept on ride X + timeout-expiry on ride Y) interleave and one clobbers the other, silently corrupting the score `rank_by_eta_with_acceptance` uses to order dispatch. **Fix:** move the EWMA into a `SECURITY DEFINER` SQL function or row-locked RPC.
- **P4 [scale] The 19 polling loops run on *every worker of every replica*.** `core/lifespan.py`. Fly `min_machines_running=2 × UVICORN_WORKERS=2` ≈ dozens of loop instances all scanning Supabase on intervals — a DB-read storm with no backpressure, and dispatch/payment latency bounded by poll interval rather than event arrival. This is the single highest-leverage architectural bottleneck (see D1).
- **P5 [memory, minor] Unbounded in-trip growth.** `rider-app/store/rideStore.ts:916-921` (`chatMessages` appended with de-dup but no cap; reset only per-ride) and `SOSButton.tsx:42-53` (`Animated.loop` never stopped once the hold fires or in sending/failed states — an unbounded animation driver on a screen open for a whole trip). Both bounded by trip duration, so low severity — but Uber/Lyft cap in-trip chat (e.g. last 200) for exactly this reason.

---

## 💡 Tech Stack & Architecture Recommendations

Confirmed absences (`requirements.in` matches none of `opentelemetry|kafka|celery|rq|nats|pgbouncer|supavisor`). Ranked by leverage.

- **D1 — No message queue / event bus (highest leverage).** All async work is 19 interval-polling loops. **Impact:** latency bounded by poll interval; DB-read storm at scale; no backpressure or DLQ. **Fill with:** Redis Streams + consumer groups near-term (Redis already deployed), NATS JetStream / SQS as you grow. Move dispatch offers + payment retries onto it; keep loops only for genuine cron (T4A, retention purge). This also fixes P4 and half of C2's blast radius.
- **D2 — No connection pooler / read replica.** Backend talks to Supabase via PostGREST over HTTP with no Supavisor/pgbouncer pin and no read-replica routing. **Impact:** connection exhaustion as workers/replicas fan out; heavy reads (admin lists, reconciliation) hit primary and contend with dispatch. **Fill with:** Supavisor transaction-mode pooling + a read replica for reads.
- **D3 — No CDC / analytics pipeline.** Durable state is Supabase-only; dashboards read it directly. **Impact:** analytical queries compete with the transactional path. **Fill with:** Supabase logical replication / Debezium → BigQuery/ClickHouse (Canadian-region), or at minimum a read replica.
- **D4 — No distributed tracing.** **Fill with:** OpenTelemetry SDK + OTLP → Tempo/Honeycomb/Grafana Cloud; instrument FastAPI, httpx, redis, WS fan-out. (Answers E7.)
- **D5 — Metrics need a cross-process backend.** **Fill with:** `prometheus-client` multiprocess collector or push to Prometheus remote-write/OTLP so series survive restarts and aggregate. (Answers E6 — without this your KPIs are unprovable.)
- **D6 — No feature-flag / progressive-delivery system.** Runtime config is the `app_settings` table — a manual kill-switch, not % rollout. **Fill with:** OpenFeature + Flagsmith/Unleash (self-hostable, Canadian-region) so surge-tier / dispatch-radius changes roll 1%→100% with instant rollback and an audit trail.
- **D7 — Deploy is rolling + manual DNS failover, no canary; both targets get the same bad build simultaneously.** The DNS-CNAME failover (ADR-007) protects against a *provider* outage but **not a bad release** — there's no known-good standby to fail back to. **Fill with:** a canary stage (Fly canary machine group or Cloudflare weighted DNS) and stagger Railway behind Fly on a health gate so one target always holds the last-good build.
- **D8 — Load testing exists but gates nothing.** `loadtest/locustfile.py` is referenced by no workflow. **Fill with:** k6/Locust nightly/PR-labelled job asserting thresholds against the CLAUDE.md P95 table.
- **D9 — Thin edge; turn on the gateway you already pay for.** Rate limiting is SlowAPI in-process; the admin edge only has the (spoofable) IP allowlist. **Fill with:** Cloudflare WAF + rate-limiting rules as a real L7 tier so abusive traffic never reaches the single backend process.
- **D10 — No chaos/dependency-failure testing** for the load-bearing failovers (Railway↔Fly DNS, Redis→in-process). **Fill with:** a Toxiproxy game-day that validates failover RTO ≤ TTL *and* that Redis fallback doesn't silently disable security controls (C1).

**Honest read on the intentional design choices:** single-process backend is fine to thousands of concurrent WS with Redis fan-out — its ceiling is the loop DB load (P4) and per-process metrics (E6), not request handling. DNS-CNAME failover is correct and low-cost for provider-level DR; its ceiling is that it's manual, TTL-bound, and blind to bad releases (D7). Supabase-only is excellent for velocity + RLS; its ceiling is analytics contention (D3) and no pooling/replicas (D2). None of these are wrong for a Saskatchewan launch — they're pre-scale, and the plan below sequences them before they bite.

---

## 🛠️ Maintainability & Code Smells

- **M1 — Divergent, contradictory dispatch implementations.** `services/dispatch_service.py:423-430` fails **open** on a subscription-filter DB error ("dispatching unfiltered"); the live inline path `routes/rides/matching.py:439-445` fails **closed** (`all_drivers = []`). Two implementations of the same paid-access gate with *opposite* safety semantics; the service version also duplicates `claim_driver_atomic`. Whichever a future refactor wires in flips enforcement. **Collapse to one, deliberately fail-closed.**
- **M2 — God files.** `routes/admin/rides.py` (3,363 LOC), `routes/admin/drivers.py` (2,655), `features.py` (1,910), `routes/webhooks.py` (1,828). These are graphify god-nodes and change-risk concentrators. Carve by sub-domain.
- **M3 — Three divergent deploy definitions, no source of truth.** `railway.json` (1 replica, 4 workers), `fly.toml` (2 workers, 2 machines), `render.yaml` (workers unset) — capacity + the loop-multiplier differ per env with nothing syncing them. Pick one canonical tuning; document the rest as derived.
- **M4 — `render.yaml` is stale and would boot broken.** Sets `SUPABASE_KEY` (config requires `SUPABASE_SERVICE_ROLE_KEY`, hard-fails at `config.py:249`) and `JWT_SECRET: generateValue: true` (mints a *different* secret → every cross-provider token rejected). If ever used as the failover it advertises, it invalidates every session. **Delete the stale path or align it.**
- **M5 — Root `Dockerfile` installs deps without `--require-hashes`** (`:21`, unhashed `requirements.txt`) while `backend/Dockerfile` correctly uses `--require-hashes -r requirements-locked.txt`. Deploys use the backend one, so the root is a dev leftover — but a live footgun that bypasses the hash-pinning its own comment claims. **Remove or bring up to the lockfile.**
- **M6 — Heavy/duplicated dependency surface on a latency-sensitive API.** `pandas>=3.0.2` + `numpy` on a request path, *three* HTTP clients (`requests`/`httpx`/`aiohttp`), *three* LLM SDKs (`anthropic`/`openai`/`google-generativeai`) + `mcp`, and both `loguru` and stdlib logging. Inflates image size/cold-start (matters for Fly rolling deploys) and widens the CVE surface the audit job must chase. **Split a `requirements-agents.txt`; keep the runtime image lean.**
- **M7 — 302 migration files with many duplicate numeric prefixes.** Handled correctly today by full-filename idempotency keying and a CI prefix-uniqueness gate — but a real onboarding-comprehension tax. The 2026-04-28 slot-collision incident (retention silently broken ~45 min) shows the failure mode is live; the cross-PR `CREATE OR REPLACE` check is the right next layer.
- **M8 — Stale `/metrics` comment** (`server.py:217` says "No auth" while the code implements optional bearer auth below it) — invites someone to "simplify" the auth away. Align the comment; default to token-required in prod.

---

## 🧪 Testing & QA (Missing Edge Cases)

The suite is strong on the happy paths and the closed P0s (double-accept race, Stripe idempotency, underpay guards, ride state machine). The gaps map directly to the findings above — each wants a regression test:

- **Q1 — Degraded-Redis contract.** No test asserts OTP lockout + rate limiting *stay enforced* (or fail-closed) when `REDIS_URL` is absent but `RATE_LIMIT_REDIS_URL` is set (C1/E5). This is the highest-value missing test — it encodes a security invariant a plausible prod config breaks.
- **Q2 — Loop leader-election under Redis failure.** No test that surge (and other single-writer loops) run on *exactly one* replica when `redis_set_nx` raises (C2). Assert fail-closed.
- **Q3 — Insurance-period audit correctness on the accept-race loser** (C3) — assert *no* Period-1 row is appended when the released driver is offline. Regulatory; belongs in the insurance-period suite.
- **Q4 — Transient-refresh session preservation** (E1) — assert a 5xx/network failure on `/auth/refresh` does **not** clear the refresh token or bounce to login. Directly protects the documented intent.
- **Q5 — Native safety-report location.** No test catches that `locationStore` uses web-only `navigator.geolocation`, so incident reports submit `location: null` on every real phone (see S1 below). A native-mock test asserting coordinates are attached would have caught it.
- **Q6 — Tip-after-settlement** (see S2) — assert `/tip` cannot credit driver earnings once `payment_status == 'paid'` without a corresponding charge.
- **Q7 — Perf gates.** No CI job enforces the P95 SLA table or `perf_baseline.py` (D8) — dispatch-latency regressions ship undetected.

---

## Additional safety-critical findings (mobile) — call-outs

- **S1 [HIGH, safety] Safety incident reports submit `location: null` on every native device.** `shared/store/locationStore.ts:57-117` uses web-only `navigator.permissions`/`navigator.geolocation`; on iOS/Android the `try` throws and `currentLocation` stays null. The continuous watcher was removed (comment `:94-106`) with no native replacement, and `driver-app/app/report-safety.tsx:95-99` gates the payload on `latitude != null` — so GPS is always dropped from safety/SOS reports on real phones, defeating the safety-audit purpose exactly when it matters. **Fix:** source location from `expo-location` (or the dashboard's `locationRef`), not the web-only shared store. *This one I'd escalate alongside C1–C3.*
- **S2 [MED, money] `add_tip` credits `driver_earnings` but never collects the tip from the rider.** `backend/routes/rides/payments.py:52-159` writes `tip_amount` + bumped earnings + notifies the driver, with no charge and no `payment_status` guard. If the fare already settled, `/tip` credits the driver money never taken from the rider — a direct payout shortfall on the 0%-commission model. **Fix:** confirm intended capture flow; route `/tip` through settlement, or block it once `payment_status == 'paid'`.
- **S3 [MED] Rider lifecycle WS events bypass the monotonic version guard.** `rider-app/hooks/useRiderSocket.ts:101-131`: `driver_accepted`/`driver_arrived`/`ride_started`/`ride_completed` call `applyRideStatusFromWS` with no `version`, skipping the `_lastEventVersion` staleness check that `ride_status_changed` (`:174-187`) uses. A delayed frame on a jittery reconnect transiently regresses the rider's screen. **Fix:** forward `data.version` on the lifecycle cases too.
- **S4 [MED, a11y/regulatory] SOS state transitions have no live-region announcement (WCAG 2.1.3 / 4.1.3).** `SOSButton.tsx:180-225` — the amber "FAILED"/"Sending…" states are visual + a transient `Alert` only; a low-vision driver gets no spoken confirmation the alert sent, on a safety-critical control under a WCAG 2.1 AA mandate. **Fix:** `accessibilityLiveRegion="assertive"` on the status text.

**Genuinely solid, verified, not flagged:** rider `rideStore` optimistic-update correctness (`_clearedRideId` guard, different-ride fetch guard, WS-vs-DB staleness guard); reconnect logic (generation counter, auth watchdog, jittered backoff, `last_seq` resume); `payment-confirm.tsx` error funneling + double-submit + 409/402 recovery + a11y; and on the backend, the double-accept atomic guard, Stripe idempotency, server-authoritative charging, and Decimal discipline. Credit where due — the core money and race paths hold up under scrutiny.

---

## 📈 Manager's Verdict

**Overall health: B+ / "strong core, thin edges, pre-scale infra."** This is not a codebase in trouble; it's a mature pre-launch product whose *hardest* problems (money correctness, ride-state races, Stripe idempotency, auth substance) are already solved well. The risk has migrated outward — to what happens when infra degrades, when the fleet scales past 1–2 replicas, and to a handful of secondary paths that skipped the rigor of the primary ones.

**The through-line across every serious finding is the same failure mode: silent degradation.** C1 (Redis downgrade), C2 (surge fail-open), C3 (false insurance row), E1 (silent logout), E3 (swallowed WS errors), S1 (dropped safety GPS), S2 (uncollected tip) — none of these throw, page, or fail loudly. They quietly do the wrong thing. That is *precisely* the failure class CLAUDE.md's "do not silently swallow errors" rule exists to prevent, which tells me the principle is right but not yet uniformly enforced outside the paths that got audit attention.

**Compared to Uber/Lyft:** the ride-lifecycle correctness, money discipline, and Canadian regulatory baking-in are competitive — arguably *cleaner* than a generic clone because the 0%-commission and PIPEDA/SGI constraints forced discipline. Where it trails the incumbents is the operational maturity layer they take for granted: an event bus instead of polling loops, cross-process/traced observability, progressive delivery with flags + canary, connection pooling + read replicas, and load/chaos testing as gates. None of that is a code-quality problem — it's a build-out sequence, and the plan below orders it so the launch-blockers land first and the scale work lands before it's needed rather than after.

**Maintainability:** good module boundaries and conventions, dragged down by a few god-files (M2), three unsynchronized deploy configs (M3/M4), and a heavy runtime dependency surface (M6). All addressable incrementally.

---

## Prioritized Plan (what to do, in order)

### Wave 0 — Launch blockers (this week; each ≤ 3 files, each with the regression test from §Testing)
1. **C1** — require `REDIS_URL` in prod validation + `_get_redis()` fallback + fail-closed OTP lockout with a security alert on fallback. *(Q1)*
2. **C2** — surge loop fails closed on lock error. *(Q2)*
3. **C3** — gate loser Period-1 transition on the release result. *(Q3)*
4. **S1** — native GPS on safety/SOS reports via `expo-location`. *(Q5)*
5. **C6** — gate Fly/Railway deploys on CI success.
6. **E1** — stop transient-refresh from force-logging-out. *(Q4)*

### Wave 1 — Correctness & silent-failure cleanup (next 2 weeks)
7. **S2** tip-after-settlement guard *(Q6)* · **P3** atomic acceptance-rate · **M1** collapse dispatch impls fail-closed · **E3** log swallowed WS errors · **E4** retain background-task handle · **C4/C5** XFF right-most hop + document edge gate + reconcile TTLs · **S3** WS version guard · **S4** SOS live-region.

### Wave 2 — Performance & the P95 you can't currently prove (weeks 3–4)
8. **P1** batch quest lookup · **P2** single-scan surge supply + cache `get_surge_status` · **E6/D5** cross-process metrics (so the <2s dispatch KPI becomes measurable) · **D8** wire the existing load test into CI as a gate. *(Q7)*

### Wave 3 — Scale-out infra (before it bites, not after)
9. **D1** event bus (start with Redis Streams for dispatch offers + payment retries; retires most of P4) · **D2** Supavisor pooling + read replica · **D4** OpenTelemetry tracing · **D6** feature flags · **D7** canary/staggered deploy · **D9** Cloudflare WAF/rate-limit · **D10** chaos game-day. **D3** CDC/warehouse when analytics contention appears.

### Continuous — Maintainability
10. **M4/M5** delete stale `render.yaml` + root `Dockerfile`; **M3** one canonical deploy tuning; **M6** split `requirements-agents.txt`; **M2** carve god-files by sub-domain as they're touched; **M8** fix the `/metrics` comment.
