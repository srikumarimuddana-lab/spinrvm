# Rider App — P2b Sprint Verification (items 18–33 of 33)

**Sprint file:** `reports/remediation/rider-P2-before-launch.md` (items 18–33)
**Branch:** `claude/review-pending-audits-Pu1aP`

**Result: 10 DONE · 1 PARTIAL · 5 PENDING · 0 BLOCKED**

Cumulative rider verification through P2 complete:

| Sprint | Total | DONE | PARTIAL | PENDING |
|---|---:|---:|---:|---:|
| P0 | 8 | 8 | 0 | 0 |
| P1a | 17 | 17 | 0 | 0 |
| P1b | 16 | 16 | 0 | 0 |
| P2a | 17 | 8 | 4 | 5 |
| P2b | 16 | 10 | 1 | 5 |
| **Cumulative** | **74** | **59 (80%)** | **5** | **10** |

P3 (8) and P4 (10) remaining — total rider items 92, verified 74 so far.

---

## DONE (10)
| ID | Title | Evidence |
|---|---|---|
| P2-18 | Stops max=5 + coordinate range validators | `schemas.py:302,339,350` |
| P2-19 | scheduled_time rejects past + DST gap | `schemas.py:352-358` |
| P2-20 | Fare-split participant phones +1 regex | `fare_split.py:44-47` |
| P2-21 | Saved address sanitize_string + length caps | `schemas.py:179-180` + `addresses.py:31` |
| P2-22 | Wallet pay amount Decimal le=500 | `wallet.py:91` |
| P2-23 | Wallet Decimal types throughout | `wallet.py:86,91` |
| P2-24 | search-destination KeyboardAvoidingView | `search-destination.tsx:11,322,642` |
| P2-26 | Ride-in-progress error state with retry | `ride-in-progress.tsx:249-265` |
| P2-28 | Map zoom buttons 44×44pt | `(tabs)/index.tsx:520-522` |
| P2-32 | Book button isBooking debounce + spinner | `payment-confirm.tsx:96,453-459` |

## PARTIAL (1)
| ID | Title | What's missing | Effort |
|---|---|---|---|
| P2-25 | allowFontScaling coverage on fixed-height containers | Driver-arriving/in-progress/arrived screens covered; ride-options/activity/wallet not yet | 2 h |

## PENDING (5)
| ID | Title | Why pending | Effort |
|---|---|---|---|
| P2-27 | Driver-arrived map error fallback | Conditional renders null on error; no retry UI surface | 0.5 h |
| P2-29 | fetchRide overwrites WS-fresh driver location | Polling response stomps on fresher WS coordinates → marker jump-back | 1 h |
| P2-30 | Location permission denied no UX feedback | Silent return; no Alert, no "Open Settings" link | 0.5 h |
| P2-31 | FreeCancelTimer.onExpire callback | Component is display-only; cancel button not warned/disabled at expiry | 1.5 h |
| P2-33 | validate_promo accepts client `ride_fare` | apply_promo (P1-8 DONE) reads server fare; validate_promo still trusts client | 2.5 h |

P2-33 cross-link: P1-8 fixed apply_promo to use server fare, but
validate_promo (different endpoint) still accepts client-supplied
ride_fare — minimum-fare eligibility and discount calc still
inflation-attackable until this is fixed.

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P2-18
  source_finding: "04-3"
  status: DONE
  evidence:
    file: backend/schemas.py
    lines: [302, 339, 350]
    snippet: "stops: Optional[List[Dict[str, Any]]] = Field(default=[], max_length=5)"
    test_file: null
    test_lines: null
  reason: "Stops capped at 5; coords validated [-90,90]×[-180,180]"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-19
  source_finding: "04-4"
  status: DONE
  evidence:
    file: backend/schemas.py
    lines: [352, 358]
    snippet: "@validator('scheduled_time'): naive < utcnow()+timedelta(minutes=5)"
    test_file: null
    test_lines: null
  reason: "scheduled_time validator rejects past + 5-min floor"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-20
  source_finding: "04-5"
  status: DONE
  evidence:
    file: backend/routes/fare_split.py
    lines: [44, 47]
    snippet: "@validator('participant_phones', each_item=True): re.match(r'^\\+1\\d{10}$', v)"
    test_file: null
    test_lines: null
  reason: "Participant phones validated to +1 + 10 digits server-side"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-21
  source_finding: "04-6"
  status: DONE
  evidence:
    file: backend/schemas.py
    lines: [179, 180]
    snippet: "name: max_length=100; address: max_length=300; sanitize_string in handler"
    test_file: null
    test_lines: null
  reason: "Saved address fields capped + sanitized"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-22
  source_finding: "04-7"
  status: DONE
  evidence:
    file: backend/routes/wallet.py
    lines: [91]
    snippet: "amount: Decimal = Field(..., gt=0, le=500)"
    test_file: null
    test_lines: null
  reason: "WalletPayRequest amount le=500 cap matches TopUp"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-23
  source_finding: "04-8"
  status: DONE
  evidence:
    file: backend/routes/wallet.py
    lines: [86, 91]
    snippet: "TopUpRequest.amount: Decimal; WalletPayRequest.amount: Decimal"
    test_file: null
    test_lines: null
  reason: "Decimal type prevents IEEE 754 cents loss"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-24
  source_finding: "05-2"
  status: DONE
  evidence:
    file: rider-app/app/search-destination.tsx
    lines: [11, 322, 642]
    snippet: "KeyboardAvoidingView wraps FlatList; behavior='padding' iOS"
    test_file: null
    test_lines: null
  reason: "Button stays visible above keyboard"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-25
  source_finding: "05-8"
  status: PARTIAL
  evidence:
    file: rider-app/app/driver-arriving.tsx
    lines: [280, 499, 515]
    snippet: "allowFontScaling={false} on ETA, plate, OTP — coverage incomplete"
    test_file: null
    test_lines: null
  reason: "Priority screens partially covered; ride-options/activity/wallet not yet"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: ["WCAG"]
  effort_remaining_hours: 2
  duplicate_of: null
  notes: null

- id: rider-P2-26
  source_finding: "05-6"
  status: DONE
  evidence:
    file: rider-app/app/ride-in-progress.tsx
    lines: [249, 254, 265]
    snippet: "error && !currentRide → AlertCircle + retry button"
    test_file: null
    test_lines: null
  reason: "Error state + retry wired"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-27
  source_finding: "05-7"
  status: PENDING
  evidence:
    file: rider-app/app/driver-arrived.tsx
    lines: [128, 143]
    snippet: "{currentRide ? <MapView /> : null} — null path has no retry UI"
    test_file: null
    test_lines: null
  reason: "Conditional renders map; null/error case shows blank screen, no retry"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 0.5
  duplicate_of: null
  notes: null

- id: rider-P2-28
  source_finding: "05-9"
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/index.tsx
    lines: [520, 522]
    snippet: "mapControlButton: { width: 44, height: 44 }"
    test_file: null
    test_lines: null
  reason: "iOS HIG 44pt minimum met"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-29
  source_finding: "06-3"
  status: PENDING
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [411, 420]
    snippet: "fetchRide sets {currentRide, currentDriver} unconditionally — overwrites WS state"
    test_file: null
    test_lines: null
  reason: "Polling response stomps on fresher WS-updated driver location"
  owner: rider-app
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 1
  duplicate_of: null
  notes: "Causes marker jump-back on map"

- id: rider-P2-30
  source_finding: "06-6"
  status: PENDING
  evidence:
    file: rider-app/app/(tabs)/index.tsx
    lines: [88, 89]
    snippet: "if (status !== 'granted') return; — silent return"
    test_file: null
    test_lines: null
  reason: "Location denied gives no Alert / Open Settings link; map stuck Locating..."
  owner: rider-app
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0.5
  duplicate_of: null
  notes: null

- id: rider-P2-31
  source_finding: "07-5"
  status: PENDING
  evidence:
    file: rider-app/components/FreeCancelTimer.tsx
    lines: [8, 29, 54]
    snippet: "FreeCancelTimerProps has no onExpire prop; component display-only"
    test_file: null
    test_lines: null
  reason: "Timer emits no callback at expiry; cancel button not warned/disabled"
  owner: rider-app
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 1.5
  duplicate_of: null
  notes: null

- id: rider-P2-32
  source_finding: "08-4"
  status: DONE
  evidence:
    file: rider-app/app/payment-confirm.tsx
    lines: [96, 453, 459]
    snippet: "isBooking; button disabled when isLoading || isBooking; spinner"
    test_file: null
    test_lines: null
  reason: "Book button debounce + spinner during submission"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-33
  source_finding: "08-5"
  status: PENDING
  evidence:
    file: backend/routes/promotions.py
    lines: [33, 35]
    snippet: "ValidatePromoRequest accepts ride_fare: Decimal — client-supplied"
    test_file: null
    test_lines: null
  reason: "validate_promo accepts client fare; minimum-fare + discount calc inflation-attackable"
  owner: backend
  blocked_by: null
  confidence: high
  regulations: ["PCI-DSS"]
  effort_remaining_hours: 2.5
  duplicate_of: null
  notes: "P1-8 fixed apply_promo (server fare) but validate_promo (different endpoint) still trusts client"
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P2b module=rider items=16 done=10 partial=1 pending=5 blocked=0 unverifiable=0 superseded=0
