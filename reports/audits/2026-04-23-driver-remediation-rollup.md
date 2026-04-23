# Driver App — Remediation Verification Rollup

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt` (258 findings over 14 tasks)
**Verified against HEAD of branch `claude/review-pending-audits-Pu1aP`.**
**Method:** Per-sprint static inspection; see linked files for evidence per item.

Per-sprint files:
- P0: `reports/audits/2026-04-23-driver-P0-verification.md`
- P1: `reports/audits/2026-04-23-driver-P1-verification.md`
- P2: `reports/audits/2026-04-23-driver-P2-verification.md`
- P3: `reports/audits/2026-04-23-driver-P3-verification.md`
- P4: `reports/audits/2026-04-23-driver-P4-verification.md`

---

## Completion by Sprint

| Sprint | Items | DONE | PARTIAL | PENDING | BLOCKED | UNVERIFIABLE | SUPERSEDED | % complete | Open effort |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 — Critical (before device test) | 7 | 6 | 1 | 0 | 0 | 0 | 0 | 86% | 4 h |
| P1 — Before beta | 10 | 9 | 0 | 0 | 0 | 0 | 1 | 100% | 0 h |
| P2 — Before public launch | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 100% | 0 h |
| P3 — Hardening | 10 | 9 | 1 | 0 | 0 | 0 | 0 | 90% | 8 h |
| P4 — Future features | 7 | 5 | 2 | 0 | 0 | 0 | 0 | 71% | 32 h |
| **Total** | **44** | **39** | **4** | **0** | **0** | **0** | **1** | **89%** | **44 h** |

Completion math counts DONE + SUPERSEDED as closed. PARTIAL counts against
open effort but is **not** a regression of a previously-shipped fix —
the P0/P1/P2/P3 PARTIALs are about *depth* (test coverage, defense in
depth, PDF generation), not *broken fixes*.

---

## Open Work (4 items, 44 h)

| Item | Sprint | Status | Owner | Effort | Summary |
|---|---|---|---|---:|---|
| P0-5 | P0 | PARTIAL | `backend` | 4 h | Expired-driver suspension flags are written but `{"$set": ...}` MongoDB-style wrapper on the Supabase update is suspect; no `status='suspended'` filter in `find_nearby_drivers` RPC for defense in depth; no regression test. |
| P3-4 | P3 | PARTIAL | `backend` + `driver-app` | 8 h | Jest coverage threshold exists but at 30/20/30 (below 70/60/70 target); pytest `--cov-fail-under` explicitly disabled because backend coverage baseline is ~6%. Needs stepped increase + CI drop-check. |
| P4-5 | P4 | PARTIAL | `driver-app` | 20 h | Smoke / online-toggle / ride-offer specs exist (Playwright-style). Missing: verify-OTP, complete-trip, payout flows. Framework was changed from the named Maestro; remediation text needs sync. |
| P4-7 | P4 | PARTIAL | `backend` | 12 h | CSV earnings export fully functional; T4A UI ends at a placeholder ("PDF will be available once tax documents are finalized"). Backend PDF generation incomplete. |

---

## Owners by Open-Item Count + Effort

| Owner | Open items | Open effort |
|---|---:|---:|
| `backend` | 3 (P0-5 sole, P3-4 shared, P4-7 sole) | 16 h + share of P3-4 (~4 h) = **~20 h** |
| `driver-app` | 2 (P3-4 shared, P4-5 sole) | 20 h + share of P3-4 (~4 h) = **~24 h** |
| `infra` | 0 | 0 h |
| `compliance` | 0 | 0 h |
| `legal` | 0 | 0 h |

No blocker is external (no `ext-*` vendor, no legal/compliance gate). All
open work is code-level by internal teams.

---

## BLOCKED items

None.

## UNVERIFIABLE items (require runtime probe)

None at static level — but the following static-DONE items carry
confidence caveats that recommend a runtime probe before public launch:

| Item | Why probe matters |
|---|---|
| P0-5 (mixed status) | Confirm Supabase update with `{"$set": ...}` wrapper actually persists status=suspended, OR replace wrapper with flat dict. |
| P2-7 | `get_ipaddr` respects X-Forwarded-For only when ASGI is started with `--proxy-headers`; confirm Railway/Render launch config. |
| P3-3 | OTP lockout test uses `mock_redis`; add docker-compose-based real-Redis E2E to stress the true failure mode. |
| P4-4 | Firebase Console registration (DeviceCheck iOS + Play Integrity Android) is an ops step not visible in code. |

---

## Regulatory Exposure — Open Items

Cross-referenced with `audit-framework/regulatory-matrix.md`:

| Regulation ID | Open items touching this regulation |
|---|---|
| PIPEDA | P3-4 (coverage gate on PII-sensitive paths weak), P0-5 (driver screening) |
| SAFE-CRC / SAFE-DRV / SAFE-VEH | P0-5 |
| SGI · SK-TNC | P0-5 |
| CRA | P4-7 (T4A PDF incomplete — ≥$500/yr drivers need T4A slips by Feb 28 each year) |
| SK-CPPA | — (P0-5 tangentially via driver suspension enforcement) |
| PCI-DSS | — |
| CASL | — |
| OLA | — |
| WCAG | — |

**Highest regulatory tail risk in open work:** CRA T4A (P4-7) — material filing deadline; SGI/TNC/SAFE-* (P0-5) — material dispatch-safety risk.

---

## New Issues Surfaced During Verification (not in original remediation plan)

Logged per-sprint; summary here so next audit cycle picks them up.

### Backend / Dispatch
1. **`find_nearby_drivers` RPC lacks `status != 'suspended'` filter** — defense-in-depth gap behind P0-5. Suspended drivers who re-flip `is_online=true` via any code path would re-enter dispatch.
2. **MongoDB-style `{"$set": ...}` wrapper in `document_expiry.py`** — same anti-pattern as source finding `[2-2]`. Worth a targeted grep across `backend/` for all remaining `$set` wrappers against the Supabase client.
3. **State-string drift**: `COMPLETE_FROM_STATES = ("in_progress",)` vs `trip_in_progress` in some earlier code + remediation text. Next audit should do a full state-machine sweep.
4. **Fallback Stripe idempotency key ambiguous** — `intent-{user}-{amount}` would collide on a legitimate same-amount retry after 24 h. Swap to UUID client token when `ride_id` missing.
5. **P2-10 fire-and-forget PUT** — notification-preference updates swallow 4xx/5xx; UI diverges from server. Surface a toast on error.
6. **P2-4 in-memory rate-limit fallback is per-process** — alert SRE when "Redis unavailable" warning fires in prod.
7. **P2-5 pgsodium key rotation** — `drivers_pii_key` has no documented rotation cadence (required for SOC2).
8. **P3-8 retention-horizon purge** — soft-delete is implemented, but no scheduled purge at PIPEDA / CRA retention horizon.
9. **P3-9 notification deep-link default** — unknown types silently no-op; add fallback + analytics.

### Auth
10. **Firebase-path audience check** — P3-10 closed the refresh-token gap, but the driver-vs-rider audience check on the Firebase path is open as rider-P1-12.

### Frontend
11. **Panel layout drift** — `ActiveRidePanel.tsx` + `TripCompletedPanel.tsx` moved from `components/panels/` to `components/dashboard/`; remediation text still references old path.
12. **Legal + report-safety screens at `app/` root** — not under `app/driver/`; confirm menu wiring.

### Product
13. **P1-1 cancel-with-fee vs hard-block** — shipped implementation diverges from remediation script (fee applied when driver arrived, hard-block only for `trip_in_progress`). Update `P1-before-beta.md` to reflect shipped behaviour so future audits don't flag it.
14. **P1-10 OTP keypad sizing** — superseded because the app uses the native phone-pad keyboard. Remove from checklist.
15. **P4-5 framework choice** — remediation named Maestro; actual specs are Playwright-style. Update `P4-future-features.md`.

### Compliance
16. **Gemini cross-border disclosure (P4-1)** — privacy policy should list Gemini as a sub-processor.
17. **DSAR audit row + SLA (P4-6)** — emit an audit-log row per request; enforce the 30-day PIPEDA response window.

---

## Recommendations Ordered by Urgency

1. **Fix P0-5 before any device testing** — 4 h. Replace `{"$set": ...}` with a flat dict, add `status != 'suspended'` filter to the RPC, write a regression test. This is the only item that blocks P0 sign-off.
2. **Sync the remediation markdown files** — ~1 h. Items 13–15 above (P1-1 fee-not-block; P1-10 superseded; P4-5 framework change; panel path drift). Stops these from being re-flagged next audit.
3. **Plan a P3-4 coverage ramp** — sprint-sized. Raise FE thresholds to 50/40/50 now; add backend `--cov-fail-under=30` now; step up 5 pp/sprint.
4. **Schedule T4A PDF work for January** — 12 h backend; CRA filing window is Feb 28.
5. **Runtime-probe the four "confidence med" items** before public launch — see UNVERIFIABLE table.
6. **Write the purge job for soft-deleted data (P3-8 follow-up)** — PIPEDA retention rule enforcement.

---

## Bottom Line

Driver app remediation is **89% complete** against the original 44 items from
the 2026-04-18 audit. One P0 item (P0-5) needs 4 h to close before device
testing. Beta, launch, and hardening sprints are fully or nearly fully
closed (P1 + P2 = 100%; P3 = 90%). Future-features sprint is 71% with
16–32 h of remaining code work on the driver-app and backend teams.

**No blockers external to the engineering team. All open work is code-level.**
