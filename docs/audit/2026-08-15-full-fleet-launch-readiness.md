# Spinr Full-Fleet Launch-Readiness Audit — whole app, all 21 reviewers

**Date:** 2026-08-15 · **Scope:** the entire application (not a diff) — run at user request days before public go-live, complementing the dual-run cutover audit (`docs/audit/2026-08-15-dual-run-cutover/`, ACTION_ITEMS A34), whose findings are referenced, not repeated.
**Method:** all 21 `spinr-*` reviewer agents dispatched independently and in parallel per `/full-audit`, each against its own domain across the whole codebase, audit-only. Findings below preserve each agent's blockers/warnings with `file:line` evidence; VERIFIED/INFO detail is condensed. Every agent stated a NOT-verified boundary; the recurring ones: static code reading only (no runtime execution, no load test, no live DB/Stripe exercise, no screen-reader/contrast tooling — E2/E6/N12 remain non-agent-shaped gaps).

```
SPINR FULL-FLEET AUDIT — whole app (launch-readiness)
=====================================================
Agents dispatched: 21/21

── MONEY & BUSINESS LOGIC ──────────────────────
  spinr-money-auditor                NEEDS HUMAN REVIEW (payment-source cascade question; no blockers)
  spinr-surge-auditor                FIX BLOCKERS (admin surge endpoint missing surge_source stamp)
  spinr-corporate-billing-reviewer   SAFE TO LAUNCH
  spinr-fraud-auditor                FIX BLOCKERS (v2 GPS path skips integrity check)
  spinr-corporate-reporting-reviewer FIX BLOCKERS (combined-tax PDF line; UTC month bounds)

── CORE PLATFORM ───────────────────────────────
  spinr-security-auditor              SAFE TO LAUNCH (launch-day checklist items)
  spinr-dispatch-reviewer             FIX BLOCKERS (accept without is_online re-check)
  spinr-insurance-period-auditor      FIX BLOCKERS (Period 2 opens at accept, not claim)
  spinr-safety-sos-reviewer           FIX BLOCKERS (emergency contacts plaintext vs doc promise)
  spinr-realtime-reliability-reviewer FIX BLOCKERS (safety check-in non-atomic sent flag)
  spinr-migration-reviewer            NEEDS HUMAN REVIEW (dup prefixes; CI gate warn-only)
  spinr-admin-rbac-reviewer           NEEDS HUMAN REVIEW (compliance gate implicit; inert module strings)
  spinr-cicd-infra-reviewer           FIX BLOCKERS (unpinned actions in deploy/security gates)
  spinr-edge-case-reviewer            FIX BLOCKERS (WS multi-device overwrite; doc-upload swallow)

── COMPLIANCE & QUALITY ────────────────────────
  spinr-regulatory-compliance-checker FIX BLOCKERS (go-online eligibility gates; polyline retention; consent versioning)
  spinr-accessibility-reviewer        FIX BLOCKERS (5 unlabeled icon buttons; hand-rolled modal)
  spinr-design-consistency-reviewer   FIX BLOCKERS (off-brand teal; payment error state)
  spinr-ai-guardrail-reviewer         FIX BLOCKERS (driver full name to provider; /mcp unscrubbed)
  spinr-performance-sla-reviewer      NEEDS LOAD TEST (no confirmed breach)
  spinr-observability-reviewer        FIX BLOCKERS (admin actions without audit rows; no cancellation metric)
  spinr-test-coverage-reviewer        NEEDS HUMAN REVIEW (per-module coverage floors unenforced)

VERDICT: FIX BLOCKERS
```

---

## Plain-English executive summary

Nothing found says "the app will fall over at launch" — the money core (corporate billing, fare math, Stripe idempotency, auth/security) audited **clean**, repeatedly described by independent agents as unusually well-hardened. The blockers cluster into three kinds:

1. **Regulatory/insurance classification gaps** that predate launch but become real liability the day real riders board: the insurance Period 2 window, go-online eligibility gates that exist as data but not as code, a route-geometry column that escapes the 3-year GPS purge, and no consent versioning for riders/drivers.
2. **A dozen small, high-confidence code fixes** — most are one-file changes: the offline-driver accept race, the v2 GPS integrity bypass, the AI driver-name egress, the surge `surge_source` stamp, the safety-check-in atomic flag, unpinned CI actions, unlabeled accessibility buttons, the off-brand teal, the payment-error state, admin audit-log rows.
3. **Decisions only a human can make**: encrypt emergency contacts vs. correct the doc; Period 2 legal sign-off vs. engineering fix; the `compliance` RBAC gate; the per-module coverage gate; and the standing E2 (load test) / C5 (Railway drift) / C21 (required checks) items that repeat across multiple agents' reports.

## Ranked blocker list (highest consequence first)

| # | Finding | Agent(s) | Where | Effort |
|---|---|---|---|---|
| 1 | **Period 2 insurance coverage opens at accept, not at claim** — production batch-offer dispatch never passes `driver_assigned`; every ride has a claimed-but-unaccepted window classified Period 1 (contingent) instead of Period 2 (TNC primary). Written policy contradicts code. `assign_driver_to_ride` is dead code with no period write. | insurance-period + dispatch (independent) | `routes/drivers/ride_flow.py:365-372`, `services/dispatch_service.py:512-535` | Eng fix at claim time OR explicit SGI/legal sign-off + doc update |
| 2 | **Driver can accept a ride while offline** — accept path never re-checks `is_online`; app-killed driver claims the ride, rider stranded in `driver_accepted`. | dispatch | `routes/drivers/ride_flow.py:46-350` | Small |
| 3 | **Go-online never re-checks SK eligibility** (license class 5, vehicle age <10, 3-yr experience) — stored but ungated; CRC+VSC collapsed into one expiry field. | regulatory | `routes/drivers/status.py:210-360` | Medium |
| 4 | **`planned_route_polyline` escapes the 3-yr GPS purge** — full pre-trip route retained indefinitely (its sibling `route_polyline` is purged correctly). | regulatory | migrations 50…285 purge set vs migration 100 | Small (add to purge) |
| 5 | **No consent-version on rider/driver signup** (corporate has it) — no mechanism to force re-consent on material T&C change. | regulatory | `routes/auth.py` | Medium |
| 6 | **Driver's full legal name sent to AI providers** when a rider asks "who's my driver"; `/mcp` surface has no PII-scrub layer at all. | ai-guardrail | `ai/tools_rides.py:74-77`, `ai/mcp_server.py:175` | Small (drop field; add scrub) |
| 7 | **v2 GPS location-batch path skips `check_location_integrity`** — spoofable position lands in live marker + append-only insurance-period location history (v1/WS paths check it). | fraud | `routes/drivers/location.py:109-170` | Small |
| 8 | **Emergency contacts stored plaintext** while `domain-safety.md` promises pgcrypto — implement encryption or correct doc with PIPEDA sign-off. | safety-sos | `routes/users.py:794-806`, migrations 08/120 | Decision + medium |
| 9 | **Safety check-in "sent" flag written non-atomically after the push** — Redis blip → duplicate "are you okay?" pushes / duplicate safety incidents. | realtime | `utils/safety_checkin_loop.py:104-127` | Small |
| 10 | **Second device silently steals the WS connection** — same-account login overwrites the socket key; first device stops receiving ride/chat/safety events with no notice. | edge-case | `socket_manager.py:64-66` | Medium |
| 11 | **Unpinned GitHub Actions in the deploy + security gates** (`flyctl-actions@master` with deploy secrets in scope; `trufflehog@main` inside the security gate; Expo/Play workflow tags). | cicd-infra | `deploy-fly.yml:63`, `ci-guardrails.yml:345`, `deploy-driver-play-testing.yml:55-61` | Small (pin SHAs) |
| 12 | **Admin actions with no audit trail**: ToS/Privacy edits, mass push/SMS blasts, incentives/faqs/vehicle-fleet CRUD — pattern exists in 27 other admin files, not applied here. | observability | `routes/admin/legal_documents.py:35`, `messaging.py:277`, `incentives.py` | Small each |
| 13 | **Document upload swallows a supersede DB error** (warning-and-continue) → duplicate active driver documents on retry. | edge-case | `documents.py:250-259` | Small |
| 14 | **Corporate PDF can ship GST/PST as one combined line** (logged, not blocked); statement month bounds computed in UTC not SK time (rides shift months). | corporate-reporting | `utils/corporate_statement_pdf.py:152-159`, `routes/corporate_company.py:804-816` | Small–medium |
| 15 | **Admin surge endpoint never sets `surge_source='manual'`** — auto engine overwrites an admin override within 2 min (endpoint live, UI not yet wired). Above-2.5× override is decorative everywhere (doc vs code). | surge | `routes/admin/service_areas.py:908-937` | Small |
| 16 | **Five unlabeled icon-only buttons** on high-traffic screens + hand-rolled admin modal without focus management. | accessibility | rider `ride-status.tsx:451`, `report-safety.tsx:56`, driver `ride-detail.tsx:211`, admin `document-reviewer.tsx:192-235` | Small each |
| 17 | **Off-brand teal on 3 launch screens**; payment screen renders fetch-failure identically to "no cards." | design | `driver (tabs)/index.tsx:758`, rider `payment-confirm.tsx:112-119` | Small |

## Decisions needed (no code until a human calls it)

- Period 2 window (item 1): engineering fix vs. documented regulatory acceptance — **legal call**.
- Emergency-contact encryption (item 8): implement vs. doc-correct + sign-off — **privacy call**.
- `compliance` admin module: make `require_super_admin` explicit or add to `AVAILABLE_MODULES` (currently super-admin-only *by omission*; allow-listed in the parity test, not fixed). Same review for inert `surge`/`pricing` module strings.
- Per-module coverage gates: CI enforces only a global 60% floor — CLAUDE.md's 90/80/70 money-path floors are unenforced; a payments-coverage regression can pass silently.
- Migration CI CHECK B is **warn-only**, contradicting CLAUDE.md's "blocks them" — duplicate prefixes 310×2 and 313×3 already merged; decide fix-forward vs. renumber.
- Money-auditor's open question: does the documented "rider wallet → allowance → master wallet → card" per-ride cascade exist anywhere, or does the doc describe payment-method *choice*? Allowance→master half verified airtight; other legs not found.
- AI1b: Redis-fail-open on the AI daily cost cap — still an undecided, documented trade-off.

## Clean bills of health (verified, not assumed)

- **Corporate billing**: every balance mutation routes through the locked SECURITY DEFINER RPCs; no bypass writes; TOCTOU/dedup races closed; surge-on-corporate blocked both at estimate and booking.
- **Security**: no pre-auth or cross-user path found; JWT trust model, OTP hashing/lockout, refresh rotation with theft detection, Sentry scrubbing all verified. Launch-day checklist: confirm real production secrets; complete Firebase App Check console registration before flipping; add tighter rate limits on payment/wallet routes.
- **Ride state machine**: every transition an atomic conditional write with pre-state filter; one-active-ride backed by a DB constraint; offer timeout idempotent.
- **Fare/receipt/Stripe**: Decimal discipline, single `claim_stripe_event` gate, receipts line-itemized (minor: `round(float())` on stored surge multiplier in 2 files outside the pre-commit hook's watch list).
- **WS layer**: auth-first, per-user Redis rate limit, cross-replica fan-out with degrade-don't-crash fallback; client backoff jittered on both apps.
- **Test suite**: no test theater found in any sampled area; webhook/state-machine/fare-branch coverage real.
- **SOS**: never-auto-dial-911 verified everywhere; degraded-auth window correct; strongest-audited surface (modulo item 8).

## Watch items (not blockers)

- 13 background loops absent from the watchdog list (incl. safety_checkin, driver_claim_reaper, route_finalizer) — one-line additions.
- No ride-cancellation metric (KPI tracked only via DB query); many `logger.error` calls without `extra={domain}` ship to Sentry untagged — systemic, wants a lint rule.
- Location-write path: redundant driver-row fetch + long sequential await chain on a 150ms budget; admin live-map reads the whole drivers table unbounded — profile under real load.
- Rider gets no "driver GPS stale" banner; booking idempotency key regenerated per retry (currently saved by the active-ride constraint); forced-upgrade gate can't reach pre-header builds; corporate-account admin edits last-write-wins.
- RLS is only ever tested at the application layer (service-role connection bypasses RLS); no test connects as anon/authenticated Postgres role.
- Cross-referenced standing items this fleet re-confirmed: C5 (Railway standby drift — cutover-week go/no-go check), C21 (required-checks list stale vs ~57 real checks), E2/E6/E7/N12 (load test, DAST, backup drill, visual regression — need real tooling, not agents).

## What was NOT verified (fleet-wide)

No agent executed code, ran the test suite, measured latency or contrast, exercised a screen reader, connected to live Stripe, or load-tested anything. Several agents sampled rather than exhaustively read their domain (each report's NOT-verified section in the session transcript states its exact boundary). The E2 load test and E6 DAST remain the two largest classes of launch risk no static audit can retire.
