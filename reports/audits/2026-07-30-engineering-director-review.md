# Spinr — Engineering Director & Chief Architect Review

**Date:** 2026-07-30
**Reviewer role:** Engineering Director / Chief Architect (read-only teardown)
**Method:** Repo recon + 8 parallel domain audits (security, dispatch/state-machine, money/payments, corporate billing, insurance-periods, regulatory/PIPEDA, frontend, telemetry/perf). Every finding carries `file:line` evidence; the three highest-severity items were re-verified against source by hand.
**Codebase snapshot:** ~115k LOC Python backend (806 files), 339 SQL migrations, 3 frontend surfaces (Expo RN rider + driver apps, Next.js 16 admin), 467 backend test files, 118 frontend test suites, Playwright e2e + Locust load harness.

> This is a **read-only** review. No production code was modified. Each remediation in the plan below must carry its own Change Impact & Risk Log entry (per `CLAUDE.md`) when implemented — the product is in live app testing.

> **Relationship to `SPINR_CODE_REVIEW.md` (2026-07-28, 301 findings):** that sweep's 16 criticals were spot-checked here and the sampled ones (driver-cancel ownership bypass, admin-wallet lost-update race, `password_hash` leak to the driver app, corporate allowance credit-instead-of-debit) are **all already fixed** in current code with finding-referenced comments and migrations. This review is a fresh pass; the findings below are **new or still-open**, not a re-print of that document.

---

## Executive summary

Spinr is a **substantially more mature codebase than its stage implies.** The money paths use `Decimal` end-to-end with deterministic Stripe idempotency keys, the ride-acceptance race is a real atomic conditional-UPDATE, the DB layer has a production-grade circuit breaker, Sentry PII scrubbing is genuinely careful, the insurance-period table is append-only enforced by a DB trigger, and data residency is fail-fast at boot. Process discipline (9 ADRs, 12+ runbooks, 22 CI workflows, a coverage ratchet, a breach register) is well beyond typical pre-launch norms, and remediation velocity is high — a 301-finding audit from two days ago is already largely closed.

The problems are not sloppiness; they are **the seams you get when a fast-moving team changes an architecture faster than its invariants and docs can follow.** The most serious findings are all "the code moved, the contract didn't":

- A **privilege-escalation P0** on the admin WebSocket that was fixed on the HTTP surface but never ported.
- A **corporate-wallet drain P0** where a cap counter is read but never incremented.
- A **money-recovery dead-end** (`payment_status="pending"`) that two independent auditors found from opposite directions.
- An **insurance-period misclassification** created when dispatch switched to batched offers — a regulatory/SGI surface where the documented Period-2 boundary no longer matches the live path.
- A **PIPEDA deletion-model change** (migration 216) that is well-engineered but diverged from the governing compliance docs without a cited legal review.

None of these are "rewrite the system." They are precise, mostly-small fixes plus a discipline layer (invariant tests, doc reconciliation, one blocking float-gate) so the seams stop reopening. Full detail and a sequenced plan below.

---

## 🚨 Critical Issues & Security Flaws

### C1 — [P0, VERIFIED] Admin WebSocket trusts the raw `users.role` column — privilege escalation
`backend/routes/websocket.py:521,610-611`
When a WS client connects with `client_type=admin`, `_verify_admin_payload()` returns `None` for a non-admin token (rider/driver `aud`), and the code **falls through** to `user = get_user_by_id(payload["user_id"])` and then admits on `user.get("role") not in _ADMIN_ROLES`. This is the exact anti-pattern the HTTP path documents and defends against at `dependencies/__init__.py:659` (gates on the `_admin_verified` marker, whose docstring explains that a bare `role` check "let any account whose users.role was ever set to an admin value pass every admin endpoint using a normal 15-min mobile token, with no MFA").
**Why it matters:** any account whose `users.role` was ever set to an admin value (done via `make_admin.py`/`admin/users.py`) can join the admin broadcast channel — live driver-location fan-out, admin alerts — on a **15-minute rider OTP token**: no MFA, no JTI revocation, no `admin_staff.is_active` check, no idle timeout.
**Root cause:** the `_admin_verified` fix was applied to `get_admin_user` and `documents.py` but `websocket.py` was never updated to match.
**Fix:** gate the admin WS branch on `user.get("_admin_verified")`, not `role`. ~2-line change; add a regression test asserting a rider token with `role="admin"` is rejected on the WS admin channel.

### C2 — [P0, VERIFIED] Corporate auto-approved allowance cap is decorative — company-wallet drain
`backend/routes/corporate_rider.py:244-268`
`submit_request` auto-grants a top-up when `used_auto < auto_approve_monthly_count`, reading `used_auto = allowance.get("auto_approved_this_period")`. A full-repo grep confirms `auto_approved_this_period` is **only ever** declared (default 0), reset to 0 at period rollover, and read here — **never incremented.** So `used_auto` is permanently 0 and the monthly cap never binds. The "one outstanding request" guard only blocks `status="pending"` rows; auto-grants are inserted as `status="auto_approved"` and dodge it.
**Why it matters:** a corporate rider can call the endpoint repeatedly, each request ≤ `auto_approve_topup_amount`, and drain the company master wallet with no effective cap and no dedup. Real money via `corporate_wallet_apply_delta`.
**Fix:** increment `auto_approved_this_period` atomically alongside the grant (ideally inside the RPC under the same row lock), and apply the same dedup/rate-limit to `auto_approved` inserts that `pending` gets. Add a test that the Nth+1 auto-request in a period falls through to `pending`.

### C3 — [P0, convergent — money + corporate] `payment_status="pending"` is a recovery dead-end
`backend/services/payment_service.py:268,284,496` set `pending`; `backend/utils/payment_retry.py:248-249` scans only `{failed, requires_action, processing}`; `backend/routes/rides/booking.py:407-423` blocks re-booking only on `failed`.
Three settlement-failure paths (wallet insufficient/suspended, corporate master-wallet insufficient, abandoned no-hold card) reset a **completed** ride to `pending`. Nothing retries `pending`, no admin alert fires (that only comes from the `failed`-path exhaustion branch), and the rider isn't blocked from booking again.
**Why it matters:** a delivered ride's fare can go **uncollected indefinitely with zero operational visibility.** Two auditors reached this independently (money from the retry-scan side, corporate from the self-booked-settlement side) — it is a structural gap, not a one-off.
**Fix:** fold `pending`-past-a-grace-window into the retry scan (or a dedicated sweep with an alert), and widen the re-booking block to include `pending`. Highest-ROI single fix in this review.

### C4 — [P1] Twilio inbound-SMS webhook fails **open** on missing secret
`backend/routes/webhooks.py:1868-1883` (contrast the Stripe handler 40 lines up at `:454-459`, which fails closed)
If `twilio_auth_token` is unset in `app_settings`, the STOP/START handler logs a warning and **still processes the unsigned request**, flipping CASL marketing-consent state for any spoofed `From` number. No `ENV` gate. Low blast radius (no money/PII) but an unauthenticated write to a compliance-relevant flag, and inconsistent with the Stripe path in the same file.
**Fix:** fail closed (reject unverified) exactly like the Stripe branch.

### C5 — [P1, known/tracked — confirm closure] Service-role key exposure incident not fully closed
`docs/incidents/2026-07-30-supabase-service-role-key-public-exposure.md` §5
The public `SUPABASE_SERVICE_ROLE_KEY` incident (commit `6583ed6`) is well-scoped and Supabase-key rotation is in progress, but the doc's own remaining action — **rotating the chained Stripe/Twilio/Google Maps secrets that live in `app_settings`, readable via the leaked key** — is not yet done. Until then the incident is open: a rotated Supabase key still leaves live payment/SMS credentials exposed via the same historical git blob.
**Fix:** rotate the downstream vendor secrets; then close.

### C6 — [P1] Corporate `admin_cancel_ride` orphans the open insurance-period row
`backend/routes/admin/rides.py:387-516` (driver freed at 452-465, no `record_period_transition`)
Frees the driver on cancel but never writes a period transition — unlike the sibling `admin_complete_ride` which correctly closes to Period 1 at `:577`. Its guard only rejects `completed`/`cancelled`, so it can also cancel an `in_progress` (Period 3, passenger-aboard) ride.
**Why it matters:** the open Period 2/3 row (with `ride_id`) is left dangling and only gets a false `ended_at` on the driver's next unrelated transition — corrupting the SGI commercial-insurance audit trail. Worst case: an orphaned Period 3 row from a force-cancelled in-progress trip.
**Fix:** add the same `record_period_transition(driver_id, 1)` call after driver release.

*(See the committed matrix / `SPINR_CODE_REVIEW.md` for the already-fixed prior criticals; C1–C6 above are new or still-open.)*

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**User-facing side is strong.** The mobile client is disciplined: 155 `getApiErrorMessage(…, contextual-fallback)` call sites vs. 37 raw alerts, no `JSON.stringify(error)`/`.stack` reaches users, and technical noise ("JSON Parse error", "Network Error") is explicitly filtered (`shared/api/client.ts:459-476`). Backend 5xx details are sanitized by registered handlers with a request-ID echoed for support correlation (`utils/error_handling.py:27`). Raw-exception-in-HTTPException leaks are down to 3 (all in `documents.py` upload paths, low value).

**Admin-logging side has real gaps:**

- **T1 — [P1] Metrics are emitted but nothing scrapes or alerts on them.** `utils/metrics.py` produces SLA-tuned histograms (`spinr_ws_fanout_duration_ms`, dispatch counters), but there are **zero** scrape/alert configs anywhere in `.github/`, `deploy/`, or fly config, and `/metrics` fail-closes in prod unless `METRICS_AUTH_TOKEN` is set. The only live alert is a loop-staleness Slack webhook (`core/lifespan.py:510-531`). **The SLA table in `CLAUDE.md` is instrumented but unenforced** — a P95 dispatch/fanout breach is invisible until users complain. This is the single largest observability gap.
- **T2 — [P1] Audit writes are fire-and-forget.** `utils/audit_logger.py:91-99` logs and drops the row on any DB failure — no retry, no outbox, no counter, and callers ignore the return. These rows back 7-year Saskatchewan/SGI retention; a transient Supabase blip silently loses regulatory evidence.
- **T3 — [P2] Quests swallow DB errors into half-valid 200s.** `routes/quests.py:465-467,481-482` returns empty quests / zeroed stats on `get_rows` failure — masking a DB outage as "no quests," exactly the `CLAUDE.md` "no half-valid responses" rule. Should 503.
- **T4 — [P2] Dual logging stacks.** 173 files use stdlib `logging`, 33 use loguru; the request-ID contextvar binds into loguru only, so stdlib lines carry no correlation ID, and the loguru PII sink-guard (`utils/log_guard.py`) doesn't cover stdlib. Sentry egress is separately scrubbed for all sources, so this is defense-in-depth, not a confirmed leak — but route stdlib through an InterceptHandler.

---

## 🐢 Performance Bottlenecks & Optimizations

**The structural ceiling:** every DB op is a PostgREST HTTP call executed on a 64-thread pool via `run_sync` (`repositories/_base.py:161`). At 20–80 ms/call that caps a replica at ~800–2,000 DB calls/s, and **sequential `await` chains buy latency, not throughput.** This is the backdrop for the N+1s below and the reason settlement is request-path-synchronous.

- **P1 — [P1] Driver-referrals N+1.** `routes/drivers/referrals.py:108-118` — 2 sequential queries per referred user; 50 referrals → ~100 serial round-trips (~3–5 s on a driver screen). Batch with one `$in` + one grouped count.
- **P2 — [P1] Admin-quest N+1.** `routes/quests.py:470-477` fetches up to **1000 progress rows per quest just to count them**; `:552-558` does 2 queries per participant (~400 round-trips for a 200-row page). Batch + server-side counts.
- **P3 — [P2] Location heartbeat = 3–4 round-trips per driver per tick.** `routes/drivers/location.py:440-528` (driver lookup, integrity check, update, breadcrumb insert, a **second** driver re-read, plus Redis). 100 drivers @1 Hz ≈ 300–400 pool ops/s — the biggest steady-state consumer. *Known/tracked (B3 breadcrumb-batching plan).*
- **P4 — [P2] Stripe webhook awaits FCM pushes inline** at 8 sites (`routes/webhooks.py:610…1543`); only one uses `create_task`. Risks the 500 ms webhook SLA → Stripe retry backlog. Background them, as `ride_complete.py:875` already does.
- **P5 — [P2] ~30 background loops run on every replica** (`core/lifespan.py` — the "16 loops" in `CLAUDE.md` is stale). Queries are projected/capped, but polling tax scales linearly with replica count and only daily money jobs take a leader lock.
- **P6 — [P3] Missing pagination caps** on 6 admin endpoints (`admin/faqs.py`, `maintenance.py`, `messaging.py`, `promotions.py`) — `Query()` with no `le=`. Add `le=200`.
- **Frontend perf:** whole-store Zustand subscriptions re-render the heaviest map screens on every write (`rider-app/app/ride-options.tsx:71-80`, a 2,137-line screen polling every 10–15 s); admin ships only 6 `dynamic()` splits with recharts statically imported into 8 pages and 2,800-line client pages. No mobile TTI/cold-start budget or bundle-size CI gate.

**Genuine perf strengths:** the dispatch hot path is already batched (`$in` lookups, Redis MGET for skip-keys, bounding-box pre-filter with a 500-cap, ETA call bounded to 1.2 s inside a 2 s SLA — `matching.py:270-673`); the WS layer splits durable vs. ephemeral so 1 Hz location can't evict ride events; the DB breaker + retry budget + deadline propagation is authentic load-shedding machinery.

---

## 💡 Tech Stack & Architecture Recommendations

**The stack is well-chosen for Saskatchewan scale** (FastAPI + Supabase/Postgres + Redis + Stripe/Firebase/Twilio; Expo RN + Next.js 16). The recommendations are about the seams that will bite as volume grows.

1. **Replace `supabase-py`-over-HTTP with native async Postgres (`asyncpg`) on hot paths.** The PostgREST-over-thread-pool model is the root perf tax (see 🐢). Even keeping supabase-py for CRUD, routing dispatch/settlement/location through an `asyncpg` pool removes the HTTP + thread-hop per query and lifts the per-replica ceiling by an order of magnitude. Highest-leverage architectural change.
2. **Move settlement to an event-driven pipeline.** Today the rider's HTTP call drives the Stripe call synchronously, with sweepers bolted on as a net — which is *structurally why C3 exists.* A durable settlement queue (outbox → worker) makes `pending` a state the system owns, not a dead end. Pairs naturally with fixing C3.
3. **Add a true double-entry ledger.** `financial_events` is an append-only event log (`delta_cents`), not a balancing chart-of-accounts (platform revenue / driver payable / tax payable). Uber/Lyft enforce the balance invariant **on write**; Spinr catches drift via a nightly batch diff up to 24 h late (`stripe_reconcile.py`). A ledger that refuses to persist an unbalanced transaction turns a whole class of money bugs (C3 included) into a write-time 500 instead of a silent leak.
4. **Get secrets out of `app_settings` into a real secrets manager** (Vault / AWS / GCP Secrets Manager). Storing Stripe/Twilio/Maps keys in a Supabase table is exactly what turned one leaked key into a multi-vendor credential-chaining incident (C5). Keep the rotate-without-redeploy benefit; lose the blast radius.
5. **Move to asymmetric JWT signing (RS256/ES256) or per-audience keys via KMS.** One shared HS256 secret currently signs rider, driver, *and* admin tokens; a `JWT_SECRET` compromise forges every token class at once. Asymmetric keys let verifiers validate without being able to mint.
6. **Introduce distributed tracing** (OpenTelemetry → Jaeger/Tempo, or Sentry spans propagated into Supabase/Redis as children). Sentry APM at 10% is per-request-only today; cross-boundary P95 debugging is log-grep work. *Deferral is currently tracked and reasonable at this scale — flagging as the next observability step, not a launch blocker.*
7. **Ship Prometheus scrape config + SLO burn-rate alerts as code** (closes T1). The SLA table already exists; make it fire.
8. **Adopt TanStack Query in the admin dashboard.** Mobile already runs a well-tuned TanStack setup (`shared/api/queryClient.ts`); admin has *no* query library and hand-rolls fetch/loading/error/pagination in every 2,000-line page.

**Industry-parity snapshot vs. Uber/Lyft:** dispatch is now batched-broadcast (close to their model) but still bounding-box + haversine rather than H3/S2 cell indexing and has no forward supply-positioning; realtime is hybrid WS+poll with client refetch-on-reconnect (their RAMEN buffers server-side); no native turn-by-turn nav; payments lack a write-time ledger and a fraud/velocity risk layer; corporate lacks org-level program budgets and expense-system (Concur/QuickBooks) export. **None of these are launch blockers at provincial scale — they are the Series-A/B hardening roadmap.**

---

## 🛠️ Maintainability & Code Smells

- **M1 — [P1] Dead parallel API client that would ship unauthenticated requests.** `shared/api/cachedClient.ts` duplicates `client.ts` with none of its hardening (no timeout, no 401-refresh, no CSRF/App-Check headers) and reads the `auth_token` SecureStore key that `authStore.setTokens` explicitly deletes — so any call through it goes out **unauthenticated**. Zero importers today; a landmine for the next dev who imports "the cached client." Delete it.
- **M2 — [P1] Offline queue with a drain but no producer.** `rider-app/store/rideStore.ts:587-653` replays an `offline_queue` (typed `create_ride | cancel_ride | rate_ride | tip | emergency`) on reconnect, but **nothing enqueues** — the only writer is the drain rewriting itself. Implied resilience that doesn't exist; the `emergency` type especially suggests SOS queuing that isn't real. Add producers or delete the drain + type union so nobody trusts it.
- **M3 — [P1] Documented float-blocking gate doesn't block.** `CLAUDE.md`/`domain-payments.md` promise a pre-commit/CI gate against float money arithmetic; in reality the local hook only prints a yellow WARNING, the Husky hook has no float check, and CI's semgrep rule is explicitly non-blocking. **Nothing actually stops a float regression in fare code except manual review.** Make one hook authoritative and blocking, or stop documenting the guarantee.
- **M4 — [P2] Doc-vs-code drift is systemic** (this is the theme behind C1/C6 too): the `CLAUDE.md` ride state-machine diagram no longer matches the live batch-dispatch path (`driver_assigned` is admin-assign-only now); "16 background loops" is ~30; `_require_ride_in_state()` — named as the mandatory transition guard — is **dead code** (every site reimplements the atomic filter inline, correctly, but a grep for the symbol returns a false negative). Reconcile docs to code and delete the dead single-offer `_offer_timeout_handler` (`matching.py:933-1105`).
- **M5 — [P2] `any` debt concentrated in screens.** admin-dashboard 522, rider 264, driver 192, shared 17 — the tracked sweeps only covered `shared/` + stores. Strict mode is on, so these are explicit `as any`/`useState<any>` escapes in exactly the surfaces users touch. Extend the sweep; ban new `any` via eslint.
- **M6 — [P2] Drifted duplicate code across the two apps.** `rider-app/utils/apiClient.ts` and `driver-app/utils/apiClient.ts` are near-copies that have *already diverged* (different logout semantics), both with zero importers; `BrandSplash`/`CancelReasonSheet` exist and differ in both apps. Drifted twins are how a fixed bug resurfaces on the other app. Delete dead ones; hoist genuinely-shared components to `shared/`.
- **M7 — [P3] Repo hygiene.** Committed build/debug artifacts (`rider-app/tsc-errors.txt`, `driver-app/final_audit_v4.txt`, `test-results/`), a deprecated-but-present `frontend/` prototype, root strays (`test_admin_endpoints.py`, `validation_output.txt`), and heavy backend deps that inflate the image (`pandas`, `numpy`, `pyiceberg`, 3 LLM SDKs) — audit whether all are runtime-required.

---

## 🧪 Testing & QA (missing edge cases)

**Backend testing is a real asset:** 467 test files, coverage ratcheted 6%→60% floor with 80–90% floors on money/dispatch paths, mocked Supabase fixtures, an e2e ride-lifecycle suite, and a Locust harness that simulates full rider+driver WS lifecycles. Recent coverage work even *surfaced and fixed* a real production bug (the safety-checkin `notify_safety_team` fallback-import).

**The gaps that map to this review's findings — i.e. the tests that would have caught the P0s:**

- **Q1** — No test asserts the **WS admin channel rejects a rider token with `role="admin"`** (C1). Add to the auth suite.
- **Q2** — No test that the **Nth+1 auto-approved allowance request in a period falls through to `pending`** (C2). The cap has never been exercised.
- **Q3** — No test that a **`pending`-settlement ride is picked up by a retry sweep and blocks re-booking** (C3).
- **Q4** — No **invariant test forcing every `ride.status` writer to also call `record_period_transition`** (C6 + the dispatch-audit's dead-`_require_ride_in_state` finding). This whole class of insurance-period gaps is "a transition handler that forgot the period write" — a structural test closes all of them at once.
- **Q5** — **[P2] Safety check-in has a live bug a test would catch:** `utils/safety_checkin_loop.py:85 vs 94` projects `id,rider_id,started_at,updated_at` but reads `ride.get("ride_started_at")` → always `None` → the 20-minute timer silently resets on *any* ride-row update. Add a test pinning the timestamp source.
- **Q6** — **Frontend unit coverage is unmeasured** (explicitly scoped out of the backend coverage item and never itemized). No jest `--coverage` floor on stores/api; native-only crashes escape CI because mobile e2e runs Playwright against Expo *web* builds, not on-device Detox/Maestro.
- **Q7** — Instant-payout tie-rounding (`payouts.py:80-87`, HALF_EVEN vs house HALF_UP) and the receipt tax-fallback bundling GST/PST into one line (`receipt_pdf.py:93-116`) both need branch tests — pennies-times-millions and a tax-line-item compliance case respectively.

---

## 📈 Manager's Verdict

**Overall code health: B+ / "strong core, leaky seams." Not a rewrite candidate; a disciplined-hardening candidate.**

I would be comfortable letting this team keep shipping, because the *foundations that are hardest to retrofit are already right*: Decimal money, atomic ride acceptance, append-only insurance audit, careful PII scrubbing, real idempotency keys, fail-fast config, and a genuinely production-grade shared API client and DB circuit breaker. You cannot fake that; it reflects engineers who understand the domain's failure modes. Remediation velocity (a 301-finding audit largely closed in two days) is the other strong signal.

**What keeps this from an A is a single recurring failure mode, not scattered incompetence:** *the architecture is evolving faster than its invariants and documentation.* Every one of the most serious findings — the WS role-trust escalation, the insurance Period-2 shift, the dead state-machine guard, the decorative allowance cap, the PIPEDA deletion-model divergence, the non-blocking float gate — is a case where **code changed and the contract meant to protect it didn't follow.** The fix is not more code; it's a thin **enforcement layer**: executable invariant tests (every status writer touches the period table; the admin gate is `_admin_verified` everywhere; the cap counter increments), one authoritative blocking float-gate, and a doc-reconciliation pass so `CLAUDE.md` stops describing a system that no longer exists.

**Money and safety exposure is real but contained.** C2 (wallet drain) and C3 (uncollected fares) are direct financial-loss paths; C1 is a privilege escalation; C6 and the Period-2 shift are SGI/regulatory-audit-integrity issues; the migration-216 deletion change is a PIPEDA posture change that needs legal confirmation. **None require re-architecture to fix** — they are, collectively, a focused 2–3 sprint effort. The deeper items (asyncpg, event-driven settlement, double-entry ledger, secrets manager, SLO alerting) are the right *post-launch* roadmap and are mostly already acknowledged internally.

**Scalability:** the single-process + thread-pool-over-HTTP DB model is the ceiling that will show up first under growth; it's addressable incrementally (asyncpg on hot paths) without a big-bang rewrite. Maintainability is good in `shared/` and the backend service layer, weaker in the admin dashboard (god components, no query library) and in the drifted per-app duplicate code.

**Bottom line:** this is a well-built product with a small number of sharp, fixable edges and one systemic discipline gap. Close C1–C3 and the insurance/PIPEDA items before scaling live traffic, stand up the invariant-test + blocking-gate layer so they stay closed, and this is a launch-ready platform that compares favorably to any regional rideshare entrant.

---

## Prioritized action plan

Each item gets its own branch, tests, and a Change Impact & Risk Log entry per `CLAUDE.md`. Grouped by urgency, sequenced for lowest blast radius first.

### Sprint 1 — Close the money/security/safety P0s (do before scaling live traffic)
| # | Item | Files | Effort | Test that locks it |
|---|---|---|---|---|
| 1 | **C1** WS admin gate → `_admin_verified` | `routes/websocket.py:610` | S | Q1: rider-token-with-role=admin rejected on WS |
| 2 | **C2** Increment `auto_approved_this_period` + dedup auto-grants | `routes/corporate_rider.py`, allowance RPC | M | Q2: N+1 auto-request → `pending` |
| 3 | **C3** `pending` into retry sweep + widen re-booking block | `payment_service.py`, `payment_retry.py`, `booking.py` | M | Q3: pending ride retried + blocks re-book |
| 4 | **C6** + period-write invariant | `admin/rides.py`, new invariant test | M | Q4: every status writer touches period table |
| 5 | **C4** Twilio webhook fail-closed | `routes/webhooks.py:1868` | S | signature-missing → reject |
| 6 | **C5** Rotate chained `app_settings` vendor secrets | ops, not code | S | incident doc closed |
| 7 | **Q5** Safety check-in timestamp bug | `utils/safety_checkin_loop.py:85` | S | timer source pinned |

### Sprint 2 — Regulatory + telemetry integrity
| # | Item | Effort |
|---|---|---|
| 8 | **Insurance Period-2 boundary**: compliance decision on the offered-but-unaccepted window, then wire period-open at claim/offer time (or document the accepted deviation) | M + legal |
| 9 | **Migration 216 / PIPEDA**: obtain + cite legal review of the attributable-retention model, or restore the 30-day scrub; reconcile `CLAUDE.md`/`regulatory-sk.md` either way | S code + legal |
| 10 | **T1** Prometheus scrape + SLO burn-rate alerts as code; verify `METRICS_AUTH_TOKEN` set in prod | M |
| 11 | **T2** Audit-write durability (counter + Sentry on failure minimum; outbox ideally) | M |
| 12 | **M3** One authoritative *blocking* float-gate; stop documenting the non-firing one | S |
| 13 | **go_online** re-check license class / vehicle age / abstract (annual re-attestation), not just doc expiry | M |
| 14 | **Consent versioning** on ToS/Privacy at signup (extend the marketing-consent pattern) | S |

### Sprint 3 — Perf & maintainability
| # | Item | Effort |
|---|---|---|
| 15 | Batch the referrals + admin-quest N+1s (P1, P2); background webhook FCM (P4); add missing pagination caps (P6) | M |
| 16 | Delete dead/landmine code: `cachedClient.ts` (M1), offline-queue drain or add producers (M2), per-app duplicate API clients (M6), dead `_offer_timeout_handler` | S |
| 17 | Doc reconciliation pass (M4): state machine, loop count, `_require_ride_in_state` | S |
| 18 | Admin dashboard: TanStack Query + extract god-component sub-flows; `dynamic()` chart-heavy panels + bundle-size CI gate | L |
| 19 | Extend `any`-sweep to screens + eslint ban (M5); frontend jest coverage floor (Q6) | M |

### Roadmap — Post-launch architecture (mostly already acknowledged internally)
`asyncpg` on hot paths · event-driven settlement pipeline · double-entry ledger · secrets manager · asymmetric JWT signing · OpenTelemetry tracing · H3/S2 dispatch indexing · corporate program-budgets + expense-export · on-device mobile e2e (Detox/Maestro).

---

*Prepared as a read-only Engineering Director review. Findings verified against source at the cited `file:line`; C1, C2, and the migration-216 divergence were re-verified by hand. No production code was modified in producing this report.*
