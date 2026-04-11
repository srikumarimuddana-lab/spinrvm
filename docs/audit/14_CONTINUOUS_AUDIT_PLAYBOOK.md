# Spinr — Continuous Security Audit & Sprint Playbook

**Document Date:** 2026-04-09
**Purpose:** Make the audit → plan → sprint → report cycle a permanent, automated part of the Spinr development process.

---

## The Problem This Solves

Security and quality auditing is not a one-time event. Every new feature, dependency update, or configuration change can introduce new vulnerabilities. Without a structured, recurring process:

- New issues accumulate between ad-hoc reviews
- Sprint teams address visible bugs but miss systemic patterns
- No one notices when a security fix is accidentally reverted
- Compliance posture degrades silently over time

The goal is to make security auditing as automatic as running tests — something that happens on every commit, every week, and before every release, without requiring anyone to remember to do it.

---

## The Cycle: Four Layers of Continuous Auditing

```
Layer 1: Commit-Time (seconds)
    └── Pre-commit hooks (secrets, PII, branch, money arithmetic)
    └── TruffleHog on staged files

Layer 2: PR-Time (minutes)
    └── Full CI pipeline (TruffleHog history, Trivy CVE scan, tests)
    └── CODEOWNERS review enforcement
    └── PR template security checklist

Layer 3: Weekly (automated)
    └── Dependabot PRs for all 6 ecosystems
    └── Claude Code scheduled weekly mini-audit
    └── New CVEs in production dependencies flagged

Layer 4: Sprint (every 2 weeks)
    └── Structured issue triage
    └── Sprint planning against issue backlog
    └── Sprint completion report
    └── Backlog reprioritization
```

---

## Layer 1: Commit-Time (Already Implemented)

### Pre-commit Hook
The 5-check pre-commit suite installed in Sprint 1 runs automatically on every `git commit`:

1. **Secrets scan** — regex patterns for credentials, API keys, tokens
2. **Forbidden files** — blocks `.env`, `google-services.json`, `*.pem`, etc.
3. **PII in logs** — rejects unmasked phone numbers in log statements
4. **Branch check** — prevents direct commits to `main` or `master`
5. **Money arithmetic** — prevents `float()` on fare/price/total values

**How to install for a new contributor:**
```bash
# From the repo root
cp .hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

**Recommendation:** Add `scripts/install-hooks.sh` and call it from `package.json` `postinstall`:
```json
"scripts": {
  "postinstall": "bash scripts/install-hooks.sh"
}
```
This makes hook installation automatic for anyone who runs `npm install`.

---

## Layer 2: PR-Time (Already Implemented)

The CI pipeline (`.github/workflows/ci.yml`) runs on every PR:
- **TruffleHog** — scans full commit history for secrets
- **Trivy** — scans Docker image for CVEs (fails on CRITICAL/HIGH)
- **Backend tests** — runs Python test suite
- **CODEOWNERS** — security-critical files require designated reviewer

**Enhancement recommendation:** Add a dedicated security job that runs on every PR:
```yaml
security-audit:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Run Bandit (Python SAST)
      run: pip install bandit && bandit -r backend/ -ll
    - name: Run npm audit
      run: |
        cd rider-app && npm audit --audit-level=high
        cd ../driver-app && npm audit --audit-level=high
        cd ../admin-dashboard && npm audit --audit-level=high
```

---

## Layer 3: Weekly Automation

### 3a — Dependabot (Already Configured)
`.github/dependabot.yml` generates weekly PRs for:
- Backend Python packages (`pip`)
- `rider-app`, `driver-app`, `admin-dashboard`, `frontend` npm packages
- GitHub Actions version updates

**Action required:** Review and merge Dependabot PRs every Monday.

### 3b — Scheduled Weekly Mini-Audit (Set Up with Claude Code)

Claude Code's scheduled tasks feature can run a lightweight security scan every week. Set up as follows:

**In Claude Code:**
```
/schedule create --cron "0 9 * * 1" weekly-security-scan
```

Or use the `mcp__scheduled-tasks__create_scheduled_task` tool with the following prompt:

```
Weekly Spinr security scan:
1. Check for new CVEs in backend/requirements.txt using `pip-audit`
2. Check for secrets in the last week's commits using TruffleHog
3. Verify pre-commit hooks are still installed
4. Review any new Dependabot PRs
5. Check if any P0 or P1 issues from docs/audit/01_AUDIT_GAPS_REPORT.md are still open
6. Generate a brief status report
```

**Recommended schedule:** Every Monday at 9:00 AM local time.

### 3c — Upstream Sync (Already Configured)
The `spinr-upstream-sync` scheduled task runs daily at 8:00 AM:
```
git fetch upstream
git rebase main
git push origin main
```
This keeps the working fork in sync with the upstream owner's development.

---

## Layer 4: Sprint Cadence

### Recommended Sprint Rhythm
- **Sprint length:** 2 weeks
- **Sprint capacity:** 3–4 branches per sprint (one concern per branch)
- **Sprint trigger:** When 3+ P1 issues accumulate in the backlog, start a new sprint

### Sprint Process (Step by Step)

#### Week 1, Day 1 — Sprint Planning
1. Review `docs/audit/01_AUDIT_GAPS_REPORT.md` — which issues are still open?
2. Triage any new issues discovered since last sprint
3. Select 3–4 issues that:
   - Are independent (no branch conflicts)
   - Address a consistent security theme or feature area
   - Can be completed in one week
4. Write or update `docs/audit/02_SPRINT_PLAN.md` with the sprint scope

#### Week 1, Days 2–5 — Implementation
1. `git checkout main && git pull origin main`
2. `git checkout -b sprint{N}/{concern}` for each branch
3. Implement, commit (pre-commit hooks auto-run), push
4. Open PR with the standard template

#### Week 2, Day 1 — Review & Merge
1. Code review of all sprint PRs
2. Address review feedback
3. Merge approved PRs

#### Week 2, Day 5 — Sprint Completion
1. Write `docs/audit/0X_SPRINT_{N}_COMPLETION.md`
2. Update issue status in `docs/audit/01_AUDIT_GAPS_REPORT.md`
3. Update `docs/audit/06_PROJECT_SUMMARY.md`
4. Commit and push the docs branch (this branch)

---

## Issue Tracking Integration

### Option A: GitHub Issues (Recommended)
Convert each issue from `01_AUDIT_GAPS_REPORT.md` into a GitHub Issue:
1. Create labels: `security-p0`, `security-p1`, `security-p2`, `infra`, `mobile`, `feature`, `compliance`
2. Create a milestone for each sprint
3. Assign issues to milestones during sprint planning

**Script to bulk-create issues:**
```python
# Run once to seed GitHub Issues from the gap analysis
import json, urllib.request
ISSUES = [
    {"title": "SEC-001: Hardcoded JWT secret", "labels": ["security-p0"]},
    {"title": "SEC-002: Hardcoded admin credentials", "labels": ["security-p0"]},
    # ... (one per audit finding)
]
```

### Option B: `docs/ISSUES.md` (Lightweight)
Track issue status directly in the markdown file. Add a `Status` column:

```markdown
| ID | Title | Severity | Status | Sprint |
|----|-------|----------|--------|--------|
| SEC-001 | Hardcoded JWT secret | P0 | ✅ Closed | Sprint 2 |
| SEC-009 | Race condition | P1 | ✅ Closed | Sprint 3 |
| SEC-010 | Firebase credentials | P1 | 🟡 Pending owner | Sprint 4 |
```

---

## How to Trigger a New Audit

Run a full audit when:
- A major new feature is added
- A new team member joins and makes significant changes
- A security incident occurs
- A dependency with known CVEs is updated
- 3+ months have passed since the last full audit

**Full audit command sequence:**
```bash
# 1. Scan for secrets in the full commit history
trufflehog git file://. --only-verified

# 2. Scan Python dependencies for CVEs
pip install pip-audit && pip-audit -r backend/requirements.txt

# 3. Scan npm dependencies
cd rider-app && npm audit --audit-level=moderate
cd ../driver-app && npm audit --audit-level=moderate
cd ../admin-dashboard && npm audit --audit-level=moderate

# 4. Run SAST on Python
bandit -r backend/ -ll -f txt

# 5. Build and scan Docker image
docker build -t spinr-audit ./backend
trivy image spinr-audit --severity CRITICAL,HIGH

# 6. Check for outdated packages
pip list --outdated
cd rider-app && npm outdated
```

Alternatively, ask Claude Code:
```
Run a full security audit of the spinr backend and mobile apps.
Check for: new secrets in git history, Python CVEs, npm CVEs,
SAST findings (Bandit), OWASP Top 10 patterns.
Generate a new AUDIT_GAPS_REPORT with today's date.
```

---

## Document Maintenance

The 7 documents in `docs/audit/` are living documents:

| Document | Update Trigger | Owner |
|----------|---------------|-------|
| `01_AUDIT_GAPS_REPORT.md` | After each audit; after each sprint (mark issues closed) | Security lead |
| `02_SPRINT_PLAN.md` | Before each sprint; add new sprint backlog items | Tech lead |
| `03_SPRINT_1_COMPLETION.md` | One-time, after Sprint 1 | Sprint team |
| `04_SPRINT_2_COMPLETION.md` | One-time, after Sprint 2 | Sprint team |
| `05_SPRINT_3_COMPLETION.md` | One-time, after Sprint 3 | Sprint team |
| `06_PROJECT_SUMMARY.md` | After each sprint | Security lead |
| `07_CONTINUOUS_AUDIT_PLAYBOOK.md` | When process changes | Tech lead |

---

## Tooling Recommendations for Production Readiness

### Already In Place
| Tool | Purpose | Status |
|------|---------|--------|
| TruffleHog | Secret detection in CI | ✅ Active |
| Trivy | Container CVE scanning | ✅ Active |
| Dependabot | Dependency update PRs | ✅ Active |
| Pre-commit hooks | Local developer guardrails | ✅ Active |
| Loguru structured logging | Log aggregation-ready output | ✅ Active |
| audit_logger.py | Security event structured logs | ✅ Active |

### Recommended for Sprint 4
| Tool | Purpose | Effort |
|------|---------|--------|
| **Sentry** | Error tracking + alerting | Low (SDK install + DSN config) |
| **Redis** | Rate limiting across instances | Medium (Docker Compose service) |
| **Bandit** | Python SAST in CI | Low (pip install + CI step) |
| **pip-audit** | Python CVE scanning | Low (pip install + CI step) |

### Recommended for Sprint 5
| Tool | Purpose | Effort |
|------|---------|--------|
| **Detox / Maestro** | Mobile E2E testing | High (test infrastructure) |
| **k6** | Load testing (race condition verification) | Medium |
| **OPA / Conftest** | Policy-as-code for infra configs | Medium |
| **Snyk** | Advanced supply chain security | Low (GitHub app) |

---

## Escalation Matrix

| Finding | Response Time | Owner |
|---------|--------------|-------|
| Active credential exposed in git | 1 hour | Rotate immediately; notify repo owner |
| P0 security issue discovered | 24 hours | Hotfix branch; emergency PR |
| P1 security issue discovered | Next sprint | Add to sprint backlog |
| New CVE in production dependency | 1 week | Dependabot PR or manual update |
| New P2/P3 issue in audit | Next planning session | Add to backlog |

---

## Summary: What You Need to Do

To make this process self-sustaining, complete these actions in order:

| Action | When | Effort |
|--------|------|--------|
| 1. Merge all open Sprint 1–3 PRs | Now | 30 min review |
| 2. Create GitHub Issues from `01_AUDIT_GAPS_REPORT.md` | This week | 1 hour |
| 3. Add `scripts/install-hooks.sh` called by `postinstall` | Sprint 4 | 30 min |
| 4. Add Bandit + pip-audit to CI pipeline | Sprint 4 | 1 hour |
| 5. Set up weekly Claude scheduled task for mini-audit | This week | 15 min |
| 6. Owner rotates Firebase credentials (SEC-010) | Urgent | Owner action |
| 7. Add Sentry to backend + mobile apps | Sprint 4 | 2 hours |
| 8. Plan Sprint 4 using this playbook | After Sprint 3 merge | 1 hour |

Following this playbook, the Spinr platform will move from reactive (fix issues when they're found) to proactive (issues are caught before they become incidents) security management.

---

*Playbook version 1.0 — 2026-04-09. Review and update quarterly or after any major security event.*
