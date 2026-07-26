# SGI Form Field Mapping — Reference for the Reporting Module

Status: reference capture only — no code implication yet. Feeds the
"Reporting Module" design (`spinr-reporting-module-design.md`) once that work
starts, specifically the `province_report_formatters` SGI formatter plugin.

Source documents (provided 2026-07-26, stored outside the repo — not
committed here since they're user-provided attachments, not repo assets):
- SGI Form D00033 (04/2021) — "Transportation Network Company – Vehicle Details"
- SGI Form D00032 (04/2021) — "Passenger for Hire – Driver Details"
- Saskatoon Police Service — "Criminal Occurrence Security Check" (sample CRC)
- `Customer.xlsx` — internal rider export sample (892 rows)

Knight Archer's equivalent forms have not been provided yet — this document
covers SGI only. Extend with a parallel section once Knight Archer's format
is available; do not assume it mirrors SGI's shape.

---

## 1. SGI Form D00033 — TNC Vehicle Details

Submitted to SGI whenever Spinr adds, removes, or changes an affiliated
vehicle. Company must report within **7 days** of a vehicle being
authorized; removal must be reported within **14 days**.

### Company header (submitted once per form/batch)

| Field | Notes |
|---|---|
| Company name | |
| SGI customer number | Spinr's account ID with SGI — a config value, not per-vehicle |
| Email | Company's registered contact email — must be kept current with SGI per form condition 9 |
| Street address / Province/state / Postal/zip / City/town | |
| Phone | |
| Primary contact | |

### Per-vehicle repeating block

| Field | Maps to (candidate Spinr source) | Notes |
|---|---|---|
| Date | vehicle record `created_at` / `updated_at` (action-dependent) | |
| Licence plate number | `vehicles.plate_number` (verify actual column name) | |
| VIN | `vehicles.vin` | Confirm encryption-at-rest status — VIN is not explicitly PIPEDA-restricted like SIN/driver licence #, but treat as sensitive |
| Year / make / model | `vehicles.year`, `vehicles.make`, `vehicles.model` | |
| Action | **Add / Remove / Change** — tri-state, not a boolean | This is the report's core semantic: SGI wants a *diff* since last submission, not a full roster snapshot |
| Registered owner's name | May not exist in Spinr's schema today if the vehicle owner isn't the driver — needs a schema check | |
| Valid inspection verified | Yes/No | Maps to whatever tracks "passes annual inspection" per CLAUDE.md's Saskatchewan Regulatory driver-eligibility list |

**Design implication**: the SGI vehicle report is diff-based (Add/Remove/Change
since last submission), not a point-in-time snapshot. The reporting module's
query layer needs a "changes since last SGI export" mode, not just a
date-range filter — likely keyed off a `last_sgi_export_at` watermark per
company or per vehicle, not a simple `WHERE created_at BETWEEN`.

---

## 2. SGI Form D00032 — Passenger for Hire (PFH) Driver Details

Companion form for driver affiliation. Same 7-day/14-day reporting windows
as the vehicle form. Additional rules from the form's own conditions section:

- CRC (criminal record check) must be dated within **90 days** of submission
  to SGI.
- SGI does **not** notify the company if a driver's licence is cancelled,
  suspended, or expires — the company's own systems are the only check.

### Company header

Same shape as the vehicle form (company name, SGI customer number, email,
address, phone, primary contact), plus a service-type checkbox: TNC / Taxi /
Limousine (Spinr is TNC).

### Per-driver repeating block

| Field | Maps to (candidate Spinr source) | Notes |
|---|---|---|
| Effective date | | |
| Name | `drivers` full name (join to `users`) | |
| Action | **Add / Remove / Change** | Same diff semantics as the vehicle form |
| Driver's licence number | `drivers.license_number` (verify column, confirm encryption) | Government ID — PIPEDA-restricted, never in logs per CLAUDE.md |
| Licence class | Must map to CLAUDE.md's "Valid Class 5 driver's license... Class 1-4 need separate approval" rule | |
| Verified driver history | Yes/No | Corresponds to the "clean abstract, no major violations in past 3 years" eligibility check |
| Criminal record check attached | Yes/No | See CRC sample mapping below |

**Design implication**: same diff-based reporting model as the vehicle form.
The reporting module's canonical data model should share one
"affiliation change" abstraction across drivers and vehicles rather than
building two independent report types, since the Add/Remove/Change shape and
the 7/14-day windows are structurally identical.

---

## 3. Criminal Occurrence Security Check (CRC) — sample document shape

This is the evidence document referenced by the driver form's "Criminal
record check attached: Yes/No" field. Sample from Saskatoon Police Service;
format will vary by issuing agency (any SGI-approved CRC provider is
acceptable per the driver form's conditions).

| Field on the CRC document | Why it matters for reporting |
|---|---|
| Name, Address, Date of birth | Identity match against the driver record |
| Query/check date | This is the date the 90-day SGI window is measured from — **not** the document's issue/approval date if they differ |
| CPIC conviction result | Pass/fail input to "Verified driver history" |
| Vulnerable Sector Search result | Separate from the general CPIC check — both appear on this sample form |
| Date Approved | |
| Clerk # / reference # | Useful as an audit trail reference if Spinr needs to prove provenance of a CRC on file |

**Design implication**: Spinr's own driver-document schema needs to capture
the CRC's **query date** (not just an upload timestamp) as a distinct field,
so the reporting module can correctly compute "is this CRC still within the
90-day window as of the SGI submission date" — confirm whether
`driver_documents` (or equivalent) already has this field before assuming it
needs to be added.

---

## 4. `Customer.xlsx` — internal rider export sample

892 data rows, **no header row** — columns inferred positionally:

| Position | Inferred meaning | Confidence |
|---|---|---|
| A | Row/sequence ID | High |
| B | Name | High |
| C | Email | High |
| D | Phone (E.164-ish, `+1 ##########`) | High |
| E | Flag (0/1) | **Low — ambiguous.** Could be marketing consent, active/inactive status, verified-email, or something else entirely. Needs source-system confirmation before treating as a real field mapping. |
| F, G | Empty in sample rows | Unknown — may be populated in other rows, or reserved/unused |

This looks like an ad-hoc export rather than a formal reporting template
(unlike the two SGI PDFs, which are official regulator forms) — treat it as
a rough shape reference for "what a bulk rider export might look like," not
as a target format to replicate exactly. Confirm intent before using it to
drive the reporting module's onboarding-report or bulk-upload CSV shapes.

---

## 5. Open items before building the SGI formatter

1. **Knight Archer's format** — not yet provided. Do not assume it mirrors
   SGI's Add/Remove/Change diff shape.
2. **Delivery mechanism** — these are SharePoint-upload or email
   (`vehiclesforhire@sgi.sk.ca`) forms today, filled manually. Confirm
   whether SGI will accept an automated export in this same field shape, or
   requires the literal PDF form to still be hand-submitted (i.e. the
   reporting module might generate the *data* but a human still uploads it
   via SharePoint, at least initially).
3. **`last_sgi_export_at` watermark** — confirm whether this state should
   live per-company or per-entity (driver/vehicle), since a company could
   plausibly need to re-sync a subset without re-sending everything.
4. **CRC query-date field** — confirm current schema has it; if not, this is
   a prerequisite migration for the SGI driver formatter, not something the
   formatter can work around.
5. **Vehicle owner ≠ driver** — confirm whether Spinr's schema distinguishes
   "registered owner" from "driver" today; the SGI vehicle form requires the
   owner's name separately.
