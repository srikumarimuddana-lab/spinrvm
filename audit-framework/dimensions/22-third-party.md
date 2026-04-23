# Dimension 22 — Third-Party / Supply-Chain Risk

**Question:** Do we know every vendor that touches our data, what they agreed to, who they share with, and how we learn when they're breached?

---

## Checklist

### Vendor inventory (a single source of truth)
- [ ] `docs/vendor-inventory.md` (or equivalent) lists every third-party service processing Spinr data
- [ ] Per vendor: name · purpose · data classes processed (PII/PCI/SENSITIVE) · region · DPA in force? · sub-processors list URL · breach-notification contact · contract expiry · review date
- [ ] Minimum set for Spinr: Supabase, Stripe, Stripe Connect, Firebase (Auth + FCM + App Check + Crashlytics), Google Maps, Twilio, Gemini (via google-generativeai), Railway, Render (fallback), Vercel, Sentry (if used), Cloudinary (if still used)

### Data Processing Agreement (DPA) / BAA equivalent
- [ ] DPA signed with every PII-processing vendor
- [ ] DPA specifies: purpose limitation, security controls, sub-processor list, breach notification window (Spinr requires ≤ 72 h from vendor discovery)
- [ ] DPA allows cross-border transfer only to declared regions — disclosed in privacy policy

### Data residency
- [ ] Supabase region: Canadian (ca-central-1 or equivalent) for PIPEDA comfort
- [ ] Stripe: data processed in-region where possible; cross-border is disclosed
- [ ] Gemini: US-hosted — explicit disclosure in privacy policy + user-facing opt-out if feasible
- [ ] Twilio: Canadian messaging entity for SK numbers

### Sub-processors
- [ ] Every vendor's public sub-processor list reviewed at least annually
- [ ] New sub-processor additions: 30-day notice from vendor → Spinr policy decision (accept / migrate)

### Breach notification chain
- [ ] Per vendor: primary + secondary breach-notification contact on file
- [ ] Incident runbook includes: upon vendor breach notice → Spinr internal sev triage → OPC 72 h clock starts on confirmed impact
- [ ] Customer-impact estimator ready to run given vendor-provided scope

### Secrets + key management
- [ ] Each vendor's credentials rotated ≥ annually; cadence documented
- [ ] No vendor credential shared across environments (prod ≠ staging ≠ dev)
- [ ] Revocation procedure per vendor (what to do if a key leaks) — tested ≥ annually

### Supply-chain / code dependencies
- [ ] SBOM generated per release (Python: `pip-audit`; JS: `npm audit`)
- [ ] License audit: no GPL/AGPL code in proprietary surfaces unless reviewed
- [ ] Renovate / Dependabot enabled with auto-PR for vuln patches
- [ ] Lockfiles committed (package-lock.json, yarn.lock, requirements.txt)
- [ ] No `*` / unpinned versions in production dependencies
- [ ] Docker base images pinned + scanned (Trivy / equivalent); non-root user; read-only FS where possible

### CI / CD supply chain
- [ ] CI secrets scoped per repo + per environment
- [ ] Branch protection + required reviewers on main
- [ ] Signed tags / releases
- [ ] Provenance metadata (SLSA ≥ L2) for built artifacts — stretch goal but worth tracking

### External-content risks
- [ ] AI (Gemini) response injection: output is sanitised before rendering in UI
- [ ] Email templates use templating engine with autoescape — no raw HTML concatenation
- [ ] Document uploads: magic-byte validation + AV scan on driver-uploaded files (already in `backend/documents.py`?)

---

## Common Findings

- **Vendor inventory doesn't exist** — DPA questions lead to "I'll ask legal".
- **Sub-processor list never reviewed** — new sub-processor could be outside Canada for weeks before anyone notices.
- **Secrets not rotated** — Twilio/Supabase keys issued at project start still in use.
- **Lockfiles absent or outdated** — supply-chain attack via tainted dep.
- **Gemini disclosure missing** — Google US processes support chat content; not in privacy policy.
- **Docker base image never scanned** — inherited CVEs unknown.

## How to Test

```bash
# Lockfile presence
ls backend/requirements*.txt rider-app/yarn.lock driver-app/yarn.lock \
   admin-dashboard/package-lock.json shared/package-lock.json 2>/dev/null

# Unpinned Python deps
grep -E '^\s*[a-zA-Z].*$' backend/requirements.txt | grep -v "==" | head

# JS vuln audit
cd rider-app && npm audit --json 2>/dev/null | head -50
cd driver-app && npm audit --json 2>/dev/null | head -50
cd admin-dashboard && npm audit --json 2>/dev/null | head -50

# Python vuln audit
cd backend && pip-audit 2>&1 | tail -20

# Docker base image pinning
grep "^FROM" Dockerfile backend/Dockerfile 2>/dev/null

# Vendor inventory existence
ls docs/vendor-inventory.md docs/third-party.md docs/vendors.md 2>/dev/null

# Document AV / magic-byte check
grep -n "magic_byte\|python-magic\|clamav\|filetype" backend/documents.py
```

## Regulatory tags
`PIPEDA` (sub-processor disclosure, cross-border, breach chain) · `PCI-DSS` (vendor scope for cardholder data) · `SOC2` (vendor management) · `CASL` (Twilio/email sender compliance) · `SK-TNC` (operator accountability for vendor failures)
