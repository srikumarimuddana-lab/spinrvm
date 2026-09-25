# Feature Completeness Matrix (R2 + domain lanes)

Report-only. Built from `docs/audit/clean-sheet/traceability.csv` (1,788 rows, 107
story-level L3 features under 18 L2 epics — see note below), the lane reports in
`02-findings/`, `03-benchmark.md`, and `07-reverification.md`'s corrections. One row
per L3 feature, columns per `greenfield-extensions.md` §3. Every cell is filled;
`Unknown — not checked` is used where no lane covered it and a targeted check was
not feasible in this pass, per the task's own allowance for that value.

**Count note:** `01-inventory/epics.md`'s narrative text says "90 L3 features," but
its own §2 table and `traceability.csv` both enumerate **107** story-level rows
(108 `story_id`s minus the synthetic `ORPHAN` bucket). This matrix follows the CSV
(107 rows), not the narrative's "90," and flags the discrepancy here rather than
silently reconciling it — a re-count is itself an open item for W1.

## Method and evidence labels

- **Backend/API/DB presence** — derived from `traceability.csv`'s per-story `mapped`
  unit count (VERIFIED code exists at the cited paths; the *epic/story
  classification* itself is INFERRED per `epics.md` §0) plus direct greps run this
  pass for every story that showed `mapped=0` ("gap" rows) — per `07-reverification.md`
  and `epics.md` §5 (CARTO-004), a `mapped=0` row is usually a keyword-collision
  classifier artifact, not a real absence, and each one checked below was confirmed
  to have real code except **Fraud detection (S-safety-05)**, which is independently
  and repeatedly confirmed absent by `trust-safety-fraud.md` (TSF-002/003/004/005/006)
  and by this pass's own grep (`backend/tests/test_referral_payout_fraud_guards.py`
  is the only fraud-named file in the repo; no `backend/services/fraud*.py` exists).
- **Tests (real)** — `traceability.csv`'s `test` column existing and non-empty for at
  least one row of the story (INFERRED — filename match only, per `epics.md` §0/§9;
  "a test column hit means a plausibly-named test file exists, not that it
  meaningfully exercises the code"). Cited as INFERRED throughout; not upgraded to
  VERIFIED unless a lane report opened the test file.
- **Audit log / authz** — VERIFIED where a lane report (`admin-ops.md`,
  `corporate.md`, `security.md`) opened the code; otherwise INFERRED from the
  general pattern (`log_admin_action()`, `Depends(get_admin_user)`) or
  `Unknown — not checked`.
- **Compliance** — cites `compliance.md`'s `COMP-###` cards where the feature falls
  in a regulated area (driver eligibility, tax, WAV, retention, consent); otherwise
  `Unknown — not checked`.
- **Docs** — `Y` only if one of the `.claude/context/domain-*.md` files, `CLAUDE.md`,
  or a lane report names the feature; `docs/driver-faqs-saskatchewan.md` (headings
  VERIFIED read: eligibility/vehicle, documents/CRC/insurance, safety/coverage,
  going-online/trips/account, earnings/pay/taxes) is the only static support-article
  surface — rider-facing FAQ content lives in the DB-managed `faqs` table
  (`support-kb.md` SKB-005), so rider-side "support article" cells are marked
  `Partial — DB-managed FAQ table exists, topic coverage not queryable this pass`
  rather than `Y`/`N`.
- **Runbook** — checked against the full `docs/runbooks/` listing (69 files, VERIFIED
  `ls`, several spellings tried per feature keyword) — a genuine absence claim, not
  a guess.
- **Owner** — from `epics.md` §7 (28 agent frontmatter files read in full that pass);
  epics/features flagged there as UNOWNED or PARTIALLY UNOWNED are carried through
  unchanged (Maps & Routing, Shared Frontend Foundation logic layer, Admin general
  functional correctness, Promotions functional correctness).
- **Training** — `Unknown — not checked` almost everywhere; no lane audited a
  training-material corpus beyond the two driver-onboarding-email change-log entries
  found by a general `docs/` grep (2026-08-28, 2026-09-21), which are cited only
  where directly on point (driver onboarding/eligibility).

Legend: **Y**=Yes, **N**=No (checked, absent), **P**=Partial, **N/A**=not
applicable to this feature, **U**=Unknown — not checked.

---

## Epic: Ride Booking & Matching (11 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Fare estimation (S-book-01) | Y — ride-options screen | Y — `routes/rides/estimates.py` | Y — `fare_service.py` | Y — reads `service_areas` pricing | N/A | Y — rider JWT | N/A (read-only) | U | P — 3.5s Directions-wait SLA exception is a documented, accepted deviation (CLAUDE.md, decided 2026-08-21), not a gap | Y — `spinr_fare_calc_duration_ms` | Y — same metric; alert wiring U | Y (INFERRED, CSV) | P — DB-managed FAQ table, not queried | Y — `domain-payments.md` | U | N — no fare-estimate-specific runbook found (checked `docs/runbooks/` list, no hit for "fare"/"estimate"/"pricing") | U | spinr-money-auditor / spinr-surge-auditor (Covered) | STRAT-008 (strategy.md): no minimum fare, identical per-km across vehicle classes in current config |
| Ride creation (S-book-02) | Y | Y | Y | Y | Y — dispatch loop triggers on write | Y | N/A | U | U | U | Y — `spinr_dispatch_offer_sent_total` downstream | Y (INFERRED) | P | Y — `domain-dispatch.md` | U | N — no dedicated runbook | U | spinr-dispatch-reviewer (Covered) | OBS-001/OBS-006 finding_ids attached in CSV (metric-naming/observability gaps cited elsewhere in this audit, not re-derived here) |
| Offer matching (S-book-03) | Y — driver offer screen | Y — 30 mapped units | Y — `matching.py`, `dispatch_service.py` | Y | Y — `driver_{user_id}` WS push | Y | N/A (system action) | U | U | Y — `GET /api/admin/analytics/dispatch-latency` | Y — `spinr_dispatch_offer_sent_total`, `spinr_dispatch_offer_accepted_total`, `spinr_dispatch_offer_to_accept_duration_ms` (KPI P95<2s) | Y (INFERRED) | N/A (no rider/driver-facing article; internal mechanism) | Y — `domain-dispatch.md` | U | N — no offer-matching-specific runbook (nearest is `driver-not-receiving-rides.md`, matching-adjacent not matching-internals) | Y — `use_eta_ranking` flag (default True, `routes/admin/service_areas.py:230`, cited DRIVER-004) | spinr-dispatch-reviewer (Covered) | **DISPATCH-001** (VERIFIED, confirmed by 07-reverification): rider gets no WS event when offered — `matching.py:1621` sends only to `driver_{user_id}`. **DISPATCH-002/003**: decline/timeout exclusion and `is_available` release both have a dark-flagged-off-by-default atomic fix |
| Offer acceptance/decline (S-book-04) | Y | Y — `routes/drivers/offer_decisions.py` (`accept_ride`, `decline_ride`) | Y | Y | Y | Y | U | U | U | U | Y — same dispatch metrics as above | Y (INFERRED) | P (driver FAQ "going online, trips" section) | Y — `domain-dispatch.md` | U | N | U | spinr-dispatch-reviewer (Covered) | CSV shows `mapped=0` for this story (classifier artifact, CARTO-004 pattern) — **real code VERIFIED this pass**: `offer_decisions.py:80` `accept_ride`, `:654` `decline_ride` |
| Surge pricing (S-book-05) | Y — surge-acknowledgment modal pre-booking (`ride-options.tsx:666-681`, cited by rider-journey.md as steelman) | Y | Y — `utils/surge_engine.py` | Y | N/A | Y | U | N/A | Y — CLAUDE.md's 2.5× hard cap, corporate-exempt, never-retroactive rules; `test_corporate_surge_bypass.py` exists (QUAL-003 withdrawal, 07-reverification) | U | U | Y (INFERRED) | P | Y — CLAUDE.md Surge section + `domain-payments.md` | U | N | Y — admin manual override (1.0–10.0, audit-logged, clamped to 2.5× at every fare-calc call site per CLAUDE.md) | spinr-surge-auditor (Covered) | Surge is the one Tier-1 pricing rule with the most explicit, cross-checked documentation in CLAUDE.md and a real bypass-guard test |
| Scheduled rides (S-book-06) | Y | Y | Y | Y | U — re-offer/cancel WS path not traced (rider-journey.md, cited) | Y | U | U | U | U | U | Y (INFERRED) | U | Y — CLAUDE.md ride-state-machine note ("scheduled rides skip searching until dispatch time") | U | N | U | spinr-dispatch-reviewer (Covered) | rider-journey.md (e): "mostly complete, narrow display gap" — RIDERJ-005 timezone pin gap |
| Accessibility/WAV dispatch (S-book-07) | Y — greyed toggle when unavailable | Y | Y | Y | U | Y | U | U | N — **COMP-010**: "estimated wait for next WAV + standard alternative" promised in `regulatory-sk.md` is not built | U | U | Y (INFERRED) | P | Y — `regulatory-sk.md` | U | N | U | **UNOWNED per CARTO-003** (no agent names service-area/WAV geometry code specifically; `spinr-regulatory-compliance-checker` is adjacent, not the code owner) | COMP-010 (compliance.md): rider-facing WAV promise vs. code mismatch; CARTO-003 (epics.md §8): no review agent for the service-area geometry that gates this |
| Address/pickup validation (S-book-08) | Y | Y | Y | Y | N/A | Y | U | U | U | U | U | Y — `backend/tests/rls/test_saved_addresses_rls.py` (VERIFIED, CSV row) | P | U | U | N | U | spinr-dispatch-reviewer (adjacent; not explicitly named) | — |
| Multi-stop routing (S-book-09) | Y | Y | Y | Y | Y — re-pricing disclosure per sweep-catalog §3.3 item 22 | Y | U | U | U | U | U | N — CSV `has_test=False` for this story; **not independently re-checked this pass** — treat as `Unknown`, not confirmed absent | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | sweep-catalog.md 3.3#22 (stops added/removed mid-trip re-pricing disclosure) is an open edge case — see edge-case matrix |
| Ride history & detail (S-book-10) | Y | Y | Y | Y | N/A | Y | U | U | N/A | U | U | N — CSV `has_test=False`; not independently re-checked | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | — |
| Fare split (S-book-11) | U — not confirmed to exist as a UX affordance this pass | Y — 1 mapped unit | Y | Y | N/A | Y | U | U | U | U | U | N — CSV `has_test=False`; not independently re-checked | U | N — not found in `domain-payments.md` | U | N | U | spinr-money-auditor (adjacent) | Only 1 mapped code unit for the whole feature — smallest footprint in this epic; genuinely thin, not necessarily a classifier artifact |

---

## Epic: Ride Fulfillment (11 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Driver arrival tracking (S-fulfil-01) | Y | Y | Y — `routes/rides/lifecycle.py:62` sets `driver_arrived_at` | Y | Y — `_shared.py:426` status copy, WS-fanned per state-machine rule | Y | N/A | U | Y — Insurance Period 2 starts at `driver_assigned`, not `driver_accepted` (CLAUDE.md, cited) | U | U | Y (INFERRED) | P | Y — CLAUDE.md ride state machine + `domain-safety.md` | U | N | U | spinr-dispatch-reviewer / spinr-insurance-period-auditor (Covered) | CSV shows `mapped=0` (classifier artifact); **real code VERIFIED this pass** (`lifecycle.py:62`, `queries.py:511-519`) |
| Real-time location (WS) (S-fulfil-02) | Y | Y — 20 mapped units | Y | Y | Y | Y | N/A | U — CLAUDE.md bans raw GPS in logs; not independently re-audited this pass | U | U | Y — `spinr_ws_fanout_duration_ms` (KPI <100ms); driver-location-write has **no** backing `_duration_ms` histogram per CLAUDE.md's own OBS-002 citation | Y (INFERRED) | N/A | Y — CLAUDE.md WebSocket Auth section | U | Y — `websockets.md`, `redis-down.md` | N/A (core mechanism, not flagged) | spinr-dispatch-reviewer (Covered) | **REL-005** (VERIFIED, confirmed): WS Redis outage silently drops cross-replica delivery, `socket_manager.py:371` labels both local-only and pubsub-lost paths identically as `path=local`, no distinguishing metric |
| Pickup confirmation (S-fulfil-03) | Y | Y | Y | Y | Y | Y | U | U | N — **TSF-010**: no trip PIN / rider-verifies-driver mechanism exists (sweep-catalog 3.2#15 edge case) | U | U | N — CSV `has_test=False`; not independently re-checked | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | TSF-010 (trust-safety-fraud.md): wrong-rider-gets-in scenario has no verification mechanism |
| In-ride state machine (S-fulfil-04) | N/A (backend mechanism) | Y | Y — `_require_ride_in_state()` guard pattern (CLAUDE.md mandatory) | Y | Y — CLAUDE.md mandates a WS event per transition | Y | N/A | N/A | Y — CLAUDE.md's own invariants (cancelled only pre-`in_progress`, `active_statuses` at-most-one) | Y — `spinr_rides_state_transition_total{to_status=...}` | Y — same counter | Y — `test_ride_state_machine.py` named explicitly in CLAUDE.md's "what must have a test" | N/A | Y — CLAUDE.md core doc | U | N — no dedicated runbook (nearest: `driver-not-receiving-rides.md`, adjacent not on-point) | N/A (not a flagged feature; the machine itself) | spinr-dispatch-reviewer (Covered) | Best-documented feature in the whole matrix — CLAUDE.md dedicates an entire section to this state machine's invariants |
| Navigation integration (S-fulfil-05) | Y — driver hands off to Google Maps, Waze or Apple Maps from the active-ride panel (`driver-app/components/dashboard/ActiveRidePanel.tsx:376,521` → `driver-app/lib/navigation/launchNavigation.ts`; Android Auto route in `driver-app/lib/androidAuto/carRoute.ts`) | N/A (client-side handoff) | N/A | N/A | N/A | N/A | N/A | U | N/A | U | U | Y — `driver-app/__tests__/components/ActiveRidePanel.test.tsx:259-290` (Waze and Google Maps URLs, fallback when Waze is not installed) | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | **Corrected by orchestrator re-verification (2026-09-25):** the lane's first pass grepped one screen file and reported no deep-link code. That was wrong; the handoff lives in `launchNavigation.ts` and is tested. Counted as a verifier error in the Step 6 tally. VERIFIED by direct read. |
| In-ride chat (S-fulfil-06) | Y | Y — 12 mapped units | Y | Y | Y | Y | U | U | U | U | U | Y (INFERRED) | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | — |
| Trip sharing (S-fulfil-07) | Y | Y | Y | Y | U | Y | U | Y — CLAUDE.md: exact pickup/dropoff addresses must not appear in logs; not independently checked for the share-link payload itself | U | U | U | N — CSV `has_test=False`; not independently re-checked | U | U | U | N | U | spinr-dispatch-reviewer (adjacent) | — |
| Lost & found (S-fulfil-08) | Y — both apps' screens | Y — `routes/rides/lost_found.py` + driver routes | Y | Y | U | Y | U | Y — contact-masking concern flagged generally by sweep-catalog 3.4#37 (not independently re-checked) | U | U | U | Y (INFERRED) | P | Y — `epics.md` §4 explicitly names this as a genuine PRD-vs-code gap (feature real, PRD never names it) | U | N | U | spinr-dispatch-reviewer (adjacent) | epics.md §4: PRD never mentions lost & found as its own capability despite 18+ mapped code units |
| Driver online/available status (S-fulfil-09) | Y | Y | Y | Y | Y | Y | U | U | Y — CLAUDE.md's `is_available ⇒ is_online` invariant, and document-expiry-blocks-Period-1+ rule | U | U | Y (INFERRED) | Y — driver FAQ "Going online, trips & account" section | Y — CLAUDE.md dedicated convention section | U | Y — `driver-not-receiving-rides.md` | U | spinr-dispatch-reviewer (Covered) | CLAUDE.md documents this invariant explicitly; one of the best-specified features in the matrix |
| Driver-side ride flow (S-fulfil-10) | Y | Y — 13 mapped units | Y | Y | Y | Y | U | U | U | U | U | Y (INFERRED) | Y — driver FAQ | U | U | N | U | spinr-dispatch-reviewer (Covered) | **DRIVER-004** (VERIFIED, confirmed w/ call-site corrections per 07-reverification): a decline lowers future offer priority via an EWMA acceptance-rate penalty on the ETA-ranking branch, undisclosed anywhere in-app or in the FAQ (SKB-006 confirms the disclosure gap independently) |
| Route deviation / GPS integrity (S-fulfil-11) | U | Y — 2 mapped units | Y | Y | U | Y | U | U | Y — Period 2/3 insurance mapping depends on accurate ride-state, adjacent | U | U | Y (INFERRED) | U | Y — `domain-safety.md` | U | Y — `trip-route-integrity.md`, `route-pipeline-diagnostics.md` | N/A (safety-alert vs fare-adjustment split not independently checked) | spinr-dispatch-reviewer (Covered) | sweep-catalog 3.3#21 edge case (long-hauling: safety alert vs fare adjustment) |

---

## Epic: Ride Completion & Payments (8 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Fare finalization (S-pay-01) | Y | Y | Y | Y | Y | Y | Y | U | Y — CLAUDE.md Decimal-only-math rule, pre-commit-hook-enforced | U | Y — `spinr_fare_calc_duration_ms` (settlement path) | Y (INFERRED) | U | Y — `domain-payments.md` | U | N | U | spinr-money-auditor (Covered) | **MONEY-006** (money-cra.md, cited): float arithmetic found on the canonical fare-estimate path, outside every Decimal gate — a direct contradiction of CLAUDE.md's mandatory-Decimal rule |
| Wallet settlement (S-pay-02) | Y | Y — 24 mapped units | Y | Y | Y | Y | Y | U | U | U | U | Y (INFERRED) | U | Y — `domain-payments.md` | U | Y — `ledger-alerts.md` | U | spinr-money-auditor (Covered) | — |
| Stripe charge/capture (S-pay-03) | Y | Y | Y | Y | Y | Y | Y | Y — CLAUDE.md: never log even masked PANs, Stripe handles cards | Y — CLAUDE.md idempotency rule (`claim_stripe_event`) | Y | Y — `spinr_payment_settlement_total{outcome=...}` (KPI ≥99% success) | Y (INFERRED) | U | Y — CLAUDE.md + `domain-payments.md` | U | Y — `stripe-webhook-failure.md`, `stripe-reconciliation.md` | N/A (idempotency is the safety mechanism itself) | spinr-money-auditor (Covered) | Best-instrumented payment feature: named metric, named idempotency rule, two runbooks |
| Tip (S-pay-04) | Y | Y — 5 mapped units | Y | Y | Y | Y | U | U | U | U | U | N — CSV `has_test=False`; not independently re-checked | U | U | U | N | U | spinr-money-auditor (Covered) | sweep-catalog 3.4#31 edge case: "tip added after payout already sent" |
| Receipt generation (S-pay-05) | Y | Y — 7 mapped units | Y | Y | N/A | Y | N/A | U | **N — MONEY-011** (VERIFIED, confirmed by 07-reverification): no GST/HST registration number rendered on any receipt/statement/invoice, including corporate | U | U | Y (INFERRED) | U | Y — CLAUDE.md Tax section, `regulatory-sk.md` | U | N | U | spinr-money-auditor (Covered) | MONEY-011 is one of the most solidly re-verified findings in the whole audit (07-reverification checked it directly, widened scope, confirmed) |
| Rating (S-pay-06) | Y | Y | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | U | U | U | N | U | spinr-money-auditor (adjacent) | — |
| Reconciliation (S-pay-07) | N/A (internal) | Y — 11 mapped units | Y | Y | N/A | Y | Y | U | U | U | Y — daily reconciliation job | Y (INFERRED) | N/A | Y — CLAUDE.md background-loop registry | U | Y — `stripe-reconciliation.md` | U | spinr-money-auditor (Covered) | **MONEY-013** (money-cra.md, cited): daily reconciliations compare mismatched populations, so the alert is either always-red or never trusted |
| Refunds/disputes on fares (S-pay-08) | Y (admin) / N (rider self-serve — see RIDERJ-003) | Y — 8 mapped units | Y | Y | U | Y (but see ADMIN-OPS-001) | Y (partial, ADMIN-OPS-003) | U | U | U | U | Y (INFERRED) | Y — email footnote carries a dispute path (SKB-007, corrected per 07-reverification) | Y — `payment-dispute-evidence.md` (**stale**, per ADMIN-OPS §12 item 4) | U | Y — `payment-dispute-evidence.md` (stale) | N (ADMIN-OPS-001: no dual-approval/cap on refunds) | spinr-money-auditor / spinr-corporate-billing-reviewer (Covered) | **RIDERJ-003** (VERIFIED, confirmed): rider has no self-serve dispute/refund path; only driver and admin do. **ADMIN-OPS-001** (VERIFIED, confirmed): refund resolution has no second-approver or cumulative cap |

---

## Epic: Driver Earnings & Payouts (5 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Earnings calculation (S-earn-01) | Y | Y — 14 mapped units | Y | Y | N/A | Y | Y | U | Y — CLAUDE.md 0%-commission promise | U | U | Y (INFERRED) | Y — driver FAQ "Earnings, pay & taxes" | Y — CLAUDE.md | U | N | U | spinr-money-auditor (Covered) | **DRIVER-002** (driver-journey.md, cited): lifetime-earnings total and itemized trip list use two different legacy-ride inclusion rules |
| Period statements (S-earn-02) | Y | Y — 9 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | Y — driver FAQ | U | U | N | U | spinr-money-auditor (Covered) | — |
| T4A/tax generation (S-earn-03) | Y | Y — 9 mapped units | Y | Y | N/A | Y | U | Y — SIN handling implied by T4A per CLAUDE.md never-log list ("Government IDs, SIN") — not independently checked this pass for the T4A generation path specifically | Y — CLAUDE.md T4A-compatible requirement; SK regulatory checklist | U | Y — T4A annual job is one of the 45 named background loops | Y (INFERRED) | Y — driver FAQ | Y — CLAUDE.md + `regulatory-sk.md` | U | N — no dedicated runbook found | U | spinr-money-auditor (Covered) | **MONEY-012** (money-cra.md, cited): T4A slip fills Box 020 and Box 048 with the same amount for GST registrants — a real tax-form-correctness gap |
| Per-driver payouts (S-earn-04) | Y | Y — 34 mapped units (largest single-feature footprint in this epic) | Y | Y | N/A | Y | Y | U | U | U | U | Y (INFERRED) | Y — driver FAQ | U | U | N — no dedicated payout runbook found (checked list, no "payout"/"cashout" hit) | Y — `auto_payout` loop (1h, Sundays) is itself the rollout mechanism per epics.md §5 | spinr-money-auditor (Covered) | **STRAT-012** (strategy.md, cited): the most urgent finding in the finance doc — payouts set to manual, one payout ever — was never filed as a tracked item |
| Batch cash-out (S-earn-05) | U — not confirmed as a distinct UX affordance (may be subsumed into per-driver payouts UI) | Y — `auto_payout` loop + `314_auto_payout_and_instant_kill_switch.sql` (VERIFIED this pass, per CARTO §5) | Y | Y | N/A | Y | U | U | U | U | U | U — not independently re-checked | U | U | U | N | Y — "instant kill switch" migration name implies a rollback flag exists | spinr-money-auditor (Covered) | CSV `mapped=0` for this story — **classifier artifact, confirmed real code exists this session and by CARTO-004** (matched under S-earn-04's `payout` keyword instead) |

---

## Epic: Corporate / B2B Billing (7 features)

Seeded directly from `corporate.md` §7 (its own feature-completeness table), cross-checked
against `traceability.csv`'s mapped counts; not re-derived from scratch.

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Company registration & KYB (S-corp-01/02) | Y | Y | Y | Y | U | Y — `require_company_admin` (VERIFIED, corporate.md §7) | Y — `corporate_kyb_submitted` audit (VERIFIED) | U | U | U | Not traced (corporate.md §7's own words) | Y — `test_corporate_company_kyb.py` (VERIFIED) | U | Y — `domain-corporate.md` | U | Y — `corporate-guest-booking.md` (adjacent) | U | spinr-corporate-billing-reviewer (Covered) | corporate.md §7 row, unmodified |
| Allowance cap & ledger (S-corp-03) | Y | Y | Y | Y | U | Y | Y | U | U | U | Not traced | Y — incl. real-Postgres race test (VERIFIED) | U | Y — `domain-corporate.md`, `domain-payments.md` | U | N | U | spinr-corporate-billing-reviewer (Covered) | **CORP-002** (VERIFIED, confirmed by 07-reverification with citation-drift fix): fixed mig. 258 → silently regressed mig. 277 → re-fixed mig. 297/319, currently SAFE but recurrence risk flagged as a RECOMMENDATION |
| Membership lifecycle (S-corp-05) | Y | Y | Y | Y | U | Y | P — not fully re-swept (corporate.md §7) | U | U | U | Not traced | Y | U | Y — `domain-corporate.md` | U | N | U | spinr-corporate-billing-reviewer (Covered) | Reactivation-notification gap noted in corporate.md §3 (doc-carried, not independently re-derived) |
| Per-ride corporate charge (S-corp-04) | Y | Y — 11 mapped units | Y | Y | U | Y | U | U | N — surge must not apply to corporate rides per CLAUDE.md; `test_corporate_surge_bypass.py` exists confirming the guard (QUAL-003 withdrawn, 07-reverification) | U | U | Y (INFERRED) | U | Y — CLAUDE.md Surge section | U | N | U | spinr-corporate-billing-reviewer (Covered) | — |
| Invoicing/subscriptions (S-corp-06) | Y | Y (Stripe-delegated) | Y | Y | N/A | U — router-level `Depends` not independently traced (corporate.md §7 explicit UNKNOWN row) | Not traced | U | U | U | Not traced | Y — `test_corporate_subscriptions_route.py` (VERIFIED) | U | U | U | N | Y — Stripe idempotency key on create (VERIFIED) | spinr-corporate-billing-reviewer (Covered) | corporate.md §7's own explicit UNKNOWN on authz for this row, carried through unchanged |
| Winddown/offboarding (S-corp-07) | Y | Y — `corporate_wallet_winddown_service.py` (VERIFIED this pass, per CARTO §5) | Y | Y | U | Y — `get_current_admin` (VERIFIED) | Not traced for the winddown call specifically | U | U | U | Not traced | P — single-call tests only, no double-run test | U | Y — `domain-corporate.md`'s flag-gating note | U | N | Y — `corporate_close_refunds_wallet_balance` flag (dark) | spinr-corporate-billing-reviewer (Covered) | **CORP-001** (HIGH, VERIFIED): wind-down ledger debit is not idempotent; TOCTOU race on the status-flip guard; a concurrent/retried close double-debits the ledger against a single Stripe refund |
| Corporate reports/statements (S-corp-\*, folded from corporate.md §7) | Y | Y | Y | N/A (read-only) | N/A | Y | N/A | U | **N — MONEY-011** (same finding as consumer receipts) | U | U | Not independently re-verified this pass (corporate.md §7) | U | U | U | N | U | spinr-corporate-reporting-reviewer (Covered) | MONEY-011 applies identically to corporate statements per corporate.md §9 item 4 |

---

## Epic: Authentication & Authorization (6 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rider/driver signup (S-auth-01) | Y | Y — same code path as OTP (`routes/auth.py`, `_fire_signup_conversion` at `:361`, VERIFIED this pass) | Y | Y | N/A | Y | U | U | Y — COMP-008 (consent version stored on signup) with a gap: legacy-imported users and corporate self-serve signup both have consent holes | U | U | Y (INFERRED) | U | Y — CLAUDE.md OTP section | U | N | U | spinr-security-auditor (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact (same file as S-auth-02) |
| Email/phone OTP (S-auth-02) | Y | Y | Y — 4 OTP code paths (SEC-R10-016) | Y | N/A | Y | U | Y — CLAUDE.md: OTPs SHA-256 hashed at rest | Y — CLAUDE.md 5-failures/24h-lockout rule, dev-bypass `1234` only outside production | U | U | Y — VERIFIED (CSV) | U | Y — CLAUDE.md OTP Security section | U | Y — `otp-lockout-false-positive.md` | Y — `OTP_PEPPER` env, though it silently falls back to `JWT_SECRET` (SEC-R10-005) | spinr-security-auditor (Covered) | **SEC-R10-016** (security.md, cited): four OTP code paths, three hashed, one plaintext-by-design, lockout logic copied three times |
| JWT/session (S-auth-03) | N/A | Y — 14 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y — VERIFIED (CSV) | N/A | Y — CLAUDE.md Token Lifetimes section | U | Y — `auth-tokens.md` | N/A | spinr-security-auditor (Covered) | **SEC-R10-004** (security.md, cited): refresh-token reuse grace window lets a first-use attacker stay logged in undetected while the victim's retry is misclassified as benign |
| RBAC / admin access tiers (S-auth-04) | Y | Y — `routes/admin/staff.py` (VERIFIED this pass) | Y | Y | N/A | Y | Y | N/A | U | U | U | U — not independently re-checked | N/A | Y — CLAUDE.md JWT trust model section | Unknown | N | U | spinr-admin-rbac-reviewer (Covered, narrow) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact |
| Session management (S-auth-05) | Y | Y | Y | Y | N/A | Y | U | U | U | U | U | N — CSV `has_test=False`; not independently re-checked | N/A | Y — CLAUDE.md | U | Y — `auth-tokens.md` | U | spinr-security-auditor (Covered) | **SEC-R10-007** (security.md, cited): admin single-session logout depends on a fail-open Redis denylist; only logout-all has a DB-backed control |
| Profile management (S-auth-06) | Y | Y — 29 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | Y — account settings/deletion covered in rider-journey.md (e) | U | U | N | U | spinr-security-auditor (adjacent) | rider-journey.md (e): account deletion and reactivation both scored "Complete" |

---

## Epic: Admin Dashboard & Operations (12 features)

Seeded directly from `admin-ops.md` §10 (its own feature-completeness table, 10 of
12 rows already scored there); the 2 rows it did not score (Data transfer/export
beyond legacy import, Legal content mgmt) are filled from CSV + a light check.

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Driver management (S-admin-01) | Y | Y | Y | Y | N/A | Y | Y | U | U | U | U | U — not independently checked (admin-ops.md §10) | U | U | U | U | N/A | this role (admin-ops, Partially Unowned for general functional correctness per CARTO §7) | admin-ops.md §10 row, unmodified. **DRIVER-003** (driver-journey.md): 3-year licensed-experience check is a no-op for the existing fleet and dark for everyone |
| Rider management (S-admin-02) | Y | Y — 8 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | U | U | U | U | N/A | this role (Partially Unowned for general functional correctness per CARTO §7) | Not individually scored by admin-ops.md's own §10 table (which covered driver management but not rider management as a separate row); filled here from CSV only — genuinely thinner evidence than the sibling driver-management row |
| Ride intervention (cancel/complete/reassign/waive) (S-admin\*, folded) | Y | Y | Y | Y | Y (WS events per CLAUDE.md state-machine rule) | Y | Y | N/A | U | U | U | U | U | U | U | U | N/A | this role | admin-ops.md §10 row, unmodified |
| Refunds & wallet credit/debit (S-admin\*, folded — see S-pay-08) | Y | Y | Y | Y | U | Y (but see ADMIN-OPS-001) | Y (partial, ADMIN-OPS-003) | U | U | U | U | U | U | U | U | `payment-dispute-evidence.md` (STALE) | **N** (ADMIN-OPS-001) | this role | admin-ops.md §10 row, unmodified — see also S-pay-08 above |
| Financial dashboard (S-admin-03) | Y | Y — 27 mapped units | Y | Y | N/A | Y | U | U | U | Y — `GET /api/admin/analytics/overview` | U | Y (INFERRED) | N/A | U | U | U | U | this role | — |
| Dispute resolution (S-admin-04) | Y | Y — 53 mapped units | Y | Y | U | Y | Y | U | U | U | U | Y (INFERRED) | N/A | U | U | U | U | this role | **OBS-004** (support-kb.md SKB-008, cited): silent ticket-creation failures for complaints/lost-and-found/disputes have no metric |
| Support tickets (Zoho) (S-admin\*, folded) | Y | Y | Y | Y | U | Y | Y | U | U | U | U | U | U | U | U | U | N/A | this role / spinr-observability-reviewer (Zoho-specific carve-out) | **INT-002** (integrations.md, cited): Zoho Desk transport failures are downgraded to `logger.warning` and silently dropped, including the SOS-linked ticket |
| SOS handling (admin console) (S-admin\*, folded) | U | U | U | U | U | U | U | U | U | U | U | U | `sos-incident.md` (**BROKEN** per TSF-001) | U | U | `sos-incident.md` (broken) | U | R11 (not re-audited by admin-ops.md) | admin-ops.md §10 row, unmodified — every cell explicitly `?` in the source table |
| Service-area / fare config (S-admin-06, folded) | Y | Y | Y | Y | N/A | Y | Y | N/A | U | U | U | U | U | U | U | U | **N — ADMIN-OPS-004** | this role | No optimistic-concurrency check; silent last-write-wins on surge/fare config is admin-ops.md's own highest-stakes instance of this gap |
| Feature flags (`app_settings`) (S-admin-06) | Y | Y | Y | Y | N/A | Y | Y (key names only) | Y (secrets masked) | N/A | U | U | U | U | U | U | U | Y (flags ARE the rollback mechanism) | this role | admin-ops.md §6/§10: ~150 read call sites, 45 boolean flags in `SettingsUpdateRequest` alone |
| Data migration / legacy import (S-legacy-\*, cross-epic) | Y | Y | Y | Y | N/A | Y | Y | Y (B11 scope flags) | P (B11 R-G legal disclosure still draft-only) | U | U | U | U | U | U | multiple migration runbooks (not re-checked this pass) | N/A (one-shot tooling) | spinr-migration-reviewer (Covered) | admin-ops.md §10 row, unmodified |
| Data transfer / export (S-admin-05) | Y | Y — 13 mapped units | Y | Y | N/A | Y | Y | Y | P (dual-approval built, off by default — B10) | U | U | U | U | U | U | U | Y (flag) | this role | admin-ops.md §10 row, unmodified |
| Compliance export (S-admin-07) | Y | Y — `routes/admin/compliance.py`, `sgi_forms.py` (VERIFIED this pass) | Y | Y — `compliance_export_events` (migration 263, logged per code comment) | N/A | Y — `require_super_admin` (VERIFIED) | Y — `compliance_export_events` audit trail (VERIFIED, code comment) | Y | **N — COMP-005/COMP-017**: SGI monthly km reporting and quarterly volume/incident report appear mandatory but only an on-demand admin export exists, no schedule/submission record | U | U | U — not independently re-checked | N/A | Y — `docs/audit/clean-sheet/02-findings/compliance.md` | U | N | N/A | spinr-regulatory-compliance-checker (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** (`compliance.py`, `sgi_forms.py`) — classifier artifact; the genuine gap is COMP-005/017's *scheduling*, not the export mechanism's existence |
| Bulk operations / fleet (S-admin-08) | Y | Y — 15 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | U | U | U | U | this role | — |
| Maintenance mode (S-admin-09) | Y | Y — 6 mapped units | Y | Y | Y — must fan out a state change per CLAUDE.md's WS rule | Y | U | N/A | N/A | U | U | Y (INFERRED) | N/A | U | U | U | Y (this feature IS a flag) | this role | — |
| Venues (S-admin-10) | Y | Y — `routes/admin/venues.py` (VERIFIED this pass) | Y | Y | N/A | Y | U | U | U | U | U | U — not independently re-checked | N/A | U | U | N | U | **UNOWNED per CARTO-003** (epic-classifier routed it to Maps & Routing, no agent names venues) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact |
| Admin messaging/broadcast (S-admin-11) | Y | Y — `routes/admin/messaging.py` (VERIFIED this pass) | Y | Y | Y — broadcast implies fan-out | Y | U | U | U | U | U | U — not independently re-checked | N/A | U | U | N | U | spinr-notification-ux-reviewer (routed here by epic classifier, per CARTO §5) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact |
| Legal content mgmt (S-admin-12) | Y | Y — 6 mapped units | Y | Y | N/A | Y | Y | N/A | Y — **COMP-009**: independent-contractor agreement (the document built to defeat misclassification) is a draft with no e-signature flow; 9 other legal texts live without counsel review | U | U | Y (INFERRED) | N/A | Y — `docs/legal/` | U | N | U | spinr-legal-readiness-reviewer (Covered) | COMP-009 is one of the highest-severity compliance findings and lands squarely in this feature |
| Staff management (S-admin\*, folded) | Y | Y | Y | Y | N/A | Y (1 inconsistency, **ADMIN-OPS-002**) | Y (partial, ADMIN-OPS-003) | N/A | N/A | U | U | U | N/A | U | U | U | N/A | this role | admin-ops.md §10 row, unmodified |

---

## Epic: Safety, Trust & Fraud (6 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SOS / emergency (S-safety-01) | Y — button present (rider-journey.md (e)) | Y — 20 mapped units | Y | Y | U | U | **U — TSF-011**: full-PII view during an active SOS not confirmed to be itself audit-logged | U | Y — CLAUDE.md "never auto-dials 911, only offers" rule | U | U | Y — multiple SOS test files (rider-journey.md (e)) | N — **SKB-003**: `docs/trauma-support.md`, cited by the SOS runbook as a checklist step, does not exist | Y — `domain-safety.md` | U | Y — `sos-incident.md`, but **TSF-001**: describes a system that does not exist in code | **Y — TSF-012**: core SOS is unflagged; additive variants fail closed to the safer default (positive finding) | spinr-safety-sos-reviewer (Covered) | **TSF-001** (VERIFIED per header): the only documented SOS on-call runbook describes a system that does not exist in code — the single most severe finding attached to this feature |
| Insurance period tracking (S-safety-02) | N/A (backend, driver-facing indirectly via earnings/period statements) | Y — 7 mapped units | Y | Y — append-only per CLAUDE.md mandate | N/A | Y | Y — append-only audit rows ARE the feature (CLAUDE.md) | N/A | Y — CLAUDE.md's Period 0-3 table, 7-year retention rule | U | U | Y (INFERRED) | Y — driver FAQ "Safety & coverage" | Y — CLAUDE.md dedicated section, `domain-safety.md` | U | N — no dedicated runbook found | N/A (append-only, no flag concept applies) | spinr-insurance-period-auditor (Covered) | **COMP-014** (compliance.md, cited): "declared online but unreachable" keeps an open Period 1 row — the audit trail can't distinguish "waiting" from "phone dead," and SGI's rule wants km "in all phases" |
| License/ID/background checks (S-safety-03) | Y | Y — 5 mapped units | Y | Y | N/A | Y | U | Y — encrypted-at-rest per CLAUDE.md (driver full address) | Y — CLAUDE.md eligibility checklist (CRC, Vulnerable Sector Check, license class) | U | U | Y (INFERRED) | Y — driver FAQ "Documents, CRC & insurance" | Y — `regulatory-sk.md` | U | N | U | spinr-regulatory-compliance-checker (Covered) | **COMP-001** (compliance.md, cited): licence class gate may enforce the wrong class (Class 5 in code vs. a provincial regulation snippet suggesting Class 4). **COMP-002**: every SK eligibility rule beyond document expiry is dark by default |
| RLS policies (S-safety-04) | N/A | N/A | Y | Y | N/A | N/A (this IS the authz layer) | N/A | Y | Y — CLAUDE.md's own caveat that RLS proves policy logic, not production reachability (service-role key bypasses it) | N/A | U | Y — dedicated `backend/tests/rls/` tier (VERIFIED, CLAUDE.md) | N/A | Y — CLAUDE.md Testing Conventions RLS tier section | U | N | N/A | spinr-security-auditor (Covered) | **SEC-R10-002** (security.md, cited): C43 closed in production but open in the repo — RLS enabled live on `settings`/`document_files`, two import tables no longer exist, `settings` now also holds LLM provider keys |
| Fraud detection (S-safety-05) | N | N | **N — confirmed absent, not a classifier artifact** (this pass: only `backend/tests/test_referral_payout_fraud_guards.py` matches "fraud" anywhere in the repo; no `backend/services/fraud*.py`) | N/A | N/A | N/A | N/A | N/A | N/A | N | N | N | N/A | N | N | N | N/A | spinr-fraud-auditor (Covered for the *abuse-pattern* review angle, but nothing to review — no detection code exists) | **TSF-002/003/004/005/006** (all VERIFIED or confirmed by 07-reverification for TSF-004): collusion, cancellation-fee farming, chargeback velocity, SIM-swap ATO, and promo-stacking interaction all have **no detection signal**. This is the single weakest L3 feature in the entire matrix — 16 of 18 dimensions genuinely absent, not merely unverified |
| Dispute/appeal handling (S-safety-06) | Y | Y — 5 mapped units | Y | Y | N/A | Y | Y | U | U | U | U | Y (INFERRED) | U | U | U | U | U | spinr-safety-sos-reviewer / spinr-fraud-auditor (adjacent) | **DRIVER-001** (driver-journey.md, cited): a driver is never told *why* they were suspended/banned, in-app, anywhere — the appeal side of this feature has no entry point if the reason is unknown |

---

## Epic: Notifications & Messaging (4 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FCM push offers (S-notif-01) | Y | Y — 5 mapped units | Y | Y | N/A (push, not WS) | Y | N/A | **N — SKB-001/SKB-002**: `minimal_fcm_offer_payload_enabled` ships dark (default off) — every auto-dispatch offer push today carries unfiltered precise pickup/dropoff coordinates and rider rating | N/A | U | U | Y (INFERRED) | U | U | U | N | **Y — the fix exists as a flag, just off** (SKB-002) | spinr-notification-ux-reviewer (copy angle only, not delivery-mechanism correctness — CARTO §7 caveat) | **SKB-001/002** (support-kb.md, cited): an untracked FCM PII gap with a real fix already built and dark-launched — the clearest "flip the flag" recommendation in the whole audit |
| SMS OTP (S-notif-02) | Y | Y — `backend/utils/marketing_sms.py`, Twilio integration (VERIFIED this pass) | Y | Y | N/A | Y | U | Y — CLAUDE.md: use `phone_last4` if logging | Y | U | U | U — not independently re-checked | N/A | Y — CLAUDE.md OTP section | U | N | Y — dev bypass `"1234"` outside prod (CLAUDE.md) | spinr-security-auditor (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact. **INT-001** (integrations.md, cited): Twilio's REST client has no HTTP timeout configured — an OTP send can hang the request thread indefinitely during a slow (not just down) Twilio outage |
| In-app notifications (S-notif-03) | Y | Y — 16 mapped units | Y | Y | Y | Y | N/A | U | U | U | U | Y (INFERRED) | U | U | U | N | U | spinr-notification-ux-reviewer (copy) | Known-forks pair #2 (`docs/known-forks.md`): driver-app/rider-app notifications screens diverged twice by omission, no mechanical parity guard exists |
| Notification throttling/retry (S-notif-04) | N/A (backend mechanism) | Y — `push_retry` loop (VERIFIED this pass, per CARTO §5) | Y | Y | N/A | N/A | U | N/A | N/A | U | U | U — not independently re-checked | N/A | Y — CLAUDE.md background-loop registry lists `push retry` explicitly | U | N | N/A (a retry loop, not itself flagged) | spinr-observability-reviewer (adjacent) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact (routed into S-notif-01's mapped count instead) |

---

## Epic: Maps & Routing (4 features)

**UNOWNED per `epics.md` §7 (CARTO-003)** — no `.claude/agents/spinr-*.md` file
names maps, service areas, geo/H3, or venues, despite this epic gating WAV dispatch
(a Saskatchewan legal requirement, COMP-010) and the Directions-timeout fare-estimate
SLA exception. Every "Owner" cell below reflects that gap rather than a covered
agent.

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Google Maps integration (S-maps-01) | N/A | Y — 5 mapped units | Y — `maps_proxy.py` | N/A | N/A | Y | N/A | Y — CLAUDE.md: raw GPS never logged | N/A | U | U | Y (INFERRED) | N/A | Y — CLAUDE.md Performance SLA exception (3.5s Directions wait) | U | N — no dedicated runbook found (`route-pipeline-diagnostics.md` is adjacent, not Maps-vendor-specific) | U | **UNOWNED** | **INT-004** (integrations.md, cited): a cost-based circuit breaker (`maps_budget.py`) exists but there's no failure-rate breaker — every request during a sustained Maps 5xx outage still pays the full timeout before failing |
| Distance/ETA (S-maps-02) | Y | Y — 3 mapped units | Y | Y | N/A | Y | N/A | U | U | U | U | Y (INFERRED) | N/A | Y — CLAUDE.md Performance SLA exception | U | N | U | **UNOWNED** | Same 3.5s worst-case exception governs this feature directly (CLAUDE.md) |
| Service-area geometry (S-maps-03) | Y — largest footprint in this epic, 37 mapped units | Y | Y | Y | N/A | Y | U | N/A | **N — COMP-010**: WAV dispatch gating depends on this geometry and the promised wait-time UX isn't built | U | U | Y (INFERRED) | U | Y — `regulatory-sk.md` (WAV accessibility section) | U | Y — `saskatoon-launch.md` | U | **UNOWNED — CARTO-003's central example** | STRAT-007 (strategy.md, cited): production service areas include `riyadh`/`riyadh airport` — repo contradicts itself on whether this is test data or an international market |
| H3 heatmapping (S-maps-04) | Y (admin monitoring) | Y — 4 mapped units | Y | Y | N/A | Y | N/A | N/A | N/A | Y — surge/demand visualization is itself an analytics tool | U | Y (INFERRED) | N/A | U | U | N | N/A | **UNOWNED** | — |

---

## Epic: Promotions & Loyalty (5 features)

`epics.md` §7: **PARTIALLY UNOWNED** — `spinr-fraud-auditor` covers the *abuse*
angle only; nobody reviews whether a promo/quest/loyalty feature works correctly.

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Coupon/promo codes (S-promo-01) | Y | Y — 23 mapped units | Y | Y | N/A | Y | U | U | Y — `referral_payout_velocity_cap_per_day` flag exists (admin-ops.md §6) | U | U | Y — `test_referral_payout_fraud_guards.py` (VERIFIED this pass) | U | U | U | N | Y — the velocity-cap flag itself | Partial — spinr-fraud-auditor (abuse only) | **TSF-006** (trust-safety-fraud.md, cited): promo-stacking and free-ride-double-reward interaction not independently re-verified across every entry point |
| Driver quests/bonuses (S-promo-02) | Y | Y — 26 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | Y — driver FAQ (bonus mentions) | U | U | N | U | Partial — spinr-fraud-auditor (abuse only) | — |
| Rider loyalty (S-promo-03) | Y | Y — 7 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | U | U | U | N | U | **UNOWNED for functional correctness** | — |
| Referral codes (S-promo-04) | Y | Y — 22 mapped units | Y | Y | N/A | Y | U | U | Y — velocity-cap flag applies here too | U | U | Y (INFERRED) | U | U | U | N | Y (velocity-cap flag) | Partial — spinr-fraud-auditor (abuse only) | Known-forks pair #3 (`docs/known-forks.md`): a dead, mismatched `referral_link` field on both rider/driver sides was silently wrong until removed 2026-09-14 |
| Marketing/subscriptions (S-promo-05) | Y | Y — 38 mapped units (largest footprint in this epic) | Y | Y | N/A | Y | U | **N — STRAT-004**: Meta ad SDK + per-ride `Purchase` conversions collide with the "no ad SDKs" guardrail and are undisclosed to users | N — CASL consent for marketing push/SMS/email not independently verified (sweep-catalog §4.3) | U | U | Y (INFERRED) | U | U | U | N | U | **UNOWNED for functional correctness; not covered by fraud-auditor either (not an abuse vector)** | **STRAT-004** is one of the highest-severity strategy findings and directly contradicts CLAUDE.md's "What Spinr Is NOT" guardrail on ad SDKs |

---

## Epic: Observability & Monitoring (4 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Sentry error tracking (S-obs-01) | N/A | N/A | Y — 4 mapped units | N/A | N/A | N/A | N/A | Y — CLAUDE.md Sentry tag rules (`domain`/`surface`/ids/`env`), never PII | N/A | N/A | Y — this IS the alerting mechanism | Y (INFERRED) | N/A | Y — CLAUDE.md Sentry Tags section | U | N | N/A | spinr-observability-reviewer (Covered) | — |
| Prometheus metrics (S-obs-02) | N/A | Y — `backend/utils/metrics.py` (VERIFIED this pass) | Y | N/A | N/A | N/A | N/A | N/A | N/A | Y | Y — this IS the metric system | U — not independently re-checked | N/A | Y — CLAUDE.md Metric Naming section (extensive) | U | N | N/A | spinr-observability-reviewer (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact. CLAUDE.md's own OBS-002 citation: 3 of the 8 Performance SLA rows have no emitting metric at all (driver-location-write, auth-token-refresh, Stripe-webhook-processing) |
| Loop watchdog (S-obs-03) | N/A | Y — 2 mapped units | Y | N/A | N/A | N/A | N/A | N/A | N/A | Y | U | Y (INFERRED) | N/A | Y — CLAUDE.md `_WATCHDOG_LOOP_NAMES` reference | U | N | N/A | spinr-observability-reviewer (Covered) | **REL-001** (reliability.md, cited): loop-watchdog staleness thresholds are wrong in both directions for ~30 of 44 loops, and one loop can never be flagged at all. **REL-002**: all 44-loop alerting collapses to one channel (`ALERT_WEBHOOK_URL`), configured status UNKNOWN, no fallback |
| Health checks (S-obs-04) | N/A | Y — `backend/routes/main.py:/health`, `server.py:/health` (VERIFIED this pass) | Y | N/A | N/A | N/A | N/A | N/A | N/A | U | U | U — not independently re-checked | N/A | U | U | Y — `api-down.md`, `supabase-down.md` | N/A | spinr-observability-reviewer / spinr-ops-triage-investigator (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** (both `main.py` and `server.py` define `/health`) — classifier artifact |

---

## Epic: Integrations & Webhooks (4 features)

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Stripe webhooks (S-integ-01) | N/A | Y — 10 mapped units | Y | Y | N/A | Y (signature verification) | Y | Y | Y — CLAUDE.md Stripe idempotency rule | U | Y — `spinr_payment_settlement_total` | Y (INFERRED) | N/A | Y — CLAUDE.md, `domain-payments.md` | U | Y — `stripe-webhook-failure.md` | N/A | spinr-money-auditor (Covered) | sweep-catalog 3.4#29 edge case (webhook arrives twice/out of order/never) is directly addressed by the idempotency rule; not independently re-verified against every webhook type this pass |
| Zoho Desk sync (S-integ-02) | Y (admin support UI) | Y — 19 mapped units | Y | Y | U | Y | Y | U | U | U | **N — no metric** | Y (INFERRED) | Y — many support-kb.md change-log entries | U | U | U | U | spinr-observability-reviewer (explicit carve-out per CARTO §7) | **INT-002** (integrations.md, cited): Zoho transport/API failures downgraded to `logger.warning` and silently dropped in the fire-and-forget ticket-creation path, **including the SOS-linked ticket** — no retry, no metric, no Sentry signal |
| Stripe Connect/payout sync (S-integ-03) | N/A | Y — 10 mapped units | Y | Y | N/A | Y | Y | U | U | U | U | Y (INFERRED) | U | Y — `domain-payments.md` | U | Y — `stripe-legacy-migration.md` | U | spinr-money-auditor (Covered) | — |
| Stripe KYC/Identity (S-integ-04) | Y (driver onboarding) | Y — 2 mapped units | Y | Y | N/A | Y | U | Y — government ID handling, CLAUDE.md never-log rule | Y | U | U | Y (INFERRED) | Y — driver FAQ "Documents" section | U | Y — driver welcome/onboarding email (2026-08-28 change-log, cited) | Y — `stripe-identity-drift-manual-test.md` | U | spinr-money-auditor / spinr-security-auditor (Covered) | — |

---

## Epic: Legacy Import & Data Migration (6 features)

`epics.md` §7: Covered by `spinr-migration-reviewer` (explicitly names `legacy_*.py`,
`*_import_service.py` in its description).

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Driver import (S-legacy-01) | Y (admin) | Y — 8 mapped units | Y | Y | N/A | Y | U | Y — legacy PII import is exactly the driver-CSV-PII surface named by this repo's own gitleaks rule (not quoted verbatim here per this task's instructions) | U | U | **U — OBS-003** (cited) | Y (INFERRED) | N/A | Y — `legacy-migration-playbook.md` | U | Y — `legacy-booking-import-2026-08-22-batch.md`, `driver-pii-history-rewrite-plan.md` | Y — batch/rollout tooling | spinr-migration-reviewer (Covered) | `docs/runbooks/driver-pii-history-rewrite-plan.md` existing at all signals a real, historical PII-exposure remediation on this exact feature |
| Rider import (S-legacy-02) | Y (admin) | Y — 1 mapped unit | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | Y — `legacy-migration-playbook.md` | U | Y — same playbook | Y | spinr-migration-reviewer (Covered) | — |
| Earnings/payout backfill (S-legacy-03) | N/A | Y — 3 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | Y — `legacy-migration-playbook.md` | U | Y — `legacy-backfill-scripts-rollout.md` | Y | spinr-migration-reviewer (Covered) | This is the same "legacy-ride inclusion rule" surface DRIVER-002 flags as inconsistent between total and itemized views |
| Data-quality / crosswalk (S-legacy-04) | N/A | Y — 12 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | Y — `migration-data-quality-strategy.md` | U | Y — `migration-driver-rider-repair-scope.md`, `migration-conflict-detection.md` | U | spinr-migration-reviewer (Covered) | — |
| Booking/wallet/address import (S-legacy-05) | N/A | Y — 9 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | Y — `safe-data-migration-playbook.md` | U | Y — same | U | spinr-migration-reviewer (Covered) | — |
| Legacy consent/badge (S-legacy-06) | Y | Y — 3 mapped units | Y | Y | N/A | Y | U | Y — **COMP-008**: legacy-imported users are one of the three consent-coverage holes named | U | U | U | Y (INFERRED) | N/A | U | U | N | U | spinr-migration-reviewer (Covered) | COMP-008 directly names this feature's consent gap |

---

## Epic: AI Assistant (2 features)

Per `epics.md` §9, `backend/ai/**` was not walked as its own glob this audit —
only `routes/ai.py`/`routes/admin/ai_console.py` were captured, so both rows below
likely undercount the true AI surface (tools, prompts, eval harness).

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rider AI assistant (S-ai-01) | Y — `rider-app/app/ai-assistant.tsx` | Y — 2 mapped units (likely undercounted, epics.md §9) | Y | U | U | Y | U | U — what the assistant can see/say about PII not independently checked | U | U | U | N — CSV `has_test=False` | N — `backend/ai/tools_support.py`'s `escalate_to_support` opens a ticket, not a dispute row (RIDERJ-003 cross-reference, VERIFIED by 07-reverification) | U | U | N | U | spinr-ai-guardrail-reviewer (Covered) | greenfield-extensions §6 mandate (agent control-plane matrix: objective/tools/permissions/PII/budget/kill-switch/audit/eval) not independently built for this feature this pass |
| AI admin console/guardrails (S-ai-02) | Y (admin) | Y — 10 mapped units | Y | Y | N/A | Y | U | U | U | U | U | Y (INFERRED) | N/A | U | U | U | U | spinr-ai-guardrail-reviewer (Covered) | **SEC-R10-012** (security.md, cited, VERIFIED — repo claims confirmed by 07-reverification): dev-agent control plane has `git push origin main` pre-approved for agents, an account-level Supabase connector with full production write, and `agent_action_log` has never received a row (live row count UNVERIFIABLE) |

---

## Epic: Engineering Gates & CI/CD (6 features)

Not a product epic in the PRD — added by `epics.md` to give `.github/workflows/`
(52 files) a home. Owner: `spinr-cicd-infra-reviewer` (Covered).

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CI test/lint gates (S-ci-01) | N/A | N/A | Y — 3 mapped units | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N — CSV `has_test=False` for the gate's own tests (self-referential; not independently re-checked) | N/A | Y — CLAUDE.md Commands/Testing sections | U | U | N/A (a gate, not a flag) | spinr-cicd-infra-reviewer (Covered) | **QUAL-001** (quality.md, cited): CI's two required "summary" gates are, per the card title, some form of not-actually-blocking pattern (full detail not re-read this pass — cite the card, don't over-claim) |
| Security/audit gates (S-ci-02) | N/A | N/A | Y — 6 mapped units | N/A | N/A | N/A | N/A | N/A | Y — **COMP-018**: no compliance-labelled gate runs in CI; narrowed per 07-reverification to "the checker agent and its checkboxes are advisory, but the underlying compliance tests already run in the blocking full pytest step" | N/A | N/A | N — CSV `has_test=False` | N/A | Y — CLAUDE.md PR review handling section | U | U | N/A | spinr-cicd-infra-reviewer (Covered) | **QUAL-002** (quality.md, cited): `.gitleaks.toml`'s custom driver-export-PII rule (not quoted here per this task's rules) — see quality.md for the finding, not reproduced |
| Migration safety check (S-ci-03) | N/A | N/A | Y — 1 mapped unit | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Y (INFERRED) | N/A | Y — CLAUDE.md Database & Migration Conventions section (extensive) | U | Y — `migration-conflict-detection.md` | N/A | spinr-cicd-infra-reviewer / spinr-migration-reviewer (Covered) | CLAUDE.md documents CHECK B's two closed gaps (cross-PR race, same-PR sibling collision) in detail — one of the best-specified CI mechanisms in the repo |
| Visual regression (S-ci-04) | N/A (tooling) | N/A | Y — `admin-dashboard/e2e/visual-regression.spec.ts` (VERIFIED this pass, snapshots present for all 6 named pages) | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Y — VERIFIED this pass (spec + snapshot files present) | N/A | Y — CLAUDE.md's extensive, dated (2026-09-04, B38) documentation of this exact gate | U | U | N/A | spinr-cicd-infra-reviewer (Covered) | CSV `mapped=0`; **real code VERIFIED this pass** — classifier artifact (routed into S-ci-01). CLAUDE.md is explicit that rider-app/driver-app have **no** equivalent tooling at all — that absence is a real, documented gap, not a classifier miss |
| Deploy pipeline (S-ci-05) | N/A | N/A | Y — 5 mapped units | N/A | N/A | N/A | N/A | N/A | N/A | N/A | U | Y (INFERRED) | N/A | Y — CLAUDE.md Deployment section (extensive, incl. the `spinr-backend`/`spinrvm` service-name postmortem) | U | Y — `railway-fly-failover.md`, `railway-fly-cloudflare.md`, `fly-mixed-fleet.md` | N/A | spinr-cicd-infra-reviewer (Covered) | CLAUDE.md's own Deployment section notes C1 (a real DNS-failover drill under live traffic) remains separately unverified even though the deploy pipeline itself is confirmed healthy |
| Review automation (S-ci-06) | N/A | N/A | Y — 1 mapped unit | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Y (INFERRED) | N/A | Y — CLAUDE.md's extensive "PR review handling (Codex auto-review)" section, dated status note (2026-08-01, silent since 30 July) | U | U | N/A | spinr-cicd-infra-reviewer (Covered) | CLAUDE.md states both Codex and the Claude review workflow are currently non-functional/off — this feature is honestly documented as broken in its own governing doc, a rare case of the doc being *more* current than a lane finding |

---

## Epic: Shared Frontend Foundation & Design System (5 features)

`epics.md` §7: **UNOWNED for logic correctness** (CARTO-002) — the three UI-quality
agents cover the visual layer only; nobody reviews `shared/api`/`shared/store`/
`shared/hooks` logic, which is the traced root cause of 3 of `docs/known-forks.md`'s
4 fork pairs.

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Shared API client (S-shared-01) | N/A | Y — 13 mapped units | N/A (client-side) | N/A | N/A | N/A (delegates to backend) | N/A | U | N/A | N/A | N/A | Y (INFERRED) | N/A | Y — `docs/design/rider-driver-app-design-system.md` names the shared `shared/theme/` token source, but API-client-specific docs not independently found | U | N | N/A | **UNOWNED (logic)** | Cited RELIABILITY-002 finding_id in CSV (not independently re-derived this pass) |
| Shared state/store (S-shared-02) | N/A | N/A | Y — 3 mapped units | N/A | N/A | N/A | N/A | U | N/A | N/A | N/A | N — CSV `has_test=False`; not independently re-checked | N/A | U | U | N | N/A | **UNOWNED (logic)** | DRIFT-005 finding_id attached in CSV (not independently re-derived) |
| Shared UI components (S-shared-03) | Y — 20 mapped units | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Y (INFERRED) | N/A | Y — `spinr-rider-driver-design-system` skill exists (confirms this is known internally, per epics.md §4) | U | N | N/A | Covered for visual consistency (`spinr-design-consistency-reviewer`); **UNOWNED for logic** | Known-forks pair #1 (`CarMarker.tsx`): diverged 5× historically; a mechanical parity guard landed 2026-09-12 but covers prop-level API surface only |
| Shared hooks (S-shared-04) | N/A | N/A | Y — 10 mapped units | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Y (INFERRED) | N/A | U | U | N | N/A | **UNOWNED (logic)** | Known-forks pair #2 note: driver-app uses the shared `notificationQueries.ts` hook, rider-app hand-rolls the same logic locally — an un-guarded, undecided divergence |
| Shared types (S-shared-05) | N/A | N/A | Y — 6 mapped units | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N — CSV `has_test=False`; not independently re-checked | N/A | U | U | N | N/A | **UNOWNED (logic)** | Known-forks pair #3: a dead, mismatched `referral_link` field existed on both sides — a shared-types-layer class of bug |

---

## Epic: Platform Foundation & Schema (1 feature)

Catch-all for domain-generic migrations that name no clear product story (`epics.md`
§4: "expected and largely benign — not every migration should map to a product
story").

| Feature (story) | UX | API | Backend | DB | Events/WS | AuthZ | Audit log | Privacy | Compliance | Analytics | Metric+Alert | Tests | Support article | Docs | Training | Runbook | Rollback/Flag | Owner | Key evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Core schema/infra (S-plat-01) | N/A | N/A | Y — 4 mapped units | Y — this feature IS the migration layer | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N — CSV `has_test=False`; migration content was **not opened/read** this pass (epics.md §0 disclosure, carried through unchanged) | N/A | Y — CLAUDE.md Database & Migration Conventions | U | Y — several migration runbooks | N/A | spinr-migration-reviewer (Covered, schema portion only — general "Platform Foundation" as a catch-all has no single named owner) | **CARTO-005** (epics.md §8, cited): `backend/migrations/` (553 files) has never been checked against production's `schema_migrations` table in this audit or, per the module map, recent prior ones — applied/pending status is genuinely UNKNOWN, not merely unverified |

---
