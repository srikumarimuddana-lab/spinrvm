# Documentation coverage matrix — one row per L2 epic

**Lane:** Tier B matrix (2 of 2) · **Wave:** post-W5 · **Date:** 2026-09-25 · **Mode:** report-only. No doc was edited to produce this file.

## §0 Method

- **Epic list**: the 18 L2 epics in `01-inventory/epics.md` §2 (the rapid baseline's 14 + AI
  Assistant, Engineering Gates & CI/CD, Shared Frontend Foundation & Design System, Platform
  Foundation & Schema, added by that lane for real code with no clean home in the original 14).
  Not re-derived; cited.
- **PRD section**: `grep -n "^## " docs/PRD.md` (VERIFIED, this session) gives 13 top-level
  sections; epics are matched to the "Functional requirements" (`:59-160`) subsection whose prose
  names the epic's domain, by direct read of that section's headings this session.
- **Runbook**: `ls docs/runbooks` (66 files, VERIFIED this session) matched by filename keyword to
  each epic. A runbook can serve more than one epic (e.g. `data-breach.md` serves both Auth/Privacy
  and Trust & Safety); listed under its primary subject.
- **ADR**: `ls docs/adr` (16 numbered ADRs + README, VERIFIED this session) matched by title.
- **Change-log entries**: `ls docs/change-log | grep -ciE '<pattern>'` (1,480 files total,
  VERIFIED this session) — a **filename keyword count**, not a semantic read of each file; the
  same over/under-count risk `01-inventory/epics.md` §0 names for its own keyword classifier
  applies here. Not additive across epics (a filename can match more than one pattern).
- **Support/FAQ content**: `docs/driver-faqs-saskatchewan.md` (driver-facing, file-based) plus the
  DB-seeded `faqs` table (rider + driver rows, seeded via `backend/migrations/230_seed_rider_faqs.sql`,
  `212_seed_saskatchewan_driver_faqs.sql`, and later consolidation migrations `322`/`327` — cited
  from `02-findings/support-kb.md` §0's own read list, not re-opened this session).
- **Training material**: checked for a dedicated onboarding/training doc per epic; `support-kb.md`
  §8 (cited) already establishes there is **no** `docs/support/` or `docs/onboarding/` directory
  and no staff-facing "start here" document anywhere in the repo — that absence is recorded once
  here and referenced per epic rather than re-stated 18 times.
- **Staleness**: an entry is marked **STALE** when a number, status word, or claim in the doc
  disagrees with a directly-read code fact from this session or a cited lane's direct read. Every
  stale row names the two disagreeing facts and their sources.
- Evidence labels: VERIFIED (this session) / cited-VERIFIED (another lane's direct read this audit,
  not re-run here) / INFERRED / ASSUMED / UNKNOWN.

## §1 Coverage table

| L2 Epic | PRD section | Runbook(s) | ADR(s) | Change-log (filename-keyword count) | Support/FAQ | Training material |
|---|---|---|---|---|---|---|
| Ride Booking & Matching | `docs/PRD.md:59-160` "Functional requirements" — booking/fare/surge subsections present (VERIFIED, heading read) | none dedicated (booking flow is covered piecemeal inside `dast-and-pentest.md`, `stripe-reconciliation.md`) | 016 (measured-distance-single-source-of-truth) | ≈211 (`ride\|book\|fare\|surge\|scheduled`) | Rider FAQ rows: fare, surge, promo, cancellation (seeded, cited support-kb.md §4) | None (no dedicated booking-flow staff doc) |
| Ride Fulfillment | same PRD section, dispatch/arrival subsections | `driver-not-receiving-rides.md`, `trip-route-integrity.md`, `websockets.md` | none dedicated | ≈33 (`dispatch\|offer\|matching\|arriv`) | Rider FAQ: WAV, service animal (seeded); driver FAQ: going-online troubleshooting | None |
| Ride Completion & Payments | PRD "Functional requirements" payments subsection | `stripe-reconciliation.md`, `stripe-webhook-failure.md`, `ledger-alerts.md`, `payment-dispute-evidence.md` | none dedicated | ≈83 (`payment\|stripe\|wallet\|receipt\|tip`) | Rider FAQ: refunds, wallet top-up, promo (seeded) | None |
| Driver Earnings & Payouts | PRD payments subsection (shared with above, no dedicated earnings heading found — INFERRED gap) | none dedicated to payout-only ops (folded into `stripe-reconciliation.md`) | none | ≈42 (`earning\|payout\|t4a`) | Driver FAQ: payout timing (deliberately vague, per `support-kb.md` §3 row 15 — "by design, not a gap") | None |
| Corporate / B2B Billing | no dedicated PRD heading found this session (INFERRED gap — CARTO-001 already flags the admin/corporate PRD-footprint gap generally) | `corporate-compensating-transaction.md`, `corporate-guest-booking.md` | none dedicated | ≈111 (`corporate`) | No dedicated corporate-admin FAQ found | None |
| Authentication & Authorization | PRD "Personas and non-negotiables" (`:50-59`) touches auth expectations, no dedicated functional-requirements auth heading | `auth-tokens.md`, `otp-lockout-false-positive.md`, `pii-key-rotation.md`, `secret-rotation.md` | 005 (jwt-firebase-dual-auth), 015 (web-token-storage) | ≈37 (`auth\|otp\|jwt\|session`) | none rider/driver-facing (auth is infrastructure, not a support topic) | None |
| Admin Dashboard & Operations | no dedicated PRD heading — `01-inventory/epics.md` §8 (CARTO-001, cited) already found the admin surface has only an "eight-bullet PRD footprint" against 415 mapped code units | `admin-pwa.md`, `admin-rollback.md`, `full-app-audit.md` | 008 (report-branding), 013 (zod-incremental-risk-first-form-validation) | ≈322 (`admin`) — the largest bucket, consistent with `00-history.md`'s hotspot list naming `admin-dashboard/src/lib/api.ts` (234 commits) and `routes/admin/drivers.py` (143) as the two busiest files repo-wide | n/a (internal tool) | **None found** — `support-kb.md` §8's "no start-here doc" gap applies to admin staff too, not just support agents |
| Safety, Trust & Fraud | PRD "Personas and non-negotiables" safety language; no dedicated functional-requirements safety heading | `sos-incident.md`, `security-incident.md`, `data-breach.md` | none dedicated | ≈52 (`safety\|sos\|insurance`) | Rider+driver FAQ: safety features, WAV, service animal | `docs/trauma-support.md` **referenced by `sos-incident.md`'s own checklist and does not exist** (SKB-003, cited support-kb.md §2 — see §2 below) |
| Notifications & Messaging | no dedicated PRD heading | none dedicated | none | ≈38 (`notif\|push\|fcm`) | none dedicated (notification copy reviewed by support-kb.md §7, not a support topic per se) | None |
| Maps & Routing | no dedicated PRD heading | `route-pipeline-diagnostics.md` | 016 (shared with Ride Booking) | ≈70 (`map\|routing\|geocod\|h3`) | none | None — `01-inventory/epics.md` §7 (CARTO-003, cited) independently finds this epic has **no owning `spinr-*` review agent** despite gating WAV dispatch |
| Promotions & Loyalty | no dedicated PRD heading | none | none | ≈20 (`promo\|referral\|loyalty`) | Rider FAQ: promo code use (seeded) | None |
| Observability & Monitoring | PRD "Non-functional / SLA requirements" (`:160-204`) | `synthetic-monitoring.md`, `on-call.md`, `capacity-scaling.md` | 004 (redis-in-process-fallback), 010 (metrics-aggregation-and-alerting), 014 (distributed-tracing-deferred) | ≈57 (`observab\|metric\|monitor\|alert`) | n/a | None |
| Integrations & Webhooks | no dedicated PRD heading | `supabase-down.md`, `redis-down.md`, `stripe-webhook-failure.md` | 012 (ai-egress-trust-boundaries, AI-vendor specific) | ≈16 (`webhook\|zoho\|integrat`, narrowest bucket — likely undercounted by this keyword set) | none | None |
| Legacy Import & Data Migration | no dedicated PRD heading (product-facing PRD would not cover a one-time migration epic — reasonable scope choice) | `legacy-migration-playbook.md`, `legacy-backfill-scripts-rollout.md`, `legacy-booking-import-2026-08-22-batch.md`, `migration-driver-rider-repair-scope.md`, `safe-data-migration-playbook.md`, `dual-run-driver-roster-policy.md`, `old-app-decommission.md` — the best-documented epic by runbook count | none dedicated | ≈134 (`legacy\|import\|migrat`) — second-largest bucket after Admin | none (internal-only epic) | None dedicated, but the runbook density above substitutes for a training doc in this one epic |
| AI Assistant | no dedicated PRD heading (`docs/PRD.md` predates the AI surface — `01-inventory/epics.md` §2 notes this epic is "not in PRD" at all) | none dedicated | 012 (ai-egress-trust-boundaries) | ≈26 (`ai-\|ai_\|assistant`) | AI assistant answers ARE the support surface for other epics (grounded on `faqs` table, per support-kb.md §6) — but has no FAQ/doc *about itself* | None |
| Engineering Gates & CI/CD | PRD "CI/CD requirements" (`:213-219`) and "Testing requirements" (`:204-213`) — the one non-product epic with a real PRD section | none in `docs/runbooks/` (CI process docs live in `.github/` comments and `backend/migrations/CLAUDE.md`-style per-directory files instead) | 011 (flag-read-failure-semantics) | ≈91 (`ci\|workflow\|gate\|pipeline`) | n/a | None |
| Shared Frontend Foundation & Design System | no dedicated PRD heading (`01-inventory/epics.md` §2 notes this epic is "not in PRD" at all, "added for `shared/`") | none | 002 (expo-react-native), 013 (zod-incremental-risk-first-form-validation, admin-side) | ≈38 (`shared\|design-system\|theme`) | n/a | None — `01-inventory/epics.md` §7 (CARTO-002, cited) independently finds **no agent owns `shared/` logic correctness**, "root of 3 live cross-app bugs" |
| Platform Foundation & Schema | no dedicated PRD heading (catch-all epic, per `01-inventory/epics.md` §2's own description) | `supabase-region-migration.md`, `migration-conflict-detection.md`, `migration-tool-order.md` | 001 (supabase-postgres), 003 (fastapi-backend), 006/007 (railway/fly deployment), 009 (data-transfer-background-export) | ≈6 (`schema\|platform`, narrowest bucket, likely undercounted since most schema-relevant change-log entries would key on a specific table/feature name instead) | n/a | `backend/migrations/CLAUDE.md` functions as this epic's one real training doc (append-only rule, naming convention, duplicate-prefix history) — the strongest "training material" found for any epic in this table |

**Six epics have no dedicated PRD subsection at all** (Corporate, Admin, Notifications, Maps &
Routing, Integrations, and the two non-product epics AI Assistant and Shared Frontend, which the
inventory lane already flagged as "not in PRD"). This matches `01-inventory/epics.md`'s own
finding that orphan rows cluster in admin-dashboard (415 mapped units on an eight-bullet PRD
footprint) and `shared/` (93 files, "2-sentence PRD footprint").

**Zero epics have a dedicated staff-facing training document.** The one partial exception,
`backend/migrations/CLAUDE.md`, is a developer-convention doc, not an onboarding doc for a new
hire in any role (support, ops, admin). `support-kb.md` §8 (cited) already establishes this for
the support/ops case specifically; this table confirms the same absence holds for every other
epic checked this session.

## §2 Stale docs — numbers or statuses that disagree with code

| # | Doc claim | Code/live fact | Disagreement | Source |
|---|---|---|---|---|
| 1 | CLAUDE.md "Background task safety": "42 background asyncio loops" | `backend/core/background_loop_registry.py`'s `LOOP_CATALOG` has **45 entries** (44 loops + the watchdog itself); `01-inventory/epics.md` §0 independently transcribed the same 45 by opening the file in full | CLAUDE.md's count has drifted at least 5 times per `00-history.md` HIST-005 | cited-VERIFIED (`01-inventory/epics.md` §0; `02-findings/reliability.md` §0) |
| 2 | CLAUDE.md "PR review handling": "Codex has been silent since 30 July" | `matrices/risk-register.md` RR-04 (cited, itself citing a 2026-09-25 GitHub search): "Codex resumed on 2026-09-16 after 48 silent days... commented on 8 PRs created since 2026-08-01, the first #5480 on 2026-09-16; #5748 carried a full review with 8 findings acted on" (`docs/change-log/2026-09-24-pr5748-codex-review-fixes.md`) | CLAUDE.md and `ACTION_ITEMS.md` C9 still say silent — a live, actionable status claim in the checked-in guidance file is wrong as of this session | cited-VERIFIED (`00-history.md` erratum line 9; `matrices/risk-register.md` RR-04) |
| 3 | `.claude/context/domain-dispatch.md:40` documents a `driver_assigned \| backend → rider` WebSocket event | `07-reverification.md` DISPATCH-001 (CONFIRMED, re-checked this audit): grepped every `send_`/`emit`/`publish`/`notify`/`rider` token in the offer loop and found **no rider-keyed send at offer time** — `rider_{` occurs only 3 times in `matching.py`, none in the offer path | The domain-dispatch doc promises a rider-facing event the code does not emit — `08-hostile-review.md` §1.3 frames this as an open product decision (emit vs. correct the doc), not yet resolved either way | cited-VERIFIED |
| 4 | "admin JWT fully trusted" (CLAUDE.md's JWT trust model section, paraphrased) | `00-history.md` HIST-005 (cited): "the 'admin JWT fully trusted' sentence is false in 3 files" | Not independently re-derived which 3 files this session — flagged as INFERRED-from-citation, not re-verified | cited (not independently confirmed) |
| 5 | `docs/data-classification.md:20,53,168` states GPS trace retention as 2 years | `backend/migrations/296` (`purge_pii_retention()`) sets the GPS anonymize horizon at **3 years** (`296:152`) | Three-way doc-vs-SQL mismatch, already tracked as COMP-007 in this audit and open since 2026-08-17 per its own checklist note | cited-VERIFIED (`02-findings/compliance.md` COMP-007) |
| 6 | `docs/legal/legal-text-publication-checklist.md` — `privacy-policy.md` row: "DV-8 30-day deletion job — NOT built" | `core/lifespan.py:513` → `purge_pii_retention()` Step N (migration 296:415-470) scrubs first/last name, email, profile image and deletes saved addresses at 30 days — **already built** | The checklist row is stale relative to the code; `compliance.md` §3 flags this as "NEWLY CLOSED vs the checklist text" | cited-VERIFIED (`02-findings/compliance.md` §3, `privacy-policy.md` block) |
| 7 | Same checklist — `background-check-consent.md` row: "Draft" | Migration 406 seeds the driver-only `legal_documents` row for this text; `driver-app/app/become-driver.tsx:133-142,596-612` presents it in the onboarding wizard | The document is Published, not Draft, per `compliance.md` §3's direct read | cited-VERIFIED |
| 8 | `ACTION_ITEMS.md`'s own tracker has 9 duplicated numeric ids (A34, A40, C100, C111, C112, C118, C129, C13, G2) per `00-history.md` §6 | n/a — the tracker is itself the doc being checked for staleness | Breaks the "grep before filing" de-duplication convention this same file asks every audit lane to follow; `RR-07` (risk-register.md, cited) scores this MEDIUM, S×B×L=50 | cited-VERIFIED |
| 9 | `docs/runbooks/capacity-scaling.md` | described by `matrices/risk-register.md` RR-90 (cited) as documenting "the fleet replaced on 2026-09-16" | Capacity numbers in the runbook predate the current fleet topology | cited (not independently re-opened this session) |
| 10 | `audit-framework/regulatory-matrix.md` — driver age "≥21 (SK policy)" | `backend/routes/drivers/status.py:704` and `profile.py:47` enforce **≥18** | Doc-vs-code age mismatch; also carries an audit-log retention mismatch (3y in the matrix vs 7y in SQL) | cited-VERIFIED (`02-findings/compliance.md` COMP-016) |
| 11 | `docs/audit/clean-sheet/04-blueprint.md` §1/§3 — "8 pending [migrations] incl. RLS enable" | `08-hostile-review.md` I-1 (cited): the RLS enable for `settings` (C43) is **live but untracked** in the repo's own migration-status text, and migration 450 is a 9th pending file (SEC-R10-008) | Blueprint text inherited `00-history.md`'s pre-correction wording and was not updated after the correction landed | cited-VERIFIED |
| 12 | `04-blueprint.md` cards 2/8 — "BUILD trip PIN" | A 4-digit rider-visible pickup code already gates trip start (`backend/dependencies/__init__.py:78-80`, checked with lockout at `routes/drivers/ride_flow.py:1165-1210`, shown on 3 rider screens) | TSF-010/BENCH-001's original absence claim and the blueprint's build recommendation are both refuted by direct code read; `08-hostile-review.md` §1.1 is the correction record | cited-VERIFIED |

## §3 What could not be checked this pass

- **A semantic (not filename-keyword) read of any of the 1,480 change-log files** — the §1 counts
  are keyword hits against filenames only, per the method note; a file whose content is relevant
  to an epic but whose filename doesn't carry a matching keyword is undercounted, and the reverse
  (a filename keyword match whose content is unrelated) is overcounted. Neither direction was
  corrected by reading file bodies.
- **Whether every runbook cited in §1 is itself current** — only the two staleness items already
  flagged by other lanes (§2 #6, #9) were checked; the other 64 runbooks were not spot-read this
  session for their own internal staleness.
- **admin-dashboard's own design-system doc** (`docs/design/` equivalent, referenced by the
  `spinr-admin-design-system` skill) was not opened this session — not counted in either the Shared
  Frontend or Admin epic rows above.
- **Whether a support/onboarding doc exists outside the repo** (e.g. in an HR or wiki system) —
  `support-kb.md` §12 already flags this same limitation for `docs/trauma-support.md` specifically;
  it applies equally to the "no training material found" claim across all 18 epic rows in §1. Doc
  absence in this repo is not proof of absence everywhere.
