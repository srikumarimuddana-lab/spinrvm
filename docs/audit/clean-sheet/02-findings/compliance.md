# R12 — Compliance & Regulatory Counsel-Analyst (W2)

_Spinr Clean-Sheet Rebuild Audit. Written 2026-09-24. Report-and-recommend only. Labels: VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN._

> Skeleton — sections filled incrementally in this session.

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
## 2. Attack — findings (§7.1 cards)
## 3. Legal document readiness (spinr-legal-readiness-reviewer method)
## 4. Obligation matrix (every item in regulatory-sk.md + sweep-catalog §4.1–§4.4)
## 5. Compliance-as-code table (greenfield §10)
## 6. Rebuild Delta card — Compliance-as-code (§7.3)
## 7. Top 5
## 8. NOT verified
## 9. Human-only questions
## 10. Escalations
