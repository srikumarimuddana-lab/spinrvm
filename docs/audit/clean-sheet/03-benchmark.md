# 03 — Competitive Benchmark (R18 Competitive Analyst, Wave W4)

> Scope: benchmark every L3 feature in `docs/audit/clean-sheet/01-inventory/epics.md` (90
> features across 18 L2 epics) against Uber, Lyft, and a Saskatchewan/Canadian local taxi app,
> using Dimension 24's Maturity 1-5 + Gap GREEN/YELLOW/RED scale. Spinr maturity is set from
> W1-W3 lane findings (`02-findings/*.md`) and re-read code, not re-audited from scratch.
> Competitor claims are sourced from public primary-ish pages (help centres, newsrooms, investor
> filings) with a URL cited per claim; an uncited competitor claim is ASSUMED, never VERIFIED.

---

## §0 Method, sources, and limits

**Read before writing this report (VERIFIED, opened in full or in relevant part):**
`docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md` §4/§7.1/§7.3, `docs/audit/clean-sheet-prompt/roles.md`
§R18, `docs/audit/clean-sheet-prompt/greenfield-extensions.md`, `audit-framework/dimensions/24-industry-benchmark.md`
(exact filename may differ; matched by glob), `docs/audit/clean-sheet/W0-SUMMARY.md`,
`docs/audit/clean-sheet/01-inventory/epics.md` (90 L3 features, 18 L2 epics — full list used for §2),
and all twelve on-disk W1-W3 lane reports in `docs/audit/clean-sheet/02-findings/` (`strategy.md`,
`rider-journey.md`, `driver-journey.md`, `corporate.md`, `admin-ops.md`, `dispatch.md`, `money-cra.md`,
`security.md`, `trust-safety-fraud.md`, `compliance.md`, `integrations.md`, `reliability.md` — headers
grepped for every finding ID, then the relevant finding bodies read in full). `quality.md`, `support-kb.md`,
`ux-a11y.md` are present but short/in-progress (48, 48, 83 lines) — used what's on disk, flagged where thin.
`CLAUDE.md` "What Spinr Is NOT" section (full) for §5.

**Web access:** WebSearch and WebFetch both worked in this session. Every competitor claim below
cites a URL fetched or returned 2026-09-24 (date accessed). Where a claim could not be
corroborated by a primary(-ish) source (Uber/Lyft's own newsroom, help centre, driver/rider
support pages, or investor materials) in the time budget, it is marked ASSUMED or UNKNOWN rather
than presented as fact. No local Saskatoon/Regina taxi-dispatch app publishes a comparable public
feature-documentation corpus to Uber/Lyft's — see the "local taxi" column note below.

**Local taxi comparator — explicit scoping decision:** Saskatoon and Regina's incumbent taxi
fleets (e.g. Comfort Cab/United Cabs-style dispatch operators) run white-label or third-party
dispatch apps (iCabbi, Autocab, and similar) with no public API docs, no help-centre content, and
no engineering blog — there is nothing citable in the way there is for Uber/Lyft. Where the local
taxi column is filled, it is filled from general public knowledge of Canadian taxi-dispatch apps
(dispatcher-assigned rides, phone-in booking still primary, metered fare, cash/card at pickup) and
is labelled ASSUMED throughout. This is a real limit on this section, not a shortcut — a human
with local market knowledge (a Saskatoon/Regina rider or the Spinr ops team) should correct it.

**Spinr-maturity sourcing rule used throughout §2:** every Spinr maturity score cites either a
W1-W3 finding ID (e.g. `TSF-010`) or a `path:line` this lane re-read directly. A maturity score
with neither is not given — the row is marked UNKNOWN instead of guessed.

**What this section does NOT do:** re-litigate W1-W3's own severity ratings, re-run code searches
W1-W3 already ran (their finding IDs are cited, not re-derived), or assess admin-dashboard/internal
tooling against Uber/Lyft (those are not rider/driver-facing competitive surfaces — Dimension 24
is scoped to competitive/economic positioning of the product, not internal ops tooling; admin
maturity is out of scope for this file by construction, consistent with `01-inventory/epics.md`
labelling most of Admin Dashboard & Operations as internal/undocumented-scope rather than a
customer-facing competitive surface).

---

## §1 Scoring scale (quoted from `audit-framework/dimensions/24-*`, verbatim)

**Competitive-parity scale:**

| Maturity | Meaning |
|---|---|
| 1 — Missing | Capability doesn't exist at all |
| 2 — Workaround | A manual/external workaround stands in for it |
| 3 — Functional | Works, but a recognizably below-industry-baseline approach |
| 4 — At parity | Matches what category-leading ride-share apps ship |
| 5 — Differentiated | Better than the category baseline, a real product edge |

| Gap-vs-industry | Meaning |
|---|---|
| GREEN | At or near parity (maturity 4-5); no action needed for competitiveness |
| YELLOW | Functional gap (maturity 3); worth a scoped improvement, not urgent |
| RED | Missing/workaround (maturity 1-2) **or** an active cost/security exposure regardless of maturity |

A maturity-4/5 feature can still carry a RED *defect* finding from W1-W3 (severity is reported
separately in this file's evidence column, not folded into the Gap column) — per Dimension 24's
own instruction, the two scales are never collapsed into one number.

**Defect severity scale** (reused from `audit-framework/templates/run-audit.md`, applied where a
row's Spinr status is itself a defect, not just a maturity gap): CRITICAL / HIGH / MEDIUM / LOW /
PASS / RECOMMENDATION.

**Evidence labels** (from the audit prompt §4, applied per claim): VERIFIED (observed directly —
code path read, or a primary source fetched), INFERRED (reasoned from evidence, not executed),
ASSUMED (needed to proceed, must be confirmed), PROPOSED (future-state recommendation), UNKNOWN
(must be obtained).


---

## §2 Benchmark table

One row per L3 feature (90 rows across 18 L2 epics, per `01-inventory/epics.md` §2). Columns:
Spinr maturity (1-5) · Uber · Lyft · Local taxi · Gap (GREEN/YELLOW/RED) · Evidence label ·
Spinr citation · Competitor citation. "Local taxi" is filled only where a meaningful comparison
exists; most rows mark it N/A (a phone-dispatch taxi operator has no equivalent digital feature
to compare). Five epics (Admin Dashboard & Operations, Engineering Gates & CI/CD, Legacy Import &
Data Migration, Platform Foundation & Schema, Shared Frontend Foundation) are internal/engineering
surfaces with no rider/driver-facing competitive equivalent — per §0's scoping note, their rows
carry Uber/Lyft = "N/A — internal surface" and Gap = "N/A" rather than a forced, meaningless score.

### Ride Booking & Matching

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Fare estimation | 4 | Upfront fare shown pre-booking; timeout falls back internally, invisible to rider | Same pattern | Metered, no pre-quote (ASSUMED) | GREEN | VERIFIED (Spinr code) / VERIFIED (competitor) | CLAUDE.md "Performance SLAs — Known exception" (`routes/rides/estimates.py`, `_PRICING_ROUTE_WAIT_S`) | [Slate: Uber/Lyft replaced surge with upfront fares](https://slate.com/technology/2023/08/lyft-uber-surge-prime-time-upfront-pricing.html) |
| Ride creation | 4 | Standard one-tap booking | Standard | Phone/app booking | GREEN | INFERRED | traceability S-book-02 (1 mapped unit; core flow, no defect filed) | — |
| Offer matching | 3 | Rider sees live "finding your driver" + match animation | Same pattern | Dispatcher-assigned, rider often not shown live matching (ASSUMED) | YELLOW | VERIFIED | `DISPATCH-001` — rider gets no WS event when a driver is offered the ride | ASSUMED (general UX knowledge of Uber/Lyft matching screens; not independently re-cited) |
| Offer acceptance/decline | 3 | Publishes acceptance-rate policy and explicitly states no permanent deactivation for low acceptance | Similar published policy (ASSUMED, not independently fetched) | N/A | YELLOW | VERIFIED (Spinr) / VERIFIED (Uber) | `DRIVER-004` — decline lowers future offer priority, undisclosed in-app | [Uber: Understanding acceptance and cancellation rates](https://www.uber.com/us/en/blog/understanding-acceptance-and-cancellation-rates/) |
| Surge pricing | 3 | Hard 2.5x cap, disclosed pre-booking, 2-min area-level recompute | Uncapped in most markets, micro-zone (few-block) recompute every 1-2 min, multiplier not always shown numerically | Not applicable (metered) | YELLOW (technical sophistication) / see §4 for the trust-angle GREEN read | VERIFIED (Spinr) / VERIFIED (Uber) | CLAUDE.md "Surge pricing rules"; `backend/utils/surge_engine.py` | [Ridester: How Uber's surge pricing works 2026](https://www.ridester.com/surge-pricing/); [Uber: How surge works](https://www.uber.com/us/en/drive/driver-app/how-surge-works/) |
| Scheduled rides | 4 | Standard, multi-stop supported at schedule time | Standard | Advance phone booking common | GREEN | VERIFIED (defect only) | `RIDERJ-005` — timezone pin gap on the picker (partial defect, not a maturity blocker) | [Uber: multi-stop scheduling](https://ride.guru/lounge/p/how-do-i-schedule-an-uber-ride-for-a-trip-with-multiple-stopsdestinations) |
| Accessibility/WAV dispatch | 2 | WAV as its own product line, third-party-certified drivers, dedicated request flow | Comparable WAV program (ASSUMED parity with Uber, not independently fetched) | Some fleets carry WAV vehicles by request (ASSUMED) | RED | VERIFIED | `COMP-010` — "next WAV availability + standard alternative" promised in `regulatory-sk.md`, not built; rider sees a greyed toggle | [Uber WAV](https://www.uber.com/us/en/ride/uberwav/) |
| Address/pickup validation | 4 | Autocomplete + saved places + curated venues | Same | Verbal address to dispatcher | GREEN | INFERRED | traceability S-book-08 (11 mapped units, Maps proxy) | — |
| Multi-stop routing | 2 | Up to 3 stops, add mid-trip, auto-routed | Same pattern | Rare/manual (ASSUMED) | RED | VERIFIED | `RIDERJ-001` — mid-trip stop add/remove fully built server-side, orphaned client-side (no UI to use it) | [Uber: How to add stops](https://www.uber.com/us/en/ride/how-it-works/multiple-stops/) |
| Ride history & detail | 4 | Full trip history with receipt/map replay | Same | Often none (paper receipt only, ASSUMED) | GREEN | INFERRED | traceability S-book-10 (5 mapped units) | — |
| Fare split | 2 | In-app split among riders on the same trip | Same | N/A | RED (thin footprint, not independently confirmed as fully unusable — flagged for follow-up) | INFERRED (low confidence — unit count only, no finding card) | traceability S-book-11 (1 mapped unit) | [Uber: split price in app](https://www.uber.com/us/en/ride/how-it-works/multiple-stops/) |

### Ride Fulfillment

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Driver arrival tracking | 4 | Live ETA + map | Same | Dispatcher radio updates only (ASSUMED) | GREEN | INFERRED (S-fulfil-01's "0 mapped" is a classifier artifact per CARTO-004, not a real gap; real code lives under S-fulfil-02/S-fulfil-10) | `01-inventory/epics.md` §5 (CARTO-004 caution) | — |
| Real-time location (WS) | 3 | Smoothed/interpolated marker, industry-published techniques | Same | N/A | YELLOW | VERIFIED | `REL-005` (WS Redis outage silently drops cross-replica delivery); `00-history.md` CarMarker fork ×5 | ASSUMED (Dimension 24's own checklist references Uber/Lyft engineering-blog marker techniques generically, not independently re-fetched here) |
| Pickup confirmation | 3 | Rider-set trip verification PIN, mandatory in some markets | PIN Verification (select cities), mandatory for teen rides | No equivalent (ASSUMED — visual ID only) | YELLOW | VERIFIED (Spinr) / VERIFIED (competitor) | `TSF-010` (WITHDRAWN) — a driver-entered 4-digit pickup code gates trip start (`backend/routes/drivers/ride_flow.py:1164-1210`); rider-verifies-driver direction still missing. Was: no trip PIN / rider-verifies-driver-or-vice-versa mechanism exists at all | [Uber: Trip Verification PIN](https://www.uber.com/us/en/safety/our-commitment/); [Lyft Teen — PIN Verification](https://help.lyft.com/hc/en-us/all/articles/7019860536-Lyft-Teen) |
| In-ride state machine | 4 | `cancelled` only pre-trip, guarded transitions, WS events per change | Comparable internal state machine (ASSUMED, not published) | N/A | GREEN | VERIFIED | CLAUDE.md "Ride state machine"; `backend/routes/rides/lifecycle.py` | — |
| Navigation integration | 3 | External deep-link to Google Maps/Waze/Apple Maps, no in-app turn-by-turn | In-app guided navigation with voice/lane guidance in supported markets | Comparable in-app navigation (ASSUMED) | YELLOW | VERIFIED | `audit-framework/dimensions/24-*` itself (worked example: "safe, tested, well-engineered deep-link — and still not what Uber/Lyft/Bolt drivers get"); `docs/audit/ride-experience/REPORT.md` (2026-09-12) | ASSUMED (general knowledge of Uber/Lyft driver-app in-app navigation; not independently re-fetched this pass) |
| In-ride chat | 4 | In-app masked chat | Masked call + chat | N/A | GREEN | INFERRED | traceability S-fulfil-06 (12 mapped units) | [Uber: masked phone contact](https://www.uber.com/qa/en/blog/how-to-contact-uber-driver/) |
| Trip sharing | 4 | Share trip status with a contact | Same | N/A | GREEN | INFERRED | traceability S-fulfil-07 (5 mapped units) | — |
| Lost & found | 3 | In-app lost-item flow | Masked call, structured flow, $20 return fee entirely to driver | Phone the dispatcher (ASSUMED) | YELLOW | INFERRED (Spinr: unit count only, no finding card filed against it — not independently re-verified this pass) | traceability S-fulfil-08 (18 mapped units) | [Uber: Forgot something in an Uber](https://help.uber.com/en/riders/article/forgot-something-in-an-uber-we-will-help-you-find-it?nodeId=feab9beb-9eac-47a2-b63a-6915d8821e1d) |
| Driver online/available status | 4 | `is_available ⇒ is_online` invariant enforced | Comparable internal state (ASSUMED) | N/A | GREEN | VERIFIED | CLAUDE.md "Driver online/available flags" | — |
| Driver-side ride flow | 2 | Alert-only detection + manual admin closure for an abandoned in-progress ride; no automated recovery | RideCheck proactively detects unexpected stop/route/early end and checks in with both parties | Smart Trip Check-In — same proactive-detection pattern | RED | VERIFIED (Spinr) / VERIFIED (competitor) | `DRIVER-005`; `RIDERJ-002` | [Uber RideCheck](https://www.uber.com/us/en/safety/our-commitment/); [Lyft Smart Trip Check-In](https://www.lyft.com/safety/rider) |
| Route deviation / GPS integrity | 3 | Route-deviation safety-alert loop exists; no GPS-spoofing/long-haul fraud detection | Comparable route-deviation alerting; anti-fraud GPS-integrity systems (ASSUMED, not published in detail) | N/A | YELLOW | VERIFIED | CLAUDE.md loop registry (`route_deviation_alerter`); `TSF-002` (driver-rider collusion / fake-ride detection does not exist) | ASSUMED |


### Ride Completion & Payments

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Fare finalization | 3 | Re-pricing (if any) recomputes all line items together | Comparable (ASSUMED) | Metered at drop-off | YELLOW | VERIFIED | `MONEY-007` — completion-time re-pricing changes the fare but keeps the booking-time GST/PST | ASSUMED |
| Wallet settlement | 3 | Row-level-locked, idempotent corporate wallet function; two TOCTOU-class issues found this audit | Comparable (ASSUMED) | N/A | YELLOW | VERIFIED | `CORP-001`, `CORP-002`, `CORP-003` | — |
| Stripe charge/capture | 3 | Standard Stripe integration; reconciliation alert unreliable | Standard Stripe/Braintree-class integration (ASSUMED) | Card terminal / cash at pickup | YELLOW | VERIFIED | `MONEY-013` — daily reconciliations compare mismatched populations | — |
| Tip | 4 | Standard % or custom, 100% to driver | Standard | Often cash (ASSUMED) | GREEN | INFERRED | traceability S-pay-04 (5 mapped units); CLAUDE.md "0% commission... tip" | — |
| Receipt generation | 3 | Itemized (base/distance/time/surge/tax/tip) but no GST/HST registration number shown | Itemized with tax-registration line where required (ASSUMED) | Paper receipt, often no tax breakdown (ASSUMED) | RED (compliance-adjacent) | VERIFIED | `MONEY-011` — receipts and corporate statements carry no GST/HST registration number | ASSUMED |
| Rating | 3 | 5-star both directions (thin code footprint, 1 mapped unit — not independently re-verified for tag/reason granularity) | 5-star + compliment/complaint tags | Rare/no rating system (ASSUMED) | YELLOW | INFERRED | traceability S-pay-06 (1 mapped unit) | ASSUMED |
| Reconciliation | 3 | Daily job exists; alert compares mismatched populations so it is "always red or never trusted" | Comparable internal reconciliation (ASSUMED, unpublished) | N/A | YELLOW | VERIFIED | `MONEY-013` | — |
| Refunds/disputes on fares | 2 | No rider self-serve dispute/refund path; drivers and admin have one | Full in-app self-serve refund flow, 24-48h turnaround, GPS-based route-dispute review | Comparable self-serve (ASSUMED) | RED | VERIFIED (Spinr) / VERIFIED (competitor) | `RIDERJ-003` | [Uber Rider Refund Policy](https://www.uber.com/us/en/legal/refund-policy/); [Uber: how to get a refund](https://help.uber.com/en/riders/article/i-was-charged-a-vehicle-damage-fee?nodeId=0f8a1005-5de3-491a-8f42-5d46c70507bf) |

### Driver Earnings & Payouts

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Earnings calculation | 3 | Lifetime total and itemized trip list use two different legacy-ride inclusion rules | Comparable internal calc (ASSUMED) | N/A | YELLOW | VERIFIED | `DRIVER-002` | — |
| Period statements | 4 | Weekly statement (background loop) | Weekly Monday statement, fare/promo/tip/adjustment breakdown | N/A | GREEN | VERIFIED (Spinr) / VERIFIED (competitor) | CLAUDE.md loop registry ("driver earnings statements"); `backend/utils/driver_statement.py` | [Uber: understand your weekly earnings statements](https://help.uber.com/en/driving-and-delivering/article/understand-your-weekly-earnings-statements?nodeId=ceebe501-e329-4891-98dd-f1cc026137ad) |
| T4A/tax generation | 3 | T4A slip fills Box 020 and Box 048 with the same amount for GST registrants (a real defect); this is a Canadian-specific requirement not directly comparable | N/A / UNKNOWN whether Uber Canada issues a correct equivalent | N/A | YELLOW (regulatory-correctness gap, not a competitive-parity gap — this is Spinr's own CRA obligation) | VERIFIED (Spinr) / UNKNOWN (competitor) | `MONEY-012` | UNKNOWN — not independently fetched; Uber Canada's T4A handling was not found in a citable primary source in the time budget |
| Per-driver payouts | 4 | Stripe Connect payouts, 34 mapped units — most mature epic per Cartographer's own note | Stripe/Branch-class payout rails (ASSUMED) | Cash at end of shift (ASSUMED) | GREEN | INFERRED | `01-inventory/epics.md` §4 ("smallest orphan cluster... this epic's 5 stories cover its code unusually well") | — |
| Batch cash-out | 2 | Default is automatic weekly payout only; a fee-bearing instant-payout endpoint exists in the backend (`payouts.py:849-887`, explicitly described in its own code comment as "matches Uber Instant Pay / Lyft Express Pay") but the driver app has **no UI to reach it** — confirmed by re-reading `driver-app/app/driver/payout.tsx` and `payout-history.tsx`, neither of which contains the string "instant" | Instant Pay: on-demand cash-out, small fee, minutes to bank | Lyft Express Pay: same pattern | Weekly/biweekly cheque, no on-demand option (ASSUMED) | RED | VERIFIED (direct code read this pass, both backend and both driver-app screens) | `backend/routes/drivers/payouts.py:829-887`; `driver-app/app/driver/payout.tsx`, `payout-history.tsx` (absence confirmed); cross-referenced against `strategy.md` line 72 (U5) which independently notes the same fee/parity claim from the code comment | [Uber Instant Pay](https://www.uber.com/us/en/drive/driver-app/instant-pay/) (up to 6 cash-outs/day, $1.25 fee) |


### Corporate / B2B Billing

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Company registration | 4 | Self-serve online enrollment | Self-serve setup, praised for ease | Account/PO arrangement, manual (ASSUMED) | GREEN (code) / see §4 for the "dark, unpriced, no self-serve" strategic caveat | VERIFIED | traceability S-corp-01 (34 mapped units); `STRAT-010` (product/GTM gap, not a code-maturity gap) | [Lyft Business](https://www.g2.com/compare/lyft-business-vs-uber-for-business) |
| KYB verification | 3 | N/A (consumer-grade signup, not a comparable B2B KYB gate) | N/A | N/A | YELLOW | VERIFIED | traceability S-corp-02 (6 mapped units); `CORP-004` (self-serve corporate signup writes zero `consent_version`) | N/A — Uber/Lyft for Business enroll companies via sales-assisted onboarding, not a public KYB flow to benchmark against |
| Allowance cap & ledger | 3 | Uber for Business: admin dashboards, program controls, per-employee caps | Lyft Business: centralized ride management, automated expensing | N/A | YELLOW | VERIFIED | `CORP-002` (per-member cap race: fixed, regressed, re-fixed — currently safe but the failure mode can recur); `CORP-003` (unguarded generic column patch) | [Uber for Business](https://www.uber.com/us/en/business/solutions/rides/business-travel/) |
| Per-ride corporate charge | 4 | Corporate-paid rides route through Uber for Business billing, no surge per company policy options | Comparable | N/A | GREEN | VERIFIED | CLAUDE.md "Surge does not apply to corporate account-paid rides"; traceability S-corp-04 (11 mapped units) | — |
| Membership lifecycle | 3 | Comparable (ASSUMED, unpublished internal detail) | Comparable | N/A | YELLOW | INFERRED | traceability S-corp-05 (3 mapped units — thin footprint) | — |
| Invoicing/subscriptions | 2 | Certified direct SAP Concur integration, automated expensing, 200,000 companies enrolled | Automated expensing, loyalty-program partnerships | Manual invoice/PO (ASSUMED) | RED | VERIFIED (Spinr) / VERIFIED (competitor) | `STRAT-010` — "Corporate SaaS, the declared 'profit engine,' cannot be bought today: dark, unpriced, no self-serve, no MRR metric" | [Uber for Business — SAP Concur integration, 200k companies](https://www.uber.com/us/en/business/solutions/rides/business-travel/) |
| Winddown/offboarding | 3 | Comparable (ASSUMED, unpublished) | Comparable | N/A | YELLOW | VERIFIED | `CORP-001` — wind-down ledger debit is not idempotent, TOCTOU race on the status-flip guard | — |

### Authentication & Authorization

| L3 Feature | Spinr | Uber | Lyft | Local taxi | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|---|
| Rider/driver signup | 4 | Phone/OTP + optional social login | Same | Manual account setup by dispatcher (ASSUMED) | GREEN | INFERRED | traceability S-auth-01 (collides with OTP per CARTO-004; real code exists) | — |
| Email/phone OTP | 2 | Four OTP code paths, three hashed, one plaintext-by-design; lockout logic duplicated 3x | Single, centralized OTP implementation (ASSUMED, unpublished) | N/A | RED (defect, not a missing-feature gap) | VERIFIED | `SEC-R10-016` | — |
| JWT/session | 4 | 15min rider/driver, 1h admin, 30-day rotated refresh; admin JWT fully trusted per doc (though `HIST` flags this claim false in 3 files) | Comparable industry-standard token lifetimes (ASSUMED) | N/A | GREEN (mechanism) / see security.md for defect detail | VERIFIED | CLAUDE.md "Token lifetimes"; `00-history.md` #4 ("admin JWT fully trusted is false in 3 files") | — |
| RBAC / admin access tiers | 3 | Module-grant RBAC with a dedicated reviewer agent | Comparable internal tiering (ASSUMED, unpublished) | N/A | YELLOW | VERIFIED | `SEC-R10-006` — three env-var-controlled privileged/bypass paths exist outside MFA and outside `admin_staff` | — |
| Session management | 3 | Refresh-token reuse grace window lets a thief who uses the token first stay logged in undetected | Comparable (ASSUMED, unpublished) | N/A | YELLOW | VERIFIED | `SEC-R10-004`; `SEC-R10-007` (admin single-session logout fail-open) | — |
| Profile management | 4 | Standard profile edit, 29 mapped units | Standard | Minimal (ASSUMED) | GREEN | INFERRED | traceability S-auth-06 | — |

### Admin Dashboard & Operations, Notifications & Messaging, Maps & Routing, Promotions & Loyalty, Observability & Monitoring, Integrations & Webhooks


**Admin Dashboard & Operations** (12 features: Driver management, Rider management, Financial
dashboard, Dispute resolution, Data transfer/export, Settings/flags, Compliance export, Bulk
operations/fleet, Maintenance mode, Venues, Admin messaging/broadcast, Legal content mgmt) — all
12 rows: **Uber/Lyft = N/A — internal surface** (their equivalent back-office tooling is
proprietary and unpublished; there is nothing to cite for comparison), **Local taxi = N/A**, **Gap
= N/A**. Per §0's scope decision, admin-dashboard functional maturity is out of this file's remit
(`01-inventory/epics.md` CARTO-001 already covers it as a documentation-drift finding, not a
competitive one). One exception worth flagging for §3: **Dispute resolution** (S-admin-04, 53
mapped units, admin-only) directly causes a rider-facing gap — see `RIDERJ-003` — because riders
have no self-serve path into it; that gap is scored under "Refunds/disputes on fares" above, not
duplicated here.

**Notifications & Messaging** (4 features: FCM push offers, SMS OTP, In-app notifications,
Notification throttling/retry):

| L3 Feature | Spinr | Uber | Lyft | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|
| FCM push offers | 3 | Comparable push infrastructure (ASSUMED, unpublished) | Comparable | YELLOW | VERIFIED | `00-history.md` #2 — three FCM exclusion sets (duplication-by-default pattern) | — |
| SMS OTP | 2 | Comparable (ASSUMED) | Comparable | RED (defect) | VERIFIED | `SEC-R10-009` — per-IP OTP throttle keys on a spoofable header; real bound is 5 guesses/24h on a 4-digit code | — |
| In-app notifications | 3 | Comparable | Comparable | YELLOW | VERIFIED | `00-history.md` #2 — two notification inboxes; `docs/known-forks.md` #2 (notifications.tsx diverged twice by omission) | — |
| Notification throttling/retry | 3 | Comparable (ASSUMED) | Comparable | YELLOW | INFERRED | traceability S-notif-04 (collision artifact per CARTO-004; `push_retry` loop is real) | — |

**Maps & Routing** (4 features: Google Maps integration, Distance/ETA, Service-area geometry, H3
heatmapping) — Uber/Lyft = N/A (both are large enough to run proprietary routing/mapping stacks;
Spinr's Google-Maps-proxy approach is a build-vs-buy choice, not a feature-parity gap — this is
Dimension 24's own "Cost Governance" lens, not a rider-facing maturity score). One real finding:
**Service-area geometry** carries a process gap — `CARTO-003` (no owning review agent despite
gating WAV dispatch, a Saskatchewan legal requirement) — MEDIUM severity, process not product.

**Promotions & Loyalty** (5 features: Coupon/promo codes, Driver quests/bonuses, Rider loyalty,
Referral codes, Marketing/subscriptions):

| L3 Feature | Spinr | Uber | Lyft | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|
| Coupon/promo codes | 3 | Comparable | Comparable | YELLOW | VERIFIED | `TSF-006` — promo-stacking/double-reward interaction not independently re-verified across every entry point | — |
| Driver quests/bonuses | 3 | Comparable, well-established mechanic | Comparable | YELLOW | VERIFIED | `01-inventory/epics.md` §7 — Promotions/Loyalty functional correctness partially unowned (only the abuse angle is reviewed) | — |
| Rider loyalty | 2 | Uber One: 6% back on rides, delivery discounts, $9.99/mo | Lyft Pink: 5-10% ride discount, priority pickup, bike/scooter unlocks, airline/hotel partnerships, $9.99-199.99/yr | RED | VERIFIED (Spinr thin footprint) / VERIFIED (competitor) | traceability S-promo-03 (7 mapped units — no dedicated subscription-tier program found in W1-W3 findings) | [Uber One vs Lyft Pink 2026](https://getridewise.com/blog/lyft-pink-vs-uber-one-2026-break-even-math-by-city); [Lyft Pink FAQ](https://www.lyft.com/memberships/lyft-pink-monthly/faq?embeddedView=true) |
| Referral codes | 2 | Standard rider/driver referral credit | Standard | RED (defect) | VERIFIED | `01-inventory/epics.md` §6 — a dead, mismatched `referral_link` field on both apps was silently wrong until removed 2026-09-14, no parity guard | — |
| Marketing/subscriptions | 3 | Comparable (ASSUMED) | Comparable | YELLOW | INFERRED | traceability S-promo-05 (38 mapped units — largest in the epic) | — |

**Observability & Monitoring** and **Integrations & Webhooks** (8 features combined: Sentry error
tracking, Prometheus metrics, Loop watchdog, Health checks, Stripe webhooks, Zoho Desk sync, Stripe
Connect/payout sync, Stripe KYC/Identity) — **Uber/Lyft = N/A — internal/vendor-integration
surface**, no rider/driver-facing competitive equivalent to cite. Real defects exist
(`REL-001`-`REL-005`, `INT-001`-`INT-007`) but they are reliability/vendor-risk findings, already
owned by W2/W3, not competitive-parity gaps — cited here only for completeness, not re-scored.

**Safety, Trust & Fraud** (6 features — scored explicitly, this is a customer-facing competitive
surface even though the epic sits under W2):

| L3 Feature | Spinr | Uber | Lyft | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|
| SOS / emergency | 3 | RideCheck, 24/7 safety line, emergency button, in-app audio/video recording | ADT-powered Emergency Help, Smart Trip Check-In, silent-911 option | YELLOW/RED (see TSF-001) | VERIFIED (Spinr) / VERIFIED (competitor) | `TSF-001` — the only documented SOS on-call runbook describes a system that does not exist in code | [Uber safety commitment](https://www.uber.com/us/en/safety/our-commitment/); [Lyft + ADT Emergency Help](https://www.lyft.com/blog/posts/lyft-launches-emergency-help) |
| Insurance period tracking | 4 | Not a rider/driver-facing feature Uber/Lyft publish in comparable detail (their equivalent contingent/primary coverage layers are disclosed at a policy level, not a per-second state machine) | Comparable coverage-layer structure (ASSUMED, not published at this granularity) | GREEN (Spinr's 4-period model with append-only audit rows is arguably ahead in auditability — see §4) | VERIFIED | CLAUDE.md "Insurance periods"; `backend/utils/insurance_periods.py` | ASSUMED |
| License/ID/background checks | 2 | One-time document-expiry gate only; eligibility rules beyond that are flag-gated off by default | Continuous monitoring (pioneered 2018) flags new charges between annual re-checks; Industry Sharing Safety Program shares deactivations across Uber/Lyft/HopSkipDrive | RED | VERIFIED (Spinr) / VERIFIED (competitor) | `COMP-002`; `DRIVER-003` | [Uber background checks](https://www.uber.com/us/en/newsroom/background-checks/); [Industry Sharing Safety Program](https://www.uber.com/us/en/newsroom/background-checks/) |
| RLS policies | 3 | Policy logic tested but dormant — 100% of real traffic uses the service-role key, bypassing RLS | N/A (internal architecture, not publicly comparable) | YELLOW (defense-in-depth gap, not a feature gap) | VERIFIED | CLAUDE.md "Testing Conventions — RLS" (C108); `SEC-R10-002` | — |
| Fraud detection | 1 | No detection signal for collusion, cancellation-fee farming, chargeback velocity, or SIM-swap-specific ATO | Documented fraud-detection systems, cross-platform deactivation sharing | RED | VERIFIED (Spinr) / VERIFIED (competitor, existence only — internal detection logic itself is proprietary/ASSUMED) | `TSF-002`, `TSF-003`, `TSF-004`, `TSF-005` | [Uber/Lyft Industry Sharing Safety Program](https://www.uber.com/us/en/newsroom/background-checks/) |
| Dispute/appeal handling | 2 | Driver has no in-app explanation when suspended/banned | Uber: in-app Review Center with stated appeal path and reasons | RED | VERIFIED (Spinr) / VERIFIED (competitor) | `DRIVER-001` | [Uber deactivation review](https://www.uber.com/us/en/drive/driver-app/deactivation-review/) |


**AI Assistant** (2 features: Rider AI assistant, AI admin console/guardrails):

| L3 Feature | Spinr | Uber | Lyft | Gap | Evid. | Spinr cite | Competitor cite |
|---|---|---|---|---|---|---|---|
| Rider AI assistant | 3 | Uber's 2026 "GO-GET" push: OpenAI-powered voice booking, Travel Mode concierge (airport guidance, OpenTable-powered recommendations), driver-facing AI guidance | No comparable public AI-assistant launch found this pass (UNKNOWN, not ASSUMED absent) | YELLOW | VERIFIED (Spinr exists, thin footprint — 2 mapped units, `routes/ai.py`/`rider-app/app/ai-assistant.tsx`) / VERIFIED (Uber) | traceability S-ai-01; `01-inventory/epics.md` §9 (AI surface undercounted — `backend/ai/**` not separately walked) | [Uber GO-GET 2026](https://www.uber.com/us/en/newsroom/go-get-2026/); [Uber OpenAI driver assistant](https://thetechnologyexpress.com/uber-launches-openai-assistant-for-drivers-and-voice-ride-booking/) |
| AI admin console/guardrails | N/A | N/A — internal tooling | N/A | N/A | VERIFIED | traceability S-ai-02 (10 mapped units); `spinr-ai-guardrail-reviewer` agent exists | — |

**Engineering Gates & CI/CD**, **Shared Frontend Foundation & Design System**, **Platform
Foundation & Schema**, **Legacy Import & Data Migration** (23 features combined across these four
epics) — all rows: **Uber/Lyft = N/A — internal engineering surface**, **Local taxi = N/A**, **Gap
= N/A**. These are not rider/driver/corporate-facing competitive dimensions; Dimension 24 exists to
assess competitive/economic positioning of the *product*, and per `01-inventory/epics.md` §0 these
four epics were added by the Cartographer specifically to give internal code a home in the
traceability tree, not because they compete with anything Uber or Lyft publish. The one item worth
flagging here for the record: **Shared UI components/hooks** (part of Shared Frontend Foundation)
is the traced root cause of three live cross-app rider/driver-visible bugs (CarMarker, notifications,
referral-link forks — `CARTO-002`, `docs/known-forks.md`) — an *internal* quality problem with
*external*, customer-visible symptoms. It is scored under the rider/driver-facing rows above
(Real-time location, In-app notifications, Referral codes) where the symptom actually lands, not
duplicated as its own competitive row.

---

## §3 Missing table stakes

What riders/drivers/corporate buyers expect by default from a 2026 ride-share app, that Spinr
lacks or has only partially built. Each is a §7.1-format finding card.

### BENCH-001 — WITHDRAWN: No trip verification PIN (rider-verifies-driver-or-vice-versa)

> **Erratum — WITHDRAWN (orchestrator re-verification, 2026-09-25).** A pickup PIN already exists and is the only production path into `in_progress`. The backend generates a 4-digit pickup code (`backend/dependencies/__init__.py:78-80`, `generate_pickup_otp`). The rider app shows it (`rider-app/app/driver-arriving.tsx`, `driver-arrived.tsx`, `ride-status.tsx`). The driver enters it in `driver-app/components/dashboard/ActiveRidePanel.tsx`, checked with a failure lockout at `backend/routes/drivers/ride_flow.py:1164-1210` (`POST /rides/{ride_id}/verify-otp`). Found by the Step 6 hostile review (`08-hostile-review.md`) and confirmed by direct read. The original search missed it by name (the code calls it an OTP, not a PIN), the same failure as QUAL-003. What remains open is narrower: the rider-verifies-driver direction and the support path when the code is missing. Do not act on the card below as written.

- Hierarchy: L2 Ride Fulfillment › L3 Pickup confirmation › L4 S-fulfil-03 › L5 rider gets into the wrong vehicle
- Severity: HIGH   Priority score: S×B×L = high × high × medium (every trip, every rider, low-but-nonzero base rate of wrong-car incidents)
- Status: VERIFIED   Existing item: `TSF-010` (new this audit wave)
- Adversary: an impersonating driver (or a rider getting into the wrong car with a similar-looking vehicle), plaintiff's lawyer
- Evidence: `TSF-010` — full pickup-confirmation code path re-read, no PIN mechanism found anywhere in rider-app/driver-app or backend
- What happens (plain language): nothing today stops a rider from getting into a car that only looks like their match, or confirms to the driver that the person getting in is the rider who booked the trip.
- Root cause: never built — not a regression, a gap from day one.
- Recommendation: rider-set 4-digit PIN shown in-app, driver enters it (or the reverse — driver reads a PIN the rider confirms) before `driver_arrived → in_progress`.   Alternative considered: rely on rider photo + car photo only (already exists per traceability) — rejected: Uber/Lyft both layer a PIN on top of photo ID precisely because photo confirmation is passive and easy to skip under time pressure.
- Blast radius: `routes/rides/lifecycle.py` (state transition gate), both mobile apps' pickup screens. New, additive field — no existing consumer.
- Rollout: additive column + feature flag, opt-in rollout by service area.   Rollback: flag off.
- Verification to close: a test asserting `in_progress` cannot be reached without PIN match when the flag is on; manual QA of both apps.

### BENCH-002 — No rider self-serve dispute/refund path
- Hierarchy: L2 Ride Completion & Payments › L3 Refunds/disputes on fares › L4 S-pay-08 › L5 rider was overcharged or route-disputes a fare
- Severity: HIGH   Priority score: S×B×L = high × high × medium
- Status: VERIFIED   Existing item: `RIDERJ-003`
- Adversary: any rider with a legitimate billing dispute, forced into a slower support-ticket path; plaintiff's lawyer (undisclosed-fee optics)
- Evidence: `RIDERJ-003`
- What happens (plain language): a rider who thinks they were overcharged cannot resolve it themselves in the app — they wait on support, while Uber resolves the same class of dispute in-app in 24-48h.
- Root cause: dispute/refund tooling was built admin-first and driver-first; rider-facing self-serve was never added.
- Recommendation: expose a rider-facing subset of the existing admin dispute/refund flow (fare amount, route, cleanliness/damage-fee dispute categories) gated to the rider's own completed trips.   Alternative considered: keep support-ticket-only — rejected: this is the single most-cited table-stakes gap in this benchmark and the direct driver of "surprise charge"/"dispute rate" being unmeasured (greenfield-extensions.md §8's proposed KPIs).
- Blast radius: `routes/rides/*` refund endpoints (admin-only today), `services/fare_service.py` read paths, `admin-dashboard`'s existing dispute-resolution logic (53 mapped units) as the pattern to reuse, not rebuild.
- Rollout: additive rider-facing endpoint + screen, flagged.   Rollback: flag off, admin-only path remains.
- Verification to close: a rider can file and see resolution status for a fare dispute without contacting support, in a staging test.

### BENCH-003 — Multi-stop routing is built server-side but has no client UI
- Hierarchy: L2 Ride Booking & Matching › L3 Multi-stop routing › L4 S-book-09 › L5 rider wants to add a stop mid-trip
- Severity: MEDIUM   Priority score: S×B×L = medium × medium × high (cheap to fix — it's already built)
- Status: VERIFIED   Existing item: `RIDERJ-001`
- Adversary: none (a capability gap, not an attack surface) — but a competitor advantage: any rider who wants this today has no reason to prefer Spinr over Uber/Lyft on this specific feature.
- Evidence: `RIDERJ-001`
- What happens (plain language): the backend can already handle adding/removing a stop mid-trip; nobody can reach it from either app.
- Root cause: server-side work shipped without its paired client screen — an unusually clean, low-risk backlog item (this is the *cheapest* gap in this report to close).
- Recommendation: build the rider-app UI for the existing server capability.   Alternative considered: none — this is a straightforward finish-the-feature item, not a design decision.
- Blast radius: rider-app booking/in-trip screens only; backend already handles it, so no backend change.
- Rollout: additive UI behind a flag.   Rollback: flag off.
- Verification to close: a rider can add/remove a stop mid-trip in a manual test on both platforms.

### BENCH-004 — Driver instant cash-out exists in the backend but has no driver-app UI
- Hierarchy: L2 Driver Earnings & Payouts › L3 Batch cash-out › L4 S-earn-05 › L5 a driver wants same-day access to earnings
- Severity: MEDIUM-HIGH   Priority score: S×B×L = high × high × medium (driver-retention-relevant; Uber/Lyft treat Instant Pay as a headline driver-facing benefit)
- Status: VERIFIED (this lane, direct code read)   Existing item: new — not previously filed as its own finding; `strategy.md` U5 notes the fee mechanics but not the missing UI
- Adversary: none directly, but a real retention/competitive-disadvantage risk — CLAUDE.md's own "Weekly active driver retention" KPI is below target territory territory and Uber/Lyft's own driver-facing marketing leans on Instant Pay/Express Pay as a differentiator
- Evidence: `backend/routes/drivers/payouts.py:1081` (`POST /payouts/instant` endpoint) and `:829-887` (fee schema, and its own code comment: "don't advertise instant payout while the driver app has no instant-payout UI to tap"); `driver-app/app/driver/payout.tsx` and `payout-history.tsx` (grepped for "instant" — zero hits, confirming the comment)
- What happens (plain language): the fee-bearing instant cash-out that would match Uber Instant Pay / Lyft Express Pay already works on the server; a driver has no way to trigger it from the app they actually use.
- Root cause: same "server built, client orphaned" pattern as `RIDERJ-001` — a second, independently-discovered instance of it in this audit.
- Recommendation: build the driver-app instant-payout screen against the existing endpoint.   Alternative considered: leave weekly-only and market 0% commission instead of instant pay as the retention lever — plausible, but should be a deliberate product decision (see §9), not a silent gap.
- Blast radius: driver-app payout screens only; backend endpoint already exists and is presumably tested (`backend/tests/test_instant_payout.py` exists per this pass's grep).
- Rollout: additive UI, flag per service area (the endpoint already checks `instant_payout_enabled` per service area).   Rollback: flag off.
- Verification to close: a driver can request and receive an instant payout from the app in a staging test; `test_instant_payout.py` re-run and extended to cover the new UI's contract if one is added.

### BENCH-005 — No continuous/annual driver eligibility re-check; the built recheck logic is flag-gated off by default
- Hierarchy: L2 Safety, Trust & Fraud › L3 License/ID/background checks › L4 S-safety-03 › L5 a driver's licence class, DIP points, or criminal record changes after onboarding
- Severity: HIGH   Priority score: S×B×L = high × high × medium (regulatory + insurance liability, per CLAUDE.md's own "Driver eligibility... renewed annually" line)
- Status: VERIFIED   Existing item: `COMP-002`, `DRIVER-003`
- Adversary: regulator/SGI auditor, plaintiff's lawyer after an incident involving a driver whose eligibility lapsed post-onboarding
- Evidence: `COMP-002` — "Every SK eligibility rule beyond document expiry is dark (flag default `false`)"; `DRIVER-003` — the 3-year licensed-experience check is wired but a no-op for the existing fleet and dark for everyone regardless
- What happens (plain language): CLAUDE.md promises annual renewal of Criminal Record Check + Vulnerable Sector Check; in production, only document-expiry is actually enforced at `go_online` — everything else is built but switched off.
- Root cause: `enforce_driver_eligibility_recheck` flag defaults false; nobody has flipped it on, likely because the existing fleet was never backfilled with the data the recheck needs (`DRIVER-003`'s "no-op for the existing driver fleet").
- Recommendation: a backfill pass for the existing fleet's licence/experience data, then flip the flag in staging → canary → production, per gate 3.   Alternative considered: leave as-is and rely on document-expiry alone — rejected: this is a named CLAUDE.md regulatory commitment, not optional polish.
- Blast radius: `routes/drivers/status.py` go_online path (already named in COMP-002's own evidence); every currently-online driver whose eligibility has never been re-verified.
- Rollout: flag flip, staged by service area, after a data backfill.   Rollback: flag off (reverts to document-expiry-only, today's actual state).
- Verification to close: `COMP-002`'s own verification step (a human confirms the flag is live and the backfill is complete) plus a metric showing recheck denials are occurring where expected.

### BENCH-006 — No fraud-signal detection for collusion, cancellation-fee farming, or chargeback velocity
- Hierarchy: L2 Safety, Trust & Fraud › L3 Fraud detection › L4 S-safety-05 › L5 a driver and rider collude on a fake trip, or a rider disputes charges repeatedly
- Severity: HIGH   Priority score: S×B×L = high × high × medium
- Status: VERIFIED   Existing item: `TSF-002`, `TSF-003`, `TSF-004`
- Adversary: colluding driver-rider pair; cancellation-fee farmer; repeat chargeback abuser
- Evidence: `TSF-002`, `TSF-003`, `TSF-004`
- What happens (plain language): three of the most common ride-share fraud patterns have no detection signal at all in Spinr today — a fraud ring could run undetected until a human notices, if ever.
- Root cause: `spinr-fraud-auditor` exists and covers promo/referral/GPS fraud, but collusion/cancellation-farming/chargeback-velocity detection was never built as a distinct capability.
- Recommendation: scope a minimal detection pass (repeat driver-rider pairing frequency, cancellation-fee-eligible-cancel rate per driver, chargeback count per rider) as a background job feeding an admin review queue — not an auto-ban, per the adversary-mandate's "controlled automation over autonomous" tie-breaker.   Alternative considered: buy a third-party fraud-detection vendor — plausible for scale, but Spinr's current volume likely doesn't justify the cost yet; a home-built heuristic pass is the smaller, reversible first step.
- Blast radius: new background loop + admin queue; no existing consumer of this data today (net-new).
- Rollout: additive, dark-launched (log-only) before any action is taken on its output.   Rollback: disable the loop.
- Verification to close: a synthetic collusion/farming/chargeback scenario in a test fixture triggers a flagged row.

### BENCH-007 — No driver-facing explanation when suspended or banned
- Hierarchy: L2 Safety, Trust & Fraud › L3 Dispute/appeal handling › L4 S-safety-06 › L5 a driver is deactivated
- Severity: HIGH   Priority score: S×B×L = high × high × high (every deactivation, direct income impact, misclassification/wrongful-termination optics for a contractor model)
- Status: VERIFIED   Existing item: `DRIVER-001`
- Adversary: plaintiff's lawyer (a contractor deactivated with no stated reason and no appeal path is a stronger claim than one who received both); regulator
- Evidence: `DRIVER-001`
- What happens (plain language): a driver who is suspended or banned gets no in-app reason and no appeal mechanism — Uber's own published policy explicitly states the opposite ("if there are steps to take to regain access, they'll include them in the message sent to the driver").
- Root cause: admin-side suspend/ban tooling was built without a corresponding driver-facing notice/appeal flow.
- Recommendation: a minimum-viable in-app notice (reason category, not full investigative detail) plus a support-ticket-backed appeal path.   Alternative considered: keep reasons internal-only to avoid tipping off fraud actors — a real tension; Uber's own model resolves it by giving a reason category without full detail, which is the recommended middle ground here too.
- Blast radius: `routes/admin/drivers.py` suspend/ban endpoints, driver-app account-status screen (net-new).
- Rollout: additive.   Rollback: hide the new screen behind a flag.
- Verification to close: a suspended test driver sees a reason category and an appeal CTA in-app.

### BENCH-008 — WAV (wheelchair-accessible vehicle) dispatch is a greyed toggle, not a working feature
- Hierarchy: L2 Ride Booking & Matching › L3 Accessibility/WAV dispatch › L4 S-book-07 › L5 a rider who uses a wheelchair requests a ride
- Severity: HIGH   Priority score: S×B×L = high × high × medium (Saskatchewan regulatory requirement per CLAUDE.md; ADA/accessibility-equivalent legal exposure)
- Status: VERIFIED   Existing item: `COMP-010`
- Adversary: regulator, plaintiff's lawyer (disability-discrimination angle), competitor with a working WAV product
- Evidence: `COMP-010` — "next WAV availability + standard alternative" promised in `regulatory-sk.md` is not built; rider sees a greyed toggle
- What happens (plain language): a wheelchair-using rider opens the app, sees a WAV option that doesn't actually do anything useful, and has no fallback path the app offers them.
- Root cause: the promised fallback UX (show next WAV ETA, or offer a standard-vehicle alternative with an explanation) was never built; only the toggle shipped.
- Recommendation: build the documented fallback — even a simple "no WAV driver online, next available in ~X min" message is a large improvement over a dead toggle.   Alternative considered: hide the toggle entirely until WAV supply exists — worse: CLAUDE.md's own accessibility rule requires WAV support *if a WAV driver is online in the service area*, so an honest toggle with real state beats hiding the option.
- Blast radius: `routes/rides/booking.py` WAV branch, rider-app booking screen's WAV toggle.
- Rollout: additive (fixes existing broken UX, no new surface).   Rollback: revert to the greyed toggle (not a regression from today).
- Verification to close: a WAV request in a service area with zero WAV drivers online shows the documented fallback message, not a silent dead toggle.


### BENCH-009 — No automated recovery for an in-progress ride abandoned by a dead/killed driver phone; RideCheck/Smart Trip Check-In-class proactive detection does not exist
- Hierarchy: L2 Ride Fulfillment › L3 Driver-side ride flow › L4 S-fulfil-10 › L5 driver's phone dies or the app is force-killed mid-trip
- Severity: HIGH   Priority score: S×B×L = high × high × low-medium (rare but high-impact — a stranded rider with no resolution path)
- Status: VERIFIED   Existing item: `DRIVER-005`, `RIDERJ-002`
- Adversary: hostile network/device persona (flaky network, force-killed app), and the rider left stranded is a real safety exposure, not just a UX one
- Evidence: `DRIVER-005` (alert-only detection + manual admin closure only); `RIDERJ-002` (rider has no self-facing resolution)
- What happens (plain language): both Uber and Lyft proactively detect an unexpectedly-stopped or early-ended trip and check in with both parties automatically; Spinr detects the same condition but only alerts an admin, who must notice and act manually, while the rider sees nothing.
- Root cause: route-deviation/stuck-ride detection was built as an ops alerting feature, not as a rider/driver-facing automated recovery flow.
- Recommendation: extend the existing stuck-ride sweeper loop (already in CLAUDE.md's loop registry) to trigger a rider-facing "having trouble reaching your driver?" prompt with a one-tap rebook/cancel-without-fee path, mirroring RideCheck/Smart Trip Check-In's pattern.   Alternative considered: keep admin-only and rely on faster admin response times — rejected: this is a safety-adjacent gap during live app testing and the fix is additive to an existing loop, not a new subsystem.
- Blast radius: `stuck-ride sweeper` loop (CLAUDE.md-named), rider-app in-trip screen (net-new prompt).
- Rollout: additive, flagged.   Rollback: flag off, current alert-only behavior unchanged.
- Verification to close: a simulated dead-driver-app scenario in the `mock_supabase_client` fixture set produces a rider-facing prompt within a bounded time window.

### BENCH-010 — No customer-facing outage/incident communications; no-show fee push has no dispute affordance
- Hierarchy: L2 Notifications & Messaging › L3 In-app notifications (+ cross-cutting: Reliability) › L4 S-notif-03 › L5 a platform-wide outage, or a disputed no-show charge
- Severity: MEDIUM   Priority score: S×B×L = medium × medium × medium
- Status: VERIFIED   Existing item: `SKB-004` (no status page/outage copy), `SKB-007` (no-show fee push has no dispute-affordance, on "the most disputable charge" per the support-kb lane's own channel-coverage audit)
- Adversary: none directly — a trust/CSAT gap, and a compounding factor on top of `BENCH-002`'s missing rider dispute path
- Evidence: `SKB-004`, `SKB-007`
- What happens (plain language): during an outage, riders/drivers get silence instead of a status update; when charged a no-show fee (the single most commonly disputed charge type per Spinr's own support-tooling audit), the notification that informs them of the charge gives no way to contest it from that notification.
- Root cause: incident-comms tooling and dispute-affordance-on-charge-notifications were never built as first-class features — a gap common to a young platform, but a known industry table-stake for the two competitors here.
- Recommendation: (1) a minimal status page or templated in-app banner for known outages; (2) add a "dispute this charge" deep link to the no-show fee push, feeding into `BENCH-002`'s rider dispute path once built.   Alternative considered: rely on support-ticket volume as the outage signal — rejected: reactive, not proactive, and doesn't fix the no-show dispute gap either.
- Blast radius: `routes/rides/ride_cancel.py:808-814` (no-show push), new status-page surface (net-new, low blast radius).
- Rollout: additive.   Rollback: N/A (no existing behavior removed).
- Verification to close: a no-show push notification includes a working dispute deep link in a manual test; an outage-comms template exists and is reachable without deploying code.

---

## §4 Where Spinr is ahead

**Steelman first.**

1. **0% driver commission, disclosed at the fare layer.** `strategy.md`'s own worked example
   (VERIFIED, `payouts.py`/`fare_service.py` read) confirms `driver_earnings = total_fare −
   (booking_fee + airport_fee)`, both disclosed line items, tip 100% to driver — genuinely true at
   the fare layer, a real structural difference from Uber's ~25-30% effective take rate (VERIFIED,
   [Uber's own driver-earnings explainer](https://medium.com/uber-under-the-hood/understanding-ubers-share-of-driver-earnings-899d5eb733bd)
   and `strategy.md`'s worked comparison). This is a genuine, defensible product edge — Maturity 5,
   GREEN, on the narrow claim "driver keeps the fare."
2. **Insurance-period tracking as an append-only, per-second audit trail.** CLAUDE.md's four-period
   model (`backend/utils/insurance_periods.py`) with mandatory append-only logging for a 7-year
   regulatory window is a level of auditability Uber/Lyft don't publish at this granularity (their
   coverage-tier structure is disclosed at a policy level, not as a queryable state machine).
   Maturity 4-5, GREEN, genuinely differentiated *if* the enforcement gaps below don't undermine it.
3. **Surge cap (2.5x hard ceiling, disclosed pre-booking) vs Uber's uncapped, opaque-multiplier
   model.** Uber's own help pages (VERIFIED) no longer always show the exact multiplier, and surge
   can reach 7-8x in extreme events; Spinr's cap and pre-booking disclosure is a real trust
   advantage, even though the *engine* itself (§2) is technically less sophisticated (area-level,
   not micro-zone). This is the clearest case in this report of "behind on sophistication, ahead on
   trust" — the two are genuinely different axes and shouldn't be collapsed.
4. **Saskatchewan-specific compliance scaffolding exists as code, not just policy** — insurance
   periods, document-expiry gates, T4A generation, GST/PST line items are all *present* as real
   code paths (even where individual line items have defects, per `compliance.md`/`money-cra.md`).
   Uber/Lyft's Canadian compliance posture is not independently verifiable from public sources
   (UNKNOWN) — Spinr's is at least auditable because it's a small, single-market codebase.
5. **Corporate wallet as a first-class, row-level-locked ledger function** (`corporate_wallet_apply_delta`)
   is architecturally sound (idempotent by design, even where two TOCTOU-class bugs were found and
   are noted as risks in `CORP-001`-`003`) — this is a genuine engineering asset for the
   "profit engine" epic, even though it is not yet a sellable product (§2's Invoicing/subscriptions
   row, RED).

**Now attack — is each lead real in code, or only in docs?**

1. **0% commission** — real in code (VERIFIED), but `STRAT-002`/`STRAT-003` (per-ride contribution
   margin is negative, and "100% of your fare" is only true because the booking fee is currently
   switched off) mean the *business model* behind the claim is not yet sustainable at $0 booking
   fee. The rider-facing claim is true today; whether it survives the fee being turned back on
   (which the strategy lane flags as likely necessary) is an open question, not a settled edge.
2. **Insurance-period auditability** — real in code, but `SAFETY-003`/`COMP-014` (cited in
   `compliance.md`) note "declared online but unreachable" keeps an open Period 1 row, and the
   audit trail can't distinguish "waiting" from "phone dead." The *mechanism* is ahead; its
   *coverage of edge cases* has known gaps, so the lead is real but not yet airtight.
3. **Surge cap** — real and simple to verify (`SURGE_CAP = 2.5` in code, VERIFIED), a genuinely
   durable trust advantage that's cheap to keep. This is the strongest "ahead" claim in this report
   because it requires no further work to remain true — it just needs to not be relaxed (CLAUDE.md
   already says as much: "Never suggest raising it without explicit business + legal review").
4. **Compliance-as-code** — real code exists, but `compliance.md`'s own COMP-001 through COMP-018
   findings (18 of them) show most SK-specific rules beyond document expiry are flag-gated *off* in
   production (`COMP-002`). The scaffolding is ahead; the live enforcement is not — this is the
   single biggest gap between "ahead in the codebase" and "ahead in what a rider/driver/regulator
   actually experiences today."
5. **Corporate wallet ledger** — architecturally sound, but unsellable (`STRAT-010`): no self-serve
   signup for pricing, no MRR metric, billing dark. An engineering asset without a go-to-market is
   not yet a competitive lead — it's potential.

**Net read:** Spinr's clearest, most durable lead is the disclosed surge cap and the fare-layer 0%
commission claim (both cheap to keep true and independently verifiable in code today). Its other
claimed leads are real architecture with real enforcement gaps — "ahead in the codebase, not yet
ahead in what happens in production" is the correct, more precise framing for insurance-period
tracking and compliance-as-code specifically.

---


## §5 Deliberately do NOT copy

Per CLAUDE.md's "What Spinr Is NOT," these Uber/Lyft patterns are explicitly out of scope, and
this section flags where Spinr has already drifted toward one.

| Uber/Lyft pattern | Why Spinr should not copy it | Has Spinr already drifted? |
|---|---|---|
| Uncapped/opaque surge (7-8x observed, multiplier not always shown) | CLAUDE.md: "Not a surge-first product... Never hide surge before booking confirmation" | **No drift found.** `SURGE_CAP = 2.5` is enforced at every fare-calc call site per CLAUDE.md's own description, re-confirmed by `money-cra.md`/`dispatch.md` not flagging a bypass. Keep watching — this is the asset from §4, don't let it erode. |
| Third-party ad SDKs / behavioral retargeting (Uber's own first-party "Journey Ads"/"Journey Takeover" business, $2B+ 2025 revenue, now expanding to third-party programmatic and off-platform placements) | CLAUDE.md: "Not a data-harvesting product... Never add third-party ad SDKs or behavioral retargeting" | **Yes — this is the report's most serious "do not copy, but did" finding.** `STRAT-004` (`strategy.md`, VERIFIED): a Meta ad SDK is integrated with per-ride `Purchase` conversion events, undisclosed to users, and collides directly with the "no ad SDKs" guardrail. This is not Uber's own first-party in-app ad placements (which Spinr isn't attempting) — it is worse in one specific way: it sends *ride-taking behavior* to a third-party ad network for conversion optimization, which is exactly the "behavioral retargeting" pattern CLAUDE.md names as prohibited. Escalate per gate 9 (this touches the "What Spinr Is NOT" guardrail directly) — do not treat as a routine cleanup item. |
| Driver acceptance-rate penalties with insufficient disclosure | CLAUDE.md: "Not a driver-control platform... don't dictate shifts... Features that nudge toward control-of-work patterns require legal review" — and separately, Uber's *own* policy publicly discloses the mechanism (see §2's Offer acceptance/decline row) | **Yes — partial drift.** `DRIVER-004`: a driver's decline of an offer lowers their future offer priority, and this is undisclosed anywhere in-app. The mechanism itself (temporarily deprioritizing low-acceptance drivers) is not unusual — Uber does something similar — but Uber *discloses* it in a public policy page. Spinr's version is the same control lever with less transparency than the platform CLAUDE.md explicitly warns against emulating. Fix is disclosure, not necessarily removal of the mechanism (see `BENCH-007`-adjacent recommendation in driver-journey.md). |
| Hidden/bundled service fees (a fee not mapped to a disclosed receipt line) | CLAUDE.md: "Not a hidden-fee operator... Every charge on the receipt maps to a disclosed line item" | **Partial, flagged not confirmed.** `strategy.md` §2.2 U3: an airport surcharge is retained as platform revenue with no remittance path found — "if the airport charges Spinr a fee, that is a pass-through; if not, it is a location-based platform fee labelled 'surcharge.'" This is UNKNOWN, not confirmed drift — flagged in `strategy.md` as needing a founder/finance answer, carried into this report's §9. |
| Percentage-commission monetization | CLAUDE.md: "Not a commission-taking marketplace. Driver keeps 100% of the fare" | **Live code contradicts the stated model, though not yet exercised on a rider.** `STRAT-005`: `platform_fee_percent` is an admin-writable percentage-commission setting with no reader (dead config, not active commission-taking) — worth closing (remove the dead field or explicitly document why it exists) so it can't be silently activated later without anyone noticing it violates the stated model. |
| Control-of-work language (mandatory shifts, uniforms, quota-style passes) | CLAUDE.md: "Not a driver-control platform... Any control-of-work language... triggers re-classification risk" | **Flagged, not fully confirmed this pass.** `strategy.md` cites a "Spinr Pass" driver N-day pass with a daily ride quota as a "pay-to-work" optics risk, recommending it be modified into an optional, benefits-based subscription rather than a licence to work. This wasn't independently re-verified by this lane — carried forward as an open item from `strategy.md`, not re-derived here. |

**Where Spinr should stay clearly ahead by construction:** Uber's 2026 advertising expansion
(Journey Takeover, Offsite Ads reaching Google Shopping, $2B+ ad revenue) is the clearest sign the
incumbents are trending toward exactly what CLAUDE.md prohibits. This is a genuine long-run
positioning opportunity for Spinr — "we will never sell your ride data to advertisers" is a
credible claim only as long as `STRAT-004` is fixed, not while it's open.

---

## §6 Fairness benchmark

**What incumbents publish (or have had published about them):** a peer-reviewed-adjacent 2020-2021
study (Pandey & Caliskan, George Washington University; analyzed 100M+ Chicago trips,
Nov 2018-Dec 2019) found Uber and Lyft's per-mile fares were systematically higher for trips with a
pickup or dropoff in neighborhoods with a higher non-white population share, lower median house
price, or lower average education level, even after controlling for demand/speed — described by
the researchers as "social bias" in dynamic pricing, not proven discriminatory intent (VERIFIED,
[CACM coverage](https://cacm.acm.org/news/245817-uber-lyft-pricing-algorithms-charge-more-in-non-white-areas/fulltext?mobile=false),
[VentureBeat](https://venturebeat.com/ai/researchers-find-racial-discrimination-in-dynamic-pricing-algorithms-used-by-uber-lyft-and-others)).
An earlier 2016 study (cited in the same coverage) found cancellation-rate disparities by
passenger-name-inferred race. Neither company has published its own fairness-by-neighborhood
methodology or a rebuttal dataset publicly (UNKNOWN — Uber's public statement disputed the
causal framing but did not release its own neighborhood-level fairness analysis, per the same
sources).

**What Spinr can measure today (VERIFIED against code, not aspirational):**
- Surge is computed per **service area**, not per micro-zone (`backend/utils/surge_engine.py`,
  CLAUDE.md's tier table) — this is coarser than Uber/Lyft's few-block zones, which cuts both ways:
  it's *less* able to price-discriminate at a fine neighborhood level than Uber/Lyft's own studied
  mechanism, but it's also less responsive to genuine hyper-local demand spikes. This coarseness is
  a plausible, if unproven, structural mitigant against the exact bias pattern the Chicago study
  found — worth stating as a hypothesis, not a proven fairness property (INFERRED, not VERIFIED;
  nobody has run the equivalent study on Spinr's own trip data).
- Dispatch matching logic (`services/dispatch_service.py`) was not re-read line-by-line by this
  lane for any neighborhood/demographic signal — none was found by W1-W3 either (no finding in
  `dispatch.md` mentions demographic or neighborhood-based dispatch weighting). Absence of evidence
  is not evidence of absence here; nobody has run a fairness audit on Spinr's own dispatch data.
- Spinr has no published or internal-only fairness-by-neighborhood metric today (UNKNOWN — not
  found in `admin-dashboard`'s analytics endpoints per the epics inventory, and not named in any
  W1-W3 finding).

**Recommendation (PROPOSED, net-new — not previously flagged by any W1-W3 lane):** before Spinr
scales beyond Saskatchewan, run an internal, Chicago-study-style analysis of Spinr's own historical
trip data (fare-per-km, match rate, and driver cancellation rate by service-area geography cross-
referenced with publicly available census/neighborhood demographic data) as a proactive
self-audit, and publish the methodology (not necessarily the raw data) as a trust differentiator —
directly extending greenfield-extensions.md §12's "Fairness audit: dispatch, ETA, and pricing
outcomes by neighbourhood" item, which named this lane (R18) alongside R8/R9 as an owner. This is
Maturity 1 (missing) today for Spinr specifically, and effectively Maturity 2 (documented external
critique, no internal remediation confirmed) for the industry baseline — there is no "parity"
target to match here; doing this analysis *at all* would be a differentiator, not table-stakes.

---

## §7 Top 10 gaps ranked by severity × blast radius × likelihood

1. **`STRAT-004` — Meta ad SDK + per-ride `Purchase` conversions, undisclosed, direct violation of "What Spinr Is NOT."** Severity CRITICAL (guardrail breach) × blast radius (every rider) × likelihood (already live). See §5.
2. ~~**`TSF-010` / `BENCH-001` — No trip verification PIN.**~~ WITHDRAWN 2026-09-25: a 4-digit pickup code already gates trip start (see the BENCH-001 erratum); only the rider-verifies-driver direction remains. A basic, cheap-to-build safety table-stake every competitor ships. HIGH × every trip × low-but-nonzero incident rate.
3. **`RIDERJ-003` / `BENCH-002` — No rider self-serve dispute/refund path.** HIGH × every rider with a billing question × frequent.
4. **`COMP-002` / `DRIVER-003` / `BENCH-005` — Driver eligibility re-check is flag-gated off; only document-expiry is live.** HIGH × entire active driver fleet × regulatory/insurance liability window is continuous, not one-time.
5. **`TSF-002`/`003`/`004`/`005` / `BENCH-006` — No fraud-collusion/cancellation-farming/chargeback-velocity detection at all.** HIGH × platform-wide × increases with scale.
6. **`COMP-010` / `BENCH-008` — WAV dispatch fallback UX not built (regulatory accessibility requirement).** HIGH × every WAV-needing rider in a service area with no WAV driver online × Saskatchewan legal requirement, not optional.
7. **`DRIVER-001` / `BENCH-007` — No driver-facing suspension/ban explanation or appeal path.** HIGH × every deactivation × misclassification/wrongful-termination legal exposure for a contractor model.
8. **`DRIVER-005` / `RIDERJ-002` / `BENCH-009` — No automated recovery for an abandoned in-progress ride; competitors proactively check in (RideCheck/Smart Trip Check-In).** HIGH × rare but safety-relevant × a stranded rider today has no path but manual admin action.
9. **`MONEY-011` — Receipts carry no GST/HST registration number.** HIGH-leaning-CRITICAL as a compliance gap × every receipt × ongoing tax-audit exposure (see `compliance.md`/`money-cra.md` for the full CRA thread; this file does not re-litigate the tax determination itself).
10. **Driver instant cash-out (new finding, this pass) — built in the backend, unreachable in the driver app; Instant Pay/Express Pay is a headline Uber/Lyft driver-retention feature Spinr already half-has.** MEDIUM-HIGH × entire driver fleet × directly relevant to CLAUDE.md's own "Weekly active driver retention" KPI, which the PRD itself flags as below-target/unmeasured.

**Honorable mention, not in the top 10 only because it is process, not product:** `CARTO-002`/
`CARTO-003` (shared-layer and Maps & Routing have no owning review agent) are the root cause behind
several of the above (CarMarker/notification/referral forks trace to the same unowned surface) —
fixing agent ownership is the highest-leverage single process change available, even though it
doesn't itself close any one gap above.

---

## §8 What was NOT verified

- **Every Uber/Lyft feature cited above** was sourced from a public help-centre, newsroom, or blog
  page — none of it is Uber/Lyft's actual production code, so "parity" claims compare Spinr's code
  to competitors' *documented* behavior, not their implementation. A documented feature could be
  imperfectly implemented in practice (this report cannot know that either way).
- **The local Saskatoon/Regina taxi-dispatch comparator** — no citable primary source exists (see
  §0); every "local taxi" cell in §2 is ASSUMED from general knowledge of Canadian taxi-dispatch
  operations, not fetched or confirmed.
- **Whether Uber Canada's receipts carry a GST/HST registration number** (relevant to the `MONEY-011`
  comparison) — not independently fetched; marked ASSUMED/UNKNOWN in §2's Receipt generation row.
- **Whether Uber Canada correctly issues T4A-equivalent tax documents** — UNKNOWN, not found in the
  time budget; the `MONEY-012` row is scored on Spinr's own regulatory-correctness defect, not a
  head-to-head comparison.
- **Rating, Lost & found, Fare split, Membership lifecycle rows** — scored from traceability unit
  counts (a proxy for implementation depth) rather than a specific W1-W3 finding card or this
  lane's own direct code read; flagged INFERRED with explicitly lower confidence in §2, not treated
  as equivalent-strength evidence to a cited finding ID.
- **`ux-a11y.md` and `support-kb.md`'s full content** — `ux-a11y.md` is a section-header skeleton
  with almost no body content as of this write (18 headers, 83 lines); this report used
  `support-kb.md`'s final content (confirmed on disk, `SKB-001` through `SKB-008`) but could not
  draw on a completed accessibility lane report. Any WCAG/accessibility-specific competitive
  positioning claim beyond what `COMP-011`/`RIDERJ-004` already established is therefore not made
  here.
- **`admin-ops.md`'s full 500 lines** were grepped for headers and spot-read for the items this
  report needed (dispute resolution, RBAC); not read in full line-by-line, consistent with §0's
  scoping decision that admin-dashboard functional maturity is out of this file's competitive remit.
- **`reliability.md`'s and `integrations.md`'s findings** — cited by ID where directly relevant to a
  rider/driver-facing row, but their full reliability/vendor-risk detail was not re-derived or
  independently re-verified by this lane; treated as W2/W3's settled findings per the audit
  prompt's own instruction not to re-audit domains other lanes already swept.
- **No 10% VERIFIED-finding re-sample was performed by this lane** — Step 6's cross-model
  re-verification is a W5/Synthesizer-stage activity per the master prompt's own execution plan,
  not something this individual lane repeats independently.
- **Whether the airport-surcharge remittance question (§5) is genuinely an undisclosed fee or a
  legitimate pass-through** — explicitly UNKNOWN, carried from `strategy.md`, not resolved here.

---

## §9 Human-only questions

1. **`STRAT-004` (Meta ad SDK): should it be removed, disclosed via consent, or is there a business
   justification for it that a founder/legal decision should weigh against the "What Spinr Is NOT"
   guardrail?** This is the single highest-severity finding in this report and needs a founder/legal
   decision, not an engineering default.
2. **Is the airport surcharge a genuine pass-through to the airport authority, or undisclosed
   platform revenue labelled as a fee?** (`strategy.md` U3, carried into this report's §5) — only
   finance/founder can answer whether a remittance agreement exists.
3. **Should Spinr build a driver-facing Instant Pay-equivalent UI (the backend already exists), or
   is 0%-commission-plus-weekly-payout the deliberate retention strategy instead?** A product
   decision, not obviously "yes, build it" — the counter-case (differentiate on commission, not
   payout speed) is plausible and should be a stated choice, not a default from an unfinished
   feature.
4. **Is Spinr's coarser (service-area-level) surge granularity a deliberate fairness safeguard, or
   just a technical limitation that happens to look that way?** (§6) If deliberate, it's worth
   stating publicly as a trust differentiator; if incidental, that should be known before it's
   marketed as one.
5. **Should Spinr commission or self-run a neighborhood-level fairness audit of its own dispatch/
   pricing data before or after expanding beyond Saskatchewan?** (§6, PROPOSED, net-new to this
   audit wave) — a founder/board-level prioritization call, not something an engineering lane can
   schedule unilaterally.
6. **What is the actual current appetite to disclose the acceptance-rate-penalty mechanism
   (`DRIVER-004`) to drivers, given the "not a driver-control platform" guardrail — full disclosure
   like Uber's public policy, or a redesign of the mechanism itself?** A legal-review question per
   CLAUDE.md's own "control-of-work language... requires legal review" line.
7. **Does the founder consider WAV dispatch (`COMP-010`) a near-term regulatory-risk item to
   prioritize, or an acceptable gap given current WAV driver supply?** The fix is cheap (a fallback
   message), but the prioritization call (regulatory risk vs. current WAV rider volume) needs
   product/legal judgment this lane cannot supply.

---

*End of `03-benchmark.md`. Companion files: `02-findings/*.md` (source findings, cited throughout),
`01-inventory/epics.md` (L2/L3 feature list this report scores against), `04-blueprint.md` (W4
Chief Architect, not authored by this lane).*
