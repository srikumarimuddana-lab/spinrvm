# 01 — Inventory: L0–L4 Tree (R2 Cartographer, Wave W0)

> Companion file: `docs/audit/clean-sheet/traceability.csv` (1,788 rows, L2–L7 columns:
> `epic,feature,story_id,story,scenario,code_unit,path,test,evidence,status,finding_ids,maturity,gap`).
> This document is the narrative index into that CSV, not a duplicate of it — counts here are
> derived from the CSV by script and can be regenerated from it.

---

## §0 Method and evidence scope

**What was enumerated by script (exhaustive over the stated glob, VERIFIED to exist, INFERRED
in its epic/story classification):**
- `backend/routes/**/*.py` route decorators — regex-extracted every `@<name>router.<method>(...)`
  and `@app.<method>(...)` call across `backend/routes/`, `backend/routes/admin/`,
  `backend/routes/rides/`, `backend/routes/drivers/` → **693 endpoints**. First pass used a regex
  that only matched a literal `router` variable and non-empty path strings; this missed every
  file using `api_router`/`admin_router`/`admin_auth_router`/`offer_analytics_router` (a majority
  of admin routes) and every `@router.post("")` root endpoint. Caught and fixed mid-run (534 →
  693 endpoints) — flagged here because it is exactly the kind of silent undercount this audit
  exists to catch in the app's own code, and the fix is worth recording as a lesson for future
  static-recon passes on this repo.
- `backend/services/*.py` → 61 files (`__init__.py` excluded), one row per **file**, not per
  function-group. The charter asked for "service function-group" granularity; file-level was
  chosen for time-budget reasons across 61 files averaging several hundred lines each. Where a
  file visibly serves two unrelated concerns this is called out in §4, but most files are already
  single-purpose by name (`corporate_wallet_winddown_service.py`, `t4a_annual_job.py`, etc.), so
  file-level is a reasonable proxy for this pass.
- `backend/core/background_loop_registry.py` `LOOP_CATALOG` → **45 loops** (44 + the watchdog),
  transcribed directly from the file (VERIFIED, file opened and read in full) — this supersedes
  CLAUDE.md's stale "42" figure and confirms the task brief's "45 entries incl. watchdog."
- `backend/migrations/*.sql` → **553 files**, one row per file, filename-only classification
  (files were **not** opened/read for content — 553 files is outside this lane's time budget;
  classification is a keyword match on the filename alone, e.g. `corporate`, `insurance`,
  `driver_`). Applied/pending status against `schema_migrations` was **not** checked (no DB
  connection in this environment) — every migration row's `test` column is `NONE` by convention,
  not because tests were checked and found absent.
- `rider-app/app/**/*.tsx` → 48 screens; `driver-app/app/**/*.tsx` → 42 screens.
- `rider-app/hooks/`, `driver-app/hooks/` → 47 hook files combined.
- `shared/**/*.{ts,tsx}` (excluding `__tests__`) → 93 files (components, hooks, api, store,
  utils, types).
- `admin-dashboard/src/app/dashboard/**/page.tsx` → 63 pages; `admin-dashboard/src/components/`
  → 69 files. **Known data-quality note:** the components sweep used a blanket `find … -name
  "*.tsx" -o -name "*.ts"` and picked up at least one co-located test file
  (`geofence-map.render.test.tsx`) as if it were a component code unit — not filtered out before
  the CSV was frozen. Low blast radius (a handful of rows at most) but noted rather than silently
  left in.
- `.github/workflows/*.yml` → 52 files, mapped to the **Engineering Gates & CI/CD** epic (added
  to the rapid-baseline's 14-epic list — see below — specifically for this unit type, per this
  lane's charter).

**What was opened and read directly (VERIFIED beyond path/name):** `CLAUDE.md`; the audit
prompt's §2/§4/§8; `docs/PRD.md` (full, 271 lines); `docs/audit/module-map.md` (full);
`docs/known-forks.md` (full — all 4 fork pairs transcribed into the CSV as `sibling-missing`
rows); `docs/audit/clean-sheet/rapid-baseline-2026-09-24/standards-verified.md` §6 (draft L2
epic list) and its finding cards (34 finding IDs, `RECURRENCE-*`/`DRIFT-*`/`MONEY-*`/etc.,
parsed for `finding_ids` linkage by exact-basename match against every code-unit path — a
best-effort join, not a semantic one: a shared basename like `earnings.py` links a finding to
*a* row with that basename, not necessarily the exact line the finding cites); `backend/core/
background_loop_registry.py` (full); `.claude/agents/*.md` frontmatter (all 28, for §7).

**What was classified by keyword/path heuristic only (INFERRED, not opened):** the epic/feature/
story assignment for the great majority of the 1,788 rows. A first-match, ordered-keyword
classifier assigns each code unit to one of 18 L2 epics (the rapid baseline's 14 + **AI
Assistant**, **Engineering Gates & CI/CD**, **Shared Frontend Foundation & Design System**, and
**Platform Foundation & Schema** — added because real code exists in each and none of the
original 14 fits cleanly) and then to the first PRD-derived L4 story (see §2) whose keyword list
matches. This is fast and repeatable but has a known, disclosed failure mode: **greedy first-match
keyword collision**. Example: `backend/services/corporate_wallet_winddown_service.py` contains
both `corporate_wallet` (an earlier story's keyword, "Allowance cap & ledger") and the substring
that would map to "Winddown/offboarding" — the classifier picks the first list hit, so the file
lands under Allowance instead of Winddown even though the code plainly implements winddown.
**Every row in `traceability.csv` marked `status=gap` was spot-checked or is suspected to be this
exact artifact, not a real missing-code finding** — see §5 for the spot-check results. Treat
`gap` rows as "candidate, needs a human or a second, smarter pass to confirm," not as verified
absent code. `orphan` rows are more trustworthy (a code unit either has a plausible PRD-derived
story attached or it doesn't; the risk there is under-attribution, not false-positive gaps).

**Story IDs are this lane's own construction, not sourced from the PRD.** `docs/PRD.md` states
requirements as prose/bullets with no story IDs and no story-per-bullet granularity fine enough
to match ~1,800 code units. Rather than invent 1,800 ad hoc IDs, ~90 stories were hand-written
from PRD §"Functional requirements" content (one per named capability) and assigned `S-<epic
slug>-<nn>` IDs per the charter's instruction for un-ID'd stories. This means `story_id`/`story`
in the CSV represent this lane's *reconstruction* of the PRD's intent at feature granularity —
evidence-labelled `INFERRED` throughout, never `VERIFIED`, and should be read as a starting
scaffold for W1's Product Strategist to correct, not a settled requirements register.

---

## §1 L0/L1

**L0 Vision** (`docs/PRD.md` "Vision"): *"Spinr is the driver-first alternative to existing
Canadian ride-sharing platforms. Drivers keep 100% of every fare. Riders get transparent pricing,
reliable service, and genuine safety features. Saskatchewan goes first; the model scales
nationally."*

**L1 Objectives/KPIs** — the CLAUDE.md KPI table, transcribed, with measurement status noted:

| Metric | Target | Measured today? |
|---|---|---|
| Match rate (rides requested → driver accepted) | ≥ 85% | Yes — `GET /api/admin/analytics/overview` |
| Rider cancellation rate | ≤ 8% | Yes — same endpoint |
| Driver cancellation rate | ≤ 3% | Yes — same endpoint |
| Driver utilization (on-trip time / online time) | ≥ 55% | Yes — same endpoint |
| P95 dispatch latency (offer → accept) | < 2 s | Yes — `spinr_dispatch_offer_to_accept_duration_ms` + `GET /api/admin/analytics/dispatch-latency` |
| P95 fare calc latency | < 300 ms | Yes — `spinr_fare_calc_duration_ms` (see also the documented 3.5 s worst-case exception in CLAUDE.md "Performance SLAs") |
| Payment success rate | ≥ 99% | Yes — `spinr_payment_settlement_total{outcome=...}` |
| Weekly active driver retention (week-over-week) | ≥ 80% | **No** — CLAUDE.md's own "Known gap" note: nothing computes a rolling week-over-week active-retention number; migration 423's `driver_retention_w1_pct` is a *cohort* metric, not the rolling metric this row literally describes. Open backlog, not done. |
| Safety incident rate | < 1 / 10k rides | Not verified this pass — no endpoint/metric name found in the surfaces enumerated here; flagged for W2 Trust/Safety to confirm, not asserted absent |
| Support ticket response (P1) | < 2 h | Not verified this pass — likely lives in the Zoho Desk integration (`services/zoho_desk_*.py`, `routes/admin/support_tickets.py`), not independently confirmed to *measure* the 2h SLA rather than just hold ticket data |

Additionally, three of the CLAUDE.md **Performance SLA** table's 8 rows have no emitting metric
at all per the rapid baseline's `OBS-002` (VERIFIED there, not re-verified here): driver-location-
write duration, auth-token-refresh duration, and Stripe-webhook-processing duration are published
SLA commitments with no `_duration_ms` histogram backing them. This is a second, distinct
"documented but unmeasured" pattern from the retention-KPI gap above — see `OBS-002`'s own
evidence for the grep results.

---

## §2 The L2 → L3 → L4 tree

18 L2 epics (rapid baseline's 14 + 4 added this pass — **AI Assistant**, **Engineering Gates &
CI/CD**, **Shared Frontend Foundation & Design System**, **Platform Foundation & Schema** — each
justified by real, enumerated code with no clean home in the original 14), 90 L3 features, 90 L4
stories (one per feature; `story_id` format `S-<epic-slug>-<nn>`). Counts in the third column are
**mapped code units found for that story** (from the CSV, `status=mapped`, deduplicated by
`story_id`) — `0` means either a true gap or (far more likely per §0/§5) a keyword-collision
artifact routing that code elsewhere.

### Ride Booking & Matching (11 features)
| Feature | Story | Mapped units |
|---|---|---|
| Fare estimation | S-book-01 | 4 |
| Ride creation | S-book-02 | 1 |
| Offer matching | S-book-03 | 30 |
| Offer acceptance/decline | S-book-04 | 0 (see §5) |
| Surge pricing | S-book-05 | 5 |
| Scheduled rides | S-book-06 | 5 |
| Accessibility/WAV dispatch | S-book-07 | 1 |
| Address/pickup validation | S-book-08 | 11 |
| Multi-stop routing | S-book-09 | 4 |
| Ride history & detail | S-book-10 | 5 |
| Fare split | S-book-11 | 1 |

### Ride Fulfillment (11 features)
| Feature | Story | Mapped units |
|---|---|---|
| Driver arrival tracking | S-fulfil-01 | 0 (see §5) |
| Real-time location (WS) | S-fulfil-02 | 20 |
| Pickup confirmation | S-fulfil-03 | 2 |
| In-ride state machine | S-fulfil-04 | 4 |
| Navigation integration | S-fulfil-05 | 0 (see §5) |
| In-ride chat | S-fulfil-06 | 12 |
| Trip sharing | S-fulfil-07 | 5 |
| Lost & found | S-fulfil-08 | 18 |
| Driver online/available status | S-fulfil-09 | 4 |
| Driver-side ride flow | S-fulfil-10 | 13 |
| Route deviation / GPS integrity | S-fulfil-11 | 2 |

### Ride Completion & Payments (8 features)
| Feature | Story | Mapped units |
|---|---|---|
| Fare finalization | S-pay-01 | 1 |
| Wallet settlement | S-pay-02 | 24 |
| Stripe charge/capture | S-pay-03 | 1 |
| Tip | S-pay-04 | 5 |
| Receipt generation | S-pay-05 | 7 |
| Rating | S-pay-06 | 1 |
| Reconciliation | S-pay-07 | 11 |
| Refunds/disputes on fares | S-pay-08 | 8 |

### Driver Earnings & Payouts (5 features)
| Feature | Story | Mapped units |
|---|---|---|
| Earnings calculation | S-earn-01 | 14 |
| Period statements | S-earn-02 | 9 |
| T4A/tax generation | S-earn-03 | 9 |
| Per-driver payouts | S-earn-04 | 34 |
| Batch cash-out | S-earn-05 | 0 (see §5 — real code exists, collided into S-earn-04) |

### Corporate / B2B Billing (7 features)
| Feature | Story | Mapped units |
|---|---|---|
| Company registration | S-corp-01 | 34 |
| KYB verification | S-corp-02 | 6 |
| Allowance cap & ledger | S-corp-03 | 15 |
| Per-ride corporate charge | S-corp-04 | 11 |
| Membership lifecycle | S-corp-05 | 3 |
| Invoicing/subscriptions | S-corp-06 | 8 |
| Winddown/offboarding | S-corp-07 | 0 (see §5 — real code exists, collided into S-corp-03) |

### Authentication & Authorization (6 features)
| Feature | Story | Mapped units |
|---|---|---|
| Rider/driver signup | S-auth-01 | 0 (see §5 — same code as OTP, S-auth-02) |
| Email/phone OTP | S-auth-02 | 2 |
| JWT/session | S-auth-03 | 14 |
| RBAC / admin access tiers | S-auth-04 | 0 (see §5) |
| Session management | S-auth-05 | 2 |
| Profile management | S-auth-06 | 29 |

### Admin Dashboard & Operations (12 features)
| Feature | Story | Mapped units |
|---|---|---|
| Driver management | S-admin-01 | 57 |
| Rider management | S-admin-02 | 8 |
| Financial dashboard | S-admin-03 | 27 |
| Dispute resolution | S-admin-04 | 53 |
| Data transfer / export | S-admin-05 | 13 |
| Settings/flags | S-admin-06 | 12 |
| Compliance export | S-admin-07 | 0 (see §5) |
| Bulk operations / fleet | S-admin-08 | 15 |
| Maintenance mode | S-admin-09 | 6 |
| Venues | S-admin-10 | 0 (see §5 — real code, epic-classifier routed it to Maps & Routing instead) |
| Admin messaging/broadcast | S-admin-11 | 0 (see §5 — real code, epic-classifier routed it to Notifications) |
| Legal content mgmt | S-admin-12 | 6 |

### Safety, Trust & Fraud (6 features)
| Feature | Story | Mapped units |
|---|---|---|
| SOS / emergency | S-safety-01 | 20 |
| Insurance period tracking | S-safety-02 | 7 |
| License/ID/background checks | S-safety-03 | 5 |
| RLS policies | S-safety-04 | 14 |
| Fraud detection | S-safety-05 | 0 (see §5) |
| Dispute/appeal handling | S-safety-06 | 5 |

### Notifications & Messaging (4 features)
| Feature | Story | Mapped units |
|---|---|---|
| FCM push offers | S-notif-01 | 5 |
| SMS OTP | S-notif-02 | 0 (see §5) |
| In-app notifications | S-notif-03 | 16 |
| Notification throttling/retry | S-notif-04 | 0 (see §5 — `push_retry` loop exists, collided into S-notif-01) |

### Maps & Routing (4 features)
| Feature | Story | Mapped units |
|---|---|---|
| Google Maps integration | S-maps-01 | 5 |
| Distance/ETA | S-maps-02 | 3 |
| Service-area geometry | S-maps-03 | 37 |
| H3 heatmapping | S-maps-04 | 4 |

### Promotions & Loyalty (5 features)
| Feature | Story | Mapped units |
|---|---|---|
| Coupon/promo codes | S-promo-01 | 23 |
| Driver quests/bonuses | S-promo-02 | 26 |
| Rider loyalty | S-promo-03 | 7 |
| Referral codes | S-promo-04 | 22 |
| Marketing/subscriptions | S-promo-05 | 38 |

### Observability & Monitoring (4 features)
| Feature | Story | Mapped units |
|---|---|---|
| Sentry error tracking | S-obs-01 | 4 |
| Prometheus metrics | S-obs-02 | 0 (see §5) |
| Loop watchdog | S-obs-03 | 2 |
| Health checks | S-obs-04 | 0 (see §5 — `/health` exists, epic-classifier defaulted it to Admin) |

### Integrations & Webhooks (4 features)
| Feature | Story | Mapped units |
|---|---|---|
| Stripe webhooks | S-integ-01 | 10 |
| Zoho Desk sync | S-integ-02 | 19 |
| Stripe Connect/payout sync | S-integ-03 | 10 |
| Stripe KYC/Identity | S-integ-04 | 2 |

### Legacy Import & Data Migration (6 features)
| Feature | Story | Mapped units |
|---|---|---|
| Driver import | S-legacy-01 | 8 |
| Rider import | S-legacy-02 | 1 |
| Earnings/payout backfill | S-legacy-03 | 3 |
| Data-quality / crosswalk | S-legacy-04 | 12 |
| Booking/wallet/address import | S-legacy-05 | 9 |
| Legacy consent/badge | S-legacy-06 | 3 |

### AI Assistant (2 features — not in PRD, see §4)
| Feature | Story | Mapped units |
|---|---|---|
| Rider AI assistant | S-ai-01 | 2 |
| AI admin console/guardrails | S-ai-02 | 10 |

### Engineering Gates & CI/CD (6 features — not in PRD as a product epic, added for `.github/workflows/`)
| Feature | Story | Mapped units |
|---|---|---|
| CI test/lint gates | S-ci-01 | 3 |
| Security/audit gates | S-ci-02 | 6 |
| Migration safety check | S-ci-03 | 1 |
| Visual regression | S-ci-04 | 0 (see §5 — runs inside `ci.yml`, collided into S-ci-01) |
| Deploy pipeline | S-ci-05 | 5 |
| Review automation | S-ci-06 | 1 |

### Shared Frontend Foundation & Design System (5 features — added for `shared/`)
| Feature | Story | Mapped units |
|---|---|---|
| Shared API client | S-shared-01 | 13 |
| Shared state/store | S-shared-02 | 3 |
| Shared UI components | S-shared-03 | 20 |
| Shared hooks | S-shared-04 | 10 |
| Shared types | S-shared-05 | 6 |

### Platform Foundation & Schema (1 feature — catch-all for domain-generic migrations)
| Feature | Story | Mapped units |
|---|---|---|
| Core schema/infra | S-plat-01 | 4 |

---

## §3 Inventory counts table

| Surface | Units | mapped | orphan | gap | sibling-missing |
|---|---:|---:|---:|---:|---:|
| `backend/migrations/*.sql` | 553 | 242 | 311 | 0 | 0 |
| `backend/routes/admin/**` (endpoints) | 382 | 268 | 114 | 0 | 0 |
| `backend/routes/*.py` top-level (endpoints) | 192 | 167 | 24 | 0 | 1 |
| `shared/**` | 93 | 54 | 38 | 0 | 1 |
| `backend/routes/drivers/**` (endpoints) | 82 | 80 | 2 | 0 | 0 |
| `driver-app/**` (screens + hooks) | 72 | 19 | 51 | 0 | 2 |
| `rider-app/**` (screens + hooks) | 70 | 21 | 46 | 0 | 3 |
| `admin-dashboard/src/components/**` | 69 | 15 | 54 | 0 | 0 |
| `admin-dashboard/src/app/dashboard/**/page.tsx` | 63 | 6 | 57 | 0 | 0 |
| `backend/services/*.py` | 61 | 43 | 18 | 0 | 0 |
| `.github/workflows/*.yml` | 52 | 16 | 36 | 0 | 0 |
| background loops (`LOOP_CATALOG`) | 45 | 31 | 14 | 0 | 0 |
| `backend/routes/rides/**` (endpoints) | 38 | 34 | 4 | 0 | 0 |
| (PRD stories with zero code hit anywhere) | 16 | 0 | 0 | 16 | 0 |
| **Total** | **1,788** | **996** | **769** | **16** | **7** |

Endpoint counts are per-HTTP-route (method+path), not per file, for `rides/`, `drivers/`, and
`admin/` per the charter; all other route files also got endpoint-level rows (a superset of the
charter's minimum — file-level rows were judged less useful once endpoint extraction worked).

---

## §4 Orphans

769 orphan rows (43% of all rows) — a code unit whose path/name did not match any of the 90
hand-written PRD-derived stories. Grouped by epic, with a one-line hypothesis each. Evidence
throughout is INFERRED (path/name only) unless noted.

| Epic | Orphan count | Hypothesis |
|---|---:|---|
| Admin Dashboard & Operations | 215 | **Undocumented scope**, not dead code. PRD.md's "Admin Dashboard" section is 8 bullets; the admin surface has 382 real endpoints + 63 pages + 69 components + 60 route files (`data_transfer_*`, `legacy_*`, `migration_*`, `stripe_*`, `sgi_forms.py`, `export_approvals.py`, `driver_dormancy.py`, `driver_distance.py`, `pre_launch_flag.py` — none named in PRD at all). Example orphan pages: `support-tickets/trends`, `stripe-events`, `records`, `monitoring/redis`. This is the single largest orphan cluster and the strongest signal that **PRD.md is a launch-era document that has not kept pace with the admin surface's actual growth** — a finding, not a criticism of the code. |
| Ride Fulfillment | 124 | Mixed: some genuine PRD gaps in specificity (PRD never mentions "lost & found" as its own capability, yet `routes/rides/lost_found.py` + `routes/drivers/*` + both apps' lost-and-found screens total 18+ code units) and some keyword-collision under-attribution (see §5 — several of these are real features whose keyword just didn't fire). |
| Platform Foundation & Schema | 84 | Migrations whose filename gives no clear domain signal even after two classification passes (schema-table-add/index/constraint changes named after a column, not a feature — e.g. `01_add_driver_user_id.sql`). **Expected and largely benign** — not every migration should map to a product story; this is the honest "infra plumbing" bucket, not a finding of missing requirements. |
| Ride Booking & Matching | 77 | Similar pattern to Ride Fulfillment — dispatch/matching internals (`dispatch_candidates.py`, offer-decision plumbing) that implement a *mechanism* PRD describes only at the outcome level ("driver receives offers"), so sub-mechanisms don't individually match a story. |
| Ride Completion & Payments | 45 | Same pattern — payment-plumbing files (webhook dispatch internals, ledger projection internals) below the granularity PRD states requirements at. |
| Shared Frontend Foundation & Design System | 38 | **Genuine undocumented scope.** PRD.md's only line on `shared/` is "API client wraps axios... shared types and stores" — two sentences for what is actually 93 files across components/hooks/api/store/utils/types. The shared design-system/component layer (`spinr-rider-driver-design-system` skill exists, confirming this is known internally) has no PRD-level requirement register at all. |
| Engineering Gates & CI/CD | 36 | **By construction** — this epic and its 6 stories were added by this lane specifically to give `.github/workflows/` a home; PRD.md has zero CI/CD requirements at the individual-workflow level (only a short "CI/CD requirements" section naming test suites, not the 52 actual workflow files). Not a code defect — a PRD-scope gap for engineering process, which arguably shouldn't be in a *product* PRD at all; flagged as an open question in §10. |
| Corporate / B2B Billing | 35 | Mostly service-layer internals below PRD's stated granularity (offboarding/suspension sub-flows, Stripe-identity glue). |
| Safety, Trust & Fraud | 28 | Mix of GPS-integrity/route-deviation internals (mechanism, not outcome-level PRD text) and compliance-export endpoints with no PRD line. |
| Observability & Monitoring | 26 | **Expected** — CLAUDE.md documents Observability Conventions in detail but PRD.md's Observability section is 3 short bullets; most Sentry/metrics call-site files were never going to match a PRD story 1:1. |
| Legacy Import & Data Migration | 20 | Sub-tooling below the stated 6 stories' granularity (individual backfill/reconciliation scripts). |
| Authentication & Authorization | 11 | Admin-specific auth sub-flows (MFA enrollment, session listing) that PRD's rider/driver-focused Auth section doesn't name. |
| Notifications & Messaging | 9 | Sub-mechanism files (marketing-push, live-activity internals). |
| Maps & Routing | 8 | Sub-mechanism files (geo_utils internals, route reconstruction). |
| AI Assistant | 7 | Same "epic added by this lane" pattern as CI/CD — `backend/ai/**` wasn't enumerated separately from `routes/ai.py`/`routes/admin/ai_console.py` in this pass (out of the charter's named globs), so this count undercounts the true AI surface; flagged in §9. |
| Integrations & Webhooks | 5 | Sub-mechanism files. |
| Driver Earnings & Payouts | 1 | Smallest orphan cluster — this epic's 5 stories cover its code unusually well, likely because PRD.md and the rapid baseline both treat driver earnings as a first-class, well-specified epic (money-critical surface, extra scrutiny already applied historically per `docs/change-log`'s A25–A33 legacy-earnings work). |

**Top-line finding:** the orphan pattern is dominated by two distinct causes that should not be
conflated — (1) **PRD-granularity mismatch** (PRD states an outcome, code has many files
implementing the mechanism — expected, not a gap) and (2) **genuine PRD-vs-code drift** where an
entire surface (admin dashboard's 60 route files, `shared/`'s 93 files, all 52 CI/CD workflows)
has grown well past what any product doc names. (2) is the actionable finding; W1's Product
Strategist should decide whether PRD.md needs an admin/shared-surface addendum or whether these
were always meant to be internal/engineering scope outside a product PRD.

---

## §5 Gaps

16 CSV rows carry `status=gap` (a story with zero matching code unit anywhere). **This lane spot-
checked 4 of the 16 by direct grep and found real, existing code in all 4 cases** — the "gap" was
a classifier artifact (a broader, earlier-declared story's keyword substring-matched the same
file first). Given a 100% false-positive rate on the spot-checked sample, **none of the 16 should
be treated as a confirmed missing-requirement finding without a human or second-pass check**:

| Story | Spot-checked? | Result |
|---|---|---|
| S-earn-05 Batch cash-out | Yes | `auto_payout (1h, Sundays)` loop + `314_auto_payout_and_instant_kill_switch.sql` exist; code matched the earlier, broader "Per-driver payouts" (S-earn-04) story instead because its `payout` keyword is a substring of `auto_payout`. |
| S-corp-07 Winddown/offboarding | Yes | `backend/services/corporate_wallet_winddown_service.py` exists; matched "Allowance cap & ledger" (S-corp-03) instead because the file path also contains `corporate_wallet`. |
| S-admin-11 Admin messaging/broadcast | Yes | `backend/routes/admin/messaging.py` exists; the epic-level classifier (a separate, coarser pass than the story-level one) routed the whole file to **Notifications & Messaging** before the Admin-epic story list was ever searched. |
| S-obs-04 Health checks | Yes | `backend/routes/main.py`'s `@api_router.get("/health")` exists (and `server.py`'s own `@app.get("/health")` per that file's own code comment); the epic classifier had no rule for `main.py` and defaulted it to Admin Dashboard & Operations, where "Health checks" isn't a story. |
| S-book-04, S-fulfil-01, S-fulfil-05, S-auth-01, S-auth-04, S-admin-07, S-admin-10, S-safety-05, S-notif-02, S-notif-04, S-obs-02, S-ci-04 | No — not individually re-checked | Suspected same pattern based on the 4/4 confirmed false-positive rate above; each is plausible on its face to have real code (e.g. `routes/drivers/offer_decisions.py` obviously implements S-book-04 "Offer acceptance/decline" — its file was classified under Ride Fulfillment's driver-ride-flow story instead of Ride Booking's offer-acceptance story, another epic-boundary artifact). |

**What this section is not:** a confirmed list of unbuilt PRD requirements. Zero such confirmed
gaps were found by this lane. That is itself worth stating plainly rather than padding the report
with false positives — Wave W1 (Product Strategist) should treat this list as "re-run with a
smarter matcher or check by hand," not as backlog items.

---

## §6 Sibling parity holes

**From `docs/known-forks.md` (4 registered, intentional forks — all VERIFIED, file read in
full):**

1. `shared/components/CarMarker.tsx` ↔ `driver-app/components/CarMarker.tsx` — diverged 5×
   historically; a mechanical parity guard (`CarMarkerParity.test.ts`) landed 2026-09-12 covering
   prop-level API surface only, not internal logic.
2. `driver-app/app/driver/notifications.tsx` ↔ `rider-app/app/notifications.tsx` — diverged twice
   by omission (2026-08-18, 2026-09-14); no mechanical guard exists yet. Additional undecided
   divergence: driver-app uses the shared `notificationQueries.ts` hook, rider-app hand-rolls the
   same logic locally.
3. `rider-app/app/referral.tsx` + `routes/users.py` ↔ `driver-app/app/driver/referral.tsx` +
   `routes/drivers/referrals.py` — a dead, mismatched `referral_link` field on both sides was
   silently wrong until removed 2026-09-14; no guard exists.
4. `backend/routes/auth.py` ↔ `backend/routes/admin/auth.py` — IP-resolution plumbing drifted
   (rider/driver kept `get_remote_address()`, recording the Fly proxy IP into 7-year audit rows)
   until fixed 2026-09-21 (PR #5654); a source-text guard test now pins the fix.

**From the automated rider-app/driver-app screen-name sweep (INFERRED, name-based only — not a
substitute for #1–4's actual code-diff analysis):** comparing screen basenames for
wallet/notification/chat/otp/profile/referral/rating/legal/help/support/settings/sos/earnings-
themed screens found:
- `rider-app/app/wallet.tsx` and `rider-app/app/support.tsx` have no name-matching driver-app
  screen. **Not necessarily a gap** — drivers may reach wallet/support functionality through
  differently-named screens (`driver-app/app/earnings.tsx`-family screens, or a different
  navigation entry point) that this name-only sweep can't detect; flagged for a human to confirm
  whether drivers genuinely lack a wallet/support surface or whether the screen is just named
  differently.
- `driver-app/app/help.tsx` has no name-matching rider-app screen; same caveat — rider-app may
  route help through `legal.tsx`/`safety-hub.tsx`/`support.tsx` instead.

This name-only method is a coarse first pass, not the actual UX-parity analysis the "Driver
Journey Owner"/"Rider Journey Owner" W1 lanes should do — it exists here to seed their starting
point, not to replace their review.

---

## §7 Unowned surfaces

Cross-referencing the 18-epic inventory against all 28 `.claude/agents/spinr-*.md` frontmatter
descriptions (read in full; VERIFIED). A surface is "covered" if an agent's description names it
or a domain it clearly subsumes, per `spinr-agent-fleet-strategist.md`'s own stated coverage
definition (loaded and followed here).

| Epic / surface | Owning agent(s) | Verdict |
|---|---|---|
| Ride Booking & Matching, Ride Fulfillment (state machine, dispatch) | `spinr-dispatch-reviewer` | Covered |
| Ride Completion & Payments, Driver Earnings & Payouts | `spinr-money-auditor` | Covered |
| Corporate / B2B Billing (wallet/allowance) | `spinr-corporate-billing-reviewer` | Covered |
| Corporate reporting/exports | `spinr-corporate-reporting-reviewer` | Covered |
| Authentication & Authorization (general) | `spinr-security-auditor` | Covered |
| Admin RBAC/module-grant workflow specifically | `spinr-admin-rbac-reviewer` | Covered (narrow) |
| Safety, Trust & Fraud — SOS | `spinr-safety-sos-reviewer` | Covered |
| Safety, Trust & Fraud — insurance periods | `spinr-insurance-period-auditor` | Covered |
| Safety, Trust & Fraud — referral/promo/GPS fraud | `spinr-fraud-auditor` | Covered |
| Surge pricing (part of Ride Booking) | `spinr-surge-auditor` | Covered |
| Notifications & Messaging (copy) | `spinr-notification-ux-reviewer` | Covered (UX-copy angle only, not delivery-mechanism correctness) |
| Notifications & Messaging (support ticket / Zoho integration specifically) | `spinr-observability-reviewer` | Covered (explicit carve-out in its description) |
| Observability & Monitoring (general) | `spinr-observability-reviewer` | Covered |
| Engineering Gates & CI/CD | `spinr-cicd-infra-reviewer` | Covered |
| Legacy Import & Data Migration | `spinr-migration-reviewer` (explicitly names `legacy_*.py`, `*_import_service.py` in its description) | Covered |
| AI Assistant | `spinr-ai-guardrail-reviewer` | Covered |
| Compliance (Saskatchewan/PIPEDA) | `spinr-regulatory-compliance-checker` | Covered |
| Legal document readiness | `spinr-legal-readiness-reviewer` | Covered |
| Test coverage (cross-cutting) | `spinr-test-coverage-reviewer` | Covered |
| UI/UX quality (rider/driver/admin) | `spinr-design-consistency-reviewer`, `spinr-accessibility-reviewer`, `spinr-ui-ux-critic` | Covered (visual/UX layer) |
| **Maps & Routing** (`maps_proxy.py`, `service_areas.py`, `h3_heatmap.py`, `geo_utils.py`, venues) | **NONE found** | **UNOWNED.** No agent description names Maps, service areas, geo/H3, or venues. Not itself money/auth/safety-critical in the obvious sense, but service-area geometry gates WAV dispatch (a Saskatchewan legal requirement) and the Directions-timeout fare-estimate exception lives here too — arguably should be covered by `spinr-dispatch-reviewer` or a new narrow agent. Flag as MEDIUM priority per the fleet-strategist's own severity rule (touches a regulatory-adjacent flow but isn't itself a money/auth/safety core path). |
| **Promotions & Loyalty** (general functional correctness — quests, loyalty, subscriptions) | Partial — `spinr-fraud-auditor` covers the *abuse* angle only | **PARTIALLY UNOWNED.** Nobody reviews whether a promo/quest/loyalty feature works correctly, only whether it's exploitable. LOW/INFO per the fleet-strategist's own triage rule (not money/auth/rides/safety-critical). |
| **Shared Frontend Foundation & Design System** — functional correctness of `shared/api`, `shared/store`, `shared/hooks` (as opposed to visual consistency) | **NONE found** | **UNOWNED.** The three UI-quality agents (`design-consistency`, `accessibility`, `ui-ux-critic`) cover the *visual* layer; nobody reviews the shared API client, state store, or query-hook layer's own logic correctness, even though CarMarker/notifications/referral forks (§6) all trace back to exactly this layer's absence of a shared-logic reviewer. Arguably the highest-value unowned surface found, given §6's evidence that this exact gap has already caused live bugs 3 times. MEDIUM-HIGH. |
| **Admin Dashboard & Operations** — general functional correctness (as opposed to RBAC gating specifically) | Partial — `spinr-admin-rbac-reviewer` covers module-grant gating only | **PARTIALLY UNOWNED.** No agent reviews whether an individual admin page/endpoint (analytics correctness, vehicle-fleet CRUD, venue management, maintenance-mode behavior) is *correct*, only whether it's correctly RBAC-gated. Given this is the single largest epic by unit count (415 rows, §3), this is a real coverage gap, though most individual admin surfaces are lower-stakes than rides/payments. LOW/MEDIUM. |
| Ops/live-health monitoring | `spinr-ops-triage-investigator` | Covered (distinct "is anything on fire" role, not a diff reviewer) |
| Claude Code tooling/config itself | `spinr-tooling-hygiene-reviewer` | Covered |

**Roster size note:** 28 agent files exist on disk today (`ls .claude/agents/*.md`, VERIFIED),
vs. `spinr-agent-fleet-strategist.md`'s own stated 2026-09-19 baseline of "23 agents (24 counting
yourself)." The roster has grown by ~5 since that baseline was written and the fleet strategist's
own doc already warns not to trust either cached number — consistent with its own advice, not
treated as a discrepancy requiring action here.

---

## §8 Top 5 findings

### CARTO-001 — Admin Dashboard & Operations is the largest, least-PRD-documented epic in the codebase
- Hierarchy: L2 Admin Dashboard & Operations › L3 (all 12 features) › L4 (all) › L5 happy
- Severity: MEDIUM   Priority score: S×B×L = medium × high × high
- Status: VERIFIED (counts derived from the CSV, which is itself INFERRED-classified — the *existence* of 415 admin code units and PRD's 8-bullet admin section are both directly observed)
- Adversary: auditor/regulator asking "what does this admin page do and why does it exist" during a compliance review, finding no requirements doc that names it
- Evidence: `docs/audit/clean-sheet/traceability.csv` rows where `epic="Admin Dashboard & Operations"` (415 rows), vs `docs/PRD.md` lines 144-154 (8 bullets total)
- What happens (plain language): an internal admin can act through dozens of endpoints/pages that no product document describes — nobody outside the engineers who wrote each one can say from a spec what "correct" looks like for most of them.
- Root cause: PRD.md was consolidated 2026-09-07 from two older docs at a point-in-time; the admin surface has grown continuously since (60 route files, 63 pages) without a corresponding PRD update cadence.
- Recommendation: W1 Product Strategist should decide, per admin sub-area, whether it needs a PRD entry or is legitimately internal/engineering scope that doesn't belong in a product PRD.   Alternative considered: leave PRD as-is and treat CLAUDE.md + code comments as the de facto spec — rejected because that's what already happened and it's why this finding exists; an implicit spec that only the original author can read isn't a spec.
- Blast radius: documentation/process only — no code change implied by this finding itself.
- Rollout: N/A (a doc-process finding). Rollback: N/A.
- Verification to close: a PRD addendum (or explicit "out of PRD scope, tracked in CLAUDE.md" decision) exists per admin sub-area.

### CARTO-002 — `shared/` (93 files) has a two-sentence requirements footprint and is the traced root cause of 3 live cross-app bugs
- Hierarchy: L2 Shared Frontend Foundation & Design System › L3 Shared UI components/hooks/API client › L4 (all) › L5 happy
- Severity: HIGH   Priority score: S×B×L = high × high × medium
- Status: VERIFIED (known-forks.md read in full; each of the 3 CarMarker/notifications/referral divergences is documented there with dates and fix commits)
- Adversary: a future engineer fixing a bug in `rider-app` without realizing `driver-app` (or `shared/`) needs the identical fix, repeating the CarMarker/notifications/referral pattern a 4th–6th time
- Evidence: `docs/known-forks.md` (all 4 entries); `docs/audit/clean-sheet/traceability.csv` rows with `path` containing `shared/`
- What happens (plain language): a rider or driver gets a stale/buggy experience because a fix landed on one side of a fork and nobody had a mechanism (beyond a human noticing) to check the other side.
- Root cause: no agent or automated check owns the *logic* correctness of the shared layer (only 1 of 4 fork pairs has even a props-level parity test); §7 confirms this is a genuinely unowned surface, not just an unlucky miss.
- Recommendation: extend the `spinr-agent-fleet-strategist`'s own coverage-gap process to spin up (or extend an existing agent's scope to cover) shared-layer logic review, and prioritize a parity-test pattern (like `CarMarkerParity.test.ts`) for the other 3 known-forks.md pairs.   Alternative considered: rely on `docs/known-forks.md`'s pre-commit warning alone — already shown insufficient (a warning, not a review, per the file's own header).
- Blast radius: process/tooling change; no application code touched by the finding itself.
- Rollout: additive (new agent scope or new tests), no flag needed. Rollback: N/A.
- Verification to close: each of the 3 unguarded known-forks.md pairs has a parity test or an explicit "why a guard isn't needed" note.

### CARTO-003 — Maps & Routing has no owning review agent despite touching a Saskatchewan legal requirement (WAV dispatch)
- Hierarchy: L2 Maps & Routing › L3 Service-area geometry › L4 S-maps-03 › L5 happy
- Severity: MEDIUM   Priority score: S×B×L = medium × medium × medium
- Status: VERIFIED (28 agent descriptions read in full; none names maps/service-area/geo/venues)
- Adversary: regulator/plaintiff's lawyer asking who reviews the code that gates WAV-driver matching to a service area, finding the answer is "nobody specifically"
- Evidence: `.claude/agents/*.md` (28 files, no hits for "maps"/"service_area"/"geo"/"venue"/"h3"); `backend/services/dispatch_service.py`'s WAV-gating logic (cited by `docs/audit/module-map.md` line 64) depends on `service_areas` data that `routes/admin/service_areas.py`/`routes/service_areas.py` manage with no dedicated reviewer
- What happens (plain language): a bug in service-area boundary logic could silently break WAV dispatch (a regulatory obligation) with no agent's PROACTIVE trigger set up to catch it.
- Root cause: Maps & Routing was never named as its own epic in the original agent-fleet design; its WAV-relevant piece was assumed to be covered by `spinr-dispatch-reviewer`'s dispatch scope but that agent's description doesn't name service areas or maps explicitly.
- Recommendation: either extend `spinr-dispatch-reviewer`'s description to explicitly claim service-area/WAV-geometry code, or add a narrow `spinr-maps-routing-reviewer`.   Alternative considered: rely on `spinr-regulatory-compliance-checker` (WAV is named in its description) — plausible partial coverage, but that agent's stated triggers are eligibility/retention/receipts/accessibility, not geometry/dispatch-matching code itself, so it's an adjacent, not a code, owner.
- Blast radius: process/tooling only.
- Rollout: additive. Rollback: N/A.
- Verification to close: an agent's description explicitly names `maps_proxy.py`/`service_areas.py`/`h3_heatmap.py`/`venues.py`.

### CARTO-004 — The automated PRD-to-code matcher produced zero confirmed real gaps, only collision artifacts — the traceability CSV's `gap` rows need a smarter second pass, not action
- Hierarchy: cross-cutting (method finding, not a single epic)
- Severity: LOW (as a product risk) / MEDIUM (as a process risk to W1-W5 downstream lanes that will consume this CSV)
- Status: VERIFIED (4/4 spot-checked `gap` rows found real code on direct grep; see §5)
- Adversary: a downstream lane (W1 Product Strategist, W4 Chief Architect) treating `traceability.csv`'s `status=gap` rows at face value and reporting confirmed unbuilt requirements that don't actually exist
- Evidence: §5's 4-row spot-check table
- What happens (plain language): if this caveat is missed, later audit phases could report false "missing feature" findings to the business.
- Root cause: a fast, ordered first-match keyword classifier necessarily produces false negatives when two stories share a substring (`payout` inside `auto_payout`, `corporate_wallet` inside `corporate_wallet_winddown`) — a known, accepted trade-off for covering 1,788 units in the available time budget rather than hand-classifying each one.
- Recommendation: any downstream lane consuming `status=gap` rows should re-grep the specific story's keywords before reporting it as a finding; do not cite this CSV's `gap` column as evidence of a missing feature without that check.   Alternative considered: rewrite the classifier to prefer longest-keyword-match or run a second LLM-based pass per row — rejected for this lane's time budget, but recommended as a cheap follow-up for whoever owns `traceability.csv` next (W4/W5).
- Blast radius: report-quality only; no code/product risk.
- Rollout: N/A. Rollback: N/A.
- Verification to close: a follow-up pass re-classifies the 16 `gap` rows (and ideally the 769 `orphan` rows) with a collision-aware matcher, or each is individually confirmed/rejected by a human.

### CARTO-005 — `backend/migrations/` (553 files) has never been checked against production's `schema_migrations` table in this audit or (per the module map) recent prior ones
- Hierarchy: L2 Platform Foundation & Schema (and every epic with migrations) › L5 happy
- Severity: MEDIUM   Priority score: S×B×L = medium × high × low-medium
- Status: VERIFIED (no `DATABASE_URL`/Supabase MCP connection available in this environment — confirmed by the environment having no live DB tool call available to this lane) / UNKNOWN (whether any of the 553 files are actually applied to production)
- Adversary: an engineer relying on this inventory believing all 553 migrations are live, when CLAUDE.md's own text documents known duplicate-numeric-prefix issues (~60 shared prefixes) and a `NEVER_APPLY` skip-list whose current membership this lane did not independently verify
- Evidence: `backend/scripts/run_migrations.py --status` would answer this but was not run (read-only mandate + no DB connection); CLAUDE.md's own "Duplicate numeric prefixes exist from history" paragraph
- What happens (plain language): the traceability CSV's migration rows are a filesystem inventory, not a "what's actually live" inventory — every migration row's `test` column is `NONE` by convention and its `status` reflects filename-keyword classification only, never applied/pending state.
- Root cause: this lane's report-and-recommend mandate + no DB credentials in this environment.
- Recommendation: a lane with DB access (or a human) should run `python -m backend.scripts.run_migrations --status` and cross-reference against this CSV's 553 migration rows to flag any that are `PERMANENTLY SKIPPED` or unexpectedly still `pending`.   Alternative considered: infer applied status from `git log` dates — rejected, unreliable (a merged migration file isn't proof of an applied one, per CLAUDE.md's own repeated cautions about this exact gap).
- Blast radius: reporting-accuracy only for this audit; the actual migrations themselves are untouched.
- Rollout: N/A. Rollback: N/A.
- Verification to close: `--status` output attached to a future revision of this file or a dedicated W2/W3 finding.

---

## §9 NOT VERIFIED

- **Migration applied/pending status** — no DB connection available; see CARTO-005.
- **Function-group-level granularity inside `backend/services/*.py`** — classified at file level (61 files); a file serving 2+ distinct feature areas is under-split. Not individually checked file-by-file.
- **Migration file *content*** — all 553 rows are filename-keyword classified only; none were opened and read.
- **`backend/ai/**` as its own glob** — the charter's enumerated globs didn't name it explicitly; only `routes/ai.py`, `routes/admin/ai_console.py`, and `rider-app/app/ai-assistant.tsx` were captured, so the AI Assistant epic's 19-unit count is very likely an undercount of the true AI surface (tools, prompts, eval harness under `backend/ai/` were not walked).
- **`rider-app`/`driver-app` full directory trees beyond `app/` and `hooks/`** — components, contexts, utils directories in each app were not enumerated as their own code units (only screens + hooks per the charter's explicit list).
- **Test *quality*** — a `test` column hit means a plausibly-named test file exists, not that it meaningfully exercises the code unit (no distinction made here between a real test and a fully-mocked `STUBBED` one, despite the charter allowing that label — time did not permit opening each matched test file to check).
- **`finding_ids` linkage precision** — matched by exact basename only against the rapid baseline's 34 finding cards' cited paths; a shared basename (e.g. two different `earnings.py` files, if any existed) would over-link. Spot-checked, not exhaustively verified.
- **The `admin-dashboard/src/components` sweep's inclusion of at least one test file** (`geofence-map.render.test.tsx`) as if it were a component — a small, known data-quality gap, not corrected before freezing the CSV (see §0).
- **Whether the driver-app truly lacks wallet/support screens and rider-app truly lacks a help screen** (§6's screen-name-only sweep) — name-based only, not a functional/navigation-graph check.
- **Safety incident rate and Support ticket P1-response KPI measurement** (§1) — not traced to a specific metric/endpoint in the time available; not asserted present or absent.
- **A full read of `ACTION_ITEMS.md`** (29,244 lines) — used only for a targeted ID-format grep, not read for content; any de-duplication against open action items is left to later waves per the audit prompt's own §4 instruction ("de-duplicate before filing" applies to finding-generating lanes, not this inventory lane).

---

## §10 Open questions for a human

1. **Should `.github/workflows/` (Engineering Gates & CI/CD) and `shared/` (Shared Frontend Foundation) live inside `docs/PRD.md` at all**, or are they deliberately out of a *product* PRD's scope and belong only in CLAUDE.md/engineering docs? This lane added them as L2 epics to give real code a home, but a human should confirm whether "PRD gap" is even the right frame for engineering-process surfaces, or whether §4/§8's framing of them as "undocumented scope" is applying the wrong yardstick.
2. **Is the admin-dashboard's 60-route-file, 63-page surface intentionally undocumented at the individual-feature level** (i.e., "admin dashboard = whatever internal ops needs, no formal spec required"), or is this genuine drift that should get a PRD addendum? CARTO-001 assumes the latter is worth asking about; only a human with product ownership context can say which.
3. **Who should own Maps & Routing and Shared-layer-logic review** (CARTO-002, CARTO-003)? This lane can name the gap but not decide organizational ownership — is it worth a new narrow agent, or should an existing agent's description be widened?
4. **Does `driver-app` genuinely lack a wallet/support-equivalent screen, and does `rider-app` genuinely lack a help-equivalent screen** (§6)? A human (or the W1 Driver/Rider Journey Owners) with actual app knowledge should confirm or correct this name-only inference before it's treated as a parity finding.
5. **Should the ~90 hand-written `S-<epic>-<nn>` story IDs in this file/CSV become the canonical PRD story register**, or are they a disposable scaffold that gets replaced once W1's Product Strategist does a more careful pass? This lane treated them as scaffold-only (see §0) but the CSV will be consumed by W1-W5 regardless, so an explicit decision on their permanence would prevent downstream lanes from accidentally treating INFERRED IDs as settled requirements.
