# Driver App — P4 Remediation Verification

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Sprint:** P4 — Future Features
**Source sprint file:** `reports/remediation/P4-future-features.md`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`
**Items verified:** 7
**Method:** Static inspection at HEAD.

---

### P4-1 · AI Support Bot (Gemini)

**Status:** `DONE`
**Source finding:** `[13-15]`
**Evidence:**
- Backend: `backend/routes/support.py:2, 90, 94, 102` —
  AI-powered support chat using `google.generativeai`, model
  `gemini-1.5-flash`. Graceful fallback (line 111) when Gemini is unavailable.
- Driver UI: `driver-app/app/driver/help.tsx:69` —
  `const res = await api.post('/support/chat', {...})` wired to the backend.
**Reason:** End-to-end wiring present: driver Help tab → POST /support/chat →
Gemini → response.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (Gemini is a Google US service — verify data-flow
disclosure in privacy policy)
**Effort remaining:** 0 h
**Note:** Gemini is US-hosted — cross-border data flow should be disclosed in
the privacy policy (PIPEDA transparency). Knowledge-base priming was not
verified in this pass.

---

### P4-2 · In-App FAQ Screen

**Status:** `DONE`
**Source finding:** `[13-16]`
**Evidence:**
- `driver-app/app/driver/faq.tsx` — file exists.
**Reason:** FAQ screen exists in the driver app.
**Owner:** —
**Confidence:** med
**Regulations:** OLA (FR parity on FAQ content)
**Effort remaining:** 0 h
**Note:** Did not verify category filters, search, or deep-link wiring from
error messages into specific FAQ entries. Flag for a UX follow-up review.

---

### P4-3 · 4 Missing App Screens

**Status:** `DONE`
**Source finding:** `[1-16, 1-17, 1-18, 1-19]`
**Evidence:**
- `driver-app/app/driver/payout-history.tsx` — present.
- `driver-app/app/driver/tax-documents.tsx` — present.
- `driver-app/app/report-safety.tsx` — present.
- `driver-app/app/legal.tsx` — present.
**Reason:** All 4 screens exist at expected paths.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (legal/privacy page required), App Store / Play Store
submission (legal required)
**Effort remaining:** 0 h
**Note:** Report-safety and legal live at `app/` root rather than
`app/driver/`; confirm navigation wiring from driver menus. Legal screen
must render the current Terms + Privacy URL; confirm content freshness
before App Store submission.

---

### P4-4 · Enable Firebase App Check

**Status:** `DONE`
**Source finding:** `[11-2]`
**Evidence:**
- `backend/core/middleware.py:52-91` —
  `class FirebaseAppCheckMiddleware(BaseHTTPMiddleware)`;
  reads `X-Firebase-AppCheck` header; verifies via
  `firebase_admin.app_check.verify_token(token)`.
- `backend/core/middleware.py:390-392` —
  `app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=is_production)`
**Reason:** Middleware registered; enforcement toggles on in production
and off in dev/test. Firebase Admin verifies the attestation on every
request.
**Owner:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 0 h
**Note:** Firebase Console registration (DeviceCheck iOS + Play Integrity
Android) is an ops action not visible in code — confirm before prod launch.
Also verify admin routes have their own gate (middleware doc at line 35
suggests they're admin-JWT-gated separately).

---

### P4-5 · Maestro E2E Flows for Core Ride Scenarios

**Status:** `PARTIAL`
**Source finding:** `[9-8]`
**Evidence (what's in place):**
- `driver-app/e2e/smoke.spec.ts` — smoke test.
- `driver-app/e2e/online-toggle.spec.ts` — go-online test.
- `driver-app/e2e/ride-offer.spec.ts` — ride-offer test (part of accept flow).
- `driver-app/e2e/fixtures.ts` — shared fixtures.
**Evidence (what's missing):**
- No `03_accept_ride.yaml`, `04_verify_otp.yaml`, `05_complete_trip.yaml`,
  `06_payout.yaml` — the remediation specifically named Maestro YAML files.
- No `driver-app/.maestro/` or `driver-app/maestro/` directories.
- The existing `.spec.ts` files are Playwright/Detox-style, not Maestro.
**Reason:** Some E2E coverage exists (smoke + online + ride-offer) but not
the 4 named Maestro flows. Platform choice was changed (Playwright-like
instead of Maestro); verify-OTP, complete-trip, and payout flows are not
covered end-to-end by any framework.
**Owner:** `driver-app`
**Blocked by:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 16-24 h (write verify-OTP + complete-trip + payout
specs in the chosen framework).
**Action:**
1. Decide: Maestro (per remediation) or Playwright/Detox (per current).
2. Add `verify-otp.spec.ts`, `complete-trip.spec.ts`, `payout.spec.ts`.
3. Update remediation-file text to reflect the framework choice.

---

### P4-6 · GDPR / PIPEDA Data Export

**Status:** `DONE`
**Source finding:** `[12-14]`
**Evidence:**
- Backend endpoint: `backend/routes/drivers.py:1503-1520` —
  `POST /drivers/me/export-data` → schedules
  `background_tasks.add_task(_build_and_email_data_export, user_id, email)`.
- Backend build + email: `backend/routes/drivers.py:1523-1582` —
  JSON bundle emailed to the registered address.
- Secondary endpoint: `backend/routes/users.py:68-69` —
  `POST /data-export` (generic `request_data_export`).
- Driver UI: `driver-app/app/driver/settings.tsx:86-97` —
  `await api.post('/drivers/me/export-data')` + success/failure UI.
**Reason:** Full PIPEDA s.9 DSAR flow: UI button → POST → background job →
email with JSON export. Out-of-app delivery path avoids holding export in
device memory.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (Principle 9 — Individual Access)
**Effort remaining:** 0 h
**Note:** Verify the export covers ALL data classes from
`audit-framework/regulatory-matrix.md` (PII + SENSITIVE). Ensure audit
log row is written when an export is requested (for misuse detection).
Verify ≤ 30-day response SLA (PIPEDA).

---

### P4-7 · T4A / Earnings CSV Download

**Status:** `PARTIAL`
**Source finding:** `[8-14]`
**Evidence (what's in place):**
- `driver-app/app/driver/payout.tsx:199-220` — `handleDownloadCSV` function:
  - GET `/drivers/earnings/export?year=${year}`
  - If `res.data.url` → `Linking.openURL(url)`
  - Else if `res.data.data` → encode as `data:text/csv;charset=utf-8,...` URL
  - Else → warning toast
- `driver-app/app/driver/payout.tsx:500` — `onPress={handleDownloadCSV}`
  wired to a visible button.
**Evidence (what's missing):**
- `driver-app/app/driver/payout.tsx:173-196` — `handleDownloadT4A` calls
  `GET /drivers/t4a/${year}` but, when `res.data.pdf_url` is missing,
  shows an alert:
  "A downloadable PDF will be available once tax documents are finalized."
- There is no evidence the backend actually returns a PDF URL yet — the
  UI-layer placeholder suggests the T4A PDF generation step is not complete.
**Reason:** CSV export is fully functional. T4A flow exists in the UI but
ends at a placeholder when the backend doesn't return a PDF URL.
**Owner:** `backend` (T4A PDF generation)
**Blocked by:** —
**Confidence:** high
**Regulations:** CRA (T4A issuance to drivers with ≥$500/yr)
**Effort remaining:** 8-16 h (T4A PDF generation + filing + URL return).
**Action:**
1. Confirm backend T4A endpoint returns `pdf_url` (or inline PDF bytes).
2. If generator is pending, schedule for tax season (Feb of each year).
3. Add integration test for the T4A path end-to-end.

---

```yaml
===VERIFICATION-YAML===
- id: P4-1
  source_finding: "13-15"
  status: DONE
  evidence:
    file: backend/routes/support.py
    lines: [2, 90, 94, 102, 111]
    snippet: "model_name=\"gemini-1.5-flash\"; driver-app/app/driver/help.tsx:69 api.post('/support/chat', ...)"
    test_file: null
    test_lines: null
  reason: "End-to-end wiring: driver Help tab → /support/chat → Gemini → reply with fallback."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Gemini is US-hosted; disclose cross-border flow in privacy policy."
- id: P4-2
  source_finding: "13-16"
  status: DONE
  evidence:
    file: driver-app/app/driver/faq.tsx
    lines: null
    snippet: "file exists"
    test_file: null
    test_lines: null
  reason: "FAQ screen exists in driver app."
  owner: null
  blocked_by: null
  confidence: med
  regulations: [OLA]
  effort_remaining_hours: 0
  notes: "Category filter/search/deep-link wiring not verified in this pass."
- id: P4-3
  source_finding: "1-16, 1-17, 1-18, 1-19"
  status: DONE
  evidence:
    file: driver-app/app
    lines: null
    snippet: "payout-history.tsx, tax-documents.tsx, report-safety.tsx, legal.tsx — all present"
    test_file: null
    test_lines: null
  reason: "All 4 screens exist."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "report-safety and legal live at app/ root (not app/driver/); confirm nav wiring + content freshness before App Store."
- id: P4-4
  source_finding: "11-2"
  status: DONE
  evidence:
    file: backend/core/middleware.py
    lines: [52, 75, 90, 91, 390, 392]
    snippet: "FirebaseAppCheckMiddleware ... app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=is_production)"
    test_file: null
    test_lines: null
  reason: "App Check middleware registered; enforcement on in prod."
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  notes: "Firebase Console registration (DeviceCheck/Play Integrity) is an ops step — confirm before prod."
- id: P4-5
  source_finding: "9-8"
  status: PARTIAL
  evidence:
    file: driver-app/e2e
    lines: null
    snippet: "smoke.spec.ts, online-toggle.spec.ts, ride-offer.spec.ts, fixtures.ts"
    test_file: null
    test_lines: null
  reason: "Smoke + online + ride-offer covered via Playwright-style specs; verify-OTP, complete-trip, payout flows missing."
  owner: driver-app
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 20
  notes: "Framework choice changed from Maestro to Playwright/Detox — update remediation-file text; add missing flows."
- id: P4-6
  source_finding: "12-14"
  status: DONE
  evidence:
    file: backend/routes/drivers.py
    lines: [1503, 1504, 1519, 1520, 1523, 1581, 1582]
    snippet: "@api_router.post(\"/me/export-data\") ... background_tasks.add_task(_build_and_email_data_export, user_id, email)"
    test_file: null
    test_lines: null
  reason: "DSAR endpoint + background-job email delivery + driver settings UI wired."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Confirm export covers PII + SENSITIVE classes, write audit row on request, honour ≤30-day PIPEDA SLA."
- id: P4-7
  source_finding: "8-14"
  status: PARTIAL
  evidence:
    file: driver-app/app/driver/payout.tsx
    lines: [45, 173, 179, 190, 199, 203, 500]
    snippet: "handleDownloadCSV (works); handleDownloadT4A (ends with placeholder message when pdf_url missing)"
    test_file: null
    test_lines: null
  reason: "CSV download fully functional; T4A UI path exists but backend PDF generation appears incomplete."
  owner: backend
  blocked_by: null
  confidence: high
  regulations: [CRA]
  effort_remaining_hours: 12
  notes: "Complete T4A PDF generator + filing; add integration test end-to-end; schedule around Feb tax season."
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P4 items=7 done=5 partial=2 pending=0 blocked=0 unverifiable=0 superseded=0
```

---

## Summary

| Status | Count | IDs |
|---|---|---|
| DONE | 5 | P4-1, P4-2, P4-3, P4-4, P4-6 |
| PARTIAL | 2 | P4-5, P4-7 |
| PENDING | 0 | — |
| BLOCKED | 0 | — |
| UNVERIFIABLE | 0 | — |
| SUPERSEDED | 0 | — |

**Open P4 effort:** ~32 h across `driver-app` (E2E tests) and `backend` (T4A PDF).

## New issues discovered (not in scope for this verification)

1. **P4-1 Gemini cross-border disclosure** — Gemini is a Google US service; privacy policy should list it as a sub-processor and describe cross-border data flow (PIPEDA transparency).
2. **P4-3 nav wiring** — `report-safety.tsx` and `legal.tsx` live at `app/` root, not `app/driver/`; confirm the relevant menu/footer links route to them.
3. **P4-4 Console registration** — code-side is DONE, but DeviceCheck (iOS) and Play Integrity (Android) registration in Firebase Console are ops actions; verify as part of prod readiness.
4. **P4-5 framework decision** — remediation text specifies Maestro but the app uses Playwright-style specs. Update `P4-future-features.md` to the chosen framework to avoid future auditor confusion.
5. **P4-6 audit row + SLA** — DSAR flow exists; ensure (a) an audit-log row is emitted per request, (b) the 30-day PIPEDA SLA is enforced (not just best-effort).
6. **P4-7 T4A automation** — driver-app UI tells drivers "PDF will be available once tax documents are finalized." Define the Feb-each-year generator/filing job; consider a CRA-sandbox test path.
