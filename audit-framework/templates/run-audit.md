# How to Run a Spinr Audit

Use this guide every time you audit a module. Following these steps consistently keeps reports comparable across audits.

---

## Step 1 — Set Up (5 minutes)

1. Create a new branch: `git checkout -b audit/[module]-v[n]-[date]`
2. Read `audit-framework/ground-rules.md` — know what not to flag
3. Open the target module file: `audit-framework/modules/[module].md`
4. Decide which dimensions to run (full audit = all 16; targeted = pick relevant ones)

---

## Step 2 — Pre-Audit Scans (5 minutes)

Run these before reading any code:

```bash
# JavaScript dependencies
cd driver-app && npm audit 2>&1 | tail -5

# Python dependencies
cd backend && pip-audit 2>&1 | tail -10

# Secrets in codebase
git log --all --oneline | head -5
grep -r "sk_live_\|supabase\.co" . --include="*.ts" --include="*.py" -l
```

Record results at the top of the audit output file.

---

## Step 3 — Run Each Dimension

For each selected dimension:

1. Open `audit-framework/dimensions/[NN]-[name].md`
2. Work through the checklist, reading the actual code files
3. For each checklist item: write a finding or PASS
4. Use the finding format from `audit-framework/templates/audit-output.txt`

**Token/context tip:** Run each dimension as a separate agent call if using AI tools. Dimensions 01–04 first (Phase A), then 05–08 (Phase B), then 09–12 (Phase C), then 13–16 (Phase D). Each phase fits comfortably in one context window.

---

## Step 4 — Write the Report

1. Copy `audit-framework/templates/audit-output.txt`
2. Save as `reports/audits/YYYY-MM-DD-[module]-v[n].txt`
3. Fill in the findings from Step 3
4. Tally severities in the final table

---

## Step 5 — Write Remediation Files

1. Copy `audit-framework/templates/remediation-group.md` for each priority group needed
2. Save as `reports/remediation/P0-[module]-critical.md`, `P1-...`, etc.
3. Write in plain English — no jargon
4. Include: what's wrong, why it matters, which file, how to fix, effort estimate

---

## Step 6 — Commit and PR

```bash
git add reports/ audit-framework/
git commit -m "audit: [module] production-readiness v[n] — [N] findings"
git push -u origin audit/[module]-v[n]-[date]
# Create PR as draft — do not merge, this is a reference branch
```

---

## Step 7 — After Remediation

After completing P0 items and before beta testing:

1. Re-read all CRITICAL and HIGH findings
2. Verify each fix is in place with a code review
3. Run `npm audit` and `pip-audit` again
4. Update `reports/remediation/P0-...md` with `[x]` for completed items
5. If significant new code was added, re-run relevant dimensions

---

## Dimension Selection Guide

| Use Case | Run Dimensions |
|---|---|
| Full production-readiness audit | 01–16 |
| Quick security review | 02, 03, 04, 11, 12 |
| Pre-App Store submission | 05, 14, 15, 16 + check PrivacyInfo.xcprivacy |
| New payment feature | 04, 07, 08 |
| After major refactor | 01, 07, 09, 10 |
| Compliance / privacy review | 12, 15, 16 |
| Performance sprint | 14 |

---

## Time Estimates Per Dimension

| Dimension | Time (experienced auditor) |
|---|---|
| 01 Feature completeness | 2–3 hours |
| 02 Authentication | 2–3 hours |
| 03 Encryption & secrets | 1–2 hours |
| 04 Input validation | 2–3 hours |
| 05 Android & iOS UX | 4–6 hours |
| 06 Real-time | 2–3 hours |
| 07 State machine | 2–3 hours |
| 08 Payments | 3–4 hours |
| 09 Test coverage | 2–3 hours |
| 10 Error handling | 2–3 hours |
| 11 Security headers | 1–2 hours |
| 12 Compliance | 3–4 hours |
| 13 Notifications/AI | 2–3 hours |
| 14 Performance | 2–3 hours |
| 15 Accessibility | 3–4 hours |
| 16 i18n / French | 2–3 hours |
| **Full audit total** | **~35–50 hours** |
