# External Security Testing Program

**Purpose:** Complement the internal 23-dimension audit with black-box testing
by parties outside the Spinr engineering team. Internal audits can miss
runtime-only bugs, configuration drift, and assumption errors; external
testing catches them.

**Owner:** `security` + `devops` · **Cadence:** see per-activity below.

---

## Program Components

| # | Activity | Frequency | Audience | Output |
|---|---|---|---|---|
| 1 | Private bug bounty (invite-only) | Continuous after launch | Curated researchers | Private reports → issues |
| 2 | Coordinated disclosure (SECURITY.md) | Always on | Any researcher | Private advisory → fix → CVE (if public) |
| 3 | Third-party pen-test (full-scope) | Annual (min.) + pre-launch + after major refactor | Certified firm | Executive report + technical appendix |
| 4 | PCI-DSS ASV scan | Quarterly | Approved Scanning Vendor | Passing scan attestation |
| 5 | Red-team exercise (objectives-based) | Biennial | Spinr red team or vendor | After-action review |
| 6 | Tabletop exercise (DR/BCP) | Annual | Internal + vendor | Drill report (feeds D18) |

---

## 1 · Private Bug Bounty

**Platform:** HackerOne or Bugcrowd private program (decision: `security` + `legal`).

**Scope:**
- `api-spinr.spinr.ca`, `*.spinr.ca`
- `com.spinr.user` and `com.spinr.driver` published app versions
- Admin panel — invite-only tier

**Out of scope:** See `SECURITY.md` out-of-scope section.

**Reward tiers** (if paid program is authorised by `legal` + `finance`):

| Severity | Reward USD |
|---|---|
| CRITICAL (RCE, full account takeover, mass data exfiltration) | $2,000–$10,000 |
| HIGH (auth bypass on one user, sensitive data exposure, privilege escalation) | $500–$2,000 |
| MEDIUM | $100–$500 |
| LOW | Swag + credit |

**Pre-launch:** Invite 10–20 researchers for a 30-day closed beta with all
above scope 2 weeks before public launch.

---

## 2 · Coordinated Disclosure (SECURITY.md)

Already in place at repo root. Process:

1. Researcher emails `security@spinr.ca`.
2. `security` triages within 3 business days.
3. Severity assigned using the OPEN-ITEMS-TRACKER risk formula.
4. Remediation target:
   - CRITICAL: 7 days
   - HIGH: 30 days
   - MEDIUM: 60 days
   - LOW: 90 days or next audit cycle
5. Disclosure timeline: default 90 days; coordinated extensions negotiable.
6. CVE assigned if public disclosure warranted and vulnerability affects an
   open-source component Spinr owns.

---

## 3 · Third-Party Pen-Test

**Cadence:** Annual minimum · **Additional triggers:**
- Pre-public-launch
- After any refactor touching auth, payments, or dispatch (> 500 LOC)
- After any CRITICAL finding from internal audit or bug bounty
- After infrastructure migration (e.g. Railway → another host)

**Firm selection criteria:**
- CREST or OSCP-certified testers
- Has tested ride-share / mobility products before (ideal) or
  payments/PII mobile products (acceptable)
- Canadian or signs Canadian DPA (for cross-border test data handling)
- References from two comparable-sized platforms

**Scope template** (adjust per engagement):

```
Targets:
  - Backend API: api-spinr.spinr.ca (prod or isolated staging-mirror)
  - Rider app: com.spinr.user vX.Y.Z
  - Driver app: com.spinr.driver vX.Y.Z
  - Admin panel: admin.spinr.ca (invite tester IPs only)

In scope:
  - Authentication (OTP, Firebase, refresh tokens, admin MFA)
  - Authorization / IDOR across rider/driver/admin roles
  - Payments (Stripe Payment Intents, Connect payouts, webhooks)
  - WebSocket dispatch (ride offer hijack, impersonation)
  - Ride state machine (invalid transitions, race conditions)
  - Corporate wallet / fare-split flows
  - Admin bulk operations

Out of scope:
  - Social engineering of Spinr staff
  - Physical attacks
  - DoS beyond proof-of-concept
  - Third-party services (report upstream)

Deliverables:
  - Executive summary (5 pages)
  - Technical findings report with CVSS + remediation guidance
  - Retest of HIGH/CRITICAL findings after remediation (included)
  - Debrief call with Spinr engineering
```

**Budget band:** CAD $30K–$80K for a 2–4 week engagement (indicative).

**Artifact retention:** Report stored in `reports/pentest/YYYY-MM-DD-firm-vN.pdf`,
retained 5 years.

---

## 4 · PCI-DSS ASV Scan

**Applicability:** Spinr is in SAQ-A scope (Stripe Elements handles card data).
ASV scanning is still required for SAQ-A's inherited network scope — confirm
with QSA.

**Vendor:** Qualys, Trustwave, or equivalent ASV.

**Target:** `api-spinr.spinr.ca` (prod public endpoints).

**Cadence:** Quarterly (required by PCI-DSS v4).

**Pass criteria:** No HIGH or CRITICAL findings. Retests until passing.

**Artifact retention:** Scan reports in `reports/compliance/pci/YYYY-Q.pdf`,
retained 3 years.

---

## 5 · Red-Team Exercise

**Cadence:** Biennial (or after major architecture change).

**Objectives-based scenarios** (pick 2–3 per exercise):

1. "Steal 100 riders' phone numbers and home addresses."
2. "Drain $1,000 from the corporate wallet master account."
3. "Hijack an active ride to redirect a rider to an attacker-chosen address."
4. "Publish a malicious update to the rider app via compromised EAS credentials."
5. "Gain persistent admin access via a support-tier account."
6. "Trigger 10,000 fake SOS events without detection."

**Rules of engagement:**
- Conducted in staging (mirror of prod) with canary data if available
- All engineering team notified *after* exercise
- Must not impact real riders/drivers
- Discovered vulns enter the normal remediation pipeline with risk_score ≥ 64

---

## 6 · Tabletop Exercises

**Cadence:** Annual · **Owner:** `devops` + `security`.

Scenarios (rotate annually):

1. **Supabase outage 6 h** — does the rider app degrade gracefully? Is PITR
   restore rehearsed?
2. **Stripe webhook delivery failure 24 h** — do wallet top-ups and fare
   settlements reconcile? Is the queue persistent?
3. **Compromised EAS build credentials** — how do we revoke, rotate, and push
   a patched release to stores within 72 h?
4. **Leaked Supabase service-role key** — rotate, audit access, notify OPC if
   PII exposure is proven.
5. **Mass cancellation at 5pm Friday** — is dispatch + fare-cancellation
   system robust against a coordinated user event?
6. **Driver impersonation detected via rider SOS** — law-enforcement handoff
   procedure, evidence preservation, PR response.

**Artifact:** After-action review per scenario in `reports/tabletop/YYYY-Q-scenario.md`,
including a list of improvements filed as OPEN-ITEMS-TRACKER entries.

---

## Integration with Internal Audit

External findings feed back into the internal framework:

- Each external finding → a row in `OPEN-ITEMS-TRACKER.md`
- If the finding reveals a pattern missed by internal audits → add a dimension
  checklist item or a Semgrep rule (closes the loop)
- If three consecutive bug bounty reports hit the same dimension → that
  dimension is due for a focused re-audit

---

## Pre-Launch Gate

Before the rider/driver apps are published to public store listings:

- [ ] Internal audits complete for all 4 modules (rider, driver, backend, admin)
- [ ] At least one third-party pen-test completed; no open HIGH/CRITICAL
- [ ] PCI-DSS ASV scan passing
- [ ] Private bug bounty has run ≥ 30 days with no unremediated HIGH findings
- [ ] At least one tabletop exercise completed in the past 12 months
- [ ] All items in OPEN-ITEMS-TRACKER closing checklist satisfied

---

Last updated: 2026-04-24
