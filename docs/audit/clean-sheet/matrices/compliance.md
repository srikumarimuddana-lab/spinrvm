# Compliance obligation matrix — PIPEDA, Saskatchewan TNC/SGI, CRA/tax, WCAG 2.1 AA, retention

**Lane:** Tier B matrix (2 of 2) · **Wave:** post-W5 · **Date:** 2026-09-25 · **Mode:** report-only. No code, config or data changed.

## §0 Method

- **This file is built entirely from prior audit lanes' direct reads**, not a fresh primary-source
  fetch. `02-findings/compliance.md` §0.1 attempted 11 primary/secondary regulatory fetches this
  audit (saskatchewan.ca, sgi.sk.ca, canada.ca/CRA, priv.gc.ca, regina.ca, saskatoon.ca, a PST
  bulletin, CanLII, a Saskatchewan government PDF host, a Lyft driver-requirements mirror, the SK
  Human Rights Commission) and **all 11 were EGRESS_BLOCKED** by the environment's network proxy —
  the same standing limitation `ACTION_ITEMS.md` G9 and `docs/legal/legal-text-publication-checklist.md`
  already record. This session did not retry those fetches (same proxy, same environment); treat
  every regulatory-requirement cell below as carrying the same block unless marked otherwise.
- **Per this audit's rule**, every legal/tax claim without a primary source is **ASSUMED**. Code
  facts cited with a `path:line` are VERIFIED (this session's own read) or cited-VERIFIED (another
  lane's direct read this audit, not re-opened here).
- **Flag state** column reports the schema/code default and whether any lane found evidence of the
  live production value; where no live value is known, it says **UNKNOWN (no DB session)**.
- **Test** column names the cited test file if one exists; "none found" means grepped and not
  located, not proven absent for all time.
- **Escalation ID** cites `05-escalations.md`'s own id (verified against that file's full id list
  this session — every `E-R*`/`E-S*`/`E-T*`/`E-F*` id below was confirmed to exist in
  `05-escalations.md` before being cited).
- Rows are seeded from `02-findings/compliance.md` (COMP-001..018), `matrices/risk-register.md`
  §5 (RR-69..83) and its money/tax rows in §3 (RR-42..57), and `05-escalations.md` §D/§B/§C — cited
  throughout, not re-derived from scratch.

## §1 PIPEDA (Personal Information Protection and Electronic Documents Act)

| # | Obligation (PIPEDA principle) | Enforced in code (path:line) | Flag state | Test | Evidence label | Escalation ID |
|---|---|---|---|---|---|---|
| P1 | Accountability — an accountable privacy officer exists | `docs/audit/breach-record.md` header: "Owner: Privacy Officer" (a documentation role, not a verified named/trained individual) | n/a | none found | ASSUMED (role named in a doc; not independently confirmed as a real, trained appointment) | none dedicated — folded into E-S1/E-R8 |
| P2 | Identifying purposes at collection | `routes/auth.py:1300-1306` requires `consent_accepted`; consent text is version-stamped (`CONSENT_VERSION = "consumer-tos-2026-09-v1"`, `:184`) | n/a (not flag-gated at signup for new users) | not located this session | cited-VERIFIED (compliance.md §1.6) | none |
| P3 | Consent (3rd principle) | Signup consent as above; CRC/VSC consent separately versioned (`services/driver_crc_consent.py`), captured at `driver-app/app/become-driver.tsx:596-612` | **Three holes, all flagged off/missing**: legacy-imported riders/drivers have `consent_version IS NULL` (`routes/legacy_consent.py`); re-consent notice gated by `legacy_consent_notice_enabled` default **False** (`schemas.py:722`); corporate self-serve signup writes zero `consent_version` (`routes/corporate_signup.py`, grep confirms 0 hits) | none for the three gap paths | cited-VERIFIED (COMP-008) | none dedicated (folded under E-R11's "is correcting the legal texts material enough to trigger re-consent") |
| P4 | Limiting collection to stated purpose | Driver full address collected for background check, encrypted, not shared with riders (CLAUDE.md's own stated rule) | n/a | not located this session | ASSUMED (design intent stated in CLAUDE.md, not independently re-verified against the schema this session) | none |
| P5 | Limiting use, disclosure and retention | `utils/retention_purge.py` → `purge_pii_retention()` (migrations 50→296) runs every ~24h from `core/lifespan.py:513`; encodes 3y GPS anonymize, 7y ride hard-delete, 90d chat/location/AI, 7y audit logs, 30d profile scrub on DSAR (migration 296:415-470, Step N) | Leader-locked, replay-safe (cited pattern); dry-run mode exists | `test_data_export_purge*.py` (3 files, cited) | cited-VERIFIED — but **retention drifts in both directions**: driver tombstones and `safety_incidents` are kept **indefinitely** (no purge step for either, grep confirms 0 hits in migrations 216/289/296); three doc-vs-SQL retention numbers disagree (GPS 2y in `data-classification.md` vs 3y in SQL; comms "anonymized after 1 year" in `regulatory-sk.md` vs 90d hard-delete in SQL; audit-log 3y in `regulatory-matrix.md` vs 7y in SQL) | E-R8 |
| P6 | Accuracy | Profile self-serve correction (CLAUDE.md "profile fields are self-serve; non-trivial corrections go through Support") | n/a | not located this session | ASSUMED (design intent per CLAUDE.md, not re-verified) | none |
| P7 | Safeguards | Driver SIN/emergency-contact encryption via `pgsodium`; admin JWTs fully trusted per CLAUDE.md's stated model | `pgsodium` marked pending-deprecation by Supabase; key-rotation runbook never executed (RR-23, cited) | none found for the rotation runbook specifically | cited-VERIFIED (mechanism) / INFERRED (vendor deprecation timeline) | E-S1 (key rotation, the CRITICAL instance of this principle) |
| P8 | Openness (privacy policy accurately describes practices) | `docs/legal/privacy-policy.md` published 2026-08-17 | **Undisclosed data flows found**: LogRocket session replay on by default in production iOS builds, unmasked, not in the DPA register (RR-19); Meta ad SDK + server-side per-ride `Purchase` conversion events, undisclosed and against CLAUDE.md's own "never add third-party ad SDKs" guardrail (RR-38, STRAT-004) | none — these are disclosure gaps, not code paths with a test | cited-VERIFIED (code) / INFERRED (vendor default capture behaviour for LogRocket) | E-S2 (LogRocket), E-S3 (Meta) |
| P9 | Individual access | `POST /data-export` (`routes/users.py:242-300`) records `response_due_at = now+30d` per the docstring's own PIPEDA s.9 citation; rate-limited 3/h | n/a | not located this session (DSAR export pipeline test not independently confirmed this pass) | cited-VERIFIED | none |
| P10 | Right to delete / challenge compliance | `deletion_requested_at`/`deletion_scheduled_at` (`users.py:451-531`); scrub within 30 days per CLAUDE.md's Compliance section | Ride records stay fully attributable for the full 7-year window by deliberate design (migration 216/289 "Uber/Lyft attributable retention" model, recorded decision per ACTION_ITEMS B18/B23) — **not** a gap, a recorded choice | not located this session | cited-VERIFIED (mechanism) / cited (decision record, not re-derived) | none |
| P10.3 | Breach record-keeping and notification | `docs/audit/breach-record.md` (Incident 1, fully populated per its own template, incl. a dated written RROSH sign-off document `docs/audit/incident-1-rrosh-signoff-2026-09-13.md`) | RROSH determined **"no"** by the repo owner, twice (2026-08-27, re-affirmed 2026-09-13); OPC **not** notified; SGI **not** notified despite driver licence numbers being one of the exposed categories (breach-record.md flags this field should not be skipped as "not applicable") | n/a | VERIFIED (this session, direct read of `docs/audit/breach-record.md`) / INFERRED (whether the RROSH "no" would hold under independent privacy-counsel review — this audit does not re-litigate the owner's determination) | none dedicated — this is the COMP-013 row, §4 of this file, and per this audit's instruction is recorded as **owner-managed, closed by owner statement 2026-09-24, not independently verified**, not re-opened via an escalation id |

## §2 Saskatchewan TNC / SGI insurance obligations

| # | Obligation | Enforced in code (path:line) | Flag state | Test | Evidence label | Escalation ID |
|---|---|---|---|---|---|---|
| S1 | Correct driver licence class (Class 4 or Class 5 — contested) | `backend/routes/drivers/status.py:733-744` accepts only Class 5/5A/5-A unless `drivers.sgi_approved`; `schemas.py:313-316` comment states "must be Class 5 or SGI-approved non-standard" | n/a (unconditional gate at document level) | not located this session | UNKNOWN — code VERIFIED, but the regulatory requirement itself is only INFERRED from a search snippet that contradicts CLAUDE.md ("drivers must obtain a Class 4 driver's licence") and was never confirmed against a primary source (§0's egress block) | E-R1 |
| S2 | Age ≥18, vehicle <10 years, ≥3 years licensed experience, licence-class check re-run at every `go_online` | `status.py:690` gates all of these together | `enforce_driver_eligibility_recheck` default **False** (`schemas.py:320`); no ACTION_ITEMS entry records it ever being flipped on in production; the newer `driver_availability_service.py` (v2) path **does not contain this block at all** (`_EXPIRY_FIELDS` only) | not located for the flag-on path specifically | cited-VERIFIED (code) / UNKNOWN (production flag value; whether v2 availability routing bypasses this entirely in prod) | E-R2 |
| S3 | Document expiry (licence, ride-share-endorsed insurance, inspection, CRC/VSC) blocks go-online | `status.py:590-670`, re-checked at accept time (`_shared.py:975-1010`) | unconditional, not flag-gated | not located this session | cited-VERIFIED — the one eligibility rule this audit found **actually enforced** unconditionally, as distinct from S1/S2 | none |
| S4 | Municipal TNC licence (Regina, Saskatoon) and Saskatoon's per-trip fee | none found — no `licence_number`/`bylaw`/`municipal_fee` column or constant anywhere in `backend/` (grep, cited) | n/a | none | INFERRED (requirement, search snippet only) / VERIFIED (absence in code) | E-R3 |
| S5 | SGI monthly kilometre reporting ("even if zero km", per a search snippet) | `routes/admin/compliance.py:1064-1200` (`get_insurance_billing_sgi`, on-demand only); `_SGI_RATE_PER_KM = Decimal("0.11")` | No scheduled loop (grep of `core/lifespan.py` for an SGI/insurer loop: 0 hits); no `sgi_submissions`/`insurer_reports` table | not located this session | INFERRED (obligation) / cited-VERIFIED (code — on-demand only, no schedule) | E-R4 |
| S6 | SGI quarterly ride-volume/incident report; annual driver roster | `scripts/compliance_export.py` exists (on-demand export); `sgi_form_filler.py` D00032/D00033 form-fill exists, unscheduled | n/a | none found | cited-VERIFIED (`docs/compliance/sgi-quarterly.md` §4, open since 2026-06-02) | E-R4 (folded with S5) |
| S7 | Company-level insurance (CGL, tech E&O, cyber) | none found | n/a | n/a | ASSUMED — not confirmed to exist anywhere in the repo | E-R12 |
| S8 | WAV request support where a WAV driver is online | Dispatch/cascade filters add `is_wav=True` when `requires_wav` (`services/dispatch_service.py:231-244,455-456`; `routes/rides/matching.py:503-504,877-878`); `wav_available` computed pre-booking (`routes/rides/estimates.py:549,632`) | unconditional | `test_e2e_wav_dispatch.py` (cited) | cited-VERIFIED — one of the strongest-enforced obligations in this table | none for the core mechanism |
| S9 | "Estimated wait for next WAV + standard alternative" (documented promise) | Not built — rider sees a greyed toggle, `rider-app/app/ride-options.tsx:1062-1070` ("No WAV drivers nearby"), no ETA computation (grep `next WAV\|wav_eta` → 0 hits) | n/a | none | cited-VERIFIED (absence) | E-F15 (folded into the trust-pack decision) |
| S10 | Service-animal accommodation, no refusal, no extra fee | Driver decline/cancel with a service-animal reason is rejected HTTP 400 **and** writes an audit_logs row (`routes/drivers/ride_flow.py:708-739`, `ride_cancel.py:100-121`) | unconditional | not located this session (audit-log write path, not a dedicated test file confirmed this pass) | cited-VERIFIED — matches the SK Human Rights Commission search snippet exactly, called "best-in-class" by the citing lane | E-R9 (source citation for the underlying rule) |
| S11 | Insurance-period transitions (Periods 0-3), append-only audit trail | `driver_insurance_periods` table, DELETE forbidden, UPDATE may only set `ended_at` (`backend/migrations/64_driver_insurance_periods.sql:80-128`); Period 2 opens at `driver_assigned`/claim time (`routes/rides/matching.py`), not at Accept, per CLAUDE.md | unconditional at the DB trigger level | `test_insurance_period_rpc.py` (cited by compliance.md CC-11) | cited-VERIFIED — DB-enforced, not just convention | none (this is the one insurance-adjacent item with no open escalation — it works) |
| S12 | "Online but unreachable" (dead phone) — Period 1 linger classification | `utils/presence_sweeper.py` retired; Period 1 km accrues 0 while no location writes arrive (`routes/drivers/location.py:548`) | Deliberate design (favours the driver); no TTL close-out | not located this session | cited-VERIFIED (design) / ASSUMED (whether SGI reads an open-but-unreachable Period 1 as contingent-covered) | E-R5 |
| S13 | Independent-contractor classification — no control-of-work language | Grep of driver-app + `driver_status_notifications.py` + `driver_dormancy_service.py` for shift/uniform/minimum-hours/performance-review language: 0 real hits (2 false positives, unrelated code comments) | n/a | not located this session | cited-VERIFIED | E-R7 (folded with the ICA/acceptance-rate items below) |
| S14 | Independent-contractor agreement signed before first ride | `docs/legal/independent-contractor-agreement.md` is a **draft**, no e-signature capture flow found (grep `e-sign\|esign\|docusign` in `backend/routes/drivers`, `driver-app/app` → 0 hits) | n/a | none | cited-VERIFIED (COMP-009) | E-R7 |
| S15 | Acceptance-rate dispatch penalty for declining offers — disclosure | `services/dispatch_service.py:76-91` divides ETA by `acceptance_rate`; decline lowers it (`routes/drivers/ride_flow.py:798`); applies on the default ETA-ranking branch (`matching.py:1022-1039`); **never disclosed** in FAQ or app (grep, 0 hits) | unconditional (not flag-gated; the ranking itself is default-on) | `07-reverification.md` DRIVER-004: CONFIRMED, with corrected call-site citations | VERIFIED (re-verified this audit, `07-reverification.md`) | E-R7 |

## §3 CRA / tax obligations

| # | Obligation | Enforced in code (path:line) | Flag state | Test | Evidence label | Escalation ID |
|---|---|---|---|---|---|---|
| T1 | GST (5%) charged and shown as a separate receipt line item | `backend/features.py:820-845` computes tax per area, Decimal-quantised; `receipt_pdf.py:174-190` iterates `tax_breakdown` into one line per tax with rate | unconditional (GST) | `test_calculate_all_fees_tax.py` (cited) | cited-VERIFIED | none |
| T2 | PST (6%, where applicable) charged and shown separately | `service_areas.pst_enabled` = **False** on all SK areas today | Config only, reversible without deploy — but under-collection to date is not reversible by a later flag flip | `test_calculate_all_fees_tax.py` fixture (cited, not yet updated for a PST-on scenario) | **ASSUMED** — no primary source ever confirmed after four verbal determinations; the only evidence in hand (a search snippet of a PST bulletin) points toward PST applying to "taxicab or limousine transportation services … and other passenger transportation services" | E-T1 |
| T3 | Receipt PDF fallback does not bundle GST+PST into one line | `backend/utils/receipt_pdf.py:191-198` — `rows.append(("Tax", _money(gap)))` when `had_tax` is False | n/a | none found for the fallback path specifically | cited-VERIFIED (COMP-006/RR-49) | none dedicated |
| T4 | GST/HST registration number on receipts/statements (for corporate input-tax-credit documentation) | **None found anywhere** — grepped `RT\d{4}`, "GST #/No./number/reg", `gst_bn`, `business_number`, `registration`, `tax_id`, `hst` across `receipt_pdf.py`, `email_receipt.py`, `cancellation_receipt.py`, `subscription_invoice_pdf.py`, `driver_statement_pdf.py`, `company_details.py`, `corporate_statement_pdf.py` (`07-reverification.md` re-check, CONFIRMED) — `drivers.gst_bn` exists and is written (`services/stripe_kyc_sync.py:148-174`) but never read by any renderer | n/a | none | VERIFIED (absence, independently re-checked this audit — the widest re-check of any card in the 16-card sample) / ASSUMED (the ITC documentation requirement itself, canada.ca blocked) | E-T2 |
| T5 | GST supplier-of-record determination (driver vs. Spinr) | Driver payout hard-blocked on a GST/BN being on file (`routes/drivers/payouts.py:762-780`); treats the **driver** as supplier in practice, undetermined in writing | n/a | not located this session | ASSUMED | E-T2, E-T3 |
| T6 | CRA Part XX.1 (Digital Platform Information) reporting | SIN-free hand-off export built (`routes/admin/compliance.py:805-830`), explicitly flagged in its own docstring as "filing decision not made by this export"; no RZ account or filing path confirmed | n/a | not located this session | ASSUMED (whether Spinr is a "reporting platform operator" at all) | E-T4 |
| T7 | T4A annual slip issuance ($500 threshold, Box 048) | `core/lifespan.py:633-642` runs the annual job (Feb 28); `t4a_pdf.py:19,230` uses the $500 threshold | unconditional | not located this session | cited-VERIFIED (mechanism) — but **the code also fills Box 020 for GST registrants** (`t4a_pdf.py`, per RR-47/MONEY-012), which may be wrong depending on box semantics | E-T5 |
| T8 | Promo/discount tax treatment (gross vs. net) | Code taxes the gross fare and deducts the promo after tax lines are computed | n/a | not located this session | ASSUMED (accounting treatment) — code behaviour VERIFIED-by-citation, correctness of the treatment ASSUMED | E-T6 |
| T9 | Tax treatment of platform-absorbed, uncollected fares still paid to drivers | Paid unconditionally in the fare-settlement path (design, cited MONEY-010) | n/a | not located this session | ASSUMED | E-T7 |
| T10 | 7-year retention window vs. CRA's "six years from the end of the tax year" phrasing | Retention keyed on **ride date**, not tax-year end (`purge_pii_retention()`) | n/a | not located this session | ASSUMED (whether ride-date keying satisfies the CRA phrasing) | E-T8 |
| T11 | Prepaid wallet balances — accounting/regulatory treatment of a held customer liability | Wallet balances exist (`corporate_wallet_apply_delta` RPC, row-level locking); no report totals the float | n/a | not located this session | ASSUMED | E-T9 |

## §4 WCAG 2.1 AA (customer-facing surfaces)

| # | Obligation | Enforced in code (path:line) | Flag state | Test | Evidence label | Escalation ID |
|---|---|---|---|---|---|---|
| W1 | WCAG 2.1 AA conformance target stated | `docs/ACCESSIBILITY.md:40-42` — rider/driver/web all "Not yet audited"; the doc's legal-basis citation is Ontario's AODA, not a confirmed Saskatchewan source | n/a | none — no automated a11y test found for rider-app/driver-app | cited-VERIFIED (target stated, never independently tested) / ASSUMED (whether AODA is even the right basis for a Saskatchewan operator) | E-R9 |
| W2 | `accessibilityLabel` coverage on interactive elements | 23 of 44 `rider-app/app/*.tsx` and 14 of 38 driver-app screens carry at least one `accessibilityLabel` (grep, cited) | n/a | none | cited-VERIFIED (crude coverage grep, not a rendered-screen audit) | X15/X16 (`05-escalations.md` roadmap column, not a decision id) |
| W3 | `accessibility@spinr.ca` support inbox provisioned | Referenced 13 times across `docs/legal/`, `backend/`, `rider-app/app/` (grep, cited); no evidence the inbox itself exists | n/a | n/a | cited-VERIFIED (reference count) / UNKNOWN (inbox existence — needs a human with Workspace/Zoho admin access) | E-R9 |
| W4 | SMS fallback for ride-state-affecting push notifications (hearing-impairment accommodation, per `regulatory-sk.md`) | None found — grepped `send_sms` in `routes/rides/lifecycle.py`, `matching.py`, `utils/notifications.py`: 0 hits | n/a | none | cited-VERIFIED (absence) | E-R9 |
| W5 | `allowFontScaling={false}` usage — OS text-size settings reaching fare totals / driver earnings | 76+ occurrences repo-wide, concentrated on money-display components, no bounded fallback (RR-137, cited) | n/a | none | cited-VERIFIED | X22 (roadmap column) |
| W6 | Brand/semantic colour tokens as text meet AA contrast | 13 computed WCAG ratios independently reproduced to two decimals by `07-reverification.md`'s re-check of UXA11Y-002 (2.54–4.98 light mode, several below the 4.5:1 AA text threshold; 10.92–12.58 dark mode, all pass); AA-safe `primaryDark` token used at only 22 of ~680 sites (~3.2%, re-counted this audit) | n/a | none (arithmetic, not device-rendered or measured) | VERIFIED (arithmetic, re-confirmed independently this audit — `07-reverification.md` UXA11Y-002 note) | X22 |
| W7 | Admin-dashboard-specific a11y ratchet (separate CI surface, not mobile) | `ACTION_ITEMS.md` E11's own ratchet comment understates progress (says 64 violations remain; RR-141/UXA11Y-006, cited, found 4) | n/a | admin-dashboard's own axe-based CI job (cited, not re-run this session) | cited-VERIFIED — a positive finding (progress understated, not overstated) | N16 (roadmap column) |

## §5 Retention rules (CLAUDE.md's own stated horizons vs. SQL)

| # | Rule (as stated in CLAUDE.md "Saskatchewan Regulatory" / "Compliance (PIPEDA)") | Enforced in SQL (path:line) | Doc agreement | Escalation ID |
|---|---|---|---|---|
| R1 | Trip record: 7 years | `purge_pii_retention()`, migration 296 Step B — 7y ride hard-delete | Matches CLAUDE.md | none |
| R2 | Driver/vehicle linkage at trip time: 7 years | Same purge function, ride-record-scoped | Matches CLAUDE.md | none |
| R3 | GPS trace at pickup/dropoff: 3 years | Migration 296:152-163 — 3y GPS anonymize | **Disagrees** with `docs/data-classification.md:20,53,168` (states 2 years) | E-R8 |
| R4 | Insurance-period transitions: 7 years for regulatory audit | Append-only trigger (migration 64); purge horizon not independently re-confirmed this session | cited-VERIFIED (append-only mechanism) / not re-confirmed (7y purge horizon specifically) | none |
| R5 | Profile fields (name, email, home address, payment methods) scrubbed within 30 days of a deletion request | Migration 296:415-470, Step N | Matches CLAUDE.md | none |
| R6 | Driver documents retained 7 years post-deactivation (`regulatory-sk.md` Right-to-delete #4) | Migration 216:21 comment: drivers with insurance history stay tombstoned **indefinitely** | **Disagrees** — no 7-year purge step found for driver tombstones (grep of 216/289/296: 0 hits) | E-R8 |
| R7 | `safety_incidents`: "longer of (7 years, investigation close + 2 years)" (`regulatory-sk.md` #3) | No purge step found for `safety_incidents` in any of migrations 216/289/296 | **Disagrees** — kept indefinitely | E-R8 |
| R8 | Communication logs "anonymized after 1 year" (`regulatory-sk.md` #5) | `ride_messages` hard-delete at 90 days (migration 296:155,209) | **Disagrees** in the opposite direction (SQL is stricter/shorter than the doc claims) | E-R8 |
| R9 | Rider account PII "2 years post-deletion" (`data-classification.md:166`) | 30-day profile scrub (Step N) + 7-year ride hard-delete (Step B) — neither matches "2 years" | **Disagrees** — no 2-year horizon exists anywhere in the SQL | E-R8 |
| R10 | Audit logs: "3 years (SOC2)" (`audit-framework/regulatory-matrix.md`) | Migration 296:158 — 7 years | **Disagrees** | E-R8 (folded with COMP-016) |

## §6 COMP-013 — recorded per this audit's instruction, not re-opened

The 2026-09-12 driver-PII incident (`docs/audit/breach-record.md` Incident 1: 346 driver records
including SIN, bank details and licence numbers, public on GitHub for ≥11 days, still reachable on
187 of 500 checked non-`main` branches at last count, repository still public) is recorded here as
**owner-managed, closed by owner statement 2026-09-24, not independently verified**, per this
audit's explicit instruction and per `05-escalations.md` §0's own note ("Standing decisions
inherited, not re-opened: COMP-013 ... is owner-managed"). It is not assigned an open escalation
id and is not re-litigated by this file. See §1's P10.3 row for the underlying PIPEDA-principle
mapping, and `docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md` for the
still-unsubmitted GitHub purge request this audit found in draft form.

## §7 What could not be checked this pass

- **Every primary regulatory source** — 0 of 11 fetch attempts succeeded this audit (§0), and this
  session did not retry any of them against the same blocked proxy.
- **Live flag values for `enforce_driver_eligibility_recheck`, `pst_enabled`, `legacy_consent_notice_enabled`,
  and every other flag named in §1-§5** — no database session was available to this pass; every
  "UNKNOWN (no DB session)" or "default X" cell reflects the schema/code default only, not a
  confirmed production value.
- **Whether the driver-availability v2 path (`driver_availability_service.py`) has since been
  updated to include the eligibility checks S2 flags as missing** — cited from `02-findings/compliance.md`
  COMP-002, not independently re-grepped this session.
- **Independent counsel review of any of the 9 unreviewed legal texts** named in E-R11 — this file
  reports their review status, not their content correctness.
- **Whether COMP-013's owner-statement closure would satisfy an independent privacy-counsel review**
  — per this audit's instruction, this file records the closure without assessing it.
