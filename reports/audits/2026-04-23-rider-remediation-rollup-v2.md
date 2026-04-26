# Rider App — Remediation Rollup v2

**Date:** 2026-04-24
**Branch:** `claude/review-pending-audits-Pu1aP`
**Sources combined:**
- Phase E findings (2026-04-24): `reports/audits/2026-04-23-rider-app-v1-phase-e.txt` (60 findings across D17–D22)
- Pre-existing remediation plans: `reports/remediation/rider-P{0..4}-*.md` (92 items)
- Open-items tracker: `reports/audits/OPEN-ITEMS-TRACKER.md` (DV-* + RT-* cross-references)
- Threat model: `docs/threat-model/rider-app.md`

**Purpose:** A single sprint-plannable document consolidating every
actionable finding for the rider app surface (rider-app + shared backend
routes) with deduplication, risk scores, and sprint assignments.

---

## A · Summary of Open Work

| Source | Items | Notes |
|---|---:|---|
| Phase E actionable findings | 39 | D17–D22 (excludes PASS) |
| Pre-existing rider-P0 sprint | 8 | Not yet HEAD-verified |
| Pre-existing rider-P1 sprint | 33 | Not yet HEAD-verified |
| Pre-existing rider-P2 sprint | 33 | Not yet HEAD-verified |
| Pre-existing rider-P3 sprint | 8 | Not yet HEAD-verified |
| Pre-existing rider-P4 sprint | 10 | Not yet HEAD-verified |
| **Total unique items (after dedup)** | **~120** | See dedup table below |

Phase E dedup reduces naive total (39 + 92 = 131) by ~11 items that are
clearly the same issue surfaced from two different framework entry points
(e.g., 18-1 and 20-7 both describe wallet foreground refresh).

---

## B · Phase E Findings by Severity

| Dim | Focus | CRIT | HIGH | MED | LOW | REC | PASS | Total |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 17 | Observability | 0 | 3 | 3 | 1 | 1 | 3 | 11 |
| 18 | DR / BCP | 0 | 1 | 1 | 0 | 2 | 7 | 11 |
| 19 | Fraud | 1 | 5 | 3 | 0 | 0 | 0 | 9 |
| 20 | Financial reconciliation | 4 | 4 | 2 | 0 | 0 | 0 | 10 |
| 21 | STRIDE | 1 | 3 | 6 | 0 | 0 | 0 | 10 |
| 22 | Third-party | 1 | 4 | 4 | 1 | 0 | 0 | 10 |
| **Total** | — | **7** | **20** | **19** | **2** | **3** | **10** | **61** |

(Slight discrepancy vs 60 in Phase E summary — one item counted in two
dims re-aggregates here. The dedup table below is authoritative.)

---

## C · Top P0 — Launch Blockers (risk_score ≥ 64)

These are the items already formalized in
`reports/remediation/rider-phase-e-P0-issues.md` (20 h total):

| ID | Title | Risk | Owner | Effort |
|---|---|---:|---|---:|
| **19-1** | Rating field-name mismatch — drivers silently unrated | 64 | backend | 1 h |
| **20-1** | Wallet UPDATE bypasses RPC + float + `$set` wrapper | 128 | backend | 6 h |
| **20-2** | Daily Stripe↔DB↔wallet reconciliation cron missing | 128 | backend | 6 h |
| **20-3** | `financial_events` append-only ledger table missing | 96 | backend+data | 4 h |
| **21-2** | Rider addresses leak to driver after ride completion | 128 | backend | 2 h |
| **22-2** | Supabase Canadian region attestation not filed | 128 | legal+infra | 1 h |

See `rider-phase-e-P0-issues.md` for file/line, before/after code, and
regression tests.

---

## D · Cross-Audit Duplicates

Phase E findings that duplicate already-tracked issues — the remediation
work happens once, the dup is here for traceability.

| Phase E | Duplicates | Source file |
|---|---|---|
| 18-1, 20-7 | Each other (same wallet foreground refresh bug) | 20-7 marks dup |
| 19-6, 21-1, 22-5 | DV-10 | `OPEN-ITEMS-TRACKER.md § B2` |
| 20-1 (partial) | DV-2 pattern family (`$set` wrapper) | `OPEN-ITEMS-TRACKER.md § B1` |
| 20-4 | DV-4 Stripe idempotency key collision | `OPEN-ITEMS-TRACKER.md § B1` |
| 20-5 | P4-7 PARTIAL T4A generator | `OPEN-ITEMS-TRACKER.md § A` |
| 21-3 | DV-1 dispatch suspended filter | `OPEN-ITEMS-TRACKER.md § B1` |
| 21-4 | DV-3 ride state string mismatch | `OPEN-ITEMS-TRACKER.md § B1` |
| 21-6 | DV-6 rate-limiter in-memory fallback | `OPEN-ITEMS-TRACKER.md § B1` |
| 22-1 | DV-16 Gemini privacy policy disclosure | `OPEN-ITEMS-TRACKER.md § B5` |
| 22-2 (partial) | DPA register open-items row | `docs/dpa-register.md` |

**Implication:** Closing the 7 DV-/P4-7 items above also closes 10 Phase E
findings. The dedup is transitive — DV-10 (one fix) silently closes
19-6, 21-1, and 22-5.

---

## E · Sprint Assignment (risk-score threshold)

Per `OPEN-ITEMS-TRACKER.md` formula:
- ≥ 64 → P0 · 32–63 → P1 · 16–31 → P2 · 8–15 → P3 · < 8 → P4

### P0 (pre-launch blocker — ~20 h)
See section C. File: `reports/remediation/rider-phase-e-P0-issues.md`.

### P1 (this sprint — ~35 h)
| ID | Title | Effort |
|---|---|---:|
| 17-1 | Sentry send_default_pii + no scrubbing filter | 2 h |
| 17-3 | Backend logs not structured JSON | 4 h |
| 17-4 | `/health` doesn't check Redis | 1.5 h |
| 17-5 | Background loops have no per-cycle heartbeat | 3 h |
| 17-9 | Sentry before_send filter missing (extension of 17-1) | 3 h |
| 18-1 / 20-7 | Wallet not refreshed on app foreground | 1.5 h |
| 18-2 | Payment idempotency header not set on retry | 3 h |
| 19-2 | Rating endpoint not idempotent | 2 h |
| 19-3 | SOS endpoint has no rate limit / false-SOS flagging | 4 h |
| 19-4 | Promo stacking: no PII/payment-method freshness check | 3 h |
| 19-5 | Fare-split total_fare not validated vs ride fare | 2 h |
| 19-6 / 21-1 / 22-5 | Firebase audience check null-tolerant (= DV-10) | 4 h |
| 19-7 | Tip cumulative on retry — double-charge risk | 2 h |
| 20-6 | GST/PST not persisted as columns on `rides` | 3 h |
| 20-8 | Fare-split non-atomic wallet debit + status update | 3 h |
| 21-7 | TLS certificate pinning not implemented in rider app | 8 h |
| 21-10 | /auth/refresh rate limit too generous (per-user throttle) | 3 h |
| 22-3 | Twilio DPA not filed locally | 1 h |
| 22-4 | Upstash DPA + region unverified | 1.5 h |
| 22-6 | FCM token not revoked on logout | 2 h |
| 22-7 | Google Maps key bundle restriction unverified | 0.5 h |

### P2 (before launch — ~22 h)
| ID | Title | Effort |
|---|---|---:|
| 17-8 | WS onerror uses console.log | 1 h |
| 18-3 / 18-9 | Redis sentinel / PITR tier verification | 10 h |
| 19-8 | Tip zero-path safety gap | 1 h |
| 19-9 | Failed promo attempts not logged | 2 h |
| 20-4 (= DV-4) | Stripe idempotency UUID | 4 h |
| 20-9 | Stripe webhook replay path not implemented | 4 h |
| 20-10 | CRA monthly-remittance export missing | 4 h |
| 21-5 | GPS plausibility check missing (fake-trip detection) | 6 h |
| 21-8 | PII in AsyncStorage; no screenshot blur | 12 h |
| 22-1 (= DV-16) | Gemini sub-processor disclosure | 2 h |
| 22-8 | Email / on-call / status-page vendors TBD | 3 h |
| 22-9 | Expo SDK 54 EOL tracking | 0.5 h |

### P3 (hardening — ~4 h)
| ID | Title | Effort |
|---|---|---:|
| 17-6 | Crash reports lack ride_id context | 1 h |
| 17-9 | Crashlytics SLO dashboard link not documented | 0.5 h |
| 21-9 | Dev-mode OTP bypass hardening (compile-out) | 2 h |

### P4 (backlog — ~6 h)
| ID | Title | Effort |
|---|---|---:|
| 17-7 | `audit_logger.py` helper for structured events | 5 h |
| 22-10 | Railway DPA verification | 1 h |

---

## F · Pre-Existing Rider Sprint Files — Verification Status

These 92 items live in `reports/remediation/rider-P{0..4}-*.md` and have
**not yet been HEAD-verified** (unlike driver which is 89% verified).

| Sprint file | Items | Verification status |
|---|---:|---|
| `rider-P0-critical-fix-now.md` | 8 | ⏳ pending (verification prompt ready at `2026-04-23-rider-app-remediation-verification-prompt.md`) |
| `rider-P1-before-beta.md` | 33 | ⏳ pending |
| `rider-P2-before-launch.md` | 33 | ⏳ pending |
| `rider-P3-hardening.md` | 8 | ⏳ pending |
| `rider-P4-future-features.md` | 10 | ⏳ pending |

**Recommendation:** run the verification prompt once the six P0 issues in
section C are committed, so verification sees the most up-to-date HEAD.

---

## G · Owners Summary (after dedup)

| Owner | P0 | P1 | P2 | P3 | P4 | Total effort |
|---|---:|---:|---:|---:|---:|---:|
| backend | 4 | 13 | 6 | 2 | 1 | ~55 h |
| rider-app | 0 | 4 | 2 | 1 | 0 | ~22 h |
| backend+data | 1 | 0 | 0 | 0 | 0 | 4 h |
| backend+devops | 0 | 1 | 0 | 0 | 0 | 2 h |
| legal + infra | 1 | 3 | 1 | 0 | 1 | ~5 h |
| product + legal | 0 | 0 | 1 | 0 | 0 | 3 h |
| mobile + devops | 0 | 0 | 1 | 0 | 0 | 0.5 h |

**Largest load: backend (~55 h)** — consistent with driver Phase D results.

---

## H · Regulatory Exposure (open items)

| Regulation | Items open | Highest severity |
|---|---:|---|
| PIPEDA | 28 | CRITICAL (21-2, 22-2, DV-10 dup set) |
| CRA | 6 | CRITICAL (20-3, 20-5) |
| PCI-DSS | 11 | CRITICAL (20-1, 20-2) |
| SOC2 | 15 | CRITICAL (20-2, 20-3) |
| SK-CPPA | 12 | CRITICAL (19-1) |
| SK-HRC | 1 | CRITICAL (21-2) |
| SK-PST | 2 | HIGH (20-6) |
| CASL | 2 | MEDIUM (22-6) |
| SK-TNC, SAFE-CRC | 2 | HIGH (21-3 / DV-1) |

Every finding is tagged; nothing is regulation-less in the P0/P1 tiers.

---

## I · Launch Readiness Checklist

The rider app surface is audit-ready for public launch when:

- [ ] All 6 P0 items (section C) resolved and regression-tested
- [ ] P1 items ≥ 80% closed OR explicitly deferred with product sign-off
- [ ] Rider P0–P4 pre-existing sprint files HEAD-verified (section F)
- [ ] DV-10 closed (transitively closes 19-6, 21-1, 22-5)
- [ ] DV-2 / DV-6 closed (rate limiter + document expiry) — transitively
      hardens 20-1 related paths and 21-6
- [ ] External pen-test run per `docs/external-testing.md`
- [ ] Supabase Canadian region attestation filed (22-2)
- [ ] Privacy policy lists Gemini + every other US sub-processor (22-1 / DV-16)
- [ ] `docs/dpa-register.md` has zero "⚠ VERIFY" rows remaining

---

## J · Effort Roll-Up

| Sprint | Items | Effort |
|---|---:|---:|
| P0 | 6 | ~20 h |
| P1 | 21 | ~35 h |
| P2 | 12 | ~22 h |
| P3 | 3 | ~4 h |
| P4 | 2 | ~6 h |
| **Total actionable** | **44** | **~87 h** |

Plus: pre-existing rider P0–P4 sprint items (~92 items, effort estimates
live in those files), pending HEAD verification.

---

## K · New Issues NOT in Any Existing File

All Phase E actionable findings are either already in this rollup or the
`rider-phase-e-P0-issues.md` P0 file. No net-new work lives outside these
two artifacts.

Exception: the 17 DV-* and 2 product-drift items (DV-11..DV-15) already
in `reports/remediation/driver-new-issues-2026-04-23.md` remain
driver-side; several are referenced from here as shared-backend
duplicates.

---

## L · What's Next

1. **Engineering starts on P0** — `reports/remediation/rider-phase-e-P0-issues.md` is sprint-ready
2. **Run rider P0–P4 verification** when engineering bandwidth allows, to
   update the tracker with DONE/PARTIAL/PENDING status per existing item
3. **Defer driver Phase E** until parallel driver-app work merges to main
4. **Optionally begin backend Phase A** (auth + input validation) —
   parallelizable with everything else
