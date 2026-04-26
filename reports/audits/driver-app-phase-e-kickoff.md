# Driver App — Phase E Kickoff: Dimensions 17–22 (+ D23 Mobile Binary)

**Module:** `driver-app/` + shared backend routes used by driver  
**Branch:** `claude/review-pending-audits-Pu1aP`  
**Target audit version:** v5 (supplements v4 findings from 2026-04-18)  
**Prerequisite:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`  
**Output file:** `reports/audits/2026-04-23-driver-app-v5-phase-e.txt`
(or appended to `2026-04-18-driver-app-production-readiness-v4.txt` if
within context limits — unlikely given the size).

---

## Scope

Phase E + D23 cover seven dimensions that were not in v4 but are required for
production readiness. Most findings will be backend-shared (dedup with rider
Phase E via `duplicate_of` references).

Run each dimension as **one agent call**. Commit after each call.

| Call | Dimension | Driver-specific focus | Output sentinel |
|---|---|---|---|
| E-1 | D17 Observability | GPS-service crash reporter, earnings event log, background-loop heartbeats | `===AUDIT-COMPLETE=== dim=17` |
| E-2 | D18 DR / BCP | Offline ride-complete, WS reconnect with ride resume, payout state not lost | `===AUDIT-COMPLETE=== dim=18` |
| E-3 | D19 Fraud | Fake-trip GPS spoof, quest abuse, SOS misuse, acceptance-rate abuse | `===AUDIT-COMPLETE=== dim=19` |
| E-4 | D20 Financial reconciliation | Earnings ledger, Stripe Connect payouts, T4A generation | `===AUDIT-COMPLETE=== dim=20` |
| E-5 | D21 Threat model / STRIDE | Cross-reference `docs/threat-model/driver-app.md` — verify mitigations | `===AUDIT-COMPLETE=== dim=21` |
| E-6 | D22 Third-party risk | Expo, Google Maps, Firebase, Stripe Connect; driver-facing vendor chain | `===AUDIT-COMPLETE=== dim=22` |
| E-7 | D23 Mobile binary / release | Signed APK/IPA, PrivacyInfo.xcprivacy, App Check, TLS pinning, SBOM | `===AUDIT-COMPLETE=== dim=23` |

---

## Inputs (read once, in order)

1. `audit-framework/dimensions/17-observability.md`
2. `audit-framework/dimensions/18-dr-bcp.md`
3. `audit-framework/dimensions/19-fraud.md`
4. `audit-framework/dimensions/20-financial-reconciliation.md`
5. `audit-framework/dimensions/21-threat-model.md`
6. `audit-framework/dimensions/22-third-party.md`
7. `audit-framework/dimensions/23-mobile-binary.md`
8. `audit-framework/regulatory-matrix.md`
9. `audit-framework/modules/driver-app.md`
10. `audit-framework/ground-rules.md` (rules 1–8; note rule 7 incident-feedback + rule 8 independence)
11. `docs/threat-model/driver-app.md` — pre-scoped residual risks
12. `docs/data-classification.md` — driver column map
13. Rider Phase E output (once complete) — for shared-backend dedup

---

## Ground Rules (driver Phase E specific)

- **Do not re-audit D01–D16** — those findings are in v4.txt.
- **Shared backend paths:** if a finding affects a route used by rider too,
  mark `shared_with: rider` and reference the rider finding if one exists.
- **Dedup with driver-P* verification:** DV-1, DV-2, DV-3, DV-4, DV-6, DV-7,
  DV-8, DV-10 are *already surfaced* — do NOT re-file them. Reference
  `duplicate_of: DV-<N>` instead.
- Every CRITICAL/HIGH must carry file:line:snippet.
- Every finding must tag ≥1 regulation ID.
- Every finding must carry `risk_score` (see OPEN-ITEMS-TRACKER formula).

---

## D17 — Observability

**Driver-specific focus:**
- Background GPS service (Foreground Service on Android, background location on iOS) — does it emit heartbeats? Does it log crashes?
- Earnings events — is every payout event double-logged (client + server)?
- Driver-side audit events for quest progress, rating submissions, document uploads

### Checklist
- [ ] Firebase Crashlytics integrated; `app.config.ts` has correct config for `com.spinr.driver`
- [ ] Crash events include `driver_id`, `ride_id` (if active), error class — no PII in `extra`
- [ ] Background location failures (location-permission revoked, OS kill) emit a telemetry event so backend knows driver stopped reporting
- [ ] WebSocket disconnect events logged with last-heartbeat timestamp (for dispatch debugging)
- [ ] Backend `/drivers/*` endpoints emit structured logs with `request_id`, `driver_id_hash`
- [ ] Background loops for driver-state (document expiry, low-balance nudge) emit heartbeats

**Key files:**
- `driver-app/app/_layout.tsx`
- `driver-app/hooks/useDriverDashboard.ts`
- `driver-app/store/driverStore.ts`
- `backend/routes/drivers.py`
- `backend/utils/document_expiry.py`
- `backend/core/lifespan.py`

---

## D18 — DR / BCP

**Driver-specific focus:** Offline-mode for in-progress trips. A driver mid-trip
cannot afford to lose ride state when the network flaps.

### Checklist
- [ ] `driver-app/` — in-progress ride persisted locally; survives app kill + restart
- [ ] Trip-complete action queued offline and replayed on reconnect (idempotent via `ride_id + client_token`)
- [ ] WS reconnect: exponential backoff capped at 60s; re-subscribes to driver channel
- [ ] Earnings display uses last-known-good cache with "stale" indicator when offline
- [ ] Payout status cached; if Stripe Connect is unreachable, display "pending" not "failed"

**Key files:**
- `driver-app/store/driverStore.ts` (persistence)
- `driver-app/hooks/useDriverDashboard.ts` (WS reconnect)
- `driver-app/app/driver/payout.tsx`

---

## D19 — Fraud

**Driver-specific focus:** The driver side has more fraud surface than rider —
fake trips, quest gaming, rating manipulation, Stripe Connect payout rerouting.

### Checklist
- [ ] **Fake-trip GPS spoofing**: server-side plausibility check on GPS velocity + displacement vs time elapsed; reject implausible trajectories
- [ ] **Rider-side confirmation** of pickup + drop-off required before fare settles (prevents driver completing a trip the rider never took)
- [ ] **Quest / bonus abuse**: quests require specific ride patterns; prevent self-rides (same rider_id = driver_id somehow) or colluding accounts
- [ ] **Rating manipulation**: driver cannot see rider rating until mutual; driver cannot retry rating submission
- [ ] **Acceptance-rate gaming**: declining offers to force surge; monitor decline rate + acceptance correlation with surge multiplier
- [ ] **Stripe Connect rerouting**: changing Connect account ID requires re-KYC; cannot silently point payouts elsewhere

**Key files:**
- `backend/routes/rides.py` (complete-trip, rating)
- `backend/routes/quests.py`
- `backend/services/dispatch_service.py`
- `backend/routes/payments.py` (Connect onboarding)
- `backend/services/fare_service.py`

**Driver Phase E expected to surface:** confirmation of threat-model
DAT-1 (payout redirection), DAT-2 (fake-trip) mitigations — or find them missing.

---

## D20 — Financial Reconciliation

**Driver-specific focus:** Earnings ledger, Stripe Connect payouts, T4A.

### Checklist
- [ ] Every fare settlement creates a matching earnings row (double-entry ledger)
- [ ] Driver-side earnings total = `SUM(fare_amount × 1.0 - platform_fee - rider_discount)` per ride
  - Since platform fee is 0% (CLAUDE.md), earnings = fare_amount minus rider-side discounts
- [ ] Stripe Connect payout events land in `driver_payouts` table via webhook
- [ ] Monthly reconciliation: sum of earnings ≈ sum of payouts + pending balance (delta < $0.01)
- [ ] T4A generation: annual $500+ drivers get T4A by Feb 28
- [ ] T4A PDF downloadable via `/drivers/t4a/${year}` — **P4-7 PARTIAL; confirm current state**

**Key files:**
- `backend/routes/drivers.py` (earnings, T4A)
- `backend/routes/payments.py` (Connect payouts)
- `backend/routes/webhooks.py` (Stripe Connect webhook events)
- `docs/runbooks/stripe-reconciliation.md`

---

## D21 — Threat Model / STRIDE (Driver)

**Do not re-derive the threat model.** It's in `docs/threat-model/driver-app.md`.
Phase E verifies mitigations.

### Checklist — verify each residual risk from threat-model register
- [ ] DT-4 (DV-1 dispatch suspended filter) — CLOSED after P0-5 remediation?
- [ ] DT-6 (DV-2 `$set` wrapper) — CLOSED after P0-5 remediation?
- [ ] DT-1 (fake-trip GPS) — velocity check implemented?
- [ ] DS-5 (DV-10 cross-app Firebase) — audience check implemented?
- [ ] DI-1 (PII retention post-ride in driver app) — verify cache cleared
- [ ] DAT-1 step 2 (Stripe Connect rebind) — re-KYC required?
- [ ] DE-3 (Connect ID ownership check) — server-side binding verified?

For each, emit DONE / PARTIAL / PENDING finding with file:line evidence.

---

## D22 — Third-Party Risk (Driver)

### Checklist
- [ ] `driver-app/package.json` — no dependency with known HIGH/CRITICAL CVE > 30 days old
- [ ] Expo SDK within supported window (not EOL)
- [ ] Google Maps API key restricted to `com.spinr.driver` bundle ID
- [ ] Maps API key not in binary strings (loaded from EAS secret at build time)
- [ ] Firebase project has driver-specific App ID (closes DV-10 prerequisite)
- [ ] Stripe publishable key scoped to driver operations (driver-side key)
- [ ] No Stripe secret key in `driver-app/` codebase
- [ ] Confirm sub-processor list in `docs/vendor-inventory.md` covers all runtime deps
- [ ] EAS build secret rotation runbook exists

**Cross-reference:** `docs/vendor-inventory.md` and `docs/dpa-register.md`.

---

## D23 — Mobile Binary / Release Artifact (Driver)

**Checklist from `audit-framework/dimensions/23-mobile-binary.md`** — run the
full checklist with driver-specific emphasis:

- [ ] Signed APK bundle ID `com.spinr.driver`; iOS IPA signed with Spinr team
- [ ] MobSF scan shows no leaked secrets (Stripe, Supabase, Firebase, admin URLs)
- [ ] Android manifest includes `ACCESS_BACKGROUND_LOCATION` (driver-specific)
  with justification in Play Console
- [ ] iOS `PrivacyInfo.xcprivacy` declares location + file-timestamp + user-defaults use
- [ ] Crash-free user rate > 99.5% for past 14 days
- [ ] App Check enforced; unattested requests rejected by backend
- [ ] TLS pinning to `api.spinr.ca` configured; backup pin present
- [ ] SBOM generated for current release → `reports/sbom/driver-vX.Y.Z.cdx.json`

---

## Output Contract

Each call emits:

1. Human-readable findings in `[N-M]` format
2. `===FINDINGS-YAML===` block with the v2026-04-24 schema (includes
   `likelihood`, `risk_score`, `reviewed_by`, `blast_radius`)
3. Sentinel: `===AUDIT-COMPLETE=== dim=[N] module=driver-app findings=[N]`

**File naming:** `reports/audits/2026-04-23-driver-app-v5-phase-e.txt`
(single file; append each dim's output).

After all 7 calls complete, produce `reports/audits/2026-04-23-driver-remediation-rollup-v5.md`
with:
- New findings from Phase E + D23
- Open items from driver-P*-verification.md (still-open 4 items)
- Items closed by Phase E verification
- Updated effort estimate
- Updated launch-readiness checklist

---

## Expected Outputs (shape only — not pre-judgement)

| Dim | Expected finding count | Expected status mix |
|---|:-:|---|
| D17 | 4–8 | Crash SDK likely integrated; backend heartbeat gaps likely |
| D18 | 3–6 | Offline mode likely PARTIAL; WS reconnect likely DONE |
| D19 | 5–10 | Fake-trip GPS check likely PENDING; rating idempotency likely DONE |
| D20 | 4–8 | Reconciliation cron likely PENDING; T4A P4-7 PARTIAL |
| D21 | 6–12 | Mostly verify-only, 2–3 new residuals expected |
| D22 | 3–6 | DPA gaps from `docs/dpa-register.md` carry over |
| D23 | 5–10 | First-ever run; expect many PENDING (MobSF not yet integrated) |

Total: 30–60 findings expected.

---

## After Phase E Completes

- Update `reports/audits/INDEX.md` driver-app row: `v5 complete (YYYY-MM-DD) · [N] findings`
- Update `audit-framework/CHANGELOG.md` with v5 re-audit closing the 90-day clock
- File new items in `OPEN-ITEMS-TRACKER.md § A` (driver open) — reusing risk-score formula
- Schedule next driver re-audit 90 days out (2026-07-23 at latest)
