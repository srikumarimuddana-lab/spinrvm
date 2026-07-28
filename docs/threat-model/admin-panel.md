# Threat Model — Admin Panel

**Module:** `admin-dashboard/` (Next.js 16) + `backend/routes/admin/`
**Framework:** STRIDE · **Owner:** `backend` + `security` + `admin-dashboard`
**Version:** 1.0 · **Date:** 2026-04-24

---

## Why Admin Panels Are Higher Risk

- Blast radius is **org-wide** — one compromised admin = every rider/driver exposed
- Bulk operations are irreversible (mass suspend, mass refund)
- Admin JWT is **fully trusted** (per CLAUDE.md) — claims not re-verified per request
- Historical industry data: admin panels are the #1 source of breaches in SaaS

---

## Trust Boundaries

```
  [Admin user (browser)] <--HTTPS + MFA + IP allow-list-->  [admin-dashboard (Next.js)]
                                                                  |
                                                                  v
                                                         [Backend /admin/* routes]
                                                                  |
                                                                  v
                                                         [Supabase (service-role)]
```

**Trust levels:**
- **Z1 — Admin user**: high trust, but treated with defense-in-depth
- **Z2 — Next.js dashboard**: trusted rendering layer
- **Z3 — Backend admin routes**: service-role access
- **Z4 — Internal vendors** (email, support, analytics): per DPA

---

## Assets (admin-specific)

| Asset | Class | Why special |
|---|---|---|
| Break-glass super-admin credentials | C4 | Single-point-of-failure; needed for emergency |
| Admin MFA seeds | C4 | Loss = account recovery nightmare |
| Bulk-operation authorization tokens | C4 | Temporary, scoped |
| Admin session cookies | C3 | 30-min idle timeout |
| PII export artifacts | C3–C4 | Rider/driver data exports for compliance/DSAR |
| Audit-log (admin-actor rows) | C2 | Immutable per CLAUDE.md rule |

---

## STRIDE Analysis — Admin-Specific

### Spoofing

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AS-1 | Admin account phished (password + TOTP both stolen) | Hardware-key (WebAuthn) for super-admin tier | HIGH until WebAuthn mandatory |
| AS-2 | Break-glass account left enabled outside emergency | JIT (just-in-time) elevation with auto-expire | **OPEN** — DV-17-like item: no break-glass spec yet |
| AS-3 | Admin JWT stolen via XSS on dashboard | React + CSP; HttpOnly cookies for session | MEDIUM — CSP audit pending |
| AS-4 | Service-role key leaked via admin dashboard UI | Key never sent to browser | LOW |

### Tampering

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AT-1 | Admin modifies audit log to hide their action | Append-only table; admin role has no UPDATE/DELETE | MEDIUM — RLS verification pending |
| AT-2 | Bulk suspend all drivers by mistake (typo'd filter) | Dual-approval for bulk ops (> 10 records) | **OPEN** — not yet implemented |
| AT-3 | SQL-crafted filter returns unexpected rows | Parameterised queries; Pydantic schemas | MEDIUM |
| AT-4 | Cross-tenant data modification in corporate ops | RLS per corporation | MEDIUM — RLS audit pending |

### Repudiation

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AR-1 | Admin claims "I didn't click Suspend" | Audit log with actor_id, timestamp, before/after JSON | LOW |
| AR-2 | Admin action logged but not attributable to individual (shared account) | Individual named admin accounts mandatory | MEDIUM — policy enforcement pending |
| AR-3 | Log forgery via time-manipulation | Use server-side timestamp; never trust client | LOW |

### Information Disclosure

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AI-1 | Support-tier admin sees payment card PAN | No PAN stored (Stripe Elements); display last4 only | LOW |
| AI-2 | "View as rider" shows data the rider doesn't see | Must reflect rider's own view exactly | **VERIFY** during admin audit |
| AI-3 | Admin exports all users → offline PII leak | Approval workflow for exports > 1,000 rows | **OPEN** — not yet implemented. Scope includes `routes/admin/compliance.py`'s `gst-pst-remittance` and `insurance-period-audit` endpoints (up to `_ROW_LIMIT = 10000` rows each, no approval gate today) — see `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md` G2. Also scope includes `routes/admin/data_transfer_export.py`'s `/data-transfer/export` endpoint — the most literal match to this finding's own attack-tree wording (`AAT-1` below: `"Export all" endpoint`), returning unredacted, decrypted PII plus raw document file bytes for up to 100 entities/request; rate-limited (10/hour) but with no dual-approval gate — see `reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md` H2. Wire all three endpoints through AI-3's dual-approval mechanism when it ships, rather than building per-module one-offs. |
| AI-4 | Admin dashboard logs include rider PII | Same redactor rules as backend | MEDIUM until implemented |
| AI-5 | Admin panel indexed by search engines (open to internet) | IP allow-list + robots.txt + no-index headers | MEDIUM — verify during admin audit |

### Denial of Service

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AD-1 | Admin runs heavy query that locks prod DB | Query timeout + pool isolation | MEDIUM |
| AD-2 | Admin dashboard outage blocks incident response | Dashboard = convenience; backend /admin/* also accessible via CLI | LOW |

### Elevation of Privilege

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| AE-1 | Support-tier admin escalates to super-admin | Role-based guards on every endpoint (not just `is_admin`) | HIGH — verify per-role guards during admin audit |
| AE-2 | Admin JWT not re-read from DB (per CLAUDE.md, fully trusted) | Short expiry (12h); MFA for sensitive ops | MEDIUM |
| AE-3 | RBAC bypass via direct DB access | Service-role unreachable from browser | LOW |
| AE-4 | Session fixation | Rotating session cookie on login | LOW — verify |

---

## Attack Trees

### AAT-1: Mass data exfiltration via compromised admin

```
Goal: Exfiltrate PII of 10,000+ riders
├── 1. Compromise an admin account
│   ├── 1.1 Phish username/password → [AS-1 partial — MFA blocks]
│   ├── 1.2 Phish TOTP → [MEDIUM without hardware key]
│   └── 1.3 Session steal via XSS → [AS-3 CSP-dependent]
├── 2. Export data
│   ├── 2.1 Use "Export all" endpoint → [AI-3 OPEN — needs approval gate]
│   ├── 2.2 Paginate through normal UI → [MEDIUM — is there a page cap? verify]
│   └── 2.3 Abuse admin DB query endpoint → [not exposed, LOW]
└── 3. Exfiltrate
    └── 3.1 Email export, USB transfer, upload → [DLP not yet in place]
```

**Required mitigations** (P1 admin audit findings):
- [ ] Dual-approval on any export > 1,000 rows (AT-2 + AI-3)
- [ ] Hardware-key WebAuthn for super-admin (AS-1)
- [ ] DLP on admin workstations (out of scope for code audit, in scope for ops)

### AAT-2: Insider-threat wallet siphon

```
Goal: Credit attacker-controlled rider wallet with $10,000
├── 1. Admin with "finance" role (legitimate access)
│   ├── 1.1 Apply manual wallet adjustment → [legitimate action]
│   └── 1.2 Split into many small transactions to avoid threshold alert → [MEDIUM]
├── 2. Detection
│   ├── 2.1 Daily reconciliation cron → [D20 / docs/runbooks/stripe-reconciliation.md]
│   ├── 2.2 Bulk-adjustment dual approval → [OPEN]
│   └── 2.3 Anomaly detection on admin-initiated credits → [OPEN]
```

### AAT-3: Audit-log tampering

```
Goal: Remove admin's own entry from audit log
├── 1. Direct DB access → [service-role not exposed, LOW]
├── 2. Admin UI delete → [table should be append-only, verify]
└── 3. Backup manipulation → [PITR + off-site archival required]
```

---

## Residual Risk Register

| Threat | Risk score | Owner | Target sprint |
|---|---:|---|---|
| AS-1 (no WebAuthn) | 128 (critical × org × likely) | admin + security | P1 |
| AS-2 (break-glass undefined) | 96 | security + admin | P1 |
| AT-2 (bulk-op no dual-approval) | 96 | backend + admin | P1 |
| AI-3 (mass-export no gate) | 96 | backend + admin | P1 |
| AI-5 (internet-exposed admin panel) | 64 | admin + infra | P1 |
| AE-1 (role-guards incomplete) | 96 | backend | P1 |
| AT-1 (immutable audit log) | 48 | backend | P2 |
| AI-2 (View-as-rider fidelity) | 32 | backend + admin | P2 |

Note: admin items trend higher-scoring because `blast_weight = 2 (org)` applies
to nearly all threats.

---

## Review Cadence

- Re-run this threat model after admin-panel Phase A–E audit completes
- Re-run every 90 days per `audit-framework/CHANGELOG.md` rule
- Re-run after any new admin feature or route is added

**Next review due:** 2026-07-23 or after admin audit v1 — whichever comes first.
