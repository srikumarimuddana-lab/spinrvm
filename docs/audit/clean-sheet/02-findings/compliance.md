# R12 — Compliance & Regulatory Counsel-Analyst (W2)

_Spinr Clean-Sheet Rebuild Audit. Written 2026-09-24. Report-and-recommend only. Labels: VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN._

## 0. Method and source-access log

**Passes run:** Steelman → Attack (regulator/auditor, plaintiff's lawyer, privacy commissioner, driver claiming employee status) → Rebuild. Every obligation carries two columns: *where enforced in code* (`path:line`) and *what primary source requires it* (URL + date fetched, or ASSUMED). Reused `spinr-regulatory-compliance-checker` (rules 1–8) and `spinr-legal-readiness-reviewer` (kind 1/2/3 gating classification) as the domain rule sets.

**Core rule of this lane:** nothing below is VERIFIED against a regulator unless the primary page was actually fetched. It was not — see the table. Every regulatory claim is therefore at best INFERRED (search-engine snippet) or ASSUMED (repo doc / CLAUDE.md only). Code claims are VERIFIED where a `path:line` is cited and was read this session.

### 0.1 Primary-source fetch attempts (one each, 2026-09-24)

| Host | URL tried | Result |
|---|---|---|
| saskatchewan.ca | `/residents/transportation/vehicles-for-hire` | **EGRESS_BLOCKED** by network proxy |
| sgi.sk.ca | `/tnc` | **EGRESS_BLOCKED** |
| canada.ca | CRA `/gst-hst-businesses/ride-sharing.html` | **EGRESS_BLOCKED** |
| priv.gc.ca | PIPEDA fair-information principles page | **EGRESS_BLOCKED** |
| regina.ca | `/business-development/licensing-permits/vehicle-for-hire/` | **EGRESS_BLOCKED** |
| saskatoon.ca | `/business-development/licensing-permits/vehicle-hire` | **EGRESS_BLOCKED** |
| sets.saskatchewan.ca (PST-46 bulletin PDF, surfaced by search) | `PST.046 Service Enterprises.pdf` | **EGRESS_BLOCKED** |
| canlii.org (Vehicles for Hire Regulations) | `rrs-c-v-3.2-reg-1` | **EGRESS_BLOCKED** |
| pubsaskdev.blob.core.windows.net (Vehicles for Hire Act PDF) | `V3-2.pdf` | **EGRESS_BLOCKED** |
| help.lyft.com (SK driver requirements, secondary) | Saskatchewan Driver Information | **EGRESS_BLOCKED** |
| saskhrc.ca (Policy on Service Animals) | `/policy-on-service-animals/` | **EGRESS_BLOCKED** |

Result: **0 of 11 primary/secondary fetches succeeded.** Same outcome as `ACTION_ITEMS.md` G9 (2026-08-22) and `docs/legal/legal-text-publication-checklist.md`'s 2026-09-02 note. This is a standing environment limitation, not a one-off — a human with an ordinary browser must do the reads in §10.

### 0.2 WebSearch (budget 12, all used; every result is INFERRED — snippet only, never a primary read)

| # | Query topic | Snippet finding (INFERRED) | URL surfaced |
|---|---|---|---|
| 1 | SK PST on ride-share | PST-46 "Service Enterprises" (rev. Feb 2025) lists "taxicab or limousine transportation services … and other passenger transportation services" as PST-applicable | `sets.saskatchewan.ca/…/PST.046+Service+Enterprises.pdf` |
| 2 | SGI TNC insurance | TNC premium "$0.11 per kilometre plus PST"; TNC must submit $1M liability certificate annually; **TNC must report km monthly even if zero**; app must track km "in all phases" | `sgi.sk.ca/documents/…/Transportation+Network+Company+Application+Rideshare+form.pdf`; `rayneragencies.ca` |
| 3 | Regina TNC bylaw | TNC must obtain a City of Regina TNC licence under the Vehicles for Hire Act + "Vehicle(s) For Hire Bylaw"; no driver/vehicle detail in snippet | `regina.ca/bylaws-permits-licences/licences/rideshare/` |
| 4 | Saskatoon TNC bylaw | Bylaw No. 9651 "The Vehicles for Hire Bylaw, 2019"; TNC licence required; **$0.12/trip + $0.15/trip surcharge** remitted monthly | `saskatoon.ca/sites/default/files/documents/city-clerk/bylaws/9651.pdf`; `tnc_brochure_digital.pdf` |
| 5 | CRA GST ride-share | "taxi business" definition amended 2017-07-01 to include app-arranged passenger transport; small-supplier threshold does **not** apply; register within 30 days of first supply | `canada.ca/…/gi-196/gst-hst-commercial-ride-sharing-services.html`; `…/taxi-ride-sharing-drivers.html` |
| 6 | SK Vehicles for Hire Regs | **"drivers must obtain a Class 4 driver's licence"**; <12 DIP points in 2 yrs; no impaired suspensions in 10 yrs; annual CRC + annual vehicle inspection; **no 10-year vehicle-age rule found** | `canlii.org/…/rrs-c-v-3.2-reg-1`; `saskatchewan.ca/…/2017/november/30/vehicles-for-hire-act`; CBC 2018-12-06 |
| 7 | PIPEDA breach | Report to OPC "as soon as feasible"; RROSH test; keep record of every breach 24 months | `priv.gc.ca/…/gd_pb_201810/`; `…/rrosh-tool/` |
| 8 | CRA Part XX.1 (DPI) | Platform operators file by Jan 31 for prior year; collect seller name/address/TIN; register RZ account | `canada.ca/…/reporting-rules-digital-platforms/guidance-on-reporting-rules.html` |
| 9 | SK Human Rights Code — service animals | SHRC: "taxi and ride-for-hire companies may not deny service to a person with a Service Animal, nor may they charge additional fees"; may ask if required for disability + task trained, not nature of disability | `saskhrc.ca/…/policy-on-service-animals/`; Code 2018 c S-24.2 |
| 10 | Audio recording consent | Criminal Code s.184 one-party consent; **PIPEDA applies on top for an organization** (notice, purpose, consent) | `torontodefencelawyers.com`, `ridez.ca` (secondary only) |
| 11 | T4A box 048 | $500 calendar-year threshold; CRA not assessing box-048 penalties outside trucking (as of late 2025) | `canada.ca/…/reporting-fees-for-service.html`; `…/t4a-slip.html` |
| 12 | Contractor classification | CRA 4-factor test (control, tools, profit/loss, integration); SK Employment Standards: self-employed not covered, but treating a contractor as employee converts the relationship | `saskatchewan.ca/business/employment-standards/who-is-and-is-not-covered` |

### 0.3 Repo inputs read (VERIFIED by read)
`CLAUDE.md` (Saskatchewan Regulatory, Compliance/PIPEDA, What Spinr Is NOT); `.claude/context/regulatory-sk.md`; `docs/audit/clean-sheet-prompt/sweep-catalog.md` §2.14, §4.1–4.4; `greenfield-extensions.md` §10; `roles.md` §3; `W0-SUMMARY.md`; `audit-framework/regulatory-matrix.md`; `docs/legal/legal-text-publication-checklist.md` + draft headers; `docs/compliance/*`; `docs/privacy/*`; `docs/data-classification.md`; `docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md`; `docs/runbooks/data-breach.md`; rapid-baseline `A5-money-ledger-cra.md` (CRA table), `A8-trust-safety.md` (§e escalations), `ESCALATIONS.md` (E1–E10); `backend/routes/drivers/status.py:590-900`; `backend/routes/drivers/_shared.py:975-1010`; `backend/services/driver_availability_service.py`; `backend/schemas.py:300-330,700-725`; `backend/features.py:800-845`; `backend/utils/receipt_pdf.py:160-205`; `backend/utils/corporate_statement_pdf.py`; `backend/routes/users.py:242-300,451-531`; `backend/routes/auth.py:143-184,1295-1306`; `backend/routes/legacy_consent.py`; `backend/utils/retention_purge.py`; migrations 216/289/296 (+ list of 36 purge-touching migrations); migration 64 (`driver_insurance_periods` trigger); `backend/routes/admin/compliance.py:1064-1200`; `backend/routes/drivers/ride_flow.py:662-840`, `ride_cancel.py:33-121`; `backend/services/dispatch_service.py:219-456`; `backend/routes/rides/estimates.py:549,632`; `rider-app/app/ride-options.tsx:1061-1077`; `driver-app/app/become-driver.tsx:685`, `payout.tsx:757`; `docs/driver-faqs-saskatchewan.md:61-63`; `ACTION_ITEMS.md` G2 (both), G3, G4, G9, C63, A40#10, C111/C112 (dup ids), traceability.csv (three epics).

## 1. Steelman — what the current design gets right

All VERIFIED by reading the cited code unless marked otherwise.

1. **Insurance-period ledger is append-only at the DB layer, not just by convention.** `backend/migrations/64_driver_insurance_periods.sql:80-128` installs `driver_insurance_periods_no_mutate` (DELETE forbidden; UPDATE may only set `ended_at`). Period 2 opens at `driver_assigned`/claim time, not at Accept (CLAUDE.md; `routes/rides/matching.py`). Per-phase GPS-measured distance (`driver_period_distances`, migration 249) feeds a per-trip, per-phase insurer billing report at the two contracted rates (`routes/admin/compliance.py:1064-1200`). This is a stronger audit trail than the regulatory table demands — it is the legal defence `regulatory-sk.md` says it is.
2. **Retention horizons live in SQL, not prose.** `purge_pii_retention()` (migrations 50→296, 36 touching migrations) encodes 3y GPS anonymise (Step A/I), 7y ride hard-delete (Step B), 90d chat/location/AI, 7y audit logs, 30d profile scrub on DSAR (Step N, migration 296:415-470), and runs every ~24h from `core/lifespan.py:513` via `utils/retention_purge.py` (INTERVAL_SECONDS=86400, leader-locked). The numbers the docs quote (7y ride / 3y GPS / 30d profile) **match the SQL** (`296:152-163`). Dry-run mode exists.
3. **Service-animal refusal is made impossible, not merely discouraged.** A driver decline or cancel with a service-animal reason is rejected with HTTP 400 *and* writes an `audit_logs` row (`ride_decline_service_animal_refusal` / `ride_cancel_service_animal_refusal`) for account review (`routes/drivers/ride_flow.py:708-739`, `ride_cancel.py:100-121`). This matches the SHRC snippet (§0.2 #9) exactly and is best-in-class — Uber/Lyft handle it post-hoc via complaints.
4. **WAV requests cannot be silently downgraded.** Dispatch and cascade filters both add `is_wav=True` when `requires_wav` (`services/dispatch_service.py:231-244,455-456`; `routes/rides/matching.py:503-504,877-878`), `wav_available` is computed pre-booking (`routes/rides/estimates.py:549,632`) and shown to the rider (`rider-app/app/ride-options.tsx:1061-1077`), and `test_e2e_wav_dispatch.py` exercises it.
5. **Document expiry blocks go-online unconditionally** (licence, ride-share-endorsed insurance, inspection, CRC/VSC) via both the service-area `required_documents` config and the legacy expiry columns (`routes/drivers/status.py:590-670`), and is re-checked at accept time (`routes/drivers/_shared.py:975-1010`, `_CORE_DOC_EXPIRY_CATEGORIES`). Reason strings are specific, as `regulatory-sk.md` requires.
6. **Consent is an explicit act, versioned, and re-runnable.** Signup rejects without `consent_accepted` (`routes/auth.py:1300-1306`), stamps `CONSENT_VERSION = "consumer-tos-2026-09-v1"` (`auth.py:184`), and a generic re-consent notice exists for any user whose stored version trails it (`routes/legacy_consent.py`, dark behind `legacy_consent_notice_enabled`). CRC/VSC consent is a separate versioned record (`services/driver_crc_consent.py`) captured at onboarding submit (`driver-app/app/become-driver.tsx:596-612`) and re-checked at go-online when the flag is on (`status.py:850-880`).
7. **DSAR access is fulfilled, not just logged.** `POST /data-export` records a row with `response_due_at = now+30d` (PIPEDA s.9 wording in the docstring) and spawns the build-and-email pipeline (`routes/users.py:242-300`); rate-limited 3/h. Deletion records `deletion_requested_at`/`deletion_scheduled_at` (`users.py:451-531`).
8. **Tax is Decimal, per-area, separately quantised** (`features.py:820-845`), receipts iterate `tax_breakdown` into one line per tax with rate (`receipt_pdf.py:174-190`), and the corporate statement loudly Sentry-flags its own GST/PST fallback (`corporate_statement_pdf.py:34-84`). `SURGE_CAP` is clamped at every fare call site (`fare_service.py:533`).
9. **Contractor posture is consistent across the surfaces I could read.** No "shift/uniform/minimum hours/performance review" copy in `driver-app/app` (grep, only comments and a "must register for GST" payout note); `docs/driver-faqs-saskatchewan.md:61-63` states the contractor position plainly; the draft ICA §1.5 disclaims minimum acceptance rate / trip count / online time; dormancy flagging is explicitly reporting-only ("never suspends, never changes go-online eligibility", `services/driver_dormancy_service.py:1-12`).
10. **Privacy-incident machinery exists and was used.** `docs/runbooks/data-breach.md` has the 72h clock and 24-month record; `docs/audit/breach-record.md` has a fully populated Incident 1 with a dated, written RROSH sign-off (`incident-1-rrosh-signoff-2026-09-13.md`), corrected twice when facts changed. Whatever one thinks of the determination (COMP-013), the *record* is what PIPEDA s.10.3 requires.
11. **The repo already knows what it doesn't know.** G2, G3, G4, G9, `sgi-quarterly.md` §5, `A5` CRA table and `ESCALATIONS.md` E1–E10 all say "ASSUMED, needs a human". The problem is not honesty; it is that none of those items has an owner or a date (see §10).

## 2. Attack — findings (§7.1 cards)

Priority score = Severity (C=5,H=4,M=3,L=2) × Blast radius (1–5) × Likelihood (1–5). "Existing item" cites `ACTION_ITEMS.md` where a duplicate exists; a re-found open item is reported with *why it is stuck*, not re-filed.

### COMP-001 — Licence class gate may enforce the wrong class (Class 5 in code; provincial regulation snippet says Class 4)
- Hierarchy: L2 Driver Onboarding & Compliance › L3 Eligibility gate › L4 S-driver-onboard › L5 go_online recheck
- Severity: HIGH   Priority score: 4×5×3 = 60
- Status: **UNKNOWN** (code VERIFIED; regulatory requirement INFERRED from two search snippets, contradicting CLAUDE.md)   Existing item: new (no `Class 4` string anywhere in `ACTION_ITEMS.md`, `docs/`, `.claude/context/` or `backend/` — grep)
- Adversary: regulator / SGI auditor; plaintiff's lawyer after a collision ("your platform dispatched a driver the province says needed a Class 4")
- Evidence: `backend/routes/drivers/status.py:733-744` accepts only `"5"/"5A"/"5-A"` unless `drivers.sgi_approved`; `backend/schemas.py:313-316` comment "licence class must be Class 5 or SGI-approved non-standard"; CLAUDE.md Saskatchewan Regulatory "Valid Class 5 driver's license (standard) — Class 1-4 drivers need separate approval"; `docs/runbooks/saskatoon-launch.md:72` "Class 5 (or higher)". Against: §0.2 #6 — CanLII *Vehicles for Hire Regulations* / CBC 2018-12-06 snippet: "drivers must obtain a Class 4 driver's licence which permits them to operate taxis or limousines"; SGI Professional Driver's Handbook Class 4 page surfaced for the same query.
- What happens (plain language): if the province requires Class 4 for TNC drivers, the code's rule is inverted — it *blocks* Class 4 holders (as "needs separate approval") and *admits* Class 5 holders, so every online driver may be unlicensed for the activity. If the province has since relaxed to Class 5 (I could not confirm either way), the code is right and CLAUDE.md is right, but nobody in the repo has ever cited the source.
- Root cause: the eligibility table in `regulatory-sk.md` was authored from general knowledge; no primary-source URL/date was ever attached (the file's own header says "confirm with legal"), and the environment cannot fetch the regulation to check.
- Recommendation: a human reads *The Vehicles for Hire Regulations* (RRS c V-3.2 Reg 1) and SGI's TNC page today and records the class + date in `regulatory-sk.md`; if Class 4 is required, flip the accepted set in `status.py:734` (and `become-driver.tsx` copy) behind the existing `enforce_driver_eligibility_recheck` flag and add a `license_class` backfill task. Alternative considered: accept both 4 and 5 now — rejected because it converts an unknown into a silent policy change on a live-tested surface (CLAUDE.md gate 9).
- Blast radius: `status.py` go_online only (grep `license_class`: `status.py`, `schemas.py`, migration 221, `driver_import_service.py`, SGI form filler). Recheck is flag-gated and default off, so today's live effect is nil either way — which is itself COMP-002.
- Rollout: doc + config first; code change flagged.   Rollback: flag off.
- Verification to close: `regulatory-sk.md` row carries URL + fetch date; `test_go_online_*` eligibility test asserts the confirmed class set.

### COMP-002 — Every SK eligibility rule beyond document expiry is dark (flag default `false`), so onboarding copy promises what production does not enforce
- Hierarchy: L2 Driver Onboarding & Compliance › L3 Eligibility gate › L5 go_online recheck
- Severity: HIGH   Priority score: 4×4×4 = 64
- Status: VERIFIED (code) / UNKNOWN (production flag value, migration 405 applied?)   Existing item: A40 #10 (closed, shipped dark), C63 (closed 2026-09-04, shipped dark), B14 (licence class/number data gap), G2 (116 untracked migrations), C125 (8 pending)
- Adversary: regulator; plaintiff's lawyer ("your own app told the driver 3 years were required — you never checked")
- Evidence: `schemas.py:320` `enforce_driver_eligibility_recheck: bool = False`; `status.py:690` gates age≥18, licence class, vehicle<10y, 3-year experience and "missing field → block" all behind it; no ACTION_ITEMS entry records the flag ever being flipped on (grep "enforce_driver_eligibility_recheck" + flipped/enabled/production → 0 hits); `driver-app/app/become-driver.tsx:685` tells applicants "You must be 18 or older and have at least 3 years of licensed driving experience" while `profile.py:47` only validates DOB≥18 at profile write. Production value of the flag and whether migration 405 (`license_issue_date`) is applied are DB facts this lane cannot read (CARTO-005).
- What happens: a 17-year-old with a 2-year-old licence and a 2012 vehicle who uploads four unexpired documents goes online today. The ACTION_ITEMS closures (A40#10, C63) read as "done"; the regulatory obligation is not.
- Root cause: correct dark-ship discipline (CLAUDE.md gate 3) with no follow-through step ("verify in staging, then flip") owned by anyone — the same "closed = merged, not = enforced" pattern HIST notes for migrations.
- Recommendation: (1) read the flag from prod; (2) run the recheck in dry-run/report mode over live drivers (add a `report_only` branch that logs would-block reasons without raising) for one week; (3) backfill `license_class`/`vehicle_year`/`license_issue_date` for the ~189 legacy drivers; (4) flip on. Alternative considered: enforce at onboarding only — rejected because CLAUDE.md requires re-check on every go_online and legacy drivers never went through onboarding.
- Blast radius: every driver's go-online call; `driver_availability_service.py` (v2 path) does **not** contain the eligibility block at all (`_EXPIRY_FIELDS` only, lines 18-23) — if `driver_availability_v2_enabled` routes go-online through the service, the flag-gated checks are bypassed entirely. Grep: `enforce_driver_eligibility_recheck` appears only in `status.py` and `schemas.py`.
- Rollout: report-only mode → flag.   Rollback: flag off (no data mutation).
- Verification to close: prod `app_settings.enforce_driver_eligibility_recheck = true` observed; a `test_go_online_availability.py` case proving the v2 service path applies the same rules.

### COMP-003 — SK PST on ride-share fares: still no primary source after four verbal determinations; the only evidence in hand points the other way
- Hierarchy: L2 Payments & Receipts › L3 Tax config › L5 service_areas.pst_enabled
- Severity: HIGH   Priority score: 4×5×3 = 60
- Status: ASSUMED (config VERIFIED: GST-only)   Existing item: **G9** (`ACTION_ITEMS.md:17748`, open since 2026-08-22), A5 MONEY-003, ESCALATIONS E1 — *re-found, not re-filed*
- Adversary: SK Ministry of Finance auditor; corporate customer's accountant
- Evidence: `backend/features.py:810-816` comment + `service_areas.pst_enabled=false` on all SK areas; `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md`; §0.2 #1 snippet of PST-46 (rev. Feb 2025): PST applies to "taxicab or limousine transportation services … and other passenger transportation services" — the same snippet G9 already recorded.
- Why it is stuck (report, don't re-file): every session that touches it is egress-blocked from `sets.saskatchewan.ca`; the item's action is "get a human to read the bulletin" and it has no owner or date. It has been "stuck on a 10-minute human read" for 33 days while real Saskatoon volume starts.
- Recommendation: assign to the accountant with a 7-day deadline; record URL+date in G9; if PST applies, enable via `pst_enabled` (no deploy) *and* decide retroactive remittance with the accountant. Alternative: keep GST-only until challenged — rejected: liability compounds per ride.
- Blast radius: `features.py:838,992`, all SK `service_areas` rows, every receipt, corporate statements, `service_area_tax_history` (migration 376).
- Rollout: config only.   Rollback: config only; retroactive under-collection is *not* reversible by config.
- Verification to close: G9 checkbox with citation; `test_calculate_all_fees_tax.py` fixture matching the decided rate.

### COMP-004 — Municipal TNC licences (Regina, Saskatoon) and the Saskatoon per-trip fee have no evidence, no field and no accrual anywhere in the repo
- Hierarchy: L2 Platform Foundation › L3 Service areas › L5 municipal licensing
- Severity: HIGH   Priority score: 4×4×3 = 48
- Status: INFERRED (requirement, §0.2 #3/#4) / VERIFIED (absence in code)   Existing item: **G2 (second)** `ACTION_ITEMS.md:17542` open since 2026-08-21; `saskatoon-launch.md` R-2/I-2; `company-insurance-and-licensing.md:46-56` — *re-found*
- Adversary: municipal licensing inspector; competitor filing a complaint
- Evidence: Saskatoon Bylaw No. 9651 requires a TNC licence and "$0.12 per trip … as well as a surcharge of $0.15 per trip" remitted monthly; Regina requires a TNC licence under its Vehicle(s) For Hire Bylaw. Repo: no `licence_number`/`bylaw`/`municipal_fee` column or constant (`grep -ril "municipal_fee|city_fee|per_trip_fee|tnc_fee|licence_fee" backend` → only `booking_import_service.py`, unrelated); `service_areas` carries free-text `regulatory_authority/region/requirements_url/notes` (`test_service_area_create_regulatory.py`) but no licence id/expiry; no per-trip fee ledger line.
- What happens: Spinr may be operating without a municipal licence (UNKNOWN — a licence could exist on paper outside the repo) and, if the Saskatoon fee applies, is accruing an unbooked $0.27/trip liability that no report can total.
- Why it is stuck: G2's action is "call the City"; unowned for 34 days.
- Recommendation: (a) human confirms both licences and fee schedules; (b) additive `service_areas.municipal_licence_number/expiry` + a `municipal_fee_per_trip` config, and a monthly admin report summing trips × fee (mirror of the SGI km report) — record-only, never a rider line item unless the bylaw says so (What Spinr Is NOT: no hidden fees). Alternative: bake fee into fare — rejected (hidden fee + no source).
- Blast radius: additive; no existing caller.
- Rollout: config + report.   Rollback: drop config.
- Verification to close: licence numbers in `company-insurance-and-licensing.md` §3 with City confirmation; monthly report test.

### COMP-005 — SGI monthly kilometre reporting appears mandatory ("even if zero km") but Spinr only has an on-demand admin report, no schedule, no submission record
- Hierarchy: L2 Safety & Insurance › L3 SGI reporting › L5 monthly km return
- Severity: HIGH   Priority score: 4×3×4 = 48
- Status: INFERRED (obligation, §0.2 #2) / VERIFIED (code)   Existing item: partially `sgi-quarterly.md` §4 (quarterly/annual not built) — the *monthly km* obligation is new (not in `regulatory-sk.md` "Provincial reporting", which lists quarterly/annual/on-demand only)
- Adversary: SGI ("failure to report or submit payment may result in suspension or cancellation of the TNC's operations")
- Evidence: SGI TNC application snippet: premium "$0.11 per kilometre plus PST", "TNC is required to report even if zero kilometres have been driven"; code: `_SGI_RATE_PER_KM = Decimal("0.11")` and per-phase detail builder "Monthly cadence by default" (`routes/admin/compliance.py:1085,1064-1077`), endpoint `get_insurance_billing_sgi` (`:1411`); `core/lifespan.py` has no SGI/insurer loop (grep); no `sgi_submissions`/`insurer_reports` table; `sgi-quarterly.md:150-152` confirms no periodic pipeline. Period 1 km is accumulated (`routes/drivers/location.py:67,548`) but the billing report bills Period 2/3 only, while SGI's app requirement says "track kilometres in all phases".
- What happens: a missed month = suspension risk of the whole operation; nothing in the system reminds anyone or proves a return was filed. Whether Period 1 km must be *reported* (even if not billed) is UNKNOWN.
- Root cause: the obligation was encoded as a report, not as a calendar + evidence record.
- Recommendation: add a `regulatory_filings` table (filing_type, period, due_at, filed_at, artifact_ref, filed_by) + a monthly reminder loop (leader-locked, replay-safe) that opens an admin task on the 1st and alerts on the due date; keep the report as the artifact. Alternative: calendar reminder outside the system — rejected: no evidence trail for an SGI audit.
- Blast radius: additive; new loop must follow `spinr-background-loop` contract.
- Rollout: additive.   Rollback: disable loop.
- Verification to close: SGI confirms cadence/fields (human); loop test; first filed row.

### COMP-006 — Receipt PDF fallback bundles GST+PST into one "Tax" line (re-confirmed)
- Hierarchy: L2 Payments & Receipts › L3 Receipt PDF › L5 missing `tax_breakdown`
- Severity: MEDIUM   Priority score: 3×3×2 = 18
- Status: VERIFIED   Existing item: A5 **MONEY-002**; A29 (corporate statement analogue) — *re-found*
- Adversary: CRA/SK auditor; corporate customer claiming ITC
- Evidence: `backend/utils/receipt_pdf.py:191-198` `rows.append(("Tax", _money(gap)))` when `had_tax` is False; CLAUDE.md "must show GST … and PST … as separate line items".
- Recommendation/Alternative/Blast radius: as MONEY-002 — recompute the split from `service_area_tax_history` (migration 376) for the ride's date rather than one gap line. Additive. Verification: receipt test with `tax_breakdown=None`.

### COMP-007 — Retention drift in both directions: indefinite tombstones and never-purged safety incidents (over-retention), and three doc-vs-code number mismatches
- Hierarchy: L2 Privacy & Data Governance › L3 Retention purge › L5 horizons
- Severity: MEDIUM   Priority score: 3×4×3 = 36
- Status: VERIFIED   Existing item: B18/B23 (closed — the *decisions*), DV-8 (closed); the GPS 2-vs-3 mismatch and the indefinite-retention items have **no ACTION_ITEMS id** (grep "2-vs-3", "data-classification", "gps retention" → 0 `###` hits) → new
- Adversary: Privacy Commissioner (PIPEDA Principle 4.5 — retain only as long as necessary; documented retention schedule must match practice)
- Evidence: (a) migration `216:21` and its COMMENT: "drivers with insurance history stay tombstoned indefinitely" vs `regulatory-sk.md` Right-to-delete #4 "Driver documents … retained 7 years post-deactivation"; (b) `safety_incidents` appears in no purge step (grep in 216/289/296 → 0) vs doc #3 "longer of (7 years, investigation close + 2 years)"; (c) `docs/data-classification.md:20,53,168` GPS trace **2 y** vs SQL **3 y** (`296:152`), flagged open in the checklist since 2026-08-17; (d) `regulatory-sk.md` #5 "communication logs … anonymized after 1 year" vs `ride_messages` hard-delete at 90d (`296:155,209`); (e) `data-classification.md:166` "Rider account PII 2 years post-deletion" vs Step N 30-day scrub + Step H 7-year delete; (f) `regulatory-matrix.md` audit logs "3 y (SOC2)" vs SQL 7 y (`296:158`).
- What happens: a published `data-retention-schedule.md` (correctly held back) cannot be issued; an OPC inquiry gets three different answers for the same field; driver tombstones and incident rows outlive every stated horizon.
- Root cause: numbers were copied into four docs and one matrix by hand; only the SQL is authoritative and nothing diffs the docs against it.
- Recommendation: make SQL the single source — a test that parses `c_*_age` constants from the latest `purge_pii_retention` migration and asserts the numbers in `data-classification.md`/`data-retention-schedule.md` (compliance-as-code §5 row R-2); add explicit Step O (`safety_incidents` at max(7y, closed_at+2y)) and a driver-tombstone horizon (7y after last insurance period `ended_at`) — both flagged, dry-run first. Alternative: keep indefinite — rejected: PIPEDA 4.5 has no "audit convenience" exception.
- Blast radius: `purge_pii_retention()` callers: `utils/retention_purge.py` only; tests `test_data_export_purge*.py`.
- Rollout: additive steps behind `p_dry_run` first.   Rollback: `CREATE OR REPLACE` back to migration 436's body (SQL, no data loss until first live run).
- Verification to close: doc-vs-SQL test green; dry-run counts reviewed.

### COMP-008 — Consent coverage has three holes: legacy-imported users, corporate self-serve signup, and a re-consent mechanism that is dark
- Hierarchy: L2 Auth & Identity › L3 Consent › L5 versioning
- Severity: MEDIUM   Priority score: 3×4×3 = 36
- Status: VERIFIED   Existing item: LGL-3 (reports/audits 2026-07-22), migration 334/356, `legacy_consent_notice_enabled` (dark) — partially tracked; corporate gap flagged in `auth.py:146` comment only
- Adversary: Privacy Commissioner (Principle 3 — knowledge and consent); plaintiff contesting ToS/arbitration enforceability
- Evidence: `routes/legacy_consent.py:1-25` — every legacy-imported rider/driver (the 189-driver import, plus riders) has `consent_version IS NULL`; notice gated by `schemas.py:722` default `False`; `auth.py:143-150` "corporate self-serve signup wrote zero consent_version"; grep `consent` in `routes/corporate_signup.py` → 0 hits (still true). ToS/Privacy published 2026-08-17 **without counsel review** (checklist process note 2).
- What happens: the population most likely to dispute anything (imported drivers whose SIN/bank data was later exposed — COMP-013) never accepted any Spinr terms; corporate admins accepted none.
- Recommendation: flip `legacy_consent_notice_enabled` after a staging pass (mechanism already wired both apps); add `consent_accepted` + version stamp to corporate signup (mirror `auth.py:1300`); record decision in `memory.md`. Alternative: rely on implied consent from continued use — rejected: OPC guidance treats implied consent as insufficient for sensitive data (bank/SIN/location) (INFERRED, not fetched).
- Blast radius: `otp.tsx`/`profile-setup.tsx`/`index.tsx` in both apps (already live-wired per `schemas.py:716`); `corporate_signup.py`.
- Rollout: flag + additive field.   Rollback: flag off.
- Verification to close: count of `users.consent_version IS NULL` trending to 0; corporate signup test asserting stamp.

### COMP-009 — The independent-contractor agreement — the one document built to defeat a misclassification claim — is a draft with no e-signature flow; nine other legal texts are live without counsel review
- Hierarchy: L2 Driver Onboarding › L3 Legal documents › L5 ICA execution
- Severity: MEDIUM   Priority score: 3×4×3 = 36
- Status: VERIFIED   Existing item: checklist rows (Draft); no ACTION_ITEMS id for "ICA e-signature" (grep `e-sign|esign|docusign` in `backend/routes/drivers`, `driver-app/app` → 0 code hits)
- Adversary: driver claiming employee status (SK Employment Standards: "treats that person as an employee … employment standards would apply"; CRA 4-factor test — §0.2 #12); plaintiff's lawyer
- Evidence: `docs/legal/independent-contractor-agreement.md` header ("meant to be presented for e-signature … before their first ride"), checklist row "☐ E-signature capture flow built at onboarding"; ToS Part B §11 is the only live contractor clause. Positive: no control-of-work copy found (§1 item 9). Negative signals a lawyer would still use: mandatory "Spinr Pass" subscription gate (`require_driver_subscription`, `status.py:895+`) is a *fee paid to the platform to work* — neutral-to-helpful for contractor status but must not be framed as a condition of continued engagement; `min_driver_rating`/acceptance-rate dispatch filters exist (`matching.py:549` columns) — allowed as "quality metrics with transparent thresholds" only if thresholds are published to drivers (not verified).
- Recommendation: build in-app signature capture (typed name + timestamp + IP + document version → `driver_agreements` table, append-only) gated at approval; counsel review of ICA first (kind-3). Alternative: DocuSign — rejected for cost and a new subprocessor (DPA register already has a "VERIFY" row for Supabase).
- Blast radius: onboarding approval path (`routes/admin/drivers.py`, 143 commits — hotspot #2, HIST).
- Rollout: additive table + flag.   Rollback: flag off.
- Verification to close: `driver_agreements` row exists for every `status='active'` driver.

### COMP-010 — WAV: "estimated wait for next WAV availability + standard alternative" promised in `regulatory-sk.md` is not built; rider sees a greyed toggle
- Hierarchy: L2 Rider Booking › L3 Accessibility options › L5 no WAV online
- Severity: LOW   Priority score: 2×2×3 = 12
- Status: VERIFIED   Existing item: new (doc-vs-code)
- Adversary: SK Human Rights Commission complainant
- Evidence: `rider-app/app/ride-options.tsx:1062-1070` disables the toggle with "No WAV drivers nearby"; no ETA-to-next-WAV computation anywhere (grep `next WAV|wav_eta` → 0). Dispatch side is correct (§1 item 4).
- Recommendation: correct the doc to what ships (honest) *or* add a "notify me when a WAV driver is available" opt-in (additive, cheap). Alternative: compute an ETA from WAV drivers' `went_offline_at` history — rejected: speculative accuracy, worse than no promise.
- Blast radius: rider `ride-options.tsx` only.   Verification: doc line matches UI; or notify test.

### COMP-011 — Accessibility: WCAG 2.1 AA is a target, not a tested state; `accessibility@spinr.ca` is cited 13 times and not provisioned; no SMS fallback for ride-state pushes
- Hierarchy: L2 Shared Frontend › L3 Accessibility › L5 mobile conformance
- Severity: MEDIUM   Priority score: 3×3×3 = 27
- Status: VERIFIED   Existing item: checklist rows (accessibility-statement, privacy-policy) — "accessibility inbox" is BLOCKED on a human; WCAG mobile audit has no ACTION_ITEMS id
- Adversary: plaintiff's lawyer (inaccessible app; SK Human Rights Code duty to accommodate)
- Evidence: `docs/ACCESSIBILITY.md:40-42` rider/driver/web "Not yet audited"; crude coverage: `accessibilityLabel` present in 23/44 `rider-app/app/*.tsx` and 14/38 driver screens (grep); `grep -rn accessibility@spinr.ca docs/legal backend rider-app/app` → 13 references; hearing-impairment rule "SMS fallback for every push affecting ride state" (`regulatory-sk.md`) — no `send_sms` in `routes/rides/lifecycle.py`/`matching.py`/`utils/notifications.py` (grep). Published `accessibility-statement.md` correctly avoids a conformance claim.
- Recommendation: provision the inbox (human, 1 day); run one Expo-web axe pass per app in CI as a floor (`admin-dashboard` already has axe); decide (product) whether SMS-on-ride-state is a promise or drop it from the doc. Alternative: full manual VoiceOver/TalkBack audit — needed eventually, not a CI gate.
- Blast radius: docs + CI only.   Verification: inbox answers; axe job green; doc matches.

### COMP-012 — Audio recording: not built (correct); the framing to carry forward is *PIPEDA-governed two-party notice*, not Criminal Code one-party consent
- Hierarchy: L2 Safety › L3 Night-ride protections › L5 audio
- Severity: RECOMMENDATION   Priority score: —
- Status: VERIFIED (no code: grep `audio|recordAsync|expo-av` in both apps and `routes/*/safety.py` → 0) / INFERRED (law, §0.2 #10)   Existing item: A8 escalation, ESCALATIONS E9 — *closing the framing question, not re-filing*
- Adversary: Privacy Commissioner
- Evidence: `.claude/context/domain-safety.md:150-153` states SK is "one-party consent … but a platform recording both parties needs explicit consent from each". Snippet: s.184 one-party rule applies to *participants*; "if you are a business, PIPEDA … applies on top … and requires notification, purpose disclosure, and consent alternatives". Saskatchewan has no private-sector privacy statute, so PIPEDA governs Spinr directly.
- Recommendation: when/if built — per-ride in-app notice to both parties before pickup, opt-out = no recording (not "no ride"), encrypted at rest, access only on incident, retention ≤ the safety-incident horizon, PIA first (the AI-surfaces PIA `docs/compliance/pia-ai-surfaces-2026-08.md` is the template). Nothing to do now. Alternative: driver-owned dashcam policy — allowed today under s.184 with a visible in-car notice; Spinr should say so in community guidelines (copy-only).

### COMP-013 — Incident 1 (346 driver records incl. SIN, bank, licence numbers public ≥11 days; still exposed on 187 of 500 checked branches; repo still public): RROSH "no" is an owner call the Commissioner is unlikely to share, OPC and SGI not notified, GitHub purge request still unsubmitted
- Hierarchy: L2 Privacy & Data Governance › L3 Breach response › L5 Incident 1
- Severity: **CRITICAL** (as a compliance exposure; engineering remediation is MEDIUM)   Priority score: 5×4×4 = 80
- Status: VERIFIED (record) / INFERRED (RROSH reading, §0.2 #7)   Existing item: `docs/audit/breach-record.md` Incident 1 still-open item 1; `docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md` "**DRAFT, NOT SUBMITTED**"; W0 human question "GitHub PII-purge ticket status"
- Adversary: Privacy Commissioner; plaintiff's lawyer (class of 346 drivers); SGI
- Evidence: breach-record rows: data = SIN plaintext, bank/transit/institution, licence numbers, DOB, home address, lat/lng; window 2026-08-13 → 2026-08-26/27 (working tree) and until 2026-09-11 (git history on `main`); repo `visibility: public` re-verified 2026-09-13; "187 (37.4%) still expose … ~875 unchecked"; RROSH "no" (2026-08-27, re-affirmed 2026-09-13 with written sign-off); "OPC notified: No"; "SGI notified: No — **Applicable**"; post-mortem "Not yet written"; staging tables were live over PostgREST with RLS disabled until 2026-08-31 (still-open item 4). OPC guidance (snippet): RROSH = sensitivity × probability of misuse; SIN + bank is the OPC's own canonical "sensitive" example (Interpretation Bulletin surfaced in the same search).
- What happens: if the OPC later learns of it (a driver complaint, a security researcher), the absence of a report is a separate offence from the breach ("knowingly contravene … reporting, notification and record-keeping requirements … fines" — snippet). The 24-month record exists, which helps; the determination rationale ("only 2 contributors") measures who wrote, not who read.
- Root cause: the RROSH determination was made by the data controller's owner without privacy counsel, against a public-internet exposure of the highest-sensitivity categories.
- Recommendation: privacy counsel reviews the RROSH determination *this week*; if reversed, file the OPC report (form `pipeda_pb_form_e.pdf`), notify the 346 drivers, and notify SGI (licence numbers). Independently: submit the GitHub purge ticket (needs the `commit-map` from the WSL machine — human), run the wholesale rewrite across the remaining 1,375 branches, make the repo private (or record the launch-date business decision with counsel's initials), and write the post-mortem. Alternative: leave as decided — rejected: the determination's own record carries two "should be re-examined" caveats it never fully absorbed.
- Blast radius: none in code; reputational/legal.
- Rollout: n/a.   Rollback: n/a (notification cannot be un-sent — which is exactly why counsel, not an agent, decides).
- Verification to close: counsel's written RROSH opinion appended to `incident-1-rrosh-signoff`; purge ticket number; branch scan 0/1,375.

### COMP-014 — SAFETY-003: "declared online but unreachable" keeps an open Period 1 row — coverage reading is favourable to the driver, but the audit trail cannot distinguish "waiting" from "phone dead", and SGI's app rule wants km "in all phases"
- Hierarchy: L2 Safety & Insurance › L3 Insurance periods › L5 Period 1 linger
- Severity: LOW   Priority score: 2×3×3 = 18
- Status: VERIFIED (design) / ASSUMED (SGI reading)   Existing item: A8 SAFETY-003, ESCALATIONS E8 — *carried, with a concrete additive proposal*
- Adversary: SGI claims adjuster (post-incident: "was this driver actually available at 02:14?")
- Evidence: `utils/presence_sweeper.py` retired (A8); `compliance.py:1064-1071` bills Period 2/3 only, so no premium leakage; Period 1 km accumulates only while location writes arrive (`location.py:548`), so an unreachable driver accrues 0 km — the row is open but idle.
- Recommendation: do not re-introduce TTL close-out. Add an additive, append-only `driver_presence_gaps` (driver_id, period_row_id, lost_at, regained_at) written by the WS disconnect path, so the ledger shows *both* facts. Ask SGI whether an open-but-unreachable Period 1 is contingent-covered (E8). Alternative: TTL auto-offline — rejected: it caused mass false-offline flips (the bug the retirement fixed).
- Blast radius: `socket_manager.py`/`routes/websocket.py` disconnect path; no change to `driver_insurance_periods`.
- Verification: SGI answer recorded; gap rows appear in a WS-drop test.

### COMP-015 — CRA: the platform's own tax posture (Part XX.1 registration, GST supplier of record, GST number on receipts, T4A-vs-DPI) is undetermined while the code hard-blocks drivers on *their* GST number
- Hierarchy: L2 Driver Earnings & Payouts › L3 Tax › L5 filings
- Severity: HIGH   Priority score: 4×4×3 = 48
- Status: ASSUMED (all four; §0.2 #5/#8/#11 snippets support the *rules*, not Spinr's classification)   Existing item: A5 CRA table rows 1–5, ESCALATIONS E2/E3/E5, `2026-07-29-t4a-filer-handoff-export.md` §10 — *re-found*; the **receipt GST-number** point is new
- Adversary: CRA auditor; corporate customer denied an input tax credit
- Evidence: driver GST/BN gate `routes/drivers/payouts.py:762-780` (A5) — consistent with the snippet ("must register … even if … a small supplier"; taxi-business definition since 2017-07-01); Part XX.1 handoff export built SIN-free (`routes/admin/compliance.py:805-830`) with "filing decision … not made by this export"; T4A job `lifespan.py:633-642` (Feb 28) with $500 (`t4a_pdf.py:19,230`); **no GST registration number is printed on any receipt or corporate statement** (grep `registration|gst_number|business number` in `receipt_pdf.py`, `rides/_shared.py`, `corporate_statement_pdf.py`, `schemas.py`, `admin/settings.py` → 0). CRA ITC documentation rules require the supplier's GST/HST registration number on invoices over a small threshold (ASSUMED — canada.ca blocked).
- What happens: if the *driver* is the supplier, corporate customers need the driver's GST number on each receipt to claim ITCs and Spinr's receipts are deficient for every corporate ride; if *Spinr* is the supplier, Spinr must be remitting GST it currently treats as the driver's. Either way the Jan 31 2027 Part XX.1 return for calendar 2026 (and a possible late 2025 return) needs an RZ account nobody has confirmed exists.
- Recommendation: one accountant engagement answering E2/E3/E5 + Part XX.1 + receipt content in writing; then either print `drivers.gst_number` (already collected) on receipts or Spinr's, per the answer. Alternative: print both — rejected until supplier-of-record is settled (a wrong number is worse than none).
- Blast radius: `receipt_pdf.py`, `rides/_shared.py::_build_fare_breakdown`, `corporate_statement_pdf.py`, email receipt template.
- Rollout: additive line behind `receipt_show_gst_number` setting.   Rollback: setting off.
- Verification to close: accountant letter filed under `docs/compliance/`; receipt test.

### COMP-016 — `audit-framework/regulatory-matrix.md` over-scopes and contradicts code (OLA/ACA apply to federally-regulated entities; driver age 21 vs code 18; audit-log 3 y vs SQL 7 y; AML/FINTRAC MSB row unexamined)
- Hierarchy: L2 Engineering Gates › L3 Audit framework › L5 regulatory matrix
- Severity: LOW   Priority score: 2×2×3 = 12
- Status: INFERRED (OLA/ACA scope), VERIFIED (age, retention mismatches)   Existing item: new
- Adversary: auditor reading Spinr's own matrix and finding it wrong
- Evidence: matrix rows OLA/ACA "Federally-regulated commerce/sector" — a Saskatchewan TNC is provincially regulated (Vehicles for Hire Act) and not a federal work/undertaking (INFERRED); `SAFE-AGE` "Driver ≥21 (SK policy)" vs `status.py:704` and `profile.py:47` enforce ≥18; matrix audit logs "3 y" vs SQL 7 y; `AML` row asserts "$10k aggregate threshold reporting · MSB registration" — whether corporate wallet top-ups make Spinr a reporting entity is a real question nobody has asked (human).
- Recommendation: mark OLA/ACA "not applicable unless federally regulated (confirm)"; fix age/retention rows to match code or open items to change code; add the MSB question to §10. Alternative: delete the matrix — rejected: it is the only per-module regulatory index the audit framework has.

### COMP-017 — SGI quarterly ride-volume/incident report and annual roster remain unbuilt; on-demand export is built (re-confirmed)
- Severity: MEDIUM   Priority score: 3×3×3 = 27   Status: VERIFIED   Existing item: `docs/compliance/sgi-quarterly.md` §4/§5 (open since 2026-06-02, owner "unassigned")
- Evidence: §4 table rows 2–4 ❌/⚠️; `scripts/compliance_export.py` ✅; `sgi_form_filler.py` D00032/D00033 fill exists, unscheduled. Why stuck: §5's five questions are for SGI, unowned for 114 days.
- Recommendation: fold into COMP-005's `regulatory_filings` calendar; one SGI call answers §5 and COMP-005's cadence together.

### COMP-018 — No compliance gate runs in CI; `spinr-regulatory-compliance-checker` is invoked by humans only
- Severity: MEDIUM   Priority score: 3×4×2 = 24   Status: VERIFIED   Existing item: new (HIST decay family: advisory gates)
- Evidence: `grep -l compliance|pipeda|regulatory .github/workflows/*.yml` → `pr-checks.yml` (label only), `renewal-calendar-monitor.yml`, `subprocessor-audit.yml`, `subprocessor-monitor.yml` — none runs the checker or any retention/tax/eligibility assertion; the checker agent exists only as `.claude/agents/*.md`. Tests that *would* serve as gates exist (`test_calculate_all_fees_tax.py`, `test_e2e_wav_dispatch.py`, `test_driver_crc_consent.py`, `test_data_export_purge*.py`, `test_insurance_period_*.py`, `test_go_online_availability.py`, `test_dsar_export*.py`) but nothing marks them as the compliance suite or fails a PR on a `PIPEDA-relevant`/`SK Transportation Act` checkbox mismatch.
- Recommendation: §5/§6 — a `compliance` pytest marker + a required CI job, plus the doc-vs-SQL retention test (COMP-007). Alternative: keep agent-only — rejected: HIST shows every advisory gate decays.

## 3. Legal document readiness (spinr-legal-readiness-reviewer method)

Method: for every ☐/☒ row in `docs/legal/legal-text-publication-checklist.md` and every header gating condition, classify as **[1] code-checkable** (resolved here by grep/read), **[2] live-data** (needs a DB session), or **[3] human decision** (named party). Rows already ☑ with a cited file:line were not re-derived. "Published" below means a `legal_documents` row exists (migrations 361/400/406 + 2026-08-17 inserts) — **applied-status of those migrations in production is UNKNOWN (CARTO-005/G2)**, so "live" is itself INFERRED for every document.

```
SPINR LEGAL READINESS REVIEW — docs/legal/*.md (2026-09-24)
=========================================
terms-of-service.md: Published (with open gap)
  [3] Counsel review — BLOCKED, needs counsel licensed in SK. Published 2026-08-17 without it (owner-accepted).
  [2] Combined text vs per-audience rows in legal_documents — REQUIRES DB SESSION (header says "not re-diffed against live DB rows").

privacy-policy.md: Published (with open gaps)
  [3] Counsel review — BLOCKED (counsel).
  [3] Signed Supabase DPA — BLOCKED (Legal); docs/dpa-register.md:28 still "⚠ VERIFY".
  [2] Railway SUPABASE_REGION env — REQUIRES infra session (railway.json carries no env).
  [1] Gemini + LogRocket disclosure — STILL OPEN, blocks on subprocessor-list.md; LogRocket confirmed live in rider-app/app/_layout.tsx:53,237-244 (iOS on, Android off by default) so the disclosure is factually required.
  [1] GPS 2yr-vs-3yr in docs/data-classification.md — STILL OPEN: data-classification.md:20,53,168 still say 2 y; SQL says 3 y (migration 296:152). COMP-007.
  [1] DV-8 30-day deletion job — NEWLY CLOSED vs the checklist text (checklist row still says "NOT built"): retention_purge_loop (core/lifespan.py:513) → purge_pii_retention() Step N (migration 296:415-470) scrubs first_name/last_name/email/profile_image and deletes saved_addresses at 30 d. The draft's own note 4 already records this; the CHECKLIST ROW is stale → recommend updating the row. (Caveat [2]: migration 296 applied in prod? UNKNOWN.)
  [3] accessibility@spinr.ca provisioned — BLOCKED, needs Zoho/Workspace admin. Still referenced 13× (grep).
  [3] Data Transfer disclosure sentence into the live row — BLOCKED on counsel-reviewed republish (by design).

community-guidelines.md: Published (with open gap)
  [3] Counsel review — BLOCKED.
  [1] Safety-hold max-duration constant — STILL OPEN: grep safety_hold|hold_days in backend/services, routes/admin/drivers.py, schemas.py → 0 (only dormancy thresholds, unrelated). Document correctly states no duration.

non-discrimination-policy.md: Published (with open gap)
  [3] Counsel review — BLOCKED.
  [3] Protected-grounds list vs SK Human Rights Code 2018 — BLOCKED (counsel; saskhrc.ca egress-blocked this session, §0.1).
  [1] WAV-availability language — previously CLOSED 2026-08-20; re-confirmed estimates.py:549,632 unchanged.
  [1] Service-animal refusal handling — CLOSED (ride_flow.py:708-739, ride_cancel.py:100-121) and consistent with the SHRC snippet (§0.2 #9).

driver-deactivation-appeals-policy.md: Published (with open gap)
  [3] Counsel review — BLOCKED.
  [3] Real SLA timeframes — BLOCKED (safety team); re-confirmed no SLA/"business days" constant in services/driver_appeals.py (grep → 0).

accessibility-statement.md: Published (with open gaps)
  [3] Counsel review — BLOCKED.
  [3] accessibility@spinr.ca — BLOCKED (infra admin).
  [1] "Not yet audited" limitation still accurate — CLOSED (docs/ACCESSIBILITY.md:40-42 unchanged).

insurance-coverage-periods.md: Published (with open gap)
  [3] Counsel + SGI Auto Fund review — BLOCKED.
  [1] In-app Safety Center entry point — STILL OPEN: rider-app/app/safety-hub.tsx has no insurance/legal row (grep → 0); driver-app has only report-safety.tsx, no Safety Center.
  [1] Consistency with ToS §6/§13 — previously CLOSED 2026-08-18; not re-derived.
  [1] NEW cross-check: document describes Period 2 as starting when the driver "accepts"? — NOT re-read line-by-line this pass; CLAUDE.md says Period 2 starts at driver_assigned (claim/offer). A human should diff the published prose against that rule (a plain-language explainer that says "when you tap Accept" would be wrong by up to ~15 s per offer).

company-insurance-and-licensing.md: Draft — not eligible
  [3] Counsel/broker review; every line item; provincial TNC licence; SGI fleet endorsement; municipal bylaw number/fees — ALL BLOCKED (broker, City of Saskatoon/Regina, SGI). See COMP-004; G2/G4.

cancellation-fee-policy.md: Published (with open gap)
  [3] Counsel review — BLOCKED.
  [1] Fee split / grace / no-show from config — previously CLOSED; not re-derived.
  [1] 60-day dispute window enforced — STILL OPEN: routes/disputes.py has no time-based cutoff (grep days|window|cutoff|timedelta → only the "1-2 business days" acknowledgement copy at :114). Published as a disclosed default; product should either enforce or drop the number.

promotions-referral-terms.md: Published (with open gap)
  [3] Counsel review — BLOCKED.
  [2] No service-area override diverges from global default — previously verified 2026-08-19; STALE (36 days) — re-check immediately before any republish.

background-check-consent.md: checklist says "Draft" — NEWLY CLOSED to "Published (with open gaps)"
  [1] Published row — NEWLY CLOSED: migration 406_seed_background_check_consent_policy.sql seeds the driver-only legal_documents row (content copied 2026-09-04, owner-directed, no counsel). Checklist row is stale. ([2] applied in prod? UNKNOWN.)
  [1] "Presented during onboarding" — NEWLY CLOSED: driver-app/app/become-driver.tsx:133-142,596-612 fetches the consent text in the wizard and POSTs /drivers/crc-consent right after registerDriver(); settings.tsx:399 remains the re-consent path. The checklist's 2026-08-20 "reachable only from settings" note is stale.
  [1] Consent re-checked at go_online — CLOSED but FLAG-GATED (status.py:850-880 under enforce_driver_eligibility_recheck, default false) → in practice not enforced (COMP-002).
  [3] Counsel review — BLOCKED.
  [3] CRC/VSC retention figure — BLOCKED (Legal/Safety); re-confirmed no constant.
  [3] Adverse-decision response step — BLOCKED (safety/eligibility team).
  [3] Primary-source verification of the police-service model — BLOCKED (human browser; regina.ca/saskatoon.ca blocked again this session).

corporate-master-services-agreement.md / corporate-data-processing-addendum.md: Draft
  [3] Counsel review, every bracketed commercial term, DPA §4.2 security detail, residency attestation — ALL BLOCKED (counsel/Legal). Note for §4.2: a factual security-measures list is code-checkable once counsel asks — RLS status is NOT uniformly true (C43: settings table RLS disabled; breach-record item 4) — do not let counsel write "all tables protected by RLS".

independent-contractor-agreement.md: Draft
  [3] Counsel review against CRA worker-classification guidance — BLOCKED (counsel).
  [3] Bracketed fields — BLOCKED (product/finance).
  [1] E-signature capture at onboarding — STILL OPEN: no signature/e-sign/docusign code in backend/routes/drivers or driver-app/app (grep → 0). COMP-009.
  [3] Arbitration-clause decision — BLOCKED (counsel + owner).

subprocessor-list.md: Draft
  [3] Counsel review — BLOCKED.  [3] LogRocket processing region — BLOCKED (vendor). [1] Published together with privacy §3 — depends on above.

cookie-policy.md: Draft
  [1] Website cookie banner — CANNOT CHECK: no website source in this repo (ls → no web/site/landing dir); checklist's own "confirm which repo/host serves spinr.ca" is unanswered → [3] infra/owner.

data-retention-schedule.md: Draft — DO NOT PUBLISH (its own rule)
  [1] Gate = privacy-policy gates + GPS contradiction — STILL OPEN (COMP-007). Its header already correctly records DV-8 as closed and data-classification.md as "the stale side".

casl-marketing-consent-disclosure.md: Draft
  [3] Counsel review — BLOCKED.
  [1] Mailing address value — STILL OPEN as a VALUE ([2]/[3]): mechanism exists (schemas.py:497-499 postal-address parts; utils/email_layout.py:293 renders from admin Settings) — whether the prod Settings row is filled is DB state → REQUIRES DB SESSION.

breach-notification-letter-template.md: Internal template
  [3] Counsel review once — BLOCKED. [1] Contact-email drift fixed — previously CLOSED. NEW: Incident 1 is the first real use-case and no letter was sent (COMP-013) — the template's readiness is moot until the RROSH question is re-decided.

website-terms-of-use.md / trademark-copyright-notice.md / careers-privacy-notice.md: Draft
  [3] Counsel; hosting repo; CIPO status; ATS name — ALL BLOCKED (counsel/owner). Not code-checkable from this repo.

SUMMARY
  Closed/newly closed this pass:   4  (DV-8 checklist text; background-check-consent published + onboarding-wired; service-animal enforcement; accessibility limitation still accurate)
  Still open (code):               6  (GPS 2-vs-3 doc; Gemini/LogRocket disclosure; Safety Center link; dispute window; ICA e-signature; safety-hold constant)
  Still open (data):               4  (per-audience rows; migrations 296/361/400/406 applied?; referral overrides; CASL address value)
  Blocked (human):                 ~30 (counsel on all 23 docs; broker; SGI; two Cities; infra admin for the inbox; LogRocket region)

VERDICT: 2 checklist rows are stale in the READY direction (privacy-policy DV-8 text; background-check-consent status) — recommend a checklist-only PR with Change Impact Log; everything else STILL BLOCKED ON COUNSEL — 9 documents are live to riders/drivers with zero legal review, which is the single largest legal-readiness fact and is a recorded owner decision, not an oversight.
```

## 4. Obligation matrix

Columns: **Enforced in code** (`path:line`, VERIFIED by read unless noted) · **Primary source** (URL + date fetched, or *INFERRED* = search snippet 2026-09-24, or *ASSUMED* = repo doc only) · **Status** (met / partial / missing / unknown). Rule: a row is never "met" on the source column unless a primary page was fetched — none was (§0.1), so the best any row's *source* can be is INFERRED.

### 4.1 `regulatory-sk.md` — driver onboarding eligibility gate ("at signup, again on every go_online")

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| E1 | Class 5 licence (Class 1–4 needs approval) | `routes/drivers/status.py:733-744` — **flag-gated, default off** (`schemas.py:320`); onboarding: `become-driver.tsx` collects class, no validation | **INFERRED contradicts**: CanLII regs/CBC snippet says Class 4 (§0.2 #6). ASSUMED in repo. | **unknown** (COMP-001) |
| E2 | ≥3 years licensed | `status.py:769-806` (migration 405 `license_issue_date`), flag-gated, NULL → unblocked; copy at `become-driver.tsx:685` | ASSUMED (no source; regs snippet mentions DIP points, not years) | partial/dark (COMP-002) |
| E3 | Clean abstract (no major violations 3y) | none — no abstract field/check (grep `abstract` → only import mapping) ; `saskatoon-launch.md:476` says "upload at onboarding" — not in code | INFERRED: regs = "<12 DIP points in 2 yrs, no impaired suspensions 10 yrs" (§0.2 #6) | **missing** |
| E4 | No Criminal Code driving offences | none (relies on CRC document upload; no structured check) | ASSUMED | missing (document-only) |
| E5 | CRC + VSC on file, renewed annually | expiry: `status.py:651-670` (`background_check_expiry_date`, unconditional) + `_shared.py:991-996` accept-time; consent: `services/driver_crc_consent.py`, `status.py:850-880` (flag-gated); annual: enforced only via the expiry date the admin enters | INFERRED: "annual criminal record checks" (§0.2 #6) | met (expiry) / partial (annual = admin-entered date) |
| E6 | Vehicle < 10 model years | `status.py:746-768`, flag-gated, NULL → unblocked | **INFERRED: no 10-year rule found in provincial regs snippet**; may be municipal/insurer or Spinr policy | partial/dark; source unknown |
| E7 | Annual vehicle safety inspection | expiry `status.py:651`/`_shared.py:991` (`vehicle_inspection_expiry_date`) | INFERRED: "annual … vehicle inspections" (§0.2 #6) | met (expiry-based) |
| E8 | Ride-share endorsement on SGI Auto Fund policy | `insurance_expiry_date` expiry only; **no check that the uploaded policy carries the endorsement** (human reviewer; `saskatoon-launch.md:438` "confirm gated on a human reviewer") | INFERRED: SGI TNC application; driver personal-policy endorsement ASSUMED | partial |
| E9 | Business licence where municipality requires | none (no field) | INFERRED: Saskatoon/Regina require a *TNC* licence (company-level); per-driver licence UNKNOWN | missing/unknown (COMP-004) |
| E10 | Failures hard-block with a specific reason string | `SpinrException` messages per check (`status.py:645-650,664-670,708-712,736-744,762-768,797-806,829-836,854-862,876-882`) | n/a (Spinr rule) | met |
| E11 | Driver ≥ 18 (code) / ≥ 21 (`regulatory-matrix.md` SAFE-AGE) | `status.py:704-712`, `profile.py:47` → 18 | ASSUMED both; contradiction | unknown (COMP-016) |
| E12 | "Environment-agnostic" endorsement check (pitfalls) | expiry checks not env-gated (VERIFIED) | Spinr rule | met |

### 4.2 Insurance periods (SGI commercial layer)

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| I1 | Per-period logs retained 7 years, never truncated | `driver_insurance_periods` append-only trigger (migration 64:80-128); purge Step H keeps rows ("tombstoned indefinitely", 216:21) — *over*-retains | ASSUMED ("SGI requires") — no SGI page fetched | met (≥7y) |
| I2 | Period derivation from ride state; Period 2 at `driver_assigned` | `utils/insurance_periods.py:145` `record_period_transition`; matching.py claim-time call (CLAUDE.md); `test_insurance_period_*` (9 files) | ASSUMED (SGI period definitions; SGI TNC page blocked) | met (code) / source unknown |
| I3 | Period 3 requires `ride_id` → `in_progress` | migration 64:40 row-level check | Spinr invariant | met |
| I4 | Document expiry blocks Period 1+ on every go_online | §4.1 E5/E7/E8 | ASSUMED | met |
| I5 | Proof-of-coverage QR in driver profile | **none** (grep `qr|proof.of.coverage` in driver-app/backend → 0) | ASSUMED (regulatory-sk.md only) | **missing** |
| I6 | *(new, INFERRED)* Monthly km report to SGI even if zero; app tracks km in all phases | report on demand `admin/compliance.py:1064-1200,1411` (Period 2/3 only); Period 1 km `location.py:548`; no schedule/submission record | INFERRED §0.2 #2 | **partial** (COMP-005) |
| I7 | *(new, INFERRED)* $1M liability certificate to SGI annually | none in repo (`company-insurance-and-licensing.md` placeholders; G4) | INFERRED §0.2 #2 | unknown |

### 4.3 Trip-log retention

| # | Obligation | Enforced in code (SQL numbers, migration 296) | Primary source | Status |
|---|---|---|---|---|
| R1 | Trip record 7 y then hard-delete | `c_ride_keep_age = '7 years'` (296:153), Step B (:186) | ASSUMED (CRA 6-y books-and-records is the usual floor — canada.ca blocked) | met |
| R2 | Driver/vehicle linkage 7 y | rides rows carry driver_id 7 y (Step B); `drivers` tombstone indefinite | ASSUMED | met |
| R3 | GPS pickup+dropoff only, 3 y | `c_gps_anon_age = '3 years'` Step A (:165) + Step I ride_routes geometry (:318); full route trail 90 d (Step C `c_loc_history_age`, :154) | ASSUMED; `data-classification.md` says 2 y (stale) | met (code) / doc drift (COMP-007) |
| R4 | Insurance period transitions 7 y | never deleted (I1) | ASSUMED | met |
| R5 | Receipts with tax lines 7 y | derived from `rides`/`financial_events` (Step H gate 289) | ASSUMED | met |
| R6 | Rider identity fully attributable 7 y | Step H no anonymisation (216) | Spinr decision B18/B23 | met |
| R7 | Daily job actually runs | `core/lifespan.py:513` `retention_purge (24h)`; leader lock; escalates on tick failure (`retention_purge.py:345-360`) | n/a | met (code) / **unknown in prod** (loop health, migration applied — CARTO-005) |

### 4.4 Tax display

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| T1 | GST 5% on every fare | `features.py:836-838` (`gst_enabled` default true, 5.0) | INFERRED (CRA taxi-business rule §0.2 #5) | met |
| T2 | PST 6% where applicable | `pst_enabled=false` all SK areas (`features.py:840-841`) | **INFERRED contradicts**: PST-46 lists "other passenger transportation services" as taxable (§0.2 #1) | **unknown/at risk** (COMP-003, G9) |
| T3 | Separate line items | `receipt_pdf.py:174-190` per-tax rows; fallback bundles (`:191-198`) | Spinr rule / CRA invoice rules ASSUMED | partial (COMP-006) |
| T4 | Supplier GST registration number on receipt | none (grep → 0) | ASSUMED (CRA ITC documentation) | **missing** (COMP-015) |
| T5 | T4A-compatible earnings summary ≥ $500 | `utils/t4a_pdf.py:19,230`; `lifespan.py:633-642` Feb-28 job | INFERRED $500 box-048 threshold (§0.2 #11) | met (code) / filing decision open |
| T6 | Fare floor per municipal minimum | none; doc says "none — verify" | ASSUMED | unknown |
| T7 | Surge cap 2.5× | `fare_service.py:533` clamp; `surge_engine.SURGE_CAP` | Spinr policy ("provincial guideline" ASSUMED — no such provincial rule fetched) | met |

### 4.5 Accessibility

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| A1 | WAV served when a WAV driver is online | `dispatch_service.py:231-244,455`; `matching.py:503,877`; `estimates.py:549`; `test_e2e_wav_dispatch.py` | ASSUMED (regulatory-sk.md); "s.22" comment in dispatch_service.py:242 uncited | met |
| A2 | If none online: show ETA to next WAV + offer standard | `ride-options.tsx:1062-1070` disables toggle "No WAV drivers nearby" — no ETA | ASSUMED | partial (COMP-010) |
| A3 | Service animals mandatory; refusal → account review | `ride_flow.py:708-739`, `ride_cancel.py:100-121` (400 + audit row) | INFERRED: SHRC policy (§0.2 #9) | met |
| A4 | WCAG 2.1 AA customer-facing | admin: axe in CI (`docs/ACCESSIBILITY.md:14-29`); mobile: not audited (`:40-42`); labels 23/44 rider, 14/38 driver screens (crude grep) | ASSUMED (WCAG is a standard; legal duty is SK HRC accommodation, not WCAG per se) | partial (COMP-011) |
| A5 | Hearing: SMS fallback for ride-state pushes; in-app chat | chat exists (`ride_messages`); SMS fallback none (grep) | ASSUMED | partial |
| A6 | Driver training record (matrix SK-HRC) | none | ASSUMED | missing |

### 4.6 Driver classification

| # | Obligation | Enforced in code / copy | Primary source | Status |
|---|---|---|---|---|
| C1 | No mandatory shifts / minimum hours / uniforms / employee terminology | driver-app grep clean (§1 #9); FAQ `driver-faqs-saskatchewan.md:63`; ICA §1.4–1.5 (draft) | INFERRED CRA 4-factor + SK ES (§0.2 #12) | met (copy) |
| C2 | No penalties for going offline | dormancy = reporting-only (`driver_dormancy_service.py:1-12`) | same | met |
| C3 | Quality metrics with transparent thresholds | `min_driver_rating`/`acceptance_rate` used in dispatch columns (`matching.py:549`); thresholds published to drivers? not verified | same | unknown |
| C4 | Signed contractor agreement | ICA draft, no e-signature (COMP-009) | same | missing |
| C5 | Legal review of copy crossing the line | `spinr-regulatory-compliance-checker` rule 5 (agent, manual) | Spinr rule | partial (COMP-018) |

### 4.7 Right-to-delete (PIPEDA × SK Act)

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| D1 | Profile fields scrubbed ≤ 30 d | Step N (296:415-440): first/last name, email, profile_image, saved_addresses. **Phone is NOT in the Step N column list** (VERIFIED — `SET first_name, last_name, email, profile_image`) though doc lists "name, email, home address, payment methods"; phone stays until Step H (7 y) | INFERRED: PIPEDA principle 4.5 (§0.2 #7 page family; not fetched) | partial — **phone retained 7 y after a deletion request** (add to COMP-007) |
| D2 | Trip records retained then hard-deleted at 7 y | Step B/H | ASSUMED | met |
| D3 | Safety incidents: max(7 y, close+2 y) | no purge step (grep) → indefinite | ASSUMED | partial (over-retention) |
| D4 | Driver docs 7 y post-deactivation | tombstone indefinite (216:21) | ASSUMED | partial (over-retention) |
| D5 | Comms logs | ride_messages 90 d (296:155) vs doc "1 y anonymised" | ASSUMED | met (code) / doc drift |
| D6 | Log request in `user_deletion_requests` | **No such table exists** (grep `user_deletion_requests` in `backend/migrations`, `routes`, `repositories` → 0). `users.py:451-531` sets `status=pending_deletion` + `deletion_requested_at/scheduled_at` on `users` and writes an `audit_logs` row (`resource="users"`); phone is not nulled at request time | Spinr rule (regulatory-sk.md "Always log the deletion request in `user_deletion_requests`") | partial — proof-of-compliance exists only as an `audit_logs` row, and `audit_logs` is itself purged at 7 y (Step G) |
| D7 | Stripe-side payment-method detach | not covered by Step N (doc says so) | ASSUMED | missing |
| D8 | DSAR access ≤ 30 d | `users.py:242-300` (`response_due_at`, auto-fulfil) | INFERRED PIPEDA s.8 30-day (not fetched) | met |

### 4.8 Provincial reporting

| # | Obligation | Enforced in code | Primary source | Status |
|---|---|---|---|---|
| P1 | Quarterly ride volume + incidents to SGI | none (`sgi-quarterly.md:150`) | ASSUMED — SGI never asked (§5 open 114 d) | missing (COMP-017) |
| P2 | Annual driver roster | `sgi_form_filler.py` (D00032/D00033) fill, unscheduled | ASSUMED | partial |
| P3 | On-demand trip records ≤ 14 d, < 30 min | `scripts/compliance_export.py`; `compliance_export_events` audit | ASSUMED | met |
| P4 | *(new)* Monthly km/premium return | see I6 | INFERRED | partial |
| P5 | *(new)* Saskatoon per-trip fee remittance ($0.12 + $0.15) | none | INFERRED §0.2 #4 | missing/unknown (COMP-004) |

### 4.9 sweep-catalog §4.1 CRA / federal tax

| Item | Enforced in code | Primary source | Status |
|---|---|---|---|
| Driver GST registration (small-supplier N/A) | payout gate `routes/drivers/payouts.py:762-780` (A5) | INFERRED §0.2 #5 (GI-196; taxi-business def. 2017-07-01) — supports the gate | met (code) / cite pending |
| Supplier of record | driver treated as supplier (ToS §11; T4A not GST invoice) | ASSUMED | unknown (E3) |
| Receipts GST/PST separate; PST applicability | T3/T2 | INFERRED against current config | at risk |
| Part XX.1 platform reporting | SIN-free handoff export `admin/compliance.py:805-830`; RZ registration UNKNOWN | INFERRED §0.2 #8 (Jan 31; name/address/TIN; RZ account) | partial (COMP-015) |
| T4A rules/threshold; job correctness | T5; `test_compliance_reports*.py` | INFERRED $500 | met / decision open |
| Books & records vs 7 y | R1 | ASSUMED (6 y CRA) | met |
| Corporate invoices meet ITC documentation | `corporate_statement_pdf.py` — no GST number (T4) | ASSUMED | **missing** |
| Tax treatment of credits/incentives | discount applies to fare only (`receipt_pdf.py:163-171`) | ASSUMED | unknown |

### 4.10 sweep-catalog §4.2 Saskatchewan & municipal

| Item | Enforced in code | Primary source | Status |
|---|---|---|---|
| Provincial TNC rules + city licensing (Regina, Saskatoon), fees, data-sharing to city | none (COMP-004); `service_areas.regulatory_*` free text | INFERRED §0.2 #3/#4/#6 | missing/unknown |
| SGI ride-share product + Period 0–3 mapping | §4.2 | INFERRED §0.2 #2 (per-km, monthly) | partial |
| Driver eligibility vs current source | §4.1 | INFERRED contradicts on class; no 10-y vehicle rule found | unknown |
| SK Human Rights Code: service animals, accessibility | A3 met; A4 partial | INFERRED §0.2 #9 | partial |

### 4.11 sweep-catalog §4.3 Privacy & consumer

| Item | Enforced in code | Primary source | Status |
|---|---|---|---|
| PIPEDA consent records | `auth.py:1300`, `users.consent_version` (mig. 334/356), CRC consent table (319) — gaps COMP-008 | INFERRED (10 principles page blocked) | partial |
| Access/deletion SLAs | D8 met; D1 partial (phone) | INFERRED | partial |
| Breach reporting | runbook 72 h + 24-mo record; Incident 1 not reported (COMP-013) | INFERRED §0.2 #7 ("as soon as feasible"; 24 mo) | **at risk** |
| Cross-border transfer disclosure per vendor | privacy §3/§6; `subprocessor-list.md` draft; LogRocket region unknown; Supabase ca-central-1 VERIFIED (API, 2026-08-17 per checklist) | ASSUMED | partial |
| CASL marketing consent | `services/marketing_consent.py` explicit opt-in (mig. 190); footer address from Settings (`email_layout.py:293`) — value unverified | ASSUMED (CRTC page not fetched) | met (code) / value unknown |
| Consumer protection: price + cancellation fee before booking | fare estimate pre-booking (estimates.py); surge shown; **cancellation-fee copy not on `ride-options.tsx`** (grep) — only post-booking screens | ASSUMED (SK CPBPA not fetched) | partial |
| French language decision | no i18n/fr catalogue (grep → 0) | ASSUMED not mandated provincially (OLA applies to federal entities — INFERRED) | decision unrecorded |

### 4.12 sweep-catalog §4.4 Employment & classification

| Item | Enforced | Source | Status |
|---|---|---|---|
| Contractor indicators in copy/training/policies | §4.6 C1–C2 clean; ICA unsigned | INFERRED §0.2 #12 | partial |
| Deactivation fairness / appeal | in-app appeal wired (checklist 2026-08-19); no SLA; policy published w/o counsel | ASSUMED (no SK/federal gig-work statute fetched; none known to apply in SK) | partial |

## 5. Compliance-as-code table (greenfield §10: POLICY → IMPLEMENTATION → TEST → MONITOR → AUDIT → EVIDENCE)

For each obligation: the automated check that could prove it continuously. "Exists" = a test/job already does this; "none possible" = only a human or an external party can prove it.

| ID | Obligation | Check type | Concrete check | Exists today? |
|---|---|---|---|---|
| CC-1 | Document expiry gates go_online + accept | test + DB constraint | `test_go_online_availability.py` cases per category; add CHECK `is_available ⇒ no expired core doc` is not expressible in SQL — keep as test + a nightly query alerting on `is_online=true AND <expiry> < now()` | test partial; monitor none |
| CC-2 | SK eligibility (class, age, years, vehicle age) enforced in prod | monitor | nightly job reads `app_settings.enforce_driver_eligibility_recheck` and emits `spinr_compliance_flag_state{flag}` gauge; alert if 0 after go-live date | none |
| CC-3 | Licence class set matches cited regulation | test | `test_eligibility_class_set.py` asserts accepted set == constant in `regulatory-sk.md` front-matter (`license_class_accepted: [..]`, with `source_url`, `fetched_at`) — a doc-as-config test | none |
| CC-4 | Surge cap | test (exists) | `SURGE_CAP` clamp tests in fare suite; add semgrep rule: any `surge_multiplier` read outside `fare_service.py` must pass through `min(..., SURGE_CAP)` | partial |
| CC-5 | Retention horizons = documented horizons | test | parse `c_*_age` from newest `purge_pii_retention` migration; assert equality with a machine-readable block in `data-retention-schedule.md` and `data-classification.md` | none (COMP-007) |
| CC-6 | Retention job ran in the last 26 h | monitor (exists partially) | `retention_purge` heartbeat (`retention_purge.py:43`) → loop watchdog; alert requires `ALERT_WEBHOOK_URL` (UNKNOWN if set — W0) | partial |
| CC-7 | No table holding PII lacks a purge step | test | static test: every table with a `pii` comment/tag in migrations must appear in a purge step or an explicit `retention: indefinite (reason)` allowlist (`safety_incidents`, `drivers` tombstones would have to be justified) | none |
| CC-8 | Tax lines separate | test (exists) | `test_calculate_all_fees_tax.py`; add `receipt_pdf` test with `tax_breakdown=None` asserting ≥2 tax rows or an explicit single-tax area | partial (COMP-006) |
| CC-9 | Tax config matches decided rule | DB constraint + test | `service_areas` CHECK: `province='SK' ⇒ pst_enabled = <decided>` via a `tax_policy` table with `source_url/decided_by/decided_at` (G9 becomes a row, not a checkbox) | none |
| CC-10 | GST number on receipts | test | receipt snapshot test asserting registration line when `receipt_show_gst_number` on | none (COMP-015) |
| CC-11 | Insurance period rows append-only | DB trigger (exists) + test | migration 64 trigger; `test_insurance_period_rpc.py`; add RLS-tier test in `tests/rls/` for the trigger | exists |
| CC-12 | Period 2 opens at claim, not accept | test (exists) | `test_insurance_period_release_sites.py`, `_forced_offline_sites.py` | exists |
| CC-13 | Every ride in `in_progress` has an open Period 3 row | monitor | `insurance_period_reconciler` loop (lifespan) — add metric `spinr_insurance_period_mismatch_total` + alert | partial |
| CC-14 | SGI monthly return filed | monitor + audit | `regulatory_filings` table + loop (COMP-005): alert on `due_at < now() AND filed_at IS NULL` | none |
| CC-15 | Municipal per-trip fee accrued | job | monthly report `trips × fee` per service area from `municipal_fee_per_trip` config; test | none (COMP-004) |
| CC-16 | Consent version stamped on every new user | DB constraint + test | `users` CHECK `created_at > <flag date> ⇒ consent_version IS NOT NULL` (additive, new rows only); test for corporate signup path | none (COMP-008) |
| CC-17 | Re-consent notice live when CONSENT_VERSION bumps | test | test: bumping `CONSENT_VERSION` without `legacy_consent_notice_enabled=true` in staging fixture fails (guard against dark re-consent) | none |
| CC-18 | CRC consent current for every online driver | monitor | nightly: `is_online=true` drivers whose `driver_crc_consents.consent_version <> published version` → count metric | none |
| CC-19 | ICA signed for every active driver | DB constraint | FK from `drivers.status='active'` transition to a `driver_agreements` row (trigger) | none (COMP-009) |
| CC-20 | WAV never silently dropped | test (exists) | `test_e2e_wav_dispatch.py`; add cascade-path case | exists |
| CC-21 | Service-animal refusal impossible | test | add explicit tests for `ride_flow.py:708` and `ride_cancel.py:100` paths (none named in `backend/tests` listing) | none found |
| CC-22 | Accessibility labels on ride-flow controls | CI | axe on Expo-web export per app (admin already has it); ESLint `react-native/accessibility` rule floor | none for mobile |
| CC-23 | Copy free of employment language | CI | semgrep/regex over `driver-app/app/**/*.tsx` and `docs/driver-*` for shift/uniform/minimum hours/performance review → fail PR unless `classification-reviewed` label | none (agent-only) |
| CC-24 | PII not in logs | CI (exists partially) | gitleaks rules `spinr-sin-bank-pii`, `spinr-driver-export-pii`; `test_loguru_call_conventions.py` pattern could be extended to flag `logger.*(…phone|email|lat|lng…)` | partial |
| CC-25 | DSAR SLA | monitor | daily: `data_export_requests` rows `status='pending' AND response_due_at < now()+5d` → alert | none |
| CC-26 | Breach record complete | audit | none possible automatically; checklist in runbook; add a `docs/audit/breach-record.md` lint that every incident has RROSH + counsel sign-off fields non-empty | none |
| CC-27 | Regulatory source freshness | audit | `regulatory-sk.md` rows carry `source_url`/`fetched_at`; CI warns when `fetched_at` > 12 months (like `renewal-calendar-monitor.yml`) | none |
| CC-28 | Municipal/provincial licence + insurance certificate expiry | monitor | rows in `regulatory_filings`/`company_licences` with expiry; reuse `renewal-calendar-monitor.yml` | none |
| CC-29 | Part XX.1 / T4A annual return | job + audit | `t4a_annual_job` exists (Feb 28); add Jan-31 Part XX.1 reminder + filed evidence row | partial |
| CC-30 | Rules as configuration per service area | design | `service_areas` → `regulatory_rulesets` (licence class set, vehicle age, fee/trip, tax policy, WAV rule) so Regina/Saskatoon/AB are rows, not code forks (greenfield §10) | none — free-text `regulatory_*` columns only |

**None possible (human/external only):** counsel review of the 23 legal texts; SGI's answer on Period 1 unreachable coverage; the City's licence issuance; the accountant's supplier-of-record ruling; the OPC's view of Incident 1's RROSH.

## 6. Rebuild Delta card (§7.3) — Epic: Compliance-as-code

## Epic: Compliance-as-code (Regulatory, Tax, Privacy, Accessibility, Classification)
- Verdict per inherited pattern:
  - **KEEP** — append-only insurance ledger (mig. 64), SQL-encoded retention (`purge_pii_retention`), service-animal hard-block, WAV filter, explicit consent stamp, DSAR auto-fulfil, Decimal per-area tax. Evidence §1.
  - **MODIFY** — eligibility recheck (flag → report-only → on; move into `driver_availability_service` so v2 path can't bypass); retention (add Step O safety incidents, driver tombstone horizon, phone in Step N); receipts (GST number, no bundled tax); consent (corporate signup, flip legacy notice).
  - **REPLACE** — prose obligations in `regulatory-sk.md`/`regulatory-matrix.md`/`data-classification.md` with one machine-readable `compliance/obligations.yaml` (id, rule, source_url, fetched_at, enforced_by, test, monitor) that CI diffs against code; the three docs become generated views.
  - **REMOVE** — nothing; but stop claiming things in `regulatory-sk.md` that no code does (QR proof of coverage, WAV ETA, SMS fallback, `user_deletion_requests`).
- Keep (already best-in-class): service-animal enforcement at the API (Uber/Lyft rely on complaint → deactivation, source: their public accessibility policies, INFERRED); per-phase insurer km detail (rare even at incumbents).
- Uber/Lyft do: municipal licence numbers displayed in-app per city; per-trip city fees itemised on receipt where bylaws require (Toronto, Calgary — INFERRED, not fetched); driver GST number shown on Canadian receipts (Uber Canada receipts carry the driver's GST/HST number — INFERRED from general knowledge, unverified).   Spinr today: none of the three (COMP-004, COMP-015).
- Clean-sheet Spinr would: treat every obligation as a row with a source and a probe. `obligations.yaml` → generated matrix → `pytest -m compliance` (required CI job) → `spinr_compliance_*` metrics → `regulatory_filings` evidence table.   Why (the edge): a 0%-commission TNC has thin margin for fines/suspension; a provable compliance ledger is also the corporate-sales artefact (SOC2-lite) the B2B model needs.
- How: (1) `compliance/obligations.yaml` + doc generator; (2) `@pytest.mark.compliance` on the 9 existing suites + CC-5/8/10/16/21 new tests; (3) `regulatory_filings` + `company_licences` tables (append-only, mirroring mig. 64); (4) monthly loop per `spinr-background-loop` contract; (5) `regulatory_rulesets` per service area.   Who: Compliance owner (currently nobody — `sgi-quarterly.md` "Owner: unassigned"), backend lead, accountant/counsel for the sources.   When: (1)(2) **Now**; (3)(4) **Next** (before Regina); (5) **Later** (before any non-SK area).
- Incremental path: step 1 mark existing tests `compliance` + required CI job (1 day, no behaviour change) → step 2 CC-5 retention doc-vs-SQL test + fix the four doc numbers (1 day) → step 3 `regulatory_filings` table + SGI monthly reminder loop (flagged) → step 4 eligibility report-only mode → flip → step 5 rulesets per area.
- Cost/effort: S (1–2), M (3–4), M (5) · Risk: low (all additive/flagged) · Reversibility: high (flags, additive tables) · Build (tests/loops) / Buy nothing / Open source: semgrep rules, axe.
- Advantage type: **trust + operational** — defensible only if kept current (sources dated); feature-level parts are copyable.
- "Why not?": it doesn't exist because obligations were written as prose by agents without source access, and every "verify with a human" item has no owner. Something simpler that gets 60% of the result: a single required CI job running the nine existing suites under one marker, plus dated `source_url` fields in `regulatory-sk.md` — one afternoon.

## 7. Top 5

1. **COMP-013 — Incident 1 RROSH determination needs privacy counsel, now.** SIN + bank + licence numbers for up to 346 drivers were public for ≥11 days (and remain reachable on 187 of 500 checked branches); OPC and SGI were not notified on an owner's own RROSH "no". The 2026-09-12 GitHub purge request is still DRAFT/unsubmitted; the repo is still public. (VERIFIED record; INFERRED legal reading.)
2. **COMP-001/002 — The driver-eligibility gate may be checking the wrong licence class, and every SK rule beyond document expiry is dark in production.** Confirm Class 4 vs 5 against the Vehicles for Hire Regulations (10-minute human read), then run the recheck report-only and flip it. (Code VERIFIED; requirement UNKNOWN.)
3. **COMP-003 — SK PST on fares (G9) is 33 days stuck on a bulletin nobody can fetch from a session; the only evidence in hand says PST applies.** Assign the accountant a 7-day deadline. Compounding exposure per ride.
4. **COMP-004/005 — Municipal TNC licences (Regina/Saskatoon), the Saskatoon $0.27/trip fee, and SGI's monthly km return have no field, no schedule and no evidence in the repo.** Suspension-class risks; all additive to fix once a human confirms the facts.
5. **COMP-015 — Spinr's own tax posture is undetermined**: supplier of record, GST registration number on receipts (none printed), Part XX.1 RZ registration/Jan-31 filing, T4A-vs-DPI — while the code hard-blocks *drivers* on their GST number. One accountant engagement closes E2/E3/E5 + this.

Honourable mention: COMP-007 (phone never scrubbed on deletion; safety incidents and driver tombstones retained forever; four doc numbers wrong) and COMP-009 (the ICA — the anti-misclassification instrument — is unsigned by every driver).

## 8. NOT verified

- **Any regulatory text at the source.** 0/11 primary fetches succeeded (§0.1). Every "requirement" column is INFERRED (snippet) or ASSUMED (repo doc). Specifically unverified: licence class (4 vs 5), 10-year vehicle age (no provincial hit at all), 3-year experience, PST-46 wording, SGI period definitions and monthly-return rule, Regina bylaw contents/fees, Saskatoon fee figures, CRA ITC invoice rules, PIPEDA 30-day SLA wording, SK Human Rights Code section numbers, T4A threshold currency.
- **Production state**: `enforce_driver_eligibility_recheck`, `legacy_consent_notice_enabled`, `driver_availability_v2_enabled`, `pst_enabled` values; whether migrations 296/334/356/361/400/405/406/436 are applied (G2/C125); whether `retention_purge` has ever completed a live tick; `ALERT_WEBHOOK_URL`; the CASL address value in Settings; `legal_documents` rows' actual content vs the drafts.
- **Files not read line-by-line**: `docs/legal/*.md` bodies (headers + checklist only, except ICA/privacy notes); `docs/compliance/pia-ai-surfaces-2026-08.md`; `docs/privacy/2026-07-28-pia-data-transfer-export.md`; `routes/drivers/payouts.py` GST gate (cited from A5); `utils/insurance_periods.py` body; `driver_availability_service.py` beyond line 60 (the v2-bypass claim in COMP-002 is INFERRED from `_EXPIRY_FIELDS` scope — confirm before acting); `scripts/compliance_export.py`; `services/marketing_consent.py` body (relied on checklist's 2026-08-20 verification).
- **Accessibility numbers** (23/44, 14/38) are a file-level grep, not a control-level audit.
- **No test was executed**; test names were listed, not run.
- **Traceability rows**: used only to confirm no compliance-specific `gap` rows in the three named epics (S-safety-05 fraud detection and S-earn-05 scheduled payouts are the only `gap` rows and are not this lane's); per W0 CARTO-004, not cited further.
- **Fork registry**: `docs/known-forks.md` has no compliance-relevant entries (grep `insurance_period|legal.tsx|crc` → 0); the "two insurance-period implementations" W0 item 2 mentions was not re-examined here (Dispatch lane).
- No live-data claims were made about rider/driver counts, names, or coordinates (PII rule observed).

## 9. Human-only questions

| # | Question | Who |
|---|---|---|
| H1 | Which licence class does *The Vehicles for Hire Regulations* require for TNC drivers today — Class 4 or Class 5? (COMP-001) | SGI / counsel |
| H2 | Is there a provincial or municipal vehicle-age limit (10 years) or is that Spinr policy / insurer term? | SGI, City licensing |
| H3 | Does SK PST apply to ride-share fares under PST-46 "other passenger transportation services"? If yes, from when, and is retroactive remittance owed? (G9) | Accountant / SK Ministry of Finance |
| H4 | Does Spinr hold a TNC licence in Saskatoon (Bylaw 9651) and Regina? Licence numbers, expiry, fee schedule, per-trip fee, and any data-sharing duty to the City? (G2) | Owner / City of Saskatoon `vehicleforhire@saskatoon.ca` / City of Regina |
| H5 | SGI: what exactly is the monthly return (fields, channel, Period 1 km included?), the $1M certificate cadence, and is a driver "declared online but unreachable" in Period 1 contingent coverage? (COMP-005/014, E8) | SGI |
| H6 | Who is "Knight Archer" at $0.011/km (`compliance.py:1086`) — a second TNC insurer or a broker — and which policy covers which period? | Owner / broker (G4) |
| H7 | Supplier of record for GST; whose GST number goes on receipts; is Spinr a Reporting Platform Operator (Part XX.1) with an RZ account; T4A vs DPI for 2026? (COMP-015) | Accountant |
| H8 | Does the Incident 1 RROSH "no" survive privacy-counsel review given SIN/bank/licence exposure on a public repo and a live unauthenticated API? Should OPC, SGI and drivers be notified now? Who submits the GitHub purge ticket and when does the repo go private? (COMP-013) | Privacy counsel / owner |
| H9 | Will counsel review the 9 live legal documents, and in what order? Is the ICA to be signed in-app or via a vendor? Arbitration clause? (COMP-009, §3) | Counsel / owner |
| H10 | Product: keep or drop the doc promises for WAV ETA, SMS-fallback on ride-state pushes, QR proof-of-coverage, `user_deletion_requests`? (COMP-010/011, §4 I5/D6) | Product owner |
| H11 | Retention decisions: driver tombstones and safety incidents — indefinite (justify) or a horizon? Phone number in the 30-day scrub? (COMP-007) | Privacy officer + counsel |
| H12 | Does any corporate-wallet flow make Spinr an MSB/reporting entity under PCMLTFA? (COMP-016) | Counsel / FINTRAC |
| H13 | French-language support: a recorded "no, not mandated" decision or a corporate-customer requirement? | Owner |
| H14 | Is `accessibility@spinr.ca` going to be provisioned, and by whom? | Zoho/Workspace admin |
| H15 | Which flags are on in production today: `enforce_driver_eligibility_recheck`, `legacy_consent_notice_enabled`, `driver_availability_v2_enabled`? | Anyone with `app_settings` read access |

## 10. Escalations (anything without a fetched primary source)

| # | Claim the code/docs rely on | Label | Who answers | Repo pointer |
|---|---|---|---|---|
| X1 | TNC drivers need Class 5 (code) — snippet says Class 4 | UNKNOWN | SGI / counsel | `status.py:734`, CLAUDE.md |
| X2 | Vehicle must be < 10 model years | ASSUMED | SGI / City / insurer | `status.py:762` |
| X3 | 3 years licensed experience | ASSUMED | SGI / counsel | `status.py:797` |
| X4 | Annual CRC+VSC, annual inspection | INFERRED | SGI / police service | `status.py:651` |
| X5 | SK PST does not apply to ride-share fares (current config) | ASSUMED — contradicted by INFERRED snippet | Accountant / SK Finance | G9, `features.py:810` |
| X6 | Drivers are "taxi businesses" (no small-supplier exemption) → BN gate on payout | INFERRED (supports code) | Accountant | `payouts.py:762` |
| X7 | Driver is supplier of record; no GST number needed on Spinr receipts | ASSUMED | Accountant | `receipt_pdf.py` |
| X8 | Spinr must file Part XX.1 by Jan 31; RZ account | INFERRED | Accountant | `compliance.py:805` |
| X9 | T4A $500 threshold | INFERRED | Accountant | `t4a_pdf.py:19` |
| X10 | Saskatoon TNC licence + $0.12+$0.15/trip; Regina TNC licence | INFERRED | Cities | G2 |
| X11 | SGI monthly km return, $0.11/km + PST, $1M certificate | INFERRED | SGI | `compliance.py:1085` |
| X12 | Period 0–3 definitions; Period 1 unreachable = still covered | ASSUMED | SGI | A8 SAFETY-003 |
| X13 | 7-y trip / 3-y GPS / 7-y period retention are *required* (vs Spinr choice) | ASSUMED | Counsel / SGI / CRA | migration 296 |
| X14 | PIPEDA: 30-day access SLA; RROSH threshold for SIN/bank; 24-mo record | INFERRED | Privacy counsel | `users.py:245`, breach-record |
| X15 | Service-animal refusal prohibited, no extra fee | INFERRED (SHRC) | Counsel | `ride_flow.py:708` |
| X16 | WCAG 2.1 AA is the legal bar (vs SK HRC accommodation duty) | ASSUMED | Counsel | `docs/ACCESSIBILITY.md` |
| X17 | Audio recording: PIPEDA two-party notice on top of s.184 | INFERRED | Privacy counsel | `domain-safety.md:150` |
| X18 | Contractor status holds under CRA 4-factor / SK ES with an unsigned ICA and a Spinr Pass fee | INFERRED | Employment counsel | ICA draft |
| X19 | OLA/ACA/FINTRAC rows in `regulatory-matrix.md` apply | ASSUMED (likely not) | Counsel | `audit-framework/regulatory-matrix.md` |
| X20 | Surge cap 2.5× is a "provincial guideline" | ASSUMED (no such rule found) | Counsel | CLAUDE.md |

---
_End of R12 lane. One file written: `docs/audit/clean-sheet/02-findings/compliance.md`. No code, config, or other docs were modified._
