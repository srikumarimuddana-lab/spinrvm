# Audit Framework — Changelog

Every material change to `audit-framework/` (dimensions, modules, templates,
ground rules, regulatory matrix) is logged here. Framework changes trigger
re-audit obligations per the 90-day rule below.

---

## 90-Day Re-Run Rule

**When a dimension is added or a checklist item is added to an existing
dimension**, every module whose `modules/<module>.md` lists that dimension
as applicable must be re-audited for that dimension within **90 days**.

- Date the framework change lands in `main` → day 0
- Re-audit deadline → day 90
- Tracking: the OPEN-ITEMS-TRACKER `G · Regulatory Deadlines` section gets
  a row added for each module × changed dimension.

**Backfill for existing framework content:** When D17–D23 were added in
April 2026, re-audit obligations for these dimensions on each module are
tracked in the INDEX execution rows.

---

## 2026-04-24 · v1.3

**Added**
- `dimensions/23-mobile-binary.md` — signed APK/IPA, MobSF, PrivacyInfo.xcprivacy,
  TLS pinning, App Check, SBOM, store-submission gates
- `ground-rules.md` rule 7 — incident → audit feedback loop
- `ground-rules.md` rule 8 — auditor independence (`reviewed_by` required)
- `templates/audit-output.txt` YAML schema — `likelihood`, `risk_score`,
  `dollars_at_risk`, `reviewed_by` fields
- `/SECURITY.md` — disclosure policy, safe-harbor
- `/docs/ci-security-gates.md` — 10-gate CI matrix + Spinr-specific Semgrep rules
- `/docs/data-classification.md` — C1–C5 tiers + per-column authoritative map
- `/docs/external-testing.md` — bug bounty, pen-test, ASV, red team, tabletop

**Changed**
- `modules/driver-app.md` — dimension count 22 → 23
- `modules/rider-app.md` — dimension count 22 → 23
- `templates/run-audit.md` — D23 added to time estimates and pre-App-Store
  selection row; full-audit total raised

**Re-audit obligations (90-day clock starts 2026-04-24, deadline 2026-07-23):**
- rider-app D23 — run before next store submission
- driver-app D23 — run before next store submission
- rider-app incident-feedback rule check — confirm no open incidents trace to
  a missing rule
- driver-app incident-feedback rule check — same

---

## 2026-04-23 · v1.2

**Added**
- `dimensions/17-observability.md`
- `dimensions/18-dr-bcp.md`
- `dimensions/19-fraud.md`
- `dimensions/20-financial-reconciliation.md`
- `dimensions/21-threat-model.md`
- `dimensions/22-third-party.md`
- `regulatory-matrix.md` (Canadian federal + SK provincial + Regina/Saskatoon
  municipal + PCI/SOC2/WCAG + safety screening + OCAP + data classification
  definitions)
- `reports/audits/INDEX.md` — master switchboard
- `reports/audits/OPEN-ITEMS-TRACKER.md` — consolidated tracker
- `reports/audits/2026-04-23-{backend-api,admin-panel}-audit-plan-v1.md`
- `reports/audits/2026-04-23-rider-app-remediation-verification-prompt.md`
- `reports/audits/2026-04-23-driver-app-remediation-verification-prompt.md`

**Changed**
- `README.md` — 16 → 22 dimensions
- `modules/*.md` (all 4) — D17–22 added to applicable-dimensions tables
- `templates/run-audit.md` — Phase E row; output-schema block; time estimates
- `templates/audit-output.txt` — `===FINDINGS-YAML===` schema + sentinel
- `templates/remediation-group.md` — blast_radius, regulations, owner,
  regression_test, blocker-for fields

**Re-audit obligations (90-day clock deadline 2026-07-22):**
- rider-app D17–D22 (Phase E kickoff file created 2026-04-23)
- driver-app D17–D22 (Phase E kickoff for v5 re-audit to be created)
- backend-api D17–D22 (plan v1 includes Phase E)
- admin-panel D17–D22 (plan v1 includes Phase E)

---

## 2026-04-19 · v1.1

**Added**
- Rider app audit plan + Phase A–D kickoffs
- Rider app v1 audit findings (184 findings across 16 dimensions)
- Rider P0–P4 remediation sprint files

---

## 2026-04-18 · v1.0 (Baseline)

**Added**
- 16 dimensions (01–16)
- 4 module scope files
- `ground-rules.md` canonical rules 1–6
- `templates/{audit-output.txt, remediation-group.md, run-audit.md}`
- Driver app v4 audit (258 findings)
- Driver P0–P4 remediation sprint files

---

## Process for Adding a New Dimension

1. Draft the dimension doc at `audit-framework/dimensions/NN-slug.md` using
   an existing doc as template.
2. Update every applicable `audit-framework/modules/<module>.md` to list the
   new dimension in the applicable-dimensions table.
3. Update `audit-framework/templates/run-audit.md` to add a time estimate
   row and include it in appropriate selection-guide rows.
4. Log the change here with the date and list of modules that must re-audit.
5. Commit all four files in a single PR with title
   `feat(audits): add D<NN> <slug> dimension`.
6. File new `OPEN-ITEMS-TRACKER.md § G` rows for the 90-day re-audit deadlines.

---

## Process for Adding a New Checklist Item to an Existing Dimension

Same as above, minus step 1. Log here noting which dimension + checklist
item was added. A checklist addition is **not** a full re-audit trigger;
instead, module re-audits must run that single item's check within 90 days
and note PASS / new finding.
