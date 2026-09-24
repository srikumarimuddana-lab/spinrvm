# Blueprint — top-10 Rebuild Delta cards (Wave B Architect)

**Model:** fable / general-purpose · **Returned:** 2026-09-24 ~14:46 UTC · **Orchestrator note:** report pasted verbatim below; only this header was added. One inherited number is wrong: the Observability card repeats OBS-001's "matching.py 122 error calls"; the Verifier found 31 in that file and 121 across `routes/rides/` (see `verification.md`) — the ~290 total and the mechanism stand. The 10× figures in the Redis card are arithmetic (INFERRED), as the card itself says. "Uber/Lyft do" lines cite only `industry-stack-benchmark.md`, whose sources could not be fetched (INFERRED/UNKNOWN).

---

Read: master §4/§5/§7.3, all 10 lane files + RUN_LOG, benchmark §2–§4. Ordering = lane priority score × leverage (how many other findings the fix unblocks). Evidence labels on "Spinr today" are inherited from the lane; none upgraded. Benchmark HOLDs (Kafka, mesh, native, own maps, ML) are respected — no card proposes any of them. "Uber/Lyft do" cites only benchmark §2 (INFERRED there) or says UNKNOWN. No PII.

---

## Epic: Platform reliability — background-loop placement (process-role split)
- Verdict per inherited pattern: **MODIFY** — "asyncio loops on every replica" stays; the unconfigured placement registry is the defect. Evidence: A3-001 (score 80), `backend/core/background_loop_registry.py:21,86` (`resolve_process_role` → `"all"`), `:113-114` (spawn everything), `backend/fly.toml [env]` has no `SPINR_PROCESS_ROLE`; registry = 15 api + 3 worker_wave1 + 27 deferred = 45 (CLAUDE.md's "42" is stale).
- Keep (already best-in-class): the role/allowlist registry itself; `loop_watchdog` already filters by role (`lifespan.py:804-861`); dispatch-critical loops use atomic DB claims, not Redis locks (A4 table: `scheduled_rides.py:448-463`, `offer_expiry_reaper.py:15,71,148`).
- Uber/Lyft do: Cadence durable-workflow engine for long-running flows (benchmark §2, INFERRED; §3 verdict ASSESS — this card is the prerequisite, not the replacement).       Spinr today: **VERIFIED** — 45 loops × every process (2 warm × 2 workers = 4; 16 at burst; + Railway 4) poll one non-autoscaling DB; burst machines woken for *user* traffic add *loop* traffic.
- Clean-sheet Spinr would: API replicas serve requests only; one worker role owns loops; the burst pool never adds DB floor.      Why: DB load floor decoupled from replica count; makes the benchmark's workflow-engine ASSESS answerable (one place to swap).
- How: `SPINR_PROCESS_ROLE=api` on `app`/`burst` process groups, one `worker` machine (or `all` on a single dedicated machine); identical env on Railway.   Who: backend/SRE owner.   When: **Now**.
- Incremental path: 1) set `SPINR_PROCESS_ROLE=api` on one burst machine, watch `flyctl logs | grep "Background loop process role"` + DB audit top-5 loop-poll counts — rollback: unset env (config revert, no code). 2) add dedicated worker machine, flip remaining app machines — rollback: unset env on app machines; loops resume everywhere (duplicate execution is replay-safe for the 5 loops A4 read; the other 37 are UNKNOWN per DISPATCH-003 — read them before step 2). 3) Railway parity: same env + `UVICORN_WORKERS` 4→2 (A3-005), extend `standby-parity-monitor.yml` to diff env — rollback: revert `railway.json`.
- Cost **S** · Risk M (a loop that runs nowhere if the worker dies — watchdog must alert) · Reversibility high · **Build (config only)**.
- Advantage type: operational / cost — not defensible, but foundational.
- "Why not?": it was built, the deploy config was never set, and the safe default became production. Nothing simpler exists — this *is* the simple thing; adding leader locks to the 14 unlocked loops was rejected by the lane because locks fail open on Redis error (`surge_engine.py:483`).

---

## Epic: Engineering gates (cross-cutting) — PR-gate decay
- Verdict per inherited pattern: **REPLACE** the dead automated-review layer; **MODIFY** branch protection. Evidence: A1 §4 decay table — C7 `.github/workflows/claude-review.yml:14-19` (`ANTHROPIC_API_KEY` deliberately unset), C9 (last Codex comment PR #2877, ~200 PRs since), C73 (required-checks list last updated 2026-08-01 while ~57 checks run; PR #5048 merged 47 s after opening with checks queued), C12 (Codecov `continue-on-error: true`), RECURRENCE-004.
- Keep: SR-03 blocking semgrep money gate (promoted by *running* the tool, A5 steelman); Gitleaks/Semgrep/Bandit/ZAP; admin visual regression merge-blocking (B38); migration-check CHECK B; `test_loguru_call_conventions.py`.
- Uber/Lyft do: UNKNOWN (benchmark has no source on review gating).       Spinr today: **VERIFIED** — no automated safety review for ~55 days on PRs touching money/auth/migrations/dispatch/safety; a merge can complete before any required check finishes.
- Clean-sheet Spinr would: required-checks list derived from the workflow inventory and enforced; one funded automated reviewer path-filtered to money/auth/migration/dispatch/safety paths so cost is bounded; no `continue-on-error` on a gate job without a `[CR]` reference.      Why: every other card here ships through this gate; A43-class merges are mechanically possible today.
- How: (a) branch-protection audit (C21) adds the money/auth/migration/dispatch gates as required; (b) re-enable `claude-review.yml` with `paths:` filter (`backend/routes/{rides,payments,auth,webhooks}/**`, `backend/migrations/**`, `backend/services/payment_*`) or diagnose C9; (c) workflow lint failing on `continue-on-error: true` in a gate job lacking a CR id.   Who: repo owner (GitHub-admin action — the agent integration cannot do this).   When: **Now**.
- Incremental path: 1) human updates required checks (no code; rollback: remove entries). 2) path-filtered review with secret set, `continue-on-error` for one week to observe cost, then required — rollback: unset the secret. 3) `continue-on-error` lint — rollback: delete the job. No app_settings flag applies; all GitHub-side.
- Cost S (config) / M (recurring reviewer spend) · Risk L · Reversibility high · **Buy** (LLM review) + **Build** (lint).
- Advantage type: operational / trust — defensible only as compounding correctness.
- "Why not?": a cost decision (C7), an undiagnosed vendor silence (C9), and no owner for the branch-protection list. Simpler alternative — manual `spinr-*` reviewer runs per CLAUDE.md — is already the policy and demonstrably insufficient (A43).

---

## Epic: Observability — domain-tag CI gate + T0 backfill
- Verdict per inherited pattern: **MODIFY** — keep the loguru→Sentry bridge and PIPEDA scrubber; add enforcement. Evidence: OBS-001 (`routes/rides/matching.py` 122 error calls / 0 `.bind(domain=)`; `routes/drivers/*.py` 168 / 0; bridge `server.py:698-727`, `utils/sentry_scrub.py:209-228`), OBS-006 (`backend/tests/test_loguru_call_conventions.py:222-335` has no tag check).
- Keep: `utils/sentry_scrub.py` key+value redaction with "never drop an event on scrub failure"; `surface` stamped 100% (`sentry_scrub.py:150`); loguru-conventions static scanner with meta-tests; metric naming correct at every site found.
- Uber/Lyft do: M3 metrics + Jaeger tracing (Uber), Envoy stats (Lyft) — benchmark §2 INFERRED; §3 tracing verdict ASSESS (set the ADR-014 trigger).       Spinr today: **VERIFIED** — 290 ERROR sites in the two highest-SLA domains reach Sentry tagged only `surface=backend`; 263 stdlib-`logging` modules structurally cannot carry `domain`.
- Clean-sheet Spinr would: every ERROR emission carries `domain` + entity id by construction (module-level `logger = logger.bind(domain=...)`, the `utils/outbox_worker.py:28` pattern), enforced by a baseline-gated static test; T0 stdlib modules migrated or given tagged explicit captures.      Why: on-call can filter `domain:dispatch` at 3 a.m.; makes the SLA alerts (next card) routable; sets OpenTelemetry-compatible attribute naming ahead of the ADR-014 trigger.
- How: `test_error_level_loguru_calls_bind_domain` with an allowlist of today's ~290 offenders; `tagging-plan.md` Phase 1→2 (10 T0 files in stated order, `matching.py` first).   Who: observability owner.   When: **Now** (gate) / **Next** (backfill).
- Incremental path: 1) test + baseline (test-only; rollback: delete the test). 2) backfill `matching.py`, `drivers/ride_flow.py`, `drivers/_shared.py`, shrink baseline — rollback unnecessary (telemetry-only, no control-flow change). 3) remaining 7 T0 files + document the *mechanism* in CLAUDE.md Observability Conventions. No flag needed; no live data touched.
- Cost M · Risk L · Reversibility high · **Build**.
- Advantage type: operational — prerequisite, not defensible.
- "Why not?": the bridge was built but the requirement was never enforced; domains that adopted `.bind` did so by author habit. Simpler (review-only) rejected per OBS-006 — the "10 audits, no standing check" pattern.

---

## Epic: Observability — SLA metrics for the 3 uninstrumented rows + alerting (+ Maps tripwire)
- Verdict per inherited pattern: **MODIFY**. Evidence: OBS-002 (`routes/drivers/location.py` only `spinr_live_marker_write_failures_total`; `routes/auth.py` only event counters; `routes/webhooks.py` no duration metric); CR-2026-008/#3295 open (ADR-010 §5 Grafana agent + 2 rules unprovisioned); `monitoring/synthetic-checks.yaml` is spec-only by its own header; A3-004 (`utils/maps_budget.py:115-116` $5/day cliff, no 70% warn, fails open `:15-17`).
- Keep: `utils/metrics.py` registry; existing `spinr_dispatch_offer_to_accept_duration_ms`, `spinr_fare_calc_duration_ms`, `spinr_payment_settlement_duration_ms` (`routes/rides/payments.py:346`), `spinr_ws_fanout_duration_ms`; atomic Maps `reserve_budget` Lua (C104).
- Uber/Lyft do: M3 (benchmark §2, INFERRED).       Spinr today: **VERIFIED** — 3 of 8 CLAUDE.md SLA rows have no measuring metric; 0 of 8 have a firing alert; `capacity_watchdog` is silent because `ALERT_WEBHOOK_URL`/`ALERT_EMAIL_TO` are unset (capacity-table).
- Clean-sheet Spinr would: every SLA row maps to a named histogram and an alert rule checked into the repo (alert-as-code); capacity tripwires (Maps ratio, DB queue depth, Redis ops) on the same channel.      Why: a regulator/auditor asking "show me P95 location-write" gets an answer; the Maps cliff becomes a slope.
- How: `spinr_drivers_location_write_duration_ms`, `spinr_auth_token_refresh_duration_ms`, `spinr_payments_webhook_duration_ms{event_type}`; `spinr_maps_budget_spent_ratio` gauge alerting at 0.7; provision Grafana Cloud agent (human); dispatch-latency + payment-failure rules first, WS/match-rate second.   Who: SRE + a human with Grafana/vendor-dashboard access (A3-006).   When: **Now** (metrics, tripwire) / **Next** (alerts).
- Incremental path: 1) emit the 3 histograms + Maps ratio gauge (additive; rollback: remove the lines). 2) set `ALERT_WEBHOOK_URL`, provision agent, 2 day-one rules — rollback: disable rule / unset env. 3) move budget to `app_settings.maps_daily_budget_usd` so it can be raised without deploy — rollback: setting revert. Pair `routes/webhooks.py` metric with its `domain=payments` bind (tagging-plan item 10) so both ship together.
- Cost S (metrics) / M (alerting) · Risk L · Reversibility high · **Build** + **Buy** (Grafana Cloud).
- Advantage type: operational / trust.
- "Why not?": the CR is blocked on human provisioning; the SLA table was written as aspiration before instrumentation. Simpler (synthetic checks) rejected — that file talks to nothing.

---

## Epic: Payments — float-on-NUMERIC systemic fix
- Verdict per inherited pattern: Decimal-only rule **KEEP**; enforcement + schema **REPLACE**. Evidence: MONEY-001 (`routes/drivers/earnings.py:490,964` raw float `sum()` of `tip_amount`, two lines under a comment saying the opposite), MONEY-005 (`.semgrep/spinr-rules.yml:117-124` excludes that exact file), MONEY-004 (`.claude/hooks/pre-commit:114-121` warns, never blocks — CLAUDE.md overstates it), RECURRENCE-001 (legacy `payouts.amount` FLOAT, B28, forces `float()` at every DB boundary; 41+ change-log entries).
- Keep: `_d()/_round()/_f()` helpers; `driver_earnings_with_tip()` idempotent-by-construction recompute (`fare_service.py:252-300`); SR-03 blocking CI gate; nightly `stripe_reconcile.py`; double-entry ledger; `platform_share` genuinely absent from the codebase (0% commission is not a config that could flip).
- Uber/Lyft do: UNKNOWN (benchmark has no money-arithmetic source).       Spinr today: **VERIFIED** — two driver-visible tip totals drift from the sum of receipts; the gate's documented blind spot is the file with the bug; the root cause is a FLOAT column.
- Clean-sheet Spinr would: NUMERIC at every money column; a single typed money serializer that is the *only* place Decimal→float happens, so semgrep can be file-agnostic; local pre-commit blocks with the same rule list CI uses.      Why: a driver statement that reconciles to the cent is the proof behind the 0%-commission promise; auditor/plaintiff exposure closes as a class, not per site.
- How: (1) fix 2 sites + regression test with 1.10/2.20/3.30; (2) semgrep pattern for unwrapped `sum()` over dict values, drop the earnings.py exclusion, flip pre-commit step 6 to `BLOCKED=1` scoped to SR-03's 12 files; (3) additive `payouts.amount_decimal NUMERIC(12,2)` with dual-write + backfill + dual-read behind `app_settings.payouts_amount_decimal_read`.   Who: payments owner.   When: **Now** (1, 2) / **Next** (3).
- Incremental path: 1) display-only fix — rollback: revert is safe (no persisted change). 2) tooling — rollback: revert rule. 3) flag off → readers use the FLOAT column; dual-write keeps both consistent so no data repair is needed; a reconcile query (`amount != amount_decimal`) lists any backfill mismatch; never drop the FLOAT column during live testing.
- Cost S + S + M · Risk L / L / M · Reversibility high under dual-write · **Build**.
- Advantage type: **trust** — defensible: exact, itemized, commission-free earnings are the product claim.
- "Why not?": the schema fix was deferred post-launch and the gate became the bandage; keep-patching-sites *is* the recurrence family. Sibling MONEY-002 (receipt fallback bundles GST+PST into one "Tax" line, `utils/receipt_pdf.py:174-199`) belongs in the same epic: close the `tax_breakdown` write-path gap or compute the split from booking-time rates — additive, display-only.

---

## Epic: Auth & identity — asymmetric JWT with `kid`
- Verdict per inherited pattern: **MODIFY** ADR-005's symmetric choice. Evidence: SEC-A2-001 (score 40) — `backend/dependencies/__init__.py:50` (`HS256`), `core/config.py:136-141`, `utils/crypto.py:29` (OTP pepper falls back to `JWT_SECRET`), `routes/admin/auth.py:244-249` (admin claims incl. `modules` signed with the same secret), ES256 exists only for APNs (`utils/apns_client.py:107`); SEC-A2-008 (`modules` authorised from the claim until token_version bump).
- Keep: `algorithms=[HS256]` pinned on every decode; refresh rotation + reuse-as-theft cascade with bounded grace (`utils/refresh_tokens.py:4-73`); admin layering (aud, jti denylist, DB `is_active` + `token_version`, TOTP, fail-CLOSED break-glass); production fail-fast on weak secrets (`config.py:367-399`).
- Uber/Lyft do: UNKNOWN.       Spinr today: **VERIFIED** — one secret mints and verifies every token class and lives on both deploy providers; rotation is a hard cutover that logs everyone out.
- Clean-sheet Spinr would: ES256/EdDSA, `kid` header, private key only on the mint path, verifiers (deps, rate-limiter, middleware, WS, MCP server) hold the public key; rotate by publishing a new kid; `OTP_PEPPER` set explicitly; `token_version` bumped on any `admin_staff.modules`/role change.      Why: shrinks mint blast radius (env, heap dump, backup) to one path; rotation without forced logout; unlocks DPoP later (A2 research table).
- How: key pair via env/`app_settings`, in-process JWKS, dual-verify window of one access-token lifetime.   Who: auth owner.   When: **Next**.
- Incremental path: 0) set `OTP_PEPPER` in prod (zero code; rollback: unset — only in-flight OTPs within TTL are invalidated). 1) verifiers accept HS256 *and* ES256 (`JWT_ACCEPT_ASYMMETRIC=true`) — rollback: flag off, nothing minted yet. 2) mint ES256 (`JWT_ASYMMETRIC_MINT=true`) — rollback: flip back; verifiers still accept both, no forced logout. 3) after one refresh lifetime (30 d) stop accepting HS256 — rollback: re-enable acceptance. Blast radius is the enumerated `jwt.decode/encode` sites; dashboard and apps only consume tokens.
- Cost M · Risk M · Reversibility high inside the window · **Build** (PyJWT already does ES256 here).
- Advantage type: trust / operational — table stakes, not defensible.
- "Why not?": ADR-005 chose simplicity and no incident forced the change. Simpler (rotate HS256 more often) rejected: global logout and no blast-radius reduction.

---

## Epic: Data protection — `pgsodium` → Vault-only primitives / envelope encryption
- Verdict per inherited pattern: app-layer Vault RPC **KEEP**; direct `pgsodium.*` calls **REPLACE**. Evidence: SEC-A2-002 (score 32) — `backend/migrations/357_encrypt_emergency_contacts.sql:50-74`, `docs/runbooks/pii-key-rotation.md:31-60` call `pgsodium.valid_key/create_key/disable_key`; `utils/vault_pii.py:24,70` uses Vault RPCs; deprecation is INFERRED from a search snippet (lane could not fetch the page).
- Keep: SIN stored only as a Vault UUID with no app read path (`migrations/289:80`); SECURITY DEFINER RPC boundary; a rotation runbook exists at all.
- Uber/Lyft do: UNKNOWN.       Spinr today: **VERIFIED** (design) / **INFERRED** (deprecation impact) — 7-year-retention driver PII decryptability depends on an extension the vendor says is pending deprecation; the rotation runbook stops working the day it is removed.
- Clean-sheet Spinr would: crypto boundary in app code — AES-256-GCM data keys wrapped by a ca-central KMS, key id stored beside ciphertext; the DB extension is not load-bearing.      Why: vendor-timeline independence for regulator-facing records; PIPEDA residency is explicit; rotation is a re-wrap, not a re-encrypt.
- How: inventory every `pgsodium.*` reference → rewrite runbook to Vault-only primitives; then additive envelope column with dual-read.   Who: security owner + a human to run Supabase `list_extensions` on prod.   When: **Now** (inventory + runbook) / **Later** (envelope — reassess after step 1; Vault-only may suffice if Supabase's "interface unchanged" statement holds).
- Incremental path: 1) confirm prod extension state (read-only). 2) Vault-only runbook + round-trip test of every encrypt/decrypt RPC on a branch — rollback: keep the old runbook until the test passes. 3) additive `*_enc` column behind `app_settings.pii_envelope_read_enabled` — rollback: flag off, Vault column stays authoritative; never drop the Vault column until every row dual-verifies.
- Cost S (runbook) / L (envelope) · Risk M (undecryptable = regulatory) · Reversibility high with dual-read · **Build** + **Buy** (KMS).
- Advantage type: **trust** / compliance — defensible as regulator-facing evidence.
- "Why not?": the encryption design (migrations 32/137/289/357) predates the vendor signal. Simpler (wait for Supabase outreach) rejected by the lane: 7-year data cannot ride a vendor's timeline.

---

## Epic: Dispatch realtime — GPS-ping Redis coalescing
- Verdict per inherited pattern: **MODIFY** — Redis pub/sub + outbox stays (benchmark: Kafka HOLD, confirmed by A3 §d); the per-safeguard key sprawl is the defect. Evidence: A3-002 (score 48, INFERRED arithmetic) — per ping: `routes/websocket.py:1185` GET, `utils/location_integrity.py:190` GET+SET, `utils/location_write_gate.py:104-146` GET/SET NX, `socket_manager.py:574-596` SET, `utils/ws_pubsub.py:197-229` PUBLISH; every replica decodes every message (`ws_pubsub.py:333`).
- Keep: DB-free hot path (3 s marker gate, 5 s active-ride cache, ETA off-loop `websocket.py:1229-1250`); non-durable location publishes never evict ride events from the 50-entry replay outbox (`:1255-1261`); sequence-numbered replay on reconnect (`:960-968`); adaptive cadence in driver-app (`useDriverDashboard.ts:72-89`).
- Uber/Lyft do: Kafka backbone + H3 (benchmark §2, INFERRED) — H3 KEEP already true; Kafka HOLD stands.       Spinr today: **INFERRED** — ~3k Redis ops/s plus 8× pub/sub decode at 1,000 on-trip drivers; capacity-table names Redis as the first 10× break point, ahead of Supabase round-trips and the Maps cliff.
- Clean-sheet Spinr would: one Lua script per ping returning (active_rides, integrity_prev, gate_ok) and doing SET + PUBLISH atomically; unicast publishes routed by key-hash to a per-replica channel so consumers skip connections they do not hold.      Why: holds the <150 ms location-write and <100 ms fan-out SLAs at 10× without any new infrastructure.
- How: `app_settings.ws_location_lua_path_enabled`, shadow-compare, Locust Scenario B (E1) with 150 driver bots at 0.5 Hz.   Who: realtime owner.   When: **Next** — measure **Now**; per A3-007 do not change the wire format during live testing.
- Incremental path: 1) metrics only — Redis ops/ping via `INFO commandstats`, negotiated WS extensions per connection (rollback: n/a). 2) Lua path behind the flag in shadow mode logging divergence — rollback: flag off, nothing written differently. 3) per-replica channel sharding behind `app_settings.ws_pubsub_sharded_channels`, consumers subscribed to both during the window — rollback: flag off.
- Cost M · Risk M (gate/integrity semantics must stay byte-identical) · Reversibility high · **Build / Open source** (Redis Lua).
- Advantage type: operational / cost — not defensible.
- "Why not?": each safeguard added its own key in isolation. Simpler (bigger Redis) rejected — consumer decode scales with machine count, not Redis size.

---

## Epic: Dispatch matching contract — DB-durable declined-driver exclusion + rider offer event
- Verdict per inherited pattern: Redis fast-path **KEEP**, add DB backstop (**MODIFY**); DISPATCH-001 is a doc-or-code decision. Evidence: DISPATCH-002 — `routes/rides/matching.py:1907-1916,661,929`, `services/driver_offer_service.py:50-54,187,238`; migrations `402_dispatch_claim_batch.sql:125,390` and `403:147,420` admit "offer_skip guard is skipped when Redis is down". DISPATCH-001 — `matching.py:1621` notifies only `driver_{id}`; `.claude/context/domain-dispatch.md:40` promises the rider a `driver_assigned` event.
- Keep: exactly two canonical guards + CAS at every write site (A4 table); accept CAS scoped to `{status, driver_id}` with lost-race re-read; atomic scheduled-dispatch claim; `release_driver_and_close_period` single implementation (`utils/insurance_periods.py:272-311`); correct ordering on offer timeout.
- Uber/Lyft do: UNKNOWN for offer-protocol specifics.       Spinr today: **VERIFIED** — under Redis outage a driver who declined is re-offered the same ride next tick; the rider is silent for the full 15 s offer window.
- Clean-sheet Spinr would: `ride_offers` (already the source of truth for offer status) drives exclusion — `NOT IN (declined/expired for ride_id)` — with Redis as a latency cache; the rider gets a `matching_in_progress` event, or the doc is corrected.      Why: "never re-offer a driver who said no" holds without Redis; the rider contract matches the code.
- How: `.in_()`-based exclude list from `ride_offers` behind `app_settings.dispatch_durable_offer_skip`; rider send additive at the `:1621` site.   Who: dispatch owner + product (for 001 — intent is genuinely open).   When: **Now** (002) / **Next** (001 decision).
- Incremental path: 1) shadow: compute the DB set, log divergence from the Redis set (rollback: remove). 2) flag on: union of both sets — rollback: flag off → Redis-only; no data change. 3) product answers DISPATCH-001 → add the rider send or fix `domain-dispatch.md` — rollback: revert (no live data). Watch the round-trip budget: the capacity table cites 800–900 PostgREST round-trips per unmatched ride; one batched query per tick, not per candidate.
- Cost S · Risk L · Reversibility high · **Build**.
- Advantage type: trust (driver is never nagged with a rejected match) / feature.
- "Why not?": Redis-only was an accepted residual when batch v2 shipped; the driver side was built, the rider side was not. Simpler (leave it) rejected — re-offering an explicit "no" is worse than most degraded modes, which fail slow rather than repeat.

---

## Epic: Cross-cutting — fork parity automation
- Verdict per inherited pattern: `docs/known-forks.md` registry **KEEP**; warn-only enforcement **MODIFY** to blocking parity tests. Evidence: RECURRENCE-002 (C100/C101/C90/C70; `driver-app/components/CarMarker.tsx` vs `shared/components/CarMarker.tsx`, fixes ported months late), SEC-A2-007 (`routes/auth.py:439-463,780-803` vs `routes/users.py:1154-1255` OTP dev-bypass duplicated), INFO-004 (`notifications.tsx` divergence unchanged since 2026-09-14), RELIABILITY-002 (dead `rider-app/utils/apiClient.ts`, zero importers, weaker retry model); pre-commit check 11 warns only.
- Keep: the registry's "why forked" column; `release_driver_and_close_period` consolidation as the model (5 hand-mirrored copies → 1); CLAUDE.md gate 10's recorded incident.
- Uber/Lyft do: RIBs shared mobile architecture (benchmark §2, INFERRED; native is HOLD for Spinr).       Spinr today: **VERIFIED** — a fix proven in one app stayed live-broken for riders for a full day; no automation enforces parity for any registered fork.
- Clean-sheet Spinr would: every registered fork has either a shared implementation or a CI parity test that diffs divergent regions against an allowlisted delta.      Why: eliminates a recurrence class rather than the next instance.
- How: `test_known_forks_parity` (backend) + Jest parity test (apps) reading `known-forks.md` as data; single `issue_otp_code()` helper; static test that the `"1234"` literal appears once in `backend/routes`; delete `apiClient.ts`.   Who: platform/tooling owner.   When: **Now**.
- Incremental path: 1) OTP helper consolidation + literal-count test (pure refactor, no live data; rollback: revert). 2) promote pre-commit check 11 to a CI failure for registry pairs (rollback: downgrade to warn). 3) delete `apiClient.ts`; decide the `notifications.tsx` fork (rollback: restore file). No flag — none of this observes live data.
- Cost S · Risk L · Reversibility high · **Build**.
- Advantage type: operational / trust (rider–driver parity).
- "Why not?": the registry was step one and enforcement never followed; warnings get dismissed. Simpler (consolidate everything into `shared/`) is not always possible — Android divergence is the stated reason for the CarMarker fork — hence allowlisted-delta tests.

---

### Cut from the top 10 (one line each, so they are not lost)
- A3-003 origin gzip + streaming exports (24): ship dark behind `ENABLE_GZIP` env, S effort — bandwidth is not the 10× bind, so it lost the slot to Redis ops.
- CONCURRENCY-001 admin edit last-write-wins (8): additive `expected_updated_at` → 409; check `corporate_accounts.py` too (unread).
- SAFETY-004 device fingerprint at signup: not an engineering card — needs a PIA/purpose-consent-retention decision first (PIPEDA data-minimization); product/legal call.
- SEC-A2-004 cert pinning: docs-only — write ADR-017 and reconcile threat-model RS-4; pinning itself rejected (breaks Cloudflare fail-over).
- MONEY-003 SK PST primary source (G9): escalation, not a rebuild delta — live compounding exposure either direction.
- SEC-A2-003 admin revocation fails open on Redis: `ADMIN_REVOCATION_FAIL_CLOSED` flag, S — fold into the auth card's rollout.

---

### Top 5 rebuild moves in plain language (for a non-engineer)
First, stop every web server from also running all 45 housekeeping jobs — a one-line config change that cuts database load and lets busy-hour servers actually help instead of adding to the pile. Second, turn the code-review safety net back on: for two months nothing automated has reviewed changes to money, sign-in, or dispatch code, and a change can currently be merged before its checks finish. Third, make errors say where they came from and measure the three service-level promises we publish but do not currently count, so an outage can be found in minutes rather than by hand. Fourth, fix the money-rounding class once — replace the one leftover "floating-point" payout column and tighten the lint so driver earnings always reconcile to the cent, which is the proof behind the 0%-commission promise. Fifth, change how sign-in tokens are signed so one leaked secret can no longer mint an administrator, and move driver PII encryption off a database extension the vendor is retiring — both done in stages with no forced logouts and no destructive steps.

### What we already do well — KEEP list
- **Ride state machine and race guards** (A4): two canonical guards plus atomic CAS at every write site; accept CAS scoped to `{status, driver_id}` with lost-race re-read; scheduled-dispatch and offer-timeout paths atomic and correctly ordered; sequence-numbered WS replay on reconnect.
- **Money discipline** (A5): Decimal helpers, `driver_earnings_with_tip()` idempotent-by-construction, a genuinely mature nightly Stripe↔DB reconciliation, SR-03 promoted to blocking by running the tool, and `platform_share` absent from the codebase — 0% commission is not a setting that could flip.
- **Auth fundamentals** (A2): keyed-HMAC OTP with constant-time compare and 24 h lockout; textbook refresh rotation with reuse-as-theft cascade; every `jwt.decode` pins its algorithm; broad production fail-fast on weak or placeholder secrets; layered admin auth with fail-CLOSED break-glass.
- **Client resilience** (A7): `shared/api/client.ts` refresh-dedup + subscriber queue, bounded 503 retry with a non-idempotent exclusion list; booking idempotency backed by a real DB unique index; app-kill reconciliation with its own regression test; forced-upgrade gate with a designed-in "never strand a mid-trip passenger" carve-out.
- **Safety** (A8): SOS never auto-dials 911; idempotency key per press; expired-but-valid mobile tokens accepted narrowly for SOS; per-contact SMS status returned to the client; PII deliberately kept out of safety logs; two independent loops share the same replay-safe SET-NX idiom.
- **Realtime hot path** (A3): GPS writes are DB-free (marker gate, active-ride cache, off-loop ETA); location publishes never evict ride events from the replay outbox; Maps budget breaker is atomic and stops spend in seconds; the loop-placement registry is already built.
- **Observability plumbing** (A6): PIPEDA scrubber with key and value redaction that never drops an event; `surface` stamped on 100% of events across all four surfaces; `test_loguru_call_conventions.py` with meta-tests; metric naming convention correct everywhere it was found; admin-dashboard checks Sentry's ingest region at boot.
- **Deliberate, documented trade-offs**: the 3.5 s fare-estimate wait (accepted SLA exception), `presence_sweeper.py` retirement with dated reasoning, B15(a) SOS DB-outage residual — each has a written "why", which is exactly what the steelman pass asks for.

Time-box note: all ten cards are synthesized from the lane files as written; no lane's INFERRED was upgraded, and A3's 10× figures remain arithmetic (no load test has run). Cards were not cross-checked against the code beyond what the lanes cite.
