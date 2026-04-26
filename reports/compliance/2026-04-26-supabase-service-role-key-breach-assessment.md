# Breach Assessment — Supabase Service-Role Key Leak in `backend/.env.example`

**Date opened:** 2026-04-26
**Branch:** `claude/audit-continuation-batch-2`
**Source finding:** Rider audit `[03-1 CRITICAL]` (`reports/audits/2026-04-19-rider-app-v1.txt:366`)
**Remediation tracker:** `rider-P0-critical-fix-now.md` → R-P0-8
**Status:** ⚠ **Code-side remediated; human actions outstanding** (Supabase rotation, history scrub, breach-notification decision)

---

## 1. Incident Summary

A real, functional Supabase **service-role JWT** was committed to `backend/.env.example:3` together with the corresponding `SUPABASE_URL` (project ref `[redacted-project]`). The `role: "service_role"` claim in the JWT bypasses every Row-Level Security policy. Anyone reading the file could have performed unrestricted reads/writes on:

- `users` (rider + driver PII: phone, email, name, address)
- `rides` (trip history, GPS pickup/dropoff)
- `drivers` (licence numbers, insurance documents, banking)
- `payments` + `wallet_transactions` (Stripe customer references, balances)
- `corporate_*` (corporate billing data)

**JWT expiry per audit decode:** `exp: 2086412564` → ~year 2036. Without rotation, the leaked key remains valid for ten more years.

---

## 2. Code-Side Status (✅ done in this commit)

| Action | File(s) | Status |
|---|---|---|
| `.env.example` placeholders only | `backend/.env.example` | ✅ already sanitized (committed `7a793ca`, 2026-04-20) |
| Remove project hostname from dev DNS test | `backend/test_dns.py` | ✅ now reads `SUPABASE_URL` from env; no hardcoded host |
| Redact project ref from audit prose | `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`, `reports/audits/2026-04-19-rider-app-v1.txt`, `driver-app/final_audit_v4.txt`, `reports/remediation/rider-P0-critical-fix-now.md` | ✅ project ref replaced with `[redacted-project]` |
| HEAD scan for project ref | `git grep` post-redaction | ✅ zero matches |
| HEAD scan for full JWT payload | `git grep eyJhbGci…` | ✅ only mock JWTs in `tests/` (not the leaked key) |

---

## 3. Human Actions Outstanding (⚠ block closure)

These cannot be performed by the audit agent. Owner: **`ops`** + **`security`**.

### 3.1 Rotate the Supabase service-role key
- [ ] Log into Supabase Dashboard for the affected project (the redacted ref)
- [ ] **Settings → API → Roll service_role key**
- [ ] Update the key in:
  - Railway production env (`SUPABASE_SERVICE_ROLE_KEY`)
  - Render fallback env
  - Any CI secret that reads it
  - Local dev `.env` files for the team (announce in `#engineering`)
- [ ] Confirm the old key is invalidated by attempting an API call with it (expected: 401)
- [ ] Record rotation timestamp + actor in this file (Section 7)

### 3.2 Scrub git history (if applicable)
The current branch's history begins with the file already-sanitized (commit `7a793ca`). However the **parent** branches and any external clones may retain the leaked value.
- [ ] Run `trufflehog git file://. --since-commit <root>` against `main` and any long-lived branches
- [ ] If hits found: coordinate `git-filter-repo` + force-push window with the team; require all developers to re-clone
- [ ] Notify GitHub support to invalidate cached views if the repo was ever public

### 3.3 Determine breach-notification obligation (PIPEDA)
PIPEDA requires Privacy Commissioner notification within 72 h if the breach poses **"real risk of significant harm"**.
- [ ] Determine repo visibility timeline: was the repo ever public? was it shared with external contractors?
- [ ] Pull Supabase audit logs for the affected key window: any anomalous traffic from unfamiliar IPs?
- [ ] If neither (private repo, no anomalous traffic): document "no evidence of exploitation" and close internally
- [ ] If either: legal review → Privacy Commissioner notification + affected-user notification per PIPEDA s.10.1

### 3.4 Update internal trackers
- [ ] Mark `rider-P0-critical-fix-now.md:276` `R-P0-8` as fully closed once all above items are checked
- [ ] Add entry to `OPEN-ITEMS-TRACKER.md` cross-references

---

## 4. PIPEDA 72-h Clock

| Milestone | Target | Status |
|---|---|---|
| Suspected exposure detected (audit ran) | T+0 — 2026-04-19 | ✅ logged |
| Code-side remediation | T+ ≤ 24 h | ✅ 2026-04-26 (this commit) |
| Key rotation in Supabase Dashboard | T+ ≤ 24 h from detection | ⚠ **outstanding — see 3.1** |
| Breach assessment outcome | T+ ≤ 72 h | ⚠ **outstanding — see 3.3** |
| Privacy Commissioner notification (if required) | T+ ≤ 72 h | conditional on 3.3 |
| Affected-user notification (if required) | T+ ≤ 72 h | conditional on 3.3 |

The 72-h clock starts from **detection of breach** (2026-04-19 audit). At time of this assessment (2026-04-26), the clock has technically expired in calendar terms; the team must document why notification was/was not made on the original schedule.

---

## 5. Risk Assessment

| Vector | Likelihood | Impact | Combined |
|---|---|---|---|
| External attacker scraped public GitHub | Low (private repo if so) | Catastrophic (full DB) | Conditional HIGH |
| Departed contractor retained clone | Medium | Catastrophic | HIGH |
| CI logs leaked the value | Low (placeholder commits only) | High | LOW-MED |
| Internal employee mis-handled the key | Low | High | LOW-MED |

If repo was ever public: assume compromised, treat as confirmed breach, full notification.
If repo was always private + no anomalous Supabase traffic: rotate as precaution, document, close.

---

## 6. Lessons / Process Improvements

- [ ] Add `trufflehog --no-verification` to pre-commit hook (currently in `tests/hooks/test_pre_commit.sh` — verify it's wired into the actual hook)
- [ ] CI gate: fail the pipeline on any `eyJhbGci…` pattern in `.env*` files (allowlist mock JWTs in `tests/`)
- [ ] Document the breach runbook at `docs/runbooks/data-breach.md` (CLAUDE.md references it but it doesn't exist yet)
- [ ] Codify "any `.env.example` change requires security review" in `CODEOWNERS`

---

## 7. Action Log

_(Append entries below as actions complete. Format: `YYYY-MM-DD HH:MM TZ — [actor] — action — outcome`)_

- 2026-04-26 — audit-agent — code-side redaction across 5 files committed — done
- 2026-04-26 — audit-agent — breach-assessment stub opened — done
- TBD — ops — Supabase service-role key rotation — pending
- TBD — ops — git-history scrub assessment — pending
- TBD — security/legal — PIPEDA notification decision — pending
