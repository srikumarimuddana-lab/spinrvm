# Full Platform Audit — Security, Performance, Flow & Production Readiness

**Date:** 2026-07-01 · **Branch:** `claude/project-security-performance-flow-vp5kqu` · **Scope:** backend, admin-dashboard, rider-app, driver-app, shared, migrations 1–201, CI workflows, docs/runbooks

Five parallel audit streams: security posture, money/payment arithmetic, performance vs SLAs, flow/state-machine correctness (with deep-dives on insurance periods, scheduled dispatch/background loops, and payments/SOS), and production-readiness gap verification against `ACTION_ITEMS.md`.

---

## 1. Executive summary

The platform's core correctness machinery is in very good shape: the JWT trust model, OTP hardening, refresh-token rotation with theft detection, Stripe webhook idempotency, atomic double-charge guards, the acceptance race guard, RLS coverage on all new tables (migrations 145–201), Decimal fare arithmetic, the surge cap, PII-free logging, and the B3 GPS hot-path optimizations were all **verified correct in code**.

However, the audit surfaced **1 P0, ~12 P1s, and a set of stale-documentation traps**:

- **P0 — driver-app SOS silently swallows failures** (the exact bug fixed in the rider app during the sprint still exists on the driver side).
- **A cluster of P1 flow races** around ride cancellation/offer timeout that can cancel accepted rides, strand drivers, or double-dispatch a driver who is already on a trip.
- **A P1 authorization hole**: any driver can cancel any pre-trip ride (no ownership check).
- **Four P1 insurance-period gaps** (regulatory audit-trail liability under SGI/Saskatchewan Transportation Act).
- **Two P1 money-flow bugs**: card riders are never actually charged cancellation/no-show fees (while drivers are paid and riders are told they were charged), and pre-auth holds are never released on cancellation.
- **P1 PIPEDA**: the admin disputes endpoint re-joins full legal names that migration 142 deliberately scrubbed.
- **Two HIGH performance risks on the settlement SLA** (inline Google snap-to-roads on `/complete`; inline receipt email with synchronous PDF render blocking the event loop).

Verdict on "is everything in place": **most of it, yes — but not launch-clean.** The launch-gating items A1 (coverage floors) and A3 (breach register) are genuinely still open, the migration prefix-uniqueness CI gate has a merge race that has already produced 19 duplicate prefixes, and CLAUDE.md/ACTION_ITEMS.md contain stale facts that will misdirect future work.

---

## 2. P0 — fix immediately

### P0-1 · Driver-app SOS shows false "Emergency Alert Sent" on failure
`driver-app/app/driver/(tabs)/index.tsx:889` — the `onTrigger` callback wraps the emergency POST in `try/catch` and only `console.error`s the failure, so `SOSButton`'s success path always runs. A driver in a dead zone or during a backend outage is told help is coming; the retry/FAILED-state/Call-911 machinery (the component's documented contract, `shared/components/SOSButton.tsx:19-23`) never engages. The rider app does this correctly (`rider-app/store/rideStore.ts:837-859` rethrows). **Fix: remove the try/catch — one line.** This is the same class as sprint ticket R-P0-1, missed on the driver surface.

---

## 3. Security analysis

### Findings

| Sev | Location | Finding |
|---|---|---|
| P1 (PIPEDA) | `backend/routes/disputes.py:182-184` | `admin_get_disputes()` live-joins `users.first_name/last_name` into `user_name`, reconstructing the full legal name that migration 142 (`142_fix_rls_financial_tables.sql:157`) deliberately scrubbed from the persisted column. Return `user_id` / display alias only. (ACTION_ITEMS B2 — the *only* remaining sub-item; rounding + RLS halves are already fixed.) |
| P2 | `backend/migrations/28_corporate_wallet_rpc.sql` | `corporate_wallet_apply_delta` (money-moving RPC) lacks `SECURITY DEFINER` + pinned `search_path`, unlike every money function since (141, 196). Add an `ALTER FUNCTION` migration. |
| P2 | `backend/socket_manager.py:27-43` | WS 30 msg/s cap is in-process per replica (known B4). Multi-replica deployment gives N×30 msg/s effective. Promote to Redis `INCR`+`EXPIRE`. |
| P3 (doc drift) | `backend/core/config.py:55` | Admin access TTL is 1 h (good); CLAUDE.md still says 12 h. Reconcile docs. |

### Verified correctly in place
- **JWT trust model** — role never trusted from rider/driver JWTs; always re-read from `users` (`backend/dependencies/__init__.py:490-491`); admin claims trusted only after aud + JTI revocation + staff-active + token_version + idle-timeout checks. Mirrored in the WS handshake (`routes/websocket.py:436-457`).
- **OTP** — SHA-256 at rest, timing-safe compare, 5 failures/hr → 24 h Redis lockout, "1234" bypass gated on `ENV != production`, hard 503 (no silent fallback) if Twilio unconfigured in prod.
- **Refresh tokens** — hashed, rotated, reuse detection with token-version bump + revoke-all + WS kick + Sentry `spinr_alert=refresh_token_reuse`.
- **RLS migrations 145–201** — every new user-data table ships RLS in the same migration; no `FOR ALL` on user-writable tables. Duplicate `200_` prefix files touch different tables; runner keys on full filename — no collision.
- **Stripe webhook** — signature verified (platform + Connect secrets), explicit event allowlist, `claim_stripe_event` result checked before processing.
- **WebSocket** — auth-first-message with 30 s deadline, 64 KB cap, driver location writes bound to the authenticated driver (no spoofing another driver's location).
- **Admin routes** — all sub-routers mounted under `dependencies=[Depends(get_admin_user)]` + `require_module(...)` RBAC (`routes/admin/__init__.py:86-127`).
- **Secrets** — no live keys in source; `_guard_production_secrets` fails fast on weak JWT_SECRET/ADMIN_PASSWORD, missing Firebase audiences, missing Supabase creds, non-Canadian `SUPABASE_REGION`.
- **PII in logs** — no raw lat/lng, full phone, name, or email found in any `logger.*` call; `phone[-4:]` and hashed-email patterns used; B1 (analytics geohash-only) confirmed fixed with contract test.
- **Sentry** — `send_default_pii=False` + scrubbers; loguru→Sentry bridge present (`server.py:427-461`).

---

## 4. Money & payments analysis

### Blockers

| Sev | Location | Finding |
|---|---|---|
| P1 | `backend/features.py:928` | Live `/rides/fare-estimate` endpoint computes `grand_total = round(float + float + float, 2)` — float summation + banker's rounding in fare code; can disagree by a cent with every Decimal/`ROUND_HALF_UP` path. Fix with `_f(_round(...))` per `routes/rides.py:1936`. |
| P1 (tax doc) | `backend/utils/email_receipt.py::_build_fare_rows`, `backend/utils/receipt_pdf.py::_fare_lines` | The official CRA/SK-retained receipt never renders `discount_amount`/`promo_code`, but "Total" uses the discounted `grand_total` — itemized rows don't sum to the total on the tax document. $20 ride with $5 promo → rows sum to $20, total shows $15, no explanation. |
| P2 | `services/fare_service.py::build_fare_breakdown_lines` (~243), `routes/rides.py::_build_fare_breakdown` (~504) | All in-app surfaces bundle Base+Distance+Time into one "Ride fare (X km)" line, contradicting the required separate line items. (Emailed/PDF receipt splits them correctly — the retained document is compliant; in-app is not.) |

### Related flow-level money bugs (from the payments deep-dive)

| Sev | Location | Finding |
|---|---|---|
| P1 | `rides.py:4639-4670`, `drivers.py:5560-5594`, `services/cancellation_service.py:105-188` | **Cancellation/no-show fees are only ever charged to wallet riders.** No Stripe path for fees exists; card riders (the default) pay nothing, the driver is still credited from Spinr's pocket, and the push text falsely says "a fee has been charged" (`drivers.py:5643`). Revenue leak + disclosure violation. |
| P1 | pre-auth lifecycle | **Booking pre-auth holds are never released on cancellation** — `cancel_authorization`'s only production caller is the change-card path (`payment_service.py:631`); no cancel path or sweeper voids `authorized/fare_only` holds on cancelled rides. Rider's card stays frozen up to ~7 days. Migration 156 documents a `'released'` state no code produces. (Also the natural vehicle for fixing the fee bug above.) |
| P2 | `webhooks.py:591-599` | Out-of-order `payment_intent.payment_failed` can flip a `paid` ride back to `failed` and overwrite `payment_intent_id`; the retry loop can then re-confirm the stale PI → possible double charge. Add a `payment_status NOT IN ('paid',...)` guard. |
| P2 | `rides.py:4642-4655`, `drivers.py:5566-5579`, `cancellation_service.py:122-135` | Wallet fee debits/credits are non-atomic read-modify-write (no RPC/row lock), unlike `wallet_pay_for_ride`. Concurrent wallet writes can be lost. |
| P2 | `payment_retry.py` claim vs pickup sets | Crash between claiming `payment_status='retrying'` and writing the outcome leaves the ride in `'retrying'` forever — no query picks it up, and the rider is permanently blocked from booking by the unpaid-ride check with no self-heal. |
| P2 | `preauth_capture.py:118-124` | Completed rides stuck at `payment_status='pending'` with no open hold are never auto-settled; only backstop is blocking the rider's next booking — on a 0%-commission platform the driver eats the fare. |
| P2 (tax) | `services/fare_service.py:329-331` | Completion-time fare recalculation reuses booking-time `tax_amount` while `total_fare`/`grand_total` change with actual distance — the GST line stops equaling 5% of the fare. Mitigated only when `fare_lock_enabled`. |
| P2 (process) | `.claude/hooks/pre-commit` step 5, CI | The documented "pre-commit hook blocks float arithmetic in fare code" is actually **warning-only** (never sets `BLOCKED=1`) and no CI job enforces it. A float regression in fare code is not blocked anywhere automated. |
| P3 | `routes/disputes.py::admin_resolve_dispute` | Refund cap uses `original_fare` (pre-tax subtotal) instead of `grand_total` — can wrongly block a legitimate full refund; cannot over-refund (Stripe rejects). |

### Verified correctly in place
Full Decimal/`ROUND_HALF_UP` in `calculate_fare`; surge applied to distance+time only with min-fare after surge; `SURGE_CAP=2.5` enforced in engine **and** at the fare-read boundary; admin override bounded 1.0–10.0 with justification + audit log above 2.5; no surge on corporate-paid rides (enforced at estimate and booking, closing the work-mode-toggle loophole); no retroactive surge on scheduled rides; deterministic Stripe idempotency keys everywhere incl. retries; `dollars_to_cents()` used consistently (no `int(x*100)` truncation); atomic double-charge guards (`pending→processing` CAS + `wallet_pay_for_ride` RPC); refunds only via Stripe API; GST/PST as separately quantized line items; 0%-commission invariant holds (no `platform_share` anywhere; driver gets base+distance+time in full).

---

## 5. Performance analysis (vs P95 SLAs)

### High-risk

| Sev | SLA threatened | Location | Finding |
|---|---|---|---|
| HIGH | Settlement < 1 s | `backend/routes/drivers.py:4767-4784` | `compute_road_route(all_breadcrumbs)` (Google Roads snap) awaited inline on `/complete` for the billable trip leg — one external round-trip over potentially hundreds of points on the sub-second path. Tighten timeout or precompute incrementally during the trip. |
| HIGH | Settlement < 1 s + **event-loop-wide stall** | `rides.py:4162` → `utils/email_receipt.py:338`, `utils/email_provider.py:290` | Receipt email awaited inline in `/process-payment`: 3 sequential DB reads, **synchronous fpdf PDF render on the event loop** (no `to_thread`), and an httpx fallback with `timeout=10.0`. Worst case ~10 s added to settlement and the PDF render stalls every coroutine incl. WS fan-out. Make it `asyncio.create_task` + `to_thread`. |
| HIGH | Admin usability | `routes/admin/faqs.py:140-152` | Broadcast loops up to 10,000 users with sequential awaited pushes inside the request handler — will time out. Mirror `messaging.py:227`'s gather+Semaphore(50) background pattern. |
| HIGH | Admin DB load | `admin/drivers.py:361,471`; `admin/subscriptions.py:168,219` | Stats endpoints do 5,000–20,000-row full scans aggregated in Python per page load. |

### Medium

- **Dispatch < 2 s**: N+1 `quest_progress` query per driver inside the notify loop, before the offer sends (`rides.py:1177-1188`) — batch or move after send. Inline Distance Matrix with 3 s timeout (`rides.py:1040-1050`, `maps_eta.py:61`) can eat 150% of the SLA — tighten to ~800 ms (haversine fallback exists). Same `service_areas` row fetched up to 4× per dispatch attempt.
- **WS receive loop**: batch GPS rate limiter does Redis `INCR` per point — up to 499 sequential round-trips per 500-point batch, blocking that driver's pings (`websocket.py:820-821`). Add `INCRBY`. Batch path also bypasses the B3.1 active-ride cache (`websocket.py:884-898`).
- **Surge engine** (`utils/surge_engine.py`): no leader lock → runs on every replica; inserts a `surge_pricing` history row per area per tick **even when unchanged** (lines 288-302) → replica-multiplied table bloat (~30 rows/hr/area/replica). The PostGIS supply count exists (migration 170) but is flag-gated OFF (`SURGE_SPATIAL_COUNT`); the Python path fetches up to 5,000 drivers per area per tick. Flip the flag after staging rehearsal, add a leader lock, gate the insert on change.
- **Missing index**: `users.referral_code` is filtered at `routes/users.py:634` but migration 172 indexed only `referred_by`/`referral_code_used` — full seq-scan per referral-apply. Same shape on `drivers.referral_code` legacy fallback (`drivers.py:5882`).
- **Admin misc**: `admin/messaging.py:57` (50k-row scan), `admin/rides.py:2729` (200k payout window), `admin/rides.py:2096` (10k rides per detail), `admin/drivers.py:1798-1805` (N+1 leaderboard); cancellation-breakdown RPC exists but is uncached (D7 half-done).
- **Scaling ceiling** (note, not a bug): every GPS ping is one synchronous Supabase REST write through the 64-thread `_DB_EXECUTOR`; ~600+ concurrent online drivers saturates the pool and queues *all* DB calls. Fine today, known ceiling.

### Verified good
Dispatch candidate query matches the `idx_drivers_dispatch_ready` partial index; cooldowns via `redis_mget`; enrichment parallelized; FCM push off the request path. Estimate path has grid-cell fare cache + overlapped polyline fetch. All three B3 GPS optimizations present and correct (5 s active-ride cache w/ cached empties, ETA movement gate >100 m/120 s, breadcrumb batching with disconnect+completion flushes). `run_sync` DB layer is solid (dedicated pool, policy-gated retries with jitter, retry budget, circuit breaker). Background loops mostly follow the recipe (leader locks, jitter, bounded reads, `_restartable` + watchdog). Migrations 145+ otherwise ship indexes matching their query predicates.

---

## 6. Flow analysis (state machine, dispatch, insurance, SOS)

### P1 — state-machine races and authorization

| # | Location | Finding |
|---|---|---|
| F1 | `backend/routes/drivers.py:5341-5428` | **Driver cancel endpoint has no ownership check** — `ride.driver_id != driver.id` is never verified (contrast `noshow` at :5493). Any authenticated driver can cancel any pre-trip ride; the actually-assigned driver is never released or notified. |
| F2 | `rides.py:2066-2091` + `repositories/ride_repo.py:73-81` | **5-min auto-cancel is read-then-write with an id-only filter** — can overwrite a just-accepted ride to `cancelled`; driver drives to a cancelled pickup, is never notified, stays `is_available=False`. Also races the sweeper → duplicate cancel events. |
| F3 | `rides.py:1484-1595` | **Batch offer-timeout vs accept race** (no grace period, unlike the legacy handler's 15 s): a boundary accept can have the winner's offer expired and the accepting driver released `is_available=True` **while on the ride** → double-dispatch; also wrongly increments miss streak/acceptance-rate. Trailing `match_driver_to_ride` re-dispatches an already-accepted ride (no `status=='searching'` guard at `rides.py:638-651`), creating orphan offers. |
| F4 | `rides.py:2054-2126`, `stuck_ride_sweeper.py:41-122`, `driver_claim_reaper.py:106` | **Auto-cancel paths strand batch-claimed drivers permanently**: neither the 5-min timeout nor the sweeper expires `ride_offers` or releases batch-claimed drivers, and the claim reaper explicitly skips drivers with a pending offer — no loop ever recovers them. Pod restart during the 15 s offer window has the same effect (no durable offer-expiry backstop). Silent supply erosion; rider-cancel (`rides.py:4770-4809`) already does this correctly — system-cancel paths never got the same block. |
| F5 | `rides.py:1404-1415` | Legacy offer-timeout revert to `searching` filters on id only — clobbers an accept landing in the window. Live only via admin manual assignment, mitigated by the 15 s grace (borderline P1/P2). |
| F6 | `drivers.py:6525,6553` | Go-online writes `is_available = is_online` with no active-ride/pending-offer check — re-asserting "Go Online" mid-trip flips an on-trip driver back to available. |
| F7 | `routes/admin/rides.py:404-425` | Admin force-cancel allows cancelling `in_progress` rides (violates the never-cancel-after-trip-start invariant), is non-atomic, and frees the driver (:456) without an insurance-period transition. |
| F8 | `routes/websocket.py:946-967` | `ride_status_update` WS message relays arbitrary status strings for any ride_id to rider + admins with no ownership or state validation — spoofable client-visible transitions. (P2) |

### P1 — insurance-period regulatory gaps (SGI audit trail)

All the same shape: paths that force a driver offline or release them **without** `record_period_transition`, leaving Period 1/2/3 rows open while the driver is actually offline (or vice versa):

1. **Document-expiry loop** (`utils/document_expiry.py:146-158`) — automated and recurring; suspends drivers with no Period 0. Highest priority.
2. **Admin suspend/ban/status-override** (`admin/drivers.py:1212-1224, 1360-1362`) — also no active-ride guard, so a mid-trip suspension leaves Period 2/3 open across the suspension.
3. **Admin cancel** (`admin/rides.py:454-461`) — frees the driver, no transition (force-complete at :577 does it correctly).
4. **Doc re-upload force-offline** (`documents.py:218-230`) — sibling path at `drivers.py:714-718` records Period 0, proving this is an omission.
5. (P2) Batch offer-timeout and preempted-loser paths record Period 1 unconditionally for drivers who went offline during the offer window (`rides.py:1566-1567`, `drivers.py:4196-4198`) — the single-offer handler has the guard (`rides.py:1393-1400`); the live batch path doesn't.
6. (P2, spec question) Period 2 starts at **accept**, not assignment (`drivers.py:4132-4139` comment is load-bearing), contradicting CLAUDE.md's rationale ("Period 2 starts on driver_assigned"). Under the batch model the ride stays `searching` during the offer window, so during a pending offer (~15 s, driver claimed) the audit says Period 1. Needs a business/legal ruling, then align CLAUDE.md or the handler.

The insurance-period *mechanism itself* is excellent: append-only enforced at app **and** DB layer (trigger + CHECK `period 3 requires ride_id` + SELECT-only RLS, migration 64), race-safe one-open-row unique index, and all the mainline ride transitions (accept, arrive, OTP-start, complete, rider/driver cancel, no-show, go on/offline, subscription expiry, stale-intent reconciler) record correctly.

### P2 — scheduled rides & loops

- **Dead conflict branch**: scheduled dispatch's one-active-ride graceful-defer never runs in production because it string-matches tokens that `DuplicateRecordError`'s message doesn't contain (`scheduled_rides.py:107-108` vs `error_handling.py:549-564`); the test masks it with a raw `RuntimeError`. Degradation + ERROR-log noise every tick during the rider's live trip, not double-booking (DB unique index `rides_one_active_per_rider` holds).
- **Dangling scheduled-ride pre-auth**: dispatch-time card hold is never voided when the ride auto-cancels (same class as the P1 pre-auth finding).
- Leader-lock TTL 90 s vs 60 s loop with no release → effective dispatch cadence degrades to ~120 s (scheduled rides can go live ~2 min late). Reminder push is not an atomic claim (duplicate pushes possible). Watchdog omits 7 loops incl. the claim reaper and safety check-in — a silently-stalled safety loop never alerts (`core/lifespan.py:403-422`).

### SOS (beyond the P0)
Server side verified strong: expired-token grace is signature-verified with bounds + revocation checks; no auto-dial 911 anywhere; SOS exempt from the 401 sign-out interceptor; partial-failure honesty (`notification_warning`). P3s: endpoint not idempotent (retries can 9× duplicate SMS blasts — safe direction, but add a 60 s dedup window); the rider offline-queue `'emergency'` type is dead code (failed SOS is not queued for reconnect); SOS shares the 20/min `ride_action_limit` bucket with start/complete.

### Verified correct
Acceptance race (atomic claim, 409 + `ride_taken` to losers, losers' offers `preempted` + released); one-active-ride at booking + DB partial unique index; atomic guards on arrive/OTP-start/complete/no-show/rider-cancel; completion decoupled from payment with single-writer `payment_status`; `set_driver_available` clamps `is_available ⇒ is_online` everywhere; document expiry + status gates on every go-online; scheduled `scheduled→searching` promotion atomic and replay-safe with surge zeroed at booking; sweeper multi-row atomic claim (no cross-replica double-cancel); payment retry loop replay-safe with per-attempt idempotency keys.

---

## 7. Production readiness — "is everything in place?"

### Launch-gating items (ACTION_ITEMS P0) — verified

| Item | Status | Evidence |
|---|---|---|
| A1 per-module coverage floors | **OPEN** | `backend/pytest.ini:15` global 60% only; `ci-guardrails.yml` coverage gate is advisory (`continue-on-error: true`); no per-path 90/80 enforcement anywhere |
| A2 post-deploy smoke | **MOSTLY DONE — stale item** | `ci.yml:611-673` has a real smoke job meeting the acceptance criteria (health+DB, auth 401-not-500, fare path). Gap: not sequenced after the actual `deploy-fly.yml`/`deploy-backend.yml` workflows (separate workflows race on push to main); those only poll `/health` |
| A3 PIPEDA breach register | **MISSING** | `docs/audit/breach-record.md` does not exist |

### Notable infrastructure findings

- **Migration prefix-uniqueness gate has a merge race.** The check (`ci-guardrails.yml:479-537`) only compares a PR's new files against migrations in *that PR's checkout*. `200_data_export_objects.sql` and `200_zoho_desk_ticket_service_area.sql` were merged 3 minutes apart from divergent branches; neither saw the other. Duplicates on disk now: **80, 81, 82, 83, 84, 100, 102, 106, 110, 114, 117, 136, 137, 140, 141, 156, 157, 185, 200** — far beyond CLAUDE.md's documented list. Survivable (runner keys on full filename) but the gate needs a require-branch-up-to-date rule, merge queue, or a push-to-main re-check.
- **CLAUDE.md staleness**: "next free slot is 145" — actual next free slot is **202**; duplicate-prefix list stale; admin TTL says 12 h, code says 1 h; payment-source cascade description doesn't match implementation (single rider-selected mode with allowance→master fallback only — no wallet→card cascade); allowance-reset replay mechanism description stale; pre-commit float guard described as blocking but is warning-only.
- **ACTION_ITEMS.md staleness**: B2 marked fully open (2 of 3 sub-items fixed); E11 a11y-in-CI marked open but **shipped** (axe in Playwright, blocking on main); E10 license scan half-done (pip-licenses gate exists; JS `license-checker` missing); A2 mostly done as above.

### E-items (industry parity) — verified status

| Item | Status |
|---|---|
| E1 staging environment | missing |
| E2 load-test harness | built (`loadtest/locustfile.py`); execution blocked on E1 |
| E3 forced-upgrade gate (min version / 426) | missing |
| E4 synthetic monitoring | missing |
| E5 kill switches | partial — surge has a real DB kill switch (`surge_enabled`); none for scheduled dispatch, promo, corporate billing |
| E6 DAST/pentest | not in repo (budget item) |
| E7 PITR + failover runbooks | docs exist; drills never exercised |
| E8 CODEOWNERS | missing |
| E9 postmortem template | missing (`docs/templates/` doesn't exist) |
| E10 license scan | partial (Python yes, JS no) |
| E11 a11y in CI | **done** (stale item) |
| E12 on-call policy doc | missing |

### Confirmed in place
19 CI workflows (tests w/ hash-pinned deps, ruff, Playwright+axe, Trivy blocking, cosign, Gitleaks, Semgrep, Bandit, pip-audit/npm-audit, pip-licenses, migration safety gate ×2, subprocessor monitor, dependabot auto-merge). 278 backend test files + mobile/admin suites with configs. 31 runbooks + 8 ADRs. All 7 documented `spinr_*` metrics exist at call sites with pinning tests. Sentry PII-safe init + loguru bridge. Dockerfiles digest-pinned. fly.toml + railway.json present. Branch protection with required checks.

---

## 8. Prioritized fix plan (impact-ordered)

**Immediate (P0):**
1. Driver-app SOS: remove the swallowing try/catch (`driver-app/app/driver/(tabs)/index.tsx:889`). One line + regression test mirroring the rider app's.

**This sprint (P1 cluster — safety/money/authz):**
2. Driver-cancel ownership check (`drivers.py:5341`).
3. Shared **atomic cancel-searching-ride helper** (status-guarded CAS + expire pending `ride_offers` + release those drivers + void uncaptured hold), used by `ride_search_timeout`, the sweeper, and rider-cancel; plus a `status=='searching'` guard at the top of `match_driver_to_ride`. This single change closes flow findings F2, F3 (half), F4, and the dangling-pre-auth P2s.
4. Batch offer-timeout: re-read offer status before releasing each driver (copy the winner-guard), restore a grace period, and gate Period-1 writes on `released.get("is_available")` (guard already exists in the legacy handler at `rides.py:1393-1400`).
5. Cancellation/no-show fees for card riders: capture against the booking hold (or PaymentIntent) instead of wallet-only; fix the false "fee charged" push; make driver compensation conditional on collection policy.
6. Insurance-period transitions on the four forced-offline paths (document-expiry loop first — it's automated), + active-ride guard on admin suspend.
7. Disputes admin endpoint: stop re-joining full legal names (`disputes.py:182-184`).
8. Admin force-cancel: block `in_progress`, make atomic, record insurance transition.
9. Go-online: don't set `is_available=True` when an active ride / pending offer exists.
10. `features.py:928` float `round()` → Decimal; add `discount_amount` line to the official receipt.

**Next (P2 / perf):**
11. Background the receipt email + `to_thread` the PDF render; tighten/offload `compute_road_route` on `/complete`.
12. Webhook `payment_failed` guard against paid rides; `'retrying'` orphan self-heal; wallet fee RMW → RPC.
13. Batch the dispatch quest query; `INCRBY` in the WS batch rate limiter; surge leader lock + change-gated history insert + flip `SURGE_SPATIAL_COUNT` after staging rehearsal; `users.referral_code` index.
14. Fix the scheduled-dispatch duplicate-classification branch (use `db_error_text()`); fix the masking test; widen watchdog loop coverage.
15. A1 coverage ratchet; A3 breach register (template, 30 min); CODEOWNERS; migration-gate merge race; refresh CLAUDE.md/ACTION_ITEMS stale facts.

**Pre-launch (ops):**
16. Staging env (unblocks load-test execution + DAST), synthetic monitoring, kill switches for scheduled-dispatch/promo/corporate, failover + PITR drills, Sentry refresh-token-reuse alert rule, forced-upgrade gate (cheap now, impossible later).
