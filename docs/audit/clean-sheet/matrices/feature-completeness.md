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
