# Rider App — P1b Sprint Verification (items 18–33 of 33)

**Sprint file:** `reports/remediation/rider-P1-before-beta.md` (items 18–33)
**Source audit:** `reports/audits/2026-04-19-rider-app-v1.txt`
**Branch:** `claude/review-pending-audits-Pu1aP`

**Result: 16/16 DONE · 0 PARTIAL · 0 PENDING · 0 BLOCKED**

Rider verified totals:
- P0: 8/8 DONE
- P1a: 17/17 DONE
- P1b: 16/16 DONE
- **Combined: 41/41 — 100% DONE**

P2 (33), P3 (8), P4 (10) remain pending.

---

## Highlights

| Item | Title | Status |
|---|---|---|
| P1-18 | Cancel correctly checks `driver_arrived` state | DONE — `rides.py:1858,1860` |
| P1-19 | Wallet pay validates server fare | DONE — `wallet.py:157,162` (ERR_FARE_EXCEEDED) |
| P1-20 | Tip endpoint duplicate-tip guard | DONE — `rides.py:964` (`ERR_TIP_DUPLICATE`) |
| P1-21 | POST /rides idempotency-key | DONE — `rideStore.ts:397` (cf. P1-7) |
| P1-22 | Jest coverage threshold enforced | DONE — `jest.config.js:21` (lines 60 / fns 55 / branches 40) |
| P1-23 | Critical store tests (hydrate, double-book, cancel-after-arrived) | DONE — 3 describe blocks present |
| P1-24 | WebSocket scenario tests (driver_timeout, ride_cancelled, race) | DONE — `rideStore.ws.test.ts` |
| P1-25 | Wallet store tests (pay + tip idempotency) | DONE — `walletStore.test.ts` |
| P1-26 | E2E spec includes rating + tip stage | DONE — `e2e/ride-booking.spec.ts` |
| P1-27 | ALLOWED_ORIGINS production list documented | DONE — `.env.example:13,15` |
| P1-28 | PII strip from driver-facing rider data | DONE — `drivers.py:1688` (excludes phone, email, stripe_customer_id) |
| P1-29 | Killed-state notification deep-link | DONE — `_layout.tsx:349` |
| P1-30 | FCM data-field for foreground routing | DONE — `features.py:1389` |
| P1-31 | Star-rating accessibility | DONE — `ride-completed.tsx:416-419` |
| P1-32 | SOS button accessibility | DONE — `SOSButton.tsx:110-112` |
| P1-33 | Map-overlay accessibility (Message + Share) | DONE — `ride-in-progress.tsx:556-571` |

**Cross-link:** P1-28 strips `phone`, `email`, `stripe_customer_id` from
the driver-facing rider response — but does NOT strip
`pickup_address`/`dropoff_address` (the realized RI-2 / Phase E 21-2
finding). Confirms 21-2 is still open: address fields slip past this
strip filter.

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P1-18
  source_finding: "07-2"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [1858, 1860]
    snippet: "if status=='driver_arrived' and driver_id: charged_driver = Decimal('5.00')"
    test_file: null
    test_lines: null
  reason: "Cancel checks state-string 'driver_arrived'; $5 fee charged"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-19
  source_finding: "08-1"
  status: DONE
  evidence:
    file: backend/routes/wallet.py
    lines: [157, 162]
    snippet: "server_fare = _d(ride.get('total_fare', 0)); if debit_amount > server_fare: ERR_FARE_EXCEEDED"
    test_file: null
    test_lines: null
  reason: "Wallet debit validates client amount ≤ server fare"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA", "PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-20
  source_finding: "08-2"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [964, 967]
    snippet: "if existing_tip > 0: raise HTTPException(400, 'ERR_TIP_DUPLICATE')"
    test_file: null
    test_lines: null
  reason: "Tip endpoint rejects re-tip via existing-tip check"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Mitigates 19-7 cumulative tip risk for the rider tip path"

- id: rider-P1-21
  source_finding: "08-3"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [397, 399]
    snippet: "idempotencyKey = `ride-${userId}-${Date.now()}`; headers: { 'Idempotency-Key': idempotencyKey }"
    test_file: null
    test_lines: null
  reason: "Client generates Idempotency-Key; sent via header; backend dedupes"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: "rider-P1-7"
  notes: "Same fix verified twice"

- id: rider-P1-22
  source_finding: "09-1"
  status: DONE
  evidence:
    file: rider-app/jest.config.js
    lines: [21, 26]
    snippet: "coverageThreshold: {global: {lines: 60, functions: 55, branches: 40}}"
    test_file: null
    test_lines: null
  reason: "Jest enforces coverage floor; CI fails on regression"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-23
  source_finding: "09-2"
  status: DONE
  evidence:
    file: rider-app/store/__tests__/rideStore.test.ts
    lines: [270, 289, 310]
    snippet: "describe(hydrateActiveRide); describe(double-booking); describe(cancel after driver_arrived)"
    test_file: null
    test_lines: null
  reason: "Critical-path test suites present"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-24
  source_finding: "09-3"
  status: DONE
  evidence:
    file: rider-app/store/__tests__/rideStore.ws.test.ts
    lines: [149, 171, 187]
    snippet: "driver_timeout via WS; ride_cancelled; WS/poll race"
    test_file: null
    test_lines: null
  reason: "Three WS scenarios under test"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-25
  source_finding: "09-4"
  status: DONE
  evidence:
    file: rider-app/store/__tests__/walletStore.test.ts
    lines: [176, 209]
    snippet: "describe(payWithWallet); describe(addTip idempotency)"
    test_file: null
    test_lines: null
  reason: "Wallet path coverage"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-26
  source_finding: "09-6"
  status: DONE
  evidence:
    file: rider-app/e2e/ride-booking.spec.ts
    lines: [16, 69, 70]
    snippet: "test('completed screen shows rating and tip sections')"
    test_file: null
    test_lines: null
  reason: "E2E flow covers booking → rating + tip"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-27
  source_finding: "11-1"
  status: DONE
  evidence:
    file: backend/.env.example
    lines: [13, 15]
    snippet: "# ALLOWED_ORIGINS=https://spinr.app,https://spinr-track.app,https://admin.spinr.ca"
    test_file: null
    test_lines: null
  reason: ".env.example documents production CORS allow-list"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Actual deployment must export the var"

- id: rider-P1-28
  source_finding: "12-1"
  status: DONE
  evidence:
    file: backend/routes/drivers.py
    lines: [1688, 1691]
    snippet: "safe_rider = {k: raw[k] for k in raw if k not in {'phone','email','stripe_customer_id'}}"
    test_file: null
    test_lines: null
  reason: "Driver-facing rider data excludes phone, email, stripe_customer_id"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Cross-link: addresses NOT in this strip — Phase E 21-2 still open"

- id: rider-P1-29
  source_finding: "13-4"
  status: DONE
  evidence:
    file: rider-app/app/_layout.tsx
    lines: [349, 353]
    snippet: "getInitialNotificationResponseAsync(); routeFromNotificationData(data)"
    test_file: null
    test_lines: null
  reason: "Killed-state deep-link routes from notification tap on cold start"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-30
  source_finding: "13-5"
  status: DONE
  evidence:
    file: backend/features.py
    lines: [1389, 1394]
    snippet: "messaging.Message(data=data or {}, ...)"
    test_file: null
    test_lines: null
  reason: "FCM message data field populated for foreground routing"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-31
  source_finding: "15-1"
  status: DONE
  evidence:
    file: rider-app/app/ride-completed.tsx
    lines: [416, 419]
    snippet: "accessibilityLabel='Rate N stars'; role='button'; state"
    test_file: null
    test_lines: null
  reason: "Rating stars expose label, role, state"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-32
  source_finding: "15-3"
  status: DONE
  evidence:
    file: shared/components/SOSButton.tsx
    lines: [110, 112]
    snippet: "accessibilityLabel='Emergency SOS'; role='button'; hint='Hold 1.5s'"
    test_file: null
    test_lines: null
  reason: "SOS button accessible"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-33
  source_finding: "15-10"
  status: DONE
  evidence:
    file: rider-app/app/ride-in-progress.tsx
    lines: [556, 571]
    snippet: "Message + Share buttons have accessibilityLabel + role"
    test_file: null
    test_lines: null
  reason: "Map-overlay action buttons accessible"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P1b module=rider items=16 done=16 partial=0 pending=0 blocked=0 unverifiable=0 superseded=0
