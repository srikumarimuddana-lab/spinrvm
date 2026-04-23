# Spinr Audit Index — Master Switchboard

**Updated:** 2026-04-23 · Branch: `claude/review-pending-audits-Pu1aP`
**Maintainer:** any audit agent / reviewer arriving at the repo

This is the one-page "where do I start?" document. Everything you need to run,
verify, or remediate any module is indexed here. Execute the **Next Actions**
in order; each is a single agent call sized to fit one context window.

---

## Framework Status

| Artifact | Status |
|---|---|
| `audit-framework/README.md` | 22 dimensions registered |
| `audit-framework/ground-rules.md` | Canonical |
| `audit-framework/regulatory-matrix.md` | Canadian fed + SK prov + Regina/Saskatoon muni + PCI/SOC2/WCAG + safety + OCAP |
| `audit-framework/dimensions/01–22-*.md` | All 22 dimensions present |
| `audit-framework/modules/{backend-api,admin-panel,rider-app,driver-app}.md` | All 4 module scopes present |
| `audit-framework/templates/{audit-output.txt,remediation-group.md,run-audit.md}` | Canonical |

---

## Module Status

| Module | Audit | Remediation items | Verified @ HEAD | Re-audit scheduled |
|---|---|---|---|---|
| **Driver app** | v4 complete (2026-04-18) · 258 findings | 44 items P0–P4 | ✅ 89% (2026-04-23) — rollup ready | v5 (after dims 17–22 adoption) |
| **Rider app** | v1 complete (2026-04-19) · 184 findings | 92 items P0–P4 | ⏳ **prompt ready, execution pending** | v2 (after dims 17–22 adoption) |
| **Backend API** | Plan v1 ready · findings **pending** | — | — | Phase A → E execution |
| **Admin panel** | Plan v1 ready · findings **pending** | — | — | Phase A → E execution |

---

## Plan / Prompt Inventory

### Driver app (audit done, remediation ≈ closed)
- `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt` — v4 findings
- `reports/audits/task14-performance-scalability.txt` — perf supplement
- `reports/remediation/P{0..4}-*.md` — 5 sprint plans (44 items total)
- `reports/audits/2026-04-23-driver-app-remediation-verification-prompt.md` — verification contract
- `reports/audits/2026-04-23-driver-P{0..4}-verification.md` — per-sprint results
- `reports/audits/2026-04-23-driver-remediation-rollup.md` — 89% complete summary

### Rider app (audit done, remediation pre-populated, verification pending)
- `reports/audits/2026-04-19-rider-app-audit-plan-v1.md` — main plan
- `reports/audits/rider-app-phase-{a,b,c,d}-kickoff.md` — phase kickoffs
- `reports/audits/2026-04-19-rider-app-v1.txt` — v1 findings
- `reports/remediation/rider-P{0..4}-*.md` — 5 sprint plans (92 items total)
- `reports/audits/2026-04-23-rider-app-remediation-verification-prompt.md` — **verification contract (new)**

### Backend API (plan ready, execution pending)
- `reports/audits/2026-04-23-backend-api-audit-plan-v1.md` — plan with Phase A–E breakdown
- Findings target: `reports/audits/2026-04-23-backend-api-v1.txt`

### Admin panel (plan ready, execution pending)
- `reports/audits/2026-04-23-admin-panel-audit-plan-v1.md` — plan with Phase A–E breakdown
- Findings target: `reports/audits/2026-04-23-admin-panel-v1.txt`

### Cross-module trackers
- `reports/audits/OPEN-ITEMS-TRACKER.md` — every open item across modules with owner, effort, regulation tags, action

---

## Execution Order (sequential, API-safe)

Each row is one agent call unless noted. Finish, commit, push; then start the
next row. Do not batch multiple rows into one call — the context window will
compress and truncate the output schema.

| # | Action | Agent prompt | Expected output | Blocker? |
|---|---|---|---|---|
| 1 | Close driver **P0-5** (4 h backend) | (engineering work — not an audit prompt) | Fix commit | ⚠ blocks device test |
| 2 | Verify rider **P0** | `2026-04-23-rider-app-remediation-verification-prompt.md` (scope: P0) | `2026-04-23-rider-P0-verification.md` | — |
| 3 | Verify rider **P1** | same prompt, scope: P1 | `2026-04-23-rider-P1-verification.md` | — |
| 4 | Verify rider **P2** | same prompt, scope: P2 | `2026-04-23-rider-P2-verification.md` | — |
| 5 | Verify rider **P3** | same prompt, scope: P3 | `2026-04-23-rider-P3-verification.md` | — |
| 6 | Verify rider **P4** | same prompt, scope: P4 | `2026-04-23-rider-P4-verification.md` | — |
| 7 | Rider rollup | synthesise P0–P4 verifications | `2026-04-23-rider-remediation-rollup.md` | — |
| 8 | Backend-API **Phase A** (dims 01–04) | `backend-api-audit-plan-v1.md` § Phase A | Findings appended to `2026-04-23-backend-api-v1.txt` | — |
| 9 | Backend-API **Phase B** (dims 07–08) | same plan § Phase B | Findings appended | — |
| 10 | Backend-API **Phase C** (dims 09–12) | same plan § Phase C | Findings appended | — |
| 11 | Backend-API **Phase D** (dim 14) | same plan § Phase D | Findings appended | — |
| 12 | Backend-API **Phase E** (dims 17–22) | same plan § Phase E | Findings appended | — |
| 13 | Backend-API sprint plan derivation | post-audit — synthesise P0–P4 from findings | `reports/remediation/backend-P{0..4}-*.md` | — |
| 14–18 | Admin-Panel **Phase A–E** | `admin-panel-audit-plan-v1.md` § per phase | Findings appended to `2026-04-23-admin-panel-v1.txt` | — |
| 19 | Admin-Panel sprint derivation | post-audit | `reports/remediation/admin-P{0..4}-*.md` | — |
| 20 | Remediate rider open items | per-sprint engineering work | Commits + PRs | — |
| 21 | Remediate backend/admin P0+P1 | engineering work | Commits + PRs | blocks beta |
| 22 | Gap-follow-up (17 new issues from driver verification) | `OPEN-ITEMS-TRACKER.md` § new-issues | Commits | — |

**Halt conditions between rows:** any row producing a `CRITICAL` finding that
blocks a downstream execution dependency (e.g. backend Phase A `CRITICAL` on
auth) should pause subsequent rows until triaged.

---

## Output Contract (enforced by every audit prompt)

1. Every finding carries a YAML block under `===FINDINGS-YAML===` with fields
   defined in the plan files (backend + admin plans have full schema + a
   worked example).
2. Every finding tags ≥1 regulation ID from `audit-framework/regulatory-matrix.md`.
3. `CRITICAL`/`HIGH` findings must cite `file:line:snippet`. Otherwise downgrade
   to `RECOMMENDATION` with `confidence: low`.
4. Audit ends with `===AUDIT-COMPLETE===` sentinel. Driver scripts detect
   truncation by the absence of this line.
5. Self-review pass runs before the sentinel.

---

## Naming Convention (for new files)

```
reports/audits/YYYY-MM-DD-<module>-<artefact>-v<N>.<ext>
reports/audits/<module>-phase-<letter>-kickoff.md   (kickoff is plan-scoped, not dated)
reports/audits/<MODULE>-*.md                         (trackers are uppercase, undated)
reports/remediation/<module>-P<N>-<slug>.md
reports/remediation/P<N>-<slug>.md                   (driver; legacy unprefixed)
audit-framework/dimensions/NN-<slug>.md
audit-framework/modules/<module>.md
```

---

## Completion Criteria — "Complete Product"

The product is audit-complete when:

- [ ] All 4 modules have v1 audits on file (driver v4, rider v1 done; backend, admin pending)
- [ ] All 4 modules have HEAD-verification for all remediation items (driver done; rider pending)
- [ ] No open `CRITICAL` or `HIGH` remediation items unless explicitly deferred with sign-off
- [ ] Every open item in `OPEN-ITEMS-TRACKER.md` has an owner + target sprint
- [ ] Dimensions 17–22 findings filed per module (operational readiness)
- [ ] Regulatory tags cover every applicable ID for each module (per `regulatory-matrix.md`)
- [ ] Last audit date < 90 days for every module at launch
- [ ] External pen-test scheduled or completed pre-launch

---

## FAQ

**Q: Can I re-order the execution list?**
A: Rows 1 and 2–7 are independent (P0-5 fix is engineering; rider verification is audit). Rows 8–13 must run in order (Phase A → B → C → D → E). Rows 14–18 similarly.

**Q: Can I combine two phases in one agent call to save time?**
A: No. Each phase is sized to one context window after including framework docs + module scope + output schema. Combining will truncate the YAML output.

**Q: Can I skip dimensions 17–22 for launch?**
A: Only with explicit sign-off. They are the operational/governance dimensions that catch "day-2 incidents". See `audit-framework/dimensions/17-22-*.md`.

**Q: What if a remediation item is obsolete?**
A: Mark `SUPERSEDED` in verification with evidence. Update the source remediation markdown during the next sync pass (see driver P1-10 precedent).
