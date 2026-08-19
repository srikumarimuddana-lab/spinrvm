# Spinr Full-Fleet Whole-App Audit — Part A, all 21 reviewers (2026-08-18)

**Date:** 2026-08-18 · **Scope:** the entire application (not a diff) — `backend/` (all ~25 routers,
services, utils, background loops, migrations), `rider-app/`, `driver-app/`, `admin-dashboard/`,
`shared/`, `.github/workflows/` + deploy config, and the AI surface (`backend/ai/**`, `/mcp`,
`rider-app/app/ai-assistant.tsx`). Run per the Spinr Full-App Audit Master Prompt v3, Part A —
a 3-day drift check against the 2026-08-15 launch-readiness baseline
(`docs/audit/2026-08-15-full-fleet-launch-readiness.md`).

**Method:** all 21 `spinr-*` reviewer agents dispatched independently and in parallel, each against
its own domain across the whole codebase, audit-only — no files modified by any agent. Every agent
was instructed to reconcile against the 2026-08-15 baseline first (FIXED / STILL-OPEN / REGRESSED)
before reporting new findings (marked NEW). Two-layer output preserved below: plain-English summary
up top, `file:line` technical detail underneath.

**Standing non-agent-shaped gaps** (tracked, never silently absorbed): load/chaos testing (E2),
DAST/pentest (E6), backup-restore drills (E7), real screen-reader/visual-regression passes (N12).

> **Post-audit update (2026-08-18, same day):** ranked blocker #1/#2 (Period 2 insurance timing +
> the legal-doc contradiction) was fixed same-day, after this audit ran — see the ranked blocker
> register and `docs/change-log/2026-08-18-period-2-insurance-timing-fix.md` for detail. The
> narrative below (executive summary, baseline reconciliation table) reflects the audit's original
> findings as run; the item's row is annotated FIXED rather than rewritten, per the ledger
> convention this repo already uses elsewhere.

```
SPINR FULL-FLEET AUDIT — whole app, 3-day drift check
========================================================
Agents dispatched: 21/21

── MONEY & BUSINESS LOGIC ──────────────────────
  spinr-money-auditor                FIX BLOCKERS (receipts never itemize surge as a $ line; 3 carry-overs)
  spinr-surge-auditor                FIX BLOCKERS (surge_source stamp still missing; blast radius narrower than baseline implied)
  spinr-corporate-billing-reviewer   SAFE TO LAUNCH (new late-tip-debit code independently re-verified clean)
  spinr-fraud-auditor                FIX BLOCKERS (v2 GPS path still unguarded; NEW: $0 promo ride still qualifies for referral payout)
  spinr-corporate-reporting-reviewer FIX BLOCKERS (combined-tax PDF + UTC month bounds, both still open, zero commits since baseline)

── CORE PLATFORM ───────────────────────────────
  spinr-security-auditor              FIX BLOCKERS (4 carry-overs unchanged: emergency contacts, AI driver-name leak, admin audit gap, unpinned trufflehog)
  spinr-dispatch-reviewer             FIX BLOCKERS (confirms batch-offer model has no driver_assigned; Period 2 gap is real, not theoretical)
  spinr-insurance-period-auditor      FIX BLOCKERS (NEW: rider-facing legal draft doc says the opposite of what the code does)
  spinr-safety-sos-reviewer           FIX BLOCKERS (both carry-overs unchanged; doc now honestly corrected, not "doc lies")
  spinr-realtime-reliability-reviewer FIX BLOCKERS (both carry-overs unchanged; 13 loops still unwatchdogged)
  spinr-migration-reviewer            NEEDS HUMAN REVIEW (numbering collisions grew from 3 to 7 duplicate prefixes; found+fixed a silently-broken nightly PIPEDA purge job)
  spinr-admin-rbac-reviewer           NEEDS HUMAN REVIEW (no live security hole; 2 NEW frontend/backend module-string mismatches lock out legitimate staff)
  spinr-cicd-infra-reviewer           FIX BLOCKERS (REGRESSED: deploy-metrics-agent.yml missed by the C18b sweep that claimed to fix this class)
  spinr-edge-case-reviewer            FIX BLOCKERS (4 carry-overs unchanged, no new blockers)

── COMPLIANCE & QUALITY ────────────────────────
  spinr-regulatory-compliance-checker FIX BLOCKERS (all 5 carry-overs unchanged; retention/deletion machinery itself confirmed healthy)
  spinr-accessibility-reviewer        FIX BLOCKERS (NEW: shared Toast component has zero screen-reader wiring fleet-wide; WAV toggle doesn't announce as a switch)
  spinr-design-consistency-reviewer   FIX BLOCKERS (off-brand teal NEW 3rd instance; CustomAlert tokenization fix confirmed landed)
  spinr-ai-guardrail-reviewer         FIX BLOCKERS (root-caused: no tool RESULT is ever PII-scrubbed, a structural gap, not just the driver-name instance)
  spinr-performance-sla-reviewer      FIX BLOCKERS (NEW, non-load-test-dependent: fare-estimate can hard-block 3.5s by design; location-write has a duplicate DB fetch)
  spinr-observability-reviewer        FIX BLOCKERS (NEW: unaudited Redis flush-prefix admin endpoint; NEW structural gap — no metric on any post-acceptance ride-state transition)
  spinr-test-coverage-reviewer        NEEDS HUMAN REVIEW (corporate coverage gate shipped since baseline; payments/fare/crypto/rides/dispatch floors still unenforced; RLS never tested at the DB role level)

VERDICT: FIX BLOCKERS
```

---

## Plain-English executive summary

Three days after the 2026-08-15 launch-readiness audit, **the picture has not meaningfully
improved and has picked up one regression and several sharper findings**. Of the 17 ranked
blockers in the baseline, independent re-verification (not re-trusting the prior report's text)
found:

- **14 STILL-OPEN, unchanged** — same file, same lines, no commits touching them since 08-15.
- **0 fully FIXED.**
- **1 REGRESSED** — `deploy-metrics-agent.yml` still runs an unpinned `flyctl-actions@master`
  and `checkout@v7` with a live `FLY_API_TOKEN` in scope, even though the C18b remediation PR
  explicitly claimed to have swept this exact class of finding across "4 files." It missed a
  5th.
- **2 partially mitigated but not resolved** — the corporate PDF combined-tax line is now
  logged/Sentry-alerted internally (progress) but still ships to the customer unchanged; the
  emergency-contacts plaintext finding is no longer a "doc lies" problem (the doc was honestly
  corrected 2026-08-16) but the underlying plaintext storage — now an explicitly-flagged, still
  undecided privacy trade-off — is unchanged.

New work landed in the same window genuinely helped in places: a late-tip corporate-debit
feature (08-17) was independently re-verified clean rather than trusted from its own change-log;
a corporate coverage-floor CI gate now exists and blocks PRs (closing part of a baseline gap);
the admin document-reviewer modal gained real `role="dialog"`/`aria-modal`/Escape-key handling;
two real production bugs were caught and fixed in the same window this audit ran — a nightly
PIPEDA retention-purge job that had been silently failing since it was written, and a
corporate-allowance-reversal type that had been rejected by its own CHECK constraint since
migration 248.

**The single most consequential new finding this pass**: `docs/legal/insurance-coverage-periods.md`
— a rider/driver-facing legal draft — explicitly states insurance coverage "starts as soon as the
driver is assigned to your ride, even before they've accepted." The code does the opposite: Period 2
(TNC primary commercial coverage) opens only at `driver_accepted`, not at offer/claim time, because
the production batch-offer dispatch model structurally never writes a `driver_assigned` ride status
at all (`assign_driver_to_ride`, the only function that would, is confirmed dead code). This is no
longer just an engineering gap — it is a public-facing document promising a coverage rule the system
does not implement, and it must not go live without a legal/SGI decision one way or the other.

**Two structural (not one-off) findings, new this pass:**
1. AI guardrails: the root cause of the "driver full name leaks to the LLM provider" finding is not
   that one field — it's that **no tool result, from any tool, is ever PII-scrubbed** before
   re-entering the model's context or being returned via `/mcp`. Any future tool that surfaces free
   text inherits the same leak silently.
2. Observability: **no Prometheus metric exists for any ride-state transition after offer-acceptance**
   (arrival, trip start, completion, cancellation) — the two headline KPIs in CLAUDE.md (match rate,
   cancellation rate) are invisible to any dashboard or alert, visible only via a manual DB query.
   Compounding this, a `POST /admin/redis/flush-prefix` endpoint can wipe production
   rate-limit/OTP-lockout state with zero log line anywhere.

---

## Baseline reconciliation — all 17 ranked blockers from 2026-08-15

| # | 08-15 Finding | Status 08-18 | Evidence this pass |
|---|---|---|---|
| 1 | Period 2 insurance opens at accept, not claim | **FIXED 2026-08-18** | `backend/routes/rides/matching.py`'s `match_driver_to_ride` now opens Period 2 for every claimed driver immediately after the `ride_offers` insert succeeds — the claim/offer moment, not acceptance. `backend/routes/drivers/ride_flow.py`'s `decline_ride` now guards its Period-1 close on `set_driver_available`'s actual result (mirroring `process_expired_offer`'s existing guard) so a driver who went offline mid-offer doesn't get a false Period-1 reopen. `docs/legal/insurance-coverage-periods.md` and `terms-of-service.md` §13's existing "assigned... even before they've accepted" wording is now accurate as written — no copy change needed. See `docs/change-log/2026-08-18-period-2-insurance-timing-fix.md`. `assign_driver_to_ride` remains confirmed dead code (unrelated cleanup, not touched by this fix). |
| 2 | Driver can accept a ride while offline | **FIXED 2026-08-18** | `accept_ride` (`backend/routes/drivers/ride_flow.py`) now rejects with `DriverOfflineException` (already defined in `backend/utils/error_handling.py` for exactly this case, but never previously raised anywhere) when the already-fetched driver row shows `is_online=False`. Uses the row fetched at function entry — no extra round-trip. Fixes the concrete scenario dispatch/edge-case/security independently flagged: driver goes offline mid-offer, a stale queued push-notification tap or plain retry no longer strands the rider. See `docs/change-log/2026-08-18-driver-accept-while-offline-fix.md`. |
| 3 | Go-online never re-checks SK eligibility | **PARTIALLY FIXED 2026-08-19** | `routes/drivers/status.py` now re-checks license class (Class 5, or Class 1-4 with `drivers.sgi_approved`) and vehicle age (<10yr) alongside the existing document-expiry check, dark-shipped behind `app_settings.enforce_driver_eligibility_recheck` (default `false`) per the feature-flag gate. 3-year minimum experience check **not** implemented — no schema field records a license-issue/experience-start date; flagged as a required follow-up. See `docs/change-log/2026-08-19-go-online-sk-eligibility-recheck-fix.md`. |
| 4 | `planned_route_polyline` escapes 3-yr GPS purge | **FIXED 2026-08-19** | Migration 335 extends `purge_pii_retention()`'s existing Step A (which already nulls pickup/dropoff/`route_polyline`/`phase_polylines` at 3 years) to also null `planned_route_polyline`. Note: for legacy pre-v2 trips, this was the last surviving fallback the rider-app trip-history screen used to still draw a route line once the real GPS columns were already purged — those old trips will now show no route line (fare/driver/date unaffected); the admin-dashboard already degrades gracefully. Not yet applied to any DB (no `DATABASE_URL` in the fix's sandbox). See `docs/change-log/2026-08-19-gps-polyline-purge-fix.md`. |
| 5 | No consent-version on rider/driver signup | **FIXED 2026-08-19** | `verify_otp` and `firebase_auth_login` (`routes/auth.py`) now stamp `consent_version`/`consent_accepted_at` on the `users` insert (migration 334, additive/nullable). The corporate-portal company-email-OTP signup path (`send_company_email_otp`) was deliberately left untouched — out of scope, flagged not silently skipped. Migration not yet applied to any DB (no `DATABASE_URL` in the fix's sandbox) — required before/with deploy. See `docs/change-log/2026-08-19-consent-version-signup-fix.md`. |
| 6 | Driver full legal name sent to AI providers; `/mcp` unscrubbed | **FIXED 2026-08-18** | Two-part fix: (1) `ai/tools_rides.py::_driver_public` now uses `first_name_only` (this codebase's established driver-display-name-minimization convention, `utils/pii.py`, already used for the rider's name on the driver-facing WS/push path) instead of concatenating first+last — closes the actual leak at its source, since a plain name isn't regex-detectable by any scrub; (2) `ai/tools.py::_cap_result` — the single choke point both `execute_tool()` and `/mcp`'s `_call_tool` funnel through — now runs the new `scrub_pii_deep` (recursive value-only PII scrub, `ai/pii.py`) on every tool result, including `_client_action`, closing the general structural gap for every regex-detectable category (phone/email/GPS/card/SIN/postal) on every current and future tool. See `docs/change-log/2026-08-18-ai-tool-result-pii-scrub-fix.md`. |
| 7 | v2 GPS location-batch path skips integrity check | **FIXED 2026-08-19** | `persist_trip_location_batch` (`utils/breadcrumbs.py`) now runs a new `evaluate_gps_plausibility()` (pure, no-I/O sibling of `check_location_integrity()`) across every consecutive pair within the batch, plus the boundary pair from the driver's pre-batch last-known position. Note: a related, out-of-scope gap was found and flagged, not fixed — the WebSocket `location_batch` handler only checks the *last* point of a WS batch, so earlier WS-batch points still bypass the check; the audit named only the REST v2 path. See `docs/change-log/2026-08-19-v2-location-batch-spoofing-fix.md`. |
| 8 | Emergency contacts plaintext vs. doc promise | **STILL-OPEN, doc now honest** | `domain-safety.md` corrected 2026-08-16 to describe plaintext as shipped state — no longer a doc-mismatch, still an unresolved privacy decision |
| 9 | Safety check-in "sent" flag non-atomic | **STILL-OPEN** | `utils/safety_checkin_loop.py:104-127` unchanged; re-scoped this pass as duplicate-push risk only (escalation-insert path independently confirmed safe, cannot double-fire an incident) |
| 10 | Second device silently steals WS connection | **STILL-OPEN** | `socket_manager.py:64-66` — independently confirmed by 4 separate agents (realtime, edge-case, security, dispatch) |
| 11 | Unpinned GitHub Actions in deploy/security gates | **FIXED 2026-08-19** | All 5 identified unpinned refs across `deploy-metrics-agent.yml`, `bootstrap-metrics-agent.yml`, `claude-audit.yml`, `deploy-driver-play-testing.yml`, and `ci-guardrails.yml`'s trufflehog step now pinned to commit SHA (resolved via `git ls-remote`, trailing `# tag` comment matching existing repo convention). See `docs/change-log/2026-08-19-ci-actions-pinning-fix.md`. |
| 12 | ~~Admin actions with no audit trail~~ | **FIXED 2026-08-19** | `POST /admin/redis/flush-prefix` fixed first (see N11), then a systematic sweep of every `@router.post/put/patch/delete` across `routes/admin/*.py` found the real count was **50 endpoints across 13 files** (not the original "~12" estimate) — all now write an audit row via `log_admin_action()`, including `driver_appeals.py`'s resolve action. Read-only/dry-run endpoints deliberately excluded, documented not silently dropped. See `docs/change-log/2026-08-19-admin-audit-trail-sweep-fix.md`. |
| 13 | Document upload swallows supersede DB error | **STILL-OPEN** | `backend/documents.py:401-423` (renamed/relocated, same bug) unchanged; **NEW same-pattern instance** found in `routes/drivers/ride_cancel.py:182-185` (Stripe-hold-release DB write) |
| 14 | ~~Corporate PDF combined-tax line + UTC month bounds~~ | **FIXED 2026-08-19** | The combined-tax fallback label corrected from `"Tax (GST/PST)"` to plain `"Tax"` (matching `receipt_pdf.py`'s own fallback convention); the internal Sentry alert added between 08-15/08-18 is untouched and still fires. Month bounds now anchor to `America/Regina` (fixed UTC-6, no DST) instead of naive UTC, reusing the existing `STATEMENT_TZ` convention already used by `driver_statement.py`. See `docs/change-log/2026-08-19-corporate-statement-tax-timezone-fix.md`. |
| 15 | Admin surge endpoint missing `surge_source` stamp | **FIXED 2026-08-19** | `admin_update_surge_pricing` (`routes/admin/service_areas.py`) now stamps `surge_source="manual"` on both activate/deactivate branches, matching the correctly-wired sibling endpoint, so the auto engine can no longer silently overwrite an override made via this route. Confirmed still unreachable from the live admin-dashboard UI today (dead-code finding, not an active incident) but fixed since it's live/callable. See `docs/change-log/2026-08-19-admin-surge-source-stamp-fix.md`. |
| 16 | ~~5 unlabeled icon buttons + hand-rolled admin modal~~ | **FIXED 2026-08-19** | All 4 remaining unlabeled buttons now have `accessibilityLabel`s (`ride-status.tsx`, `report-safety.tsx`, `ride-detail.tsx`); the admin document-reviewer modal's remaining gap (focus-trap-in/restore-out) is now fixed — focus moves into the dialog on open and restores to the triggering element on close, with Tab/Shift+Tab cycling. See `docs/change-log/2026-08-19-icon-buttons-wav-toggle-focus-trap-fix.md`. |
| 17 | ~~Off-brand teal; payment error state indistinguishable from empty~~ | **FIXED 2026-08-19** | All off-brand teal instances (driver `(tabs)/index.tsx`, `subscription.tsx`, plus 2 more found via grep on the same literal color in `driver-arriving.tsx`/`ride-options.tsx`) now reuse `CustomAlert.tsx`'s fixed color tokenization instead of the ad hoc value. `payment-confirm.tsx` now renders a distinct "Couldn't load your cards — Tap to retry" state, mutually exclusive with the genuine empty-cards state. See `docs/change-log/2026-08-19-off-brand-teal-payment-error-state-fix.md`. |

**Also re-verified from the baseline's decision log:**
- `compliance` admin module (super-admin-only by omission) — still open, still regression-pinned by `test_admin_module_list_parity.py`, correctly deferred pending a human decision.
- Per-module coverage floors — a **new, blocking** gate now exists for `corporate_*.py` (`check_corporate_coverage_floor.py`, shipped since baseline) but the higher-priority `payments.py`/`fare_service.py`/`crypto.py`/`rides.py`/`dispatch_service.py` floors CLAUDE.md names explicitly still have no equivalent gate — only the 60% whole-repo aggregate is blocking.
- Migration CI CHECK B — **fixed as designed** (now a true hard-fail on a collision visible to the same PR's CI), but the residual cross-PR race window it cannot see is not hypothetical: duplicate prefixes grew from 3 (310, 313, and a partial count) to **7 distinct duplicates** across migrations 305–333, including a fresh 328×2 collision from 2026-08-18 itself.
- Money-auditor's "does the wallet→allowance→master→card cascade exist?" question — still unresolved, independently re-confirmed by both the money-auditor and corporate-billing-reviewer this pass: what exists is payment-method *choice* at booking time, not a per-ride fallback cascade across all four tiers. Needs a documentation correction or a real cascade decision.

---

## NEW findings, not in the 2026-08-15 baseline

| # | Finding | Agent | Where | Severity |
|---|---|---|---|---|
| N1 | Legal draft doc contradicts insurance-period code (see above) | insurance-period | `docs/legal/insurance-coverage-periods.md:40-45` | **FIXED 2026-08-18** — code now matches the doc, see ranked blocker #1 above |
| N2 | ~~`$0`-cost `first_ride_only` promo ride still satisfies rider-referral qualification — real wallet money farmable via throwaway phone numbers, no velocity cap~~ | fraud | `routes/users.py:846-848`, `routes/promotions.py:224-227`, `utils/referral_payout.py:298-311` | **FIXED 2026-08-19** — qualification now requires `grand_total > 0`; new rolling velocity cap (`app_settings.referral_payout_velocity_cap_per_day`, default 5/referrer/24h, admin-tunable, migration 336) mirrors the existing OTP-lockout Redis convention. See `docs/change-log/2026-08-19-referral-fraud-fix.md`. |
| N3 | ~~`deploy-metrics-agent.yml` unpinned `checkout@v7` + `flyctl-actions@master` with `FLY_API_TOKEN` in scope — missed by the C18b sweep~~ | cicd-infra | `.github/workflows/deploy-metrics-agent.yml:36,49` | **FIXED 2026-08-19** — see ranked blocker #11 above and `docs/change-log/2026-08-19-ci-actions-pinning-fix.md` |
| N4 | ~~2 more unpinned-action instances (`claude-audit.yml` literal `@master`; `bootstrap-metrics-agent.yml` partial fix)~~ | cicd-infra | `.github/workflows/claude-audit.yml:84`, `bootstrap-metrics-agent.yml:57` | **FIXED 2026-08-19** — same fix as N3 |
| N5 | Shared Toast/banner component (both mobile apps) has zero screen-reader wiring — the single error-announcement path for nearly every form/failure in the app | accessibility | `rider-app/components/Toast.tsx:106-122`, `driver-app/components/toastConfig.tsx` | High — blast radius is fleet-wide |
| N6 | ~~Driver-app WAV accessibility-declaration toggle doesn't announce as a switch or report checked state~~ | accessibility | `driver-app/app/driver/settings.tsx:217-250,327-342` | **FIXED 2026-08-19** — `settings.tsx`'s shared `renderToggle()` helper now has `accessibilityRole="switch"` + `accessibilityState={{checked}}`; this also fixed the same gap on the other 8 toggles the helper backs, an intentional additive side effect. See `docs/change-log/2026-08-19-icon-buttons-wav-toggle-focus-trap-fix.md`. |
| N7 | ~~3rd off-brand-teal instance on driver subscription screen~~ | design | `driver-app/app/driver/subscription.tsx:590-602` | **FIXED 2026-08-19** — see ranked blocker #23 / baseline #17 above |
| N8 | ~~AI guardrail gap is structural: no tool RESULT is ever PII-scrubbed anywhere in the codebase~~ | ai-guardrail | `ai/tools.py:226-240` (`_cap_result`), `ai/orchestrator.py:412-438` | **FIXED 2026-08-18** — see ranked blocker #6/#5 above and `docs/change-log/2026-08-18-ai-tool-result-pii-scrub-fix.md` |
| N9 | ~~Fare-estimate endpoint can hard-block up to 3.5s waiting on Google Directions before fallback — a real, code-confirmed >10x breach of the 300ms SLA target, by design, undocumented in the SLA table~~ | performance | `routes/rides/estimates.py:62,455` | **DOCUMENTED 2026-08-19** — behavior confirmed still live (widened 1.5s→3.0s on 2026-07-29 after a pricing-inconsistency incident); now noted in CLAUDE.md's SLA table as an exception. The underlying accept-vs-ceiling decision remains open (Product/Eng lead, due 2026-08-25) — not resolved by this doc-only change. |
| N10 | ~~Driver-location-write path (tightest 150ms SLA budget) does a byte-for-byte duplicate DB fetch inside a fully sequential await chain~~ | performance | `routes/drivers/location.py:456,487,540-542` | **FIXED 2026-08-19** — the v1 handler's second `drivers` row fetch (used only to check `is_online` before `mark_present`) removed, reusing the row already fetched earlier in the same request; no live-timing measurement taken, verified via a call-count assertion instead. See `docs/change-log/2026-08-19-location-write-duplicate-fetch-fix.md`. |
| N11 | ~~`POST /admin/redis/flush-prefix` — production cache/rate-limit/OTP-lockout wipe with zero audit-log row anywhere~~ | observability | `routes/admin/monitoring.py:509-539` | **FIXED 2026-08-19** — now writes an audit row via the existing `log_admin_action()` convention (act-then-log); the other ~12 endpoints in baseline #12 remain open. See `docs/change-log/2026-08-19-redis-flush-prefix-audit-log-fix.md`. |
| N12b | ~~No Prometheus metric on ANY ride-state transition after offer-acceptance~~ | observability | `routes/rides/lifecycle.py`, `routes/drivers/ride_complete.py`, `routes/rides/cancellation.py` | **FIXED 2026-08-18** — `spinr_rides_state_transition_total{to_status=...}` now emitted at every production-reachable write site (10 call sites across 6 files: `routes/drivers/ride_flow.py`, `routes/rides/lifecycle.py`, `routes/drivers/ride_complete.py`, `routes/rides/cancellation.py`, `routes/drivers/ride_cancel.py`, `routes/rides/matching.py`). See `docs/change-log/2026-08-18-ride-state-transition-metrics.md`. |
| N13 | ~~Ride-cancel Stripe-hold-release DB write swallows errors at `logger.warning` (same anti-pattern as baseline #13, new instance)~~ | observability/edge-case | `routes/drivers/ride_cancel.py:182-185` | **FIXED 2026-08-19** — now logs at `error` with the full exception (incl. `DatabaseError.details["original"]`), loud enough for `orphaned_hold_reconciler`'s existing self-heal to be noticed. See `docs/change-log/2026-08-19-ride-cancel-hold-release-error-fix.md`. |
| N14 | ~~Emailed/PDF receipts (7-year retained legal record) never show surge as a dollar line item — text footnote only, while the in-app UI (not retained) does show a real number~~ | money | `utils/receipt_pdf.py`, `utils/email_receipt.py:191-198` | **FIXED 2026-08-19** — both renderers now split the persisted already-surged distance/time fare into pre-surge display amounts plus a real `"Surge (X.XX×)"` dollar line, reusing the exact `_build_fare_breakdown` formula the in-app UI already used; footnote kept alongside. See `docs/change-log/2026-08-19-receipt-surge-line-item-fix.md`. |
| N15 | ~~2 new `round()`-on-Decimal slips in admin analytics/support screens, outside the pre-commit hook's protected files~~ | money | `routes/admin/support.py:179`, `routes/admin/rides.py:2470-2492` | **FIXED 2026-08-19** — both call sites (actual location: `rides.py:2498/2500`, a few lines past the cited range) now use `utils/money.to_decimal()` (`ROUND_HALF_UP`) instead of bare `round()` (banker's rounding). 4 more same-pattern instances found elsewhere in `rides.py` (lines 3031/3039/3181/3241) — not fixed in this pass, documented as a fast-follow. See `docs/change-log/2026-08-19-admin-decimal-round-convention-fix.md`. |
| N16 | Admin-RBAC frontend/backend module-string mismatches silently lock out legitimately-permissioned staff (not a leak — the opposite failure direction) | admin-rbac | Vehicle Types page keyed `"pricing"` vs backend `"vehicle_types"`; Audit Logs sidebar keyed `"settings"` vs backend `"audit"` | Low — access bug, not security hole |
| N17 | `stripe_import_router` mounted at a weaker module gate (`"drivers"`) than its documented super-admin-only sensitivity class; safe today only via handler-level check | admin-rbac | `routes/admin/__init__.py:161` | Low — single point of fragility |
| N18 | No test in the entire suite exercises an RLS policy from a real Postgres `anon`/`authenticated` role — 207 policy statements across 139 migrations have zero DB-level allow/deny coverage | test-coverage | fleet-wide | Medium — a broken policy would ship green |
| N19 | 2 production bugs independently caught and fixed in this same window (not this audit's finding, but worth recording): nightly `purge_pii_retention()` had been silently failing on Step D since it was defined (fixed 08-17/18, migrations 323/324); corporate-allowance-reversal type rejected by its own CHECK constraint since migration 248 (fixed via migration 319) | migration | migrations 319, 323, 324 | Informational — confirm the purge failure was ever loudly alerted, not just logged |

---

## Ranked blocker register (highest consequence first, owner + due date)

> Owners below are domain placeholders (Engineering — &lt;surface&gt;) pending assignment by
> whoever runs the actual sprint board; due dates are suggested working targets, not commitments —
> confirm both with the team lead before treating either as final.

| # | Finding | Agent(s) | Owner (suggested) | Due (suggested) |
|---|---|---|---|---|
| 1 | ~~Legal draft doc contradicts insurance-period code~~ | insurance-period | Legal/Compliance + Eng — Insurance | **RESOLVED 2026-08-18** (code fixed, doc already accurate) |
| 2 | ~~Period 2 insurance coverage opens at accept, not claim~~ | insurance-period, dispatch | Eng — Dispatch/Insurance | **RESOLVED 2026-08-18** — see `docs/change-log/2026-08-18-period-2-insurance-timing-fix.md` |
| 3 | ~~`deploy-metrics-agent.yml` unpinned actions with live `FLY_API_TOKEN` (regression)~~ | cicd-infra | Eng — Platform/CI | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-ci-actions-pinning-fix.md` |
| 4 | ~~Driver can accept a ride while offline~~ | dispatch, edge-case, security | Eng — Dispatch | **RESOLVED 2026-08-18** — see `docs/change-log/2026-08-18-driver-accept-while-offline-fix.md` |
| 5 | ~~AI tool-result PII scrub gap (structural) — driver name to LLM + /mcp~~ | ai-guardrail | Eng — AI | **RESOLVED 2026-08-18** — see `docs/change-log/2026-08-18-ai-tool-result-pii-scrub-fix.md` |
| 6 | ~~`$0` promo ride farmable for real referral payout, no velocity cap~~ | fraud | Eng — Growth/Fraud | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-referral-fraud-fix.md` |
| 7 | ~~v2 GPS location-batch path skips spoofing check~~ | fraud | Eng — Driver Location | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-v2-location-batch-spoofing-fix.md`; related WS-batch gap flagged as a new follow-up, not fixed |
| 8 | ~~`POST /admin/redis/flush-prefix` has zero audit trail on a destructive prod action~~ | observability | Eng — Platform | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-redis-flush-prefix-audit-log-fix.md` |
| 9 | ~~No metric on any post-acceptance ride-state transition (KPI blind spot)~~ | observability | Eng — Observability | **RESOLVED 2026-08-18** — see `docs/change-log/2026-08-18-ride-state-transition-metrics.md` |
| 10 | ~~Go-online never re-checks SK eligibility (license class/vehicle age/experience)~~ | regulatory | Eng — Driver Onboarding | **PARTIALLY RESOLVED 2026-08-19** — license class + vehicle age fixed (dark-shipped behind a flag), experience check still needs a schema decision; see `docs/change-log/2026-08-19-go-online-sk-eligibility-recheck-fix.md` |
| 11 | ~~`planned_route_polyline` escapes 3-yr GPS purge~~ | regulatory | Eng — Data/Retention | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-gps-polyline-purge-fix.md`; migration 335 not yet applied to any DB |
| 12 | ~~No consent-version on rider/driver signup~~ | regulatory | Eng — Auth + Legal | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-consent-version-signup-fix.md` |
| 13 | Emergency contacts stored plaintext (needs decision, not just fix) | safety-sos, security | Privacy/Legal | Decision by 2026-08-25 |
| 14 | ~~Safety check-in "sent" flag non-atomic (duplicate push risk)~~ | realtime | Eng — Safety | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-safety-checkin-atomic-claim-fix.md` |
| 15 | ~~Second device silently steals WS connection~~ | realtime, edge-case, security, dispatch | Eng — Realtime | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-ws-second-device-reconnect-fix.md`; client-side close-code handling not verified |
| 16 | ~~Shared Toast component has zero screen-reader wiring (fleet-wide blast radius)~~ | accessibility | Eng — Mobile/Shared UI | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-toast-screen-reader-fix.md`; no real VoiceOver/TalkBack device test performed |
| 17 | ~~Unpinned trufflehog inside the security gate itself + Expo/Play workflow~~ | cicd-infra | Eng — Platform/CI | **RESOLVED 2026-08-19** — same fix as #3, see `docs/change-log/2026-08-19-ci-actions-pinning-fix.md` |
| 18 | ~~Admin actions with no audit trail (~12 endpoints + 2 new)~~ | observability | Eng — Admin/Platform | **RESOLVED 2026-08-19** — actual scope was 50 endpoints across 13 files, all fixed. See `docs/change-log/2026-08-19-admin-audit-trail-sweep-fix.md` |
| 19 | ~~Document-upload / ride-cancel DB-error swallow (2 instances)~~ | edge-case, observability | Eng — Backend | **RESOLVED 2026-08-19** — both instances fixed: ride-cancel/Stripe-hold (see `docs/change-log/2026-08-19-ride-cancel-hold-release-error-fix.md`) and document-upload's `_supersede_and_flag_pending_review` (`backend/documents.py`, PR #4253, `docs/change-log/2026-08-19-documents-supersede-error-swallow-fix.md`) |
| 20 | ~~Corporate PDF combined-tax line + UTC month bounds~~ | corporate-reporting | Eng — Corporate Billing | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-corporate-statement-tax-timezone-fix.md`; month bounds now anchor to `America/Regina` (fixed UTC-6, no DST), reusing the existing `STATEMENT_TZ` convention |
| 21 | ~~Admin surge endpoint missing `surge_source` stamp~~ | surge | Eng — Pricing | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-admin-surge-source-stamp-fix.md` |
| 22 | ~~4 unlabeled icon buttons + WAV toggle accessibility gap~~ | accessibility | Eng — Mobile UI | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-icon-buttons-wav-toggle-focus-trap-fix.md`; no real VoiceOver/TalkBack device test performed |
| 23 | ~~Off-brand teal (3 instances) + payment error state~~ | design | Eng — Mobile UI | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-off-brand-teal-payment-error-state-fix.md`; 2 more instances found via grep beyond the audit's named 3 |
| 24 | ~~Fare-estimate 3.5s Directions wait undocumented in SLA table~~ | performance | Eng — Pricing + doc owner | **DOC RESOLVED 2026-08-19** — now documented in CLAUDE.md's SLA table; decision on ceiling vs. accepted-exception still open, Product/Eng lead, due 2026-08-25 |
| 25 | ~~Driver-location-write duplicate DB fetch~~ | performance | Eng — Driver Location | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-location-write-duplicate-fetch-fix.md`; no real &lt;150ms timing measurement taken, verified via call-count assertion |
| 26 | ~~Receipts never itemize surge as a $ line (7-yr retained record)~~ | money | Eng — Payments | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-receipt-surge-line-item-fix.md` |
| 27 | ~~13 background loops absent from the watchdog list~~ | realtime, observability | Eng — Platform | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-background-loop-watchdog-coverage-fix.md`; new startup self-check prevents recurrence |
| 28 | ~~Admin-RBAC frontend/backend module-string mismatches (access bugs)~~ | admin-rbac | Eng — Admin Dashboard | **RESOLVED 2026-08-19** — see `docs/change-log/2026-08-19-admin-rbac-module-string-mismatch-fix.md` |
| 29 | No RLS DB-role-level test coverage | test-coverage | Eng — Backend/QA | 2026-09-05 |
| 30 | ~~Money-path coverage floors unenforced beyond corporate_*~~ | test-coverage | Eng — Backend/QA | **RESOLVED 2026-08-19** — new blocking gate for `payments.py`/`fare_service.py`/`crypto.py`/`rides/`/`dispatch_service.py`, floors set just below each file's real measured coverage (blocking-but-passing today). **Escalation**: `payments.py` measured 86.1%, below its documented 90% target — flagged for a human decision (write tests or revise target), not silently patched. See `docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md`. |

---

## Decision log (needs a human, not just a fix — owner + due date required)

| Decision | Context | Owner | Due |
|---|---|---|---|
| ~~Period 2 timing: fix code to open at offer/claim, or get SGI/legal to accept "opens at accept"~~ — **RESOLVED 2026-08-18**: fixed code to open at offer/claim, matching the already-published `terms-of-service.md` §13 wording, so no legal/SGI decision was needed | insurance-period, dispatch | Legal/Compliance | Closed 2026-08-18 |
| Emergency-contact encryption: implement pgcrypto vs. accept plaintext (doc already corrected to state actual behavior) | safety-sos, security | Privacy/Legal | 2026-08-25 |
| `compliance` admin module: add to `AVAILABLE_MODULES` or switch to `require_super_admin` explicitly | admin-rbac (carried from 08-15) | Eng lead / Admin owner | 2026-08-29 |
| Inert `surge`/`pricing` grantable module strings — retire or wire up | admin-rbac (carried from 08-15) | Eng lead / Admin owner | 2026-08-29 |
~~Per-module coverage gate scope: extend `check_corporate_coverage_floor.py`'s pattern to `payments.py`/`fare_service.py`/`crypto.py`/`rides.py`/`dispatch_service.py`~~ — **ACTIONED 2026-08-19**, gate now exists; residual decision: `payments.py` measured 86.1%, below its documented 90% target — write tests to close the gap, or revise the target | test-coverage | Eng lead / QA owner | 2026-09-05 |
| Migration numbering collisions: 7 duplicate prefixes now exist (up from 3) — accept as documented convention or invest in a merge-queue/stronger CI check | migration | Eng lead | 2026-08-29 |
| Corporate payment-source cascade: correct `domain-payments.md`'s "wallet → allowance → master → card" language to describe payment-method choice, or build the literal cascade | money, corporate-billing | Product/Eng lead | 2026-08-29 |
| >2.5× admin surge override with justification is accepted/stored but always clamped to 2.5× at every fare-calc call site — decide whether the documented 1.0–10.0 capability should ever reach a rider, or correct CLAUDE.md | surge | Product/Legal | 2026-08-29 |
| Fare-estimate 3.5s Directions-wait trade-off: document as an accepted SLA exception, or add a hard latency-preserving ceiling and accept the undercharge-risk tail | performance | Product/Eng lead | 2026-08-25 |
| Confirm whether the silently-failed nightly `purge_pii_retention()` job (fixed 08-17/18) ever produced a loud, actionable alert during its failure window, or only a log line | migration, observability | Eng — Observability | 2026-08-22 |

---

## What was NOT verified (explicit boundary, every agent)

- **No runtime execution anywhere in this audit** — no code was run, no test suite executed, no live
  Supabase/Redis/Stripe/Twilio/FCM call made, no load or chaos test, no DAST/pentest scan, no
  screen-reader or contrast-ratio measurement. All 21 reports are static code + migration + config
  reading, cross-referenced against the 08-15 baseline's own text (re-verified against current
  source, not re-trusted).
- **E2 (load/chaos testing), E6 (DAST/pentest), E7 (backup-restore drills), N12 (visual/screen-reader
  regression tooling)** — standing gaps, not agent-shaped, tracked in `ACTION_ITEMS.md`, restated
  here per the master prompt's rule rather than silently absorbed.
- No agent queried a live/staging Supabase instance for row counts or dollar figures in this pass —
  this is Part A (whole-app fleet audit), not Part B (dual-run cutover); money-domain agents that
  needed dollar figures either had none to report or cited the 2026-08-15 baseline's filter-chained
  numbers without re-deriving them live.
- Frontend build verification (`npm run build` for `admin-dashboard`/`rider-app`/`driver-app`) was
  not run by any agent — all frontend findings are source-level reasoning, not a production build
  check.
- Coverage percentages (payments.py 96%, matching.py 89%, etc.) are the 2026-08-10 point-in-time
  measurements recorded in `ACTION_ITEMS.md`, not freshly re-measured — `pytest --cov` was not
  executed this session (no pytest installed in the audit sandbox).
- No agent re-verified every one of the other 20 agents' domains — each stayed in its assigned lane;
  cross-domain overlaps (e.g. WS second-device steal, flagged by 4 different agents) were each
  independently derived, not copied from one another.

---

## Rollup verdict

**FIX BLOCKERS.**

3 of 21 domains returned NEEDS HUMAN REVIEW (migration, admin-rbac, test-coverage) and 1 returned
SAFE TO LAUNCH (corporate-billing); the remaining 17 returned FIX BLOCKERS. Per the master prompt's
rule, the rollup is the worst verdict across all 21 and no single agent's verdict is softened here.
Nothing found in this pass is new-and-catastrophic — it is, overwhelmingly, **the same 17 blockers
from three days ago, still unfixed**, plus one real regression in CI pinning and a genuinely
consequential new finding (the legal-draft/code contradiction on insurance Period 2 timing) that
raises the priority of item #1 on the ranked list above everything else in this report.
