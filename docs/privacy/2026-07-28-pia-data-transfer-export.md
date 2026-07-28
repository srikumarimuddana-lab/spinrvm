# Privacy Impact Assessment — Data Transfer Module (Export Path)

> **Scoping note on legislative framework:** this PIA uses the general PIA
> structure (personal-information inventory, risk analysis, Fair Information
> Principles assessment, sharing assessment, breach response, ranked
> recommendations), but is written for **PIPEDA** (Canada's federal
> private-sector privacy law), not FOIP/HIPA. Spinr is a private-sector
> company, not a Saskatchewan government institution or health trustee, and
> Saskatchewan has no private-sector-specific privacy statute of its own —
> PIPEDA is the applicable federal law here. References to "the Commissioner"
> below mean the Office of the Privacy Commissioner of Canada (OPC), not
> Saskatchewan's IPC. No GoS branding, IPC-specific timelines, or
> classification grid apply.

## Document Header

| Field | Value |
|---|---|
| PIA Title | Data Transfer Module (Export Path) Privacy Impact Assessment |
| PIA Reference | SPINR-PIA-2026-001 |
| Version | 1.0 — Draft |
| Program/System | Admin Dashboard → Data Transfer → Export |
| Assessment Date | 2026-07-28 |
| Next Review | On any material change to `entity_export_service.py`, `data_transfer_export.py`, or the export bucket's access/retention config — or 12 months, whichever is sooner |
| Prepared by | Claude (session audit), following a structured SDLC audit that identified this PIA's absence as a P0 gap |
| Status | **This PIA does not exist prior to this document.** It is being produced specifically to close that gap, not to re-approve an already-reviewed flow. |

## Section 1: Executive Summary

The Data Transfer module lets a Spinr admin export a full-fidelity, **unredacted** copy of up to 100 driver or rider records — including profile data, uploaded identity/vehicle documents, ride history (with exact GPS pickup/dropoff coordinates), and the regulatory insurance-period audit trail — as a downloadable bundle (ZIP/CSV/JSON/XLSX). Its stated purpose is moving records between Spinr's own environments (e.g., staging↔production data seeding, environment migration), not a rider/driver-facing data-access request — that is a separate, already-existing self-service export (`routes/drivers/tax_exports.py`) which does redact/summarize.

**Privacy risk rating: Medium-High.** The core design choice — full, unredacted, bulk, admin-triggered export of sensitive personal information (including biometric-adjacent identity documents and precise location history) — is a legitimate operational need but currently carries fewer compensating controls than its sensitivity warrants: no purpose/justification capture at export time, no per-export data minimization option (e.g., excluding ride GPS when not needed), and access is broader than the sensitivity of the data suggests (see Section 4).

Recommendations: 2 Critical/High, 3 Medium, 2 Low (Section 8, 7 total: R-A through R-G). **Update 2026-07-28:** R-A (High) and R-D (Medium) are DONE — see Section 8 for what was actually fixed in each (investigating both before implementing surfaced more precise root causes than the original recommendation text assumed; R-001's risk rating was corrected downward as part of R-A's remediation). 5 of 7 recommendations remain open (R-B, R-C, R-E, R-F, R-G).

**Approval recommendation:** Do not treat this feature as newly launched pending sign-off — it is already live. Recommend the Critical/High items in Section 8 be actioned promptly as compensating controls, and that this PIA itself be treated as satisfying the "PIA on file" requirement going forward once reviewed by whoever owns privacy decisions at Spinr.

## Section 2: System/Program Description

**Purpose and business justification:** Spinr operates a Fly.io/Railway production backend and needs a way to move a subset of real driver/rider records into another environment (e.g., populating a staging environment with realistic data, or migrating between the primary/standby regions described in `docs/adr/007-fly-primary-railway-standby.md`). The module explicitly does **not** serve rider/driver self-access requests (PIPEDA's individual-access right) — that is `routes/drivers/tax_exports.py`, a separate, redacted, self-service flow.

**Data flow (export path):**
1. Admin (holding the `bulk_operations` module flag) POSTs to `/api/admin/data-transfer/export` with an entity list (driver|rider, up to 100 IDs) and format choice.
2. A `data_transfer_export_jobs` row (migration 262) is inserted (`pending`), rate-limited to 10 requests/hour per the export route's limiter.
3. A background task (`entity_export_service.gather_entity_bundle`, one call per entity, run concurrently) pulls: the `users` row, `drivers` row (if driver) with `license_number` **decrypted from Supabase Vault** to its plaintext value, `notification_preferences`, up to **500 rides per entity** (full row — includes `pickup_lat`/`pickup_lng`/dropoff-equivalent columns, confirmed present in the `rides` table since migration 08), up to 200 `driver_documents` rows **with their raw file bytes downloaded**, and up to 2000 `driver_insurance_periods` rows (the regulatory audit trail).
4. The gathered bundles are packaged (ZIP with a manifest + per-document files, or a flattened CSV/JSON/XLSX) and uploaded to the private `data-transfer-exports` Supabase Storage bucket.
5. The job row is updated to `completed` with a `storage_path` and `expires_at` (7-day retention window before purge — see step 6). **Correction (2026-07-28, post-B11/R-D fix):** this PIA originally stated the initial POST response includes a 7-day signed URL — that was inaccurate. The export route is fully backgrounded (`202` response with only `job_id`, before the file even exists); a signed URL was being generated inside the background task but never returned to any caller (dead code, since fixed — see `docs/change-log/2026-07-28-data-transfer-export-drop-unused-signed-url.md`). The only way an admin ever gets a working URL is via `data_transfer_jobs.py`'s `/download` endpoint (now super_admin-gated — see the earlier 2026-07-28 access-control fix), which mints a fresh 1-hour signed URL on demand as long as the job hasn't been purged.
6. A background purge loop (`utils/data_export_purge.py`) removes the Storage object and marks the row `deleted_at` once `expires_at` passes.

**No redaction anywhere in this path** — confirmed by reading `entity_export_service.py` end to end. The module's own docstring states this is deliberate: "Unlike the PIPEDA self-export... this gathers data for an admin moving a record between Spinr's own environments — no redaction, because the operator already has full admin visibility into the source data." That's a defensible design *if and only if* the actor triggering it is verifiably an operator who should have that visibility for a stated purpose — Section 4 examines whether the current access control actually delivers that guarantee.

**System interconnections:** Supabase Postgres (source of truth), Supabase Storage (bundle storage — same project/region as primary data, so no new data-residency exposure), Sentry (metrics/error capture — confirmed `domain=admin`, IDs only, no raw PII in the observability layer per `services/data_transfer/observability.py`). No third-party system receives this data — it stays within Spinr's own Supabase project.

## Section 3: Personal Information Inventory

| Data Element | Category | Sensitivity | Source Table | Purpose | Legal Authority (PIPEDA) | Retention | Recipients |
|---|---|---|---|---|---|---|---|
| Full name, email, phone | Basic Identity | Medium | `users` | Reconstruct the account in target environment | Implied consent under original account terms; PIPEDA Principle 4.3 (consent for the *original* collection) — this **secondary use** (admin cross-environment transfer) is not separately disclosed to the user (see Section 5, Openness) | Until export job's `expires_at` (Storage); source-table retention governed by the general 30-day-deletion/7-year-trip-record rules elsewhere | Admin with `bulk_operations` flag |
| Driver's license number | Legal / Government ID | High | `drivers.license_number` (Vault-encrypted at rest) | Regulatory driver-eligibility record, re-encrypted in target project on import | PIPEDA — collected for the stated purpose of driver eligibility verification (SGI); this export moves it, doesn't add a new collection | Same as above | Same as above |
| Uploaded documents (license photo, insurance, vehicle registration) — raw file bytes | Legal / Government ID | High | `driver_documents` (Storage) | Same as above | Same | Same as above | Same as above |
| Ride history incl. **exact pickup/dropoff GPS coordinates** | Location | High | `rides.pickup_lat/pickup_lng` (+ dropoff equivalents) | Operational/regulatory trip record | PIPEDA — trip records already have a stated 7-year regulatory retention purpose (Saskatchewan Transportation Act); this export is a *secondary use* moving the same identified data, at full precision, into a second environment | Bounded by export job `expires_at`, independent of the source `rides` table's own 7-year/3-year (GPS-specific) retention split | Same as above |
| `driver_insurance_periods` (regulatory audit trail) | Legal / Regulatory | Medium-High | `driver_insurance_periods` | SGI/insurance-period audit requirement | PIPEDA + SK Transportation Act (7-year retention, append-only) | Same as above | Same as above |
| Notification preferences | Basic Identity (low-sensitivity) | Low | `notification_preferences` | Reconstruct preferences in target env | Implied | Same as above | Same as above |

**Note on CLAUDE.md's own log-scrubbing rule** ("Raw GPS coordinates — log geohashed area at most"): that rule is written for logs/Sentry/analytics payloads, not database exports, and this export correctly does not put raw coordinates into logs or Sentry (verified in `observability.py`). But the *spirit* of data minimization that rule encodes — don't move more precision than the purpose needs — is not currently applied to this export path at all, since the purpose (environment migration/seeding) plausibly does not need exact GPS precision on every historical ride. This is the basis for Recommendation R-002 below.

## Section 4: Privacy Risk Analysis

**R-001 — RESOLVED (2026-07-28), finding corrected during remediation.** Original description (for the record): "The export/import/search/SGI-forms routes are gated on the `bulk_operations` admin module flag — a role-assignable permission, not `super_admin`. Any admin with that flag... can export full unredacted PII..." **Investigating this before implementing R-A found the premise was inaccurate**: `bulk_operations` does not appear in `AVAILABLE_MODULES`/`ALL_MODULES`/any `ROLE_PRESETS`/any migration, and the "custom" role grant path filters against `AVAILABLE_MODULES` — so no non-super_admin could ever actually hold this flag through any current code path. Effective access was already super_admin-only, but only by omission from a list (fragile — a future unrelated feature adding `bulk_operations` to the grantable list would have silently reopened this). Fixed by replacing the module-flag gate with an explicit `require_super_admin` dependency, closing the fragility without changing any real admin's actual access. See `docs/change-log/2026-07-28-data-transfer-router-super-admin-gate.md`.
- Original Risk Rating: High (3×4=12) — based on the inaccurate premise. **Corrected risk assessment:** the live risk was Low (latent/fragile-by-omission, not actively exploitable) prior to this fix; now closed to negligible via explicit enforcement.
- Owner: N/A — resolved.

**R-002 — No data-minimization option at export time**
- Description: An admin exporting for, say, "seed staging with 20 test-realistic driver profiles" has no way to exclude ride GPS history, insurance-period rows, or document bytes if the target use case doesn't need them — it's all-or-nothing per entity.
- Likelihood: 3 (Possible)
- Impact: 3 (Moderate) — over-collection relative to actual need, not a breach by itself, but increases blast radius if the bundle is later mishandled.
- Risk Rating: **Medium** (3×3=9)
- PIPEDA principle at risk: 4.4 (Limiting Collection) — by extension, limiting *use* of already-collected data to what a given purpose needs.
- Mitigation: See Recommendation R-B below.
- Residual Risk: Low.
- Owner: Data Transfer module maintainer.

**R-003 — No purpose/justification capture per export**
- Description: The export request body has no required field capturing *why* this particular export is happening. If an export bundle is later found to have been misused, there's no contemporaneous record of the stated business reason to compare against.
- Likelihood: 2 (Unlikely) — but only because nothing currently prevents it; this is a control gap, not a low-probability threat.
- Impact: 3 (Moderate)
- Risk Rating: **Medium** (2×3=6)
- PIPEDA principle at risk: 4.1 (Accountability) — being able to demonstrate compliance requires being able to show why data moved.
- Mitigation: See Recommendation R-C below.
- Residual Risk: Low.
- Owner: Data Transfer module maintainer.

**R-004 — Downloaded bundle has no in-transit or at-rest control beyond bucket privacy + signed URL**
- Description: Once an admin has the (1-hour, on-demand-regenerated — see the Section 2 correction above) signed URL and downloads the ZIP, the data is now a file on that admin's local machine, entirely outside Spinr's Supabase access-control perimeter, indefinitely.
- Likelihood: 4 (Likely) — this is the expected/intended end state of using the feature at all, not a hypothetical misuse.
- Impact: 3 (Moderate) — same sensitivity as R-001's data, now on an uncontrolled endpoint.
- Risk Rating: **Medium-High** (4×3=12, but capped at Medium because this is largely inherent to "export" as a concept and only partially mitigable)
- PIPEDA principle at risk: 4.7 (Safeguards)
- Mitigation: Largely accepted risk of the feature category; see Recommendation R-D (low-cost partial mitigation) below.
- Residual Risk: Medium (irreducible without removing the export capability itself).
- Owner: N/A — accepted operational risk, flagged for awareness rather than full mitigation.

**R-005 — Retention of the export bundle is independent of and shorter than the source data's regulatory retention**
- Description: This is actually a *positive* finding, not a gap: the export bundle's `expires_at`-driven purge (via `utils/data_export_purge.py`) is separate from and unrelated to the 7-year/3-year regulatory retention on the source `rides`/`driver_insurance_periods` tables — the export copy does not extend or interfere with the source retention clock, and gets deleted well before the source data's mandatory retention window would end.
- Likelihood/Impact/Rating: N/A (documenting a control that is working correctly, not a risk)

## Section 5: Privacy Controls Assessment (Fair Information Principles)

1. **Accountability** — Partial. No named owner/approver for this specific data flow exists in documentation prior to this PIA. *Gap.*
2. **Identifying Purposes** — Partial. The purpose is clear in code comments/docstrings but not disclosed to the end users (drivers/riders) whose data is moved this way, nor captured per-export (R-003).
3. **Consent** — Implied only, inherited from original account signup consent. This module represents a secondary use (cross-environment transfer) that was not separately itemized at consent time. Not necessarily a violation (PIPEDA allows reasonable secondary uses consistent with the original purpose), but worth explicit legal review rather than assumption.
4. **Limiting Collection** — N/A directly (this module doesn't collect new data, it moves existing data) — but see R-002 on limiting *use/movement* to what's needed.
5. **Limiting Use, Disclosure, Retention** — Retention is well-controlled (Section 4, R-005). Use/disclosure controls are the weakest area (R-001, R-002, R-003).
6. **Accuracy** — Not directly affected; this module copies current-state data, doesn't degrade accuracy.
7. **Safeguards** — Storage bucket is private, signed-URL-gated, vault-encryption round-trip for `license_number` is correct (verified: `entity_export_service.py:71-79` decrypts on export; import re-encrypts against target vault). Access-control breadth (R-001) is the main safeguards gap.
8. **Openness** — No privacy-notice language specifically covers this internal admin data-movement capability (reasonable — it's an internal operational tool, not user-facing — but worth a one-line internal data-handling policy note, see R-C).
9. **Individual Access** — Not applicable to this module (that's the separate self-export flow) — correctly out of scope here.
10. **Challenging Compliance** — No specific complaint path for "was my data exported via this tool inappropriately" — falls under Spinr's general privacy complaint process if one exists; not verified as part of this PIA.

## Section 6: Data Sharing Assessment

Not applicable in the third-party sense — this module moves data **within** Spinr's own Supabase project/environments, not to an external recipient. No Data Sharing Agreement is needed because there is no external party. If a future version of this module were extended to export to a *different* Supabase project (a true cross-organization or cross-region transfer), that would be a materially different risk profile requiring a fresh PIA section and, per CLAUDE.md's data-residency rule, explicit legal sign-off before any non-Canadian region is involved.

## Section 7: Breach Response

This module does not currently have a bespoke breach playbook; it falls under Spinr's general breach protocol (`CLAUDE.md`'s Compliance section: P0 incident classification, 24h scope assessment, 72h OPC notification if "real risk of significant harm"). Given this module moves batched, unredacted, high-sensitivity PII (government ID numbers + precise location history for up to 100 people per export), a leaked or mishandled export bundle would very plausibly meet PIPEDA's "real risk of significant harm" threshold — worth explicitly naming this module in `docs/runbooks/data-breach.md` (noted there as "to be created" in CLAUDE.md) once that runbook exists, so an incident responder doesn't have to rediscover this data flow's shape under time pressure.

## Section 8: Recommendations

**[HIGH] R-A — DONE (2026-07-28).** Original text (for the record): "Tighten export/import/search/SGI-forms access beyond the generic `bulk_operations` flag... split a new, narrower module flag... or require `super_admin` outright." Presented both options (plus a docs-only alternative) to Spinr; **option 2 (explicit `super_admin` requirement) was chosen** over splitting a new module flag — the new-flag option was functionally identical to today's behavior (still granted to nobody by default) and would have kept the same fragile-by-omission shape this recommendation was meant to close, just under a different flag name. All 5 routers (export/import/search/jobs/SGI-forms) now require `super_admin` via a new explicit `require_super_admin` dependency (`backend/dependencies/__init__.py`), independent of `AVAILABLE_MODULES` contents. See `docs/change-log/2026-07-28-data-transfer-router-super-admin-gate.md`. If a future business need arises to delegate this to a trusted non-super-admin operator without a code change, revisit the module-flag approach then — deferring that complexity until it's actually needed rather than building it speculatively now.

**[HIGH] R-B — Add optional per-export scope flags to exclude GPS-precision ride data and/or document bytes when not needed**
Extend the export request body with optional booleans (e.g. `include_ride_gps: bool = True`, `include_document_bytes: bool = True`), defaulting to current (full) behavior for backward compatibility, but letting an admin doing a lower-sensitivity operation (e.g. seeding a UI-only staging environment) explicitly opt out of the highest-sensitivity fields. Owner: Data Transfer module maintainer. Success criterion: an export can be requested and completed with ride rows present but `pickup_lat`/`pickup_lng`/dropoff-equivalent fields stripped, without breaking existing full-fidelity import round-trips (must remain opt-in-to-exclude, not a behavior change to the default).

**[MEDIUM] R-C — Require a short business-justification string on every export request, stored on the job row**
Add a required `reason: str` field (short, e.g. 10-200 chars) to the export request, stored in `data_transfer_export_jobs` alongside the existing columns. This directly closes R-003 (Accountability) and gives a contemporaneous record for any future compliance question, at very low implementation cost (one new column, one new required field, no workflow change). Owner: Data Transfer module maintainer. Success criterion: `data_transfer_export_jobs.reason` is populated for every new export, visible in the (now super_admin-only) Jobs & History tab.

**[MEDIUM] R-D — DONE (2026-07-28).** Original text (for the record): "Shorten the initial 7-day signed URL TTL, or make it configurable per-sensitivity." Investigating this recommendation while implementing it found the underlying premise was wrong: no signed URL was ever exposed to a caller at export time — the route is fully backgrounded (`202` + `job_id` only), and `_upload_bundle` was minting a 7-day signed URL inside the background task that was computed and then immediately discarded (`_ = signed_url`, never persisted or returned). There was no actual "initial URL exposure window" to shorten. Fixed the real issue instead: removed the dead `create_signed_url` call entirely — `_upload_bundle` now just uploads and returns `storage_path`; every real download URL still comes from `data_transfer_jobs.py`'s 1-hour on-demand regeneration, unchanged. See `docs/change-log/2026-07-28-data-transfer-export-drop-unused-signed-url.md`. `_EXPORT_RETENTION_SECONDS` (still 7 days, renamed from `_EXPORT_LINK_TTL_SECONDS` for clarity) continues to govern the separate, correct concern of `expires_at`/Storage purge timing — not touched, not in scope for this recommendation.

**[MEDIUM] R-E — Add this module explicitly to the (not-yet-created) `docs/runbooks/data-breach.md`**
When that runbook is authored, name this module's specific data flow (full unredacted PII, up to 100 entities, GPS precision, government ID numbers) as a designated high-sensitivity flow so an incident responder has this PIA's Section 3 inventory pre-linked rather than having to reconstruct it during an active incident. Owner: whoever authors the breach runbook (tracked as an existing open item in CLAUDE.md, not new).

**[LOW] R-F — Consider whether `notification_preferences` needs to be in a "full-fidelity, unredacted" bundle at all**
Lowest-sensitivity field in the inventory (Section 3) — likely fine as-is, but worth a five-minute confirmation with whoever owns the target-environment reconstruction use case that this field is actually needed, versus being included by inertia. Owner: Data Transfer module maintainer.

**[LOW] R-G — Formal legal review of the "implied consent" basis for this secondary use**
Section 5's Consent assessment above is this PIA author's reasoning, not a legal opinion. Recommend a short confirmation from whoever handles privacy/legal review at Spinr that treating cross-environment admin data movement as within the scope of original account consent is the correct PIPEDA position, rather than requiring a distinct disclosure. Owner: Spinr's privacy/legal function (not specified in this repo).

## Section 9: Sign-Off

| Role | Name | Date |
|---|---|---|
| Assessed by | Claude (session audit) | 2026-07-28 |
| Reviewed by | *(pending — no privacy officer role identified in this repo; recommend routing to whoever owns PIPEDA compliance decisions at Spinr)* | |
| Approved by | *(pending)* | |
| OPC submission | Not required for a PIA itself (PIPEDA doesn't mandate PIA filing the way FOIP/HIPA IPC processes do) — only a breach meeting the "real risk of significant harm" threshold would trigger OPC notification, per Section 7 | N/A |
