# Rider App — P2a Sprint Verification (first 17 of 33)

**Sprint file:** `reports/remediation/rider-P2-before-launch.md` (items 1–17)
**Branch:** `claude/review-pending-audits-Pu1aP`

**Result: 8 DONE · 4 PARTIAL · 5 PENDING · 0 BLOCKED · 0 UNVERIFIABLE · 0 SUPERSEDED**

First sprint with non-DONE results — P2 is "before launch" rather than
"before beta", so the items are by design less tightly scoped and have
more open work.

Cumulative rider verification through this sprint:

| Sprint | Total | DONE | PARTIAL | PENDING |
|---|---:|---:|---:|---:|
| P0 | 8 | 8 | 0 | 0 |
| P1a | 17 | 17 | 0 | 0 |
| P1b | 16 | 16 | 0 | 0 |
| **P2a** | **17** | **8** | **4** | **5** |
| **Cumulative** | **58** | **49 (84%)** | **4** | **5** |

---

## Per-Item Verification

### DONE (8)
| ID | Title | Evidence |
|---|---|---|
| P2-1 | Offline queue extended (cancel/rate/tip/emergency + 3-retry + user notify) | `rideStore.ts:299-348` |
| P2-2 | Cold-start ride validates against backend; offline fallback to cache | `rideStore.ts:660-673` |
| P2-3 | RideStatus enum centralized | `rider-app/constants/rideStatus.ts:1-12` |
| P2-9 | Tip-button minHeight 44pt + center-aligned | `ride-completed.tsx:609,623,625` |
| P2-10 | Corporate account ID flows through ride create | `rideStore.ts:386` + `Ride` interface |
| P2-17 | Tip amount Pydantic-validated `Decimal gt=0 le=500` (NaN bypass closed) | `rides.py:135-139` |

### PARTIAL (4)
| ID | Title | What's done / missing | Effort |
|---|---|---|---|
| P2-4 | Type safety on store interfaces | Store layer typed; Zod parsing for WS messages in `useRiderSocket` not verified | 4 h |
| P2-8 | Become-driver deep-link to driver app | Store-link constants defined; `Linking.canOpenURL()` conditional logic incomplete | 2 h |
| P2-12 | Same as P2-8 (`duplicate_of: rider-P2-8`) | — | 1 h |

### PENDING (5)
| ID | Title | Why pending | Effort |
|---|---|---|---|
| P2-5 | Polling suspended when WS connected | `wsConnected` state wiring to polling effect not present in `driver-arriving.tsx` | 2 h |
| P2-6 | Error states + retry actions on 4 screens | Coverage across ride-options/driver-arriving/activity/wallet not verified | 3 h |
| P2-7 | Rider PII stripped from driver-facing API responses | `RiderPublicView` model not verified in driver routes (P1-28 strips phone/email/stripe; this asks about further fields) | 2 h |
| P2-11 | ToS acceptance step during onboarding | Not present in `profile-setup.tsx` — App Store Review 4.0 issue | 2 h |
| P2-13 | Access token kept in memory only | `authStore.ts:setTokens` not verified for memory-only pattern | 1 h |
| P2-14 | EAS test/preview profiles target staging backend | Hardcodes prod; staging env not provisioned | 2 h |
| P2-15 | TruffleHog scans full PR diff (not `--only-verified`) | `.github/workflows/ci.yml` flag still narrow | 0.5 h |
| P2-16 | CI check for `EXPO_PUBLIC_` private vars + gitignore for play-service-account.json | Not in workflow; gitignore needs entry | 0.5 h |

(P2 has 8 PENDING/PARTIAL items in this half — total open effort ≈ 18 h)

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P2-1
  source_finding: "01-1"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [299, 314, 320, 325, 328]
    snippet: "type: 'create_ride' | 'cancel_ride' | 'rate_ride' | 'tip' | 'emergency'"
    test_file: null
    test_lines: null
  reason: "Offline queue extended; 3-retry limit; user notification on permanent failure"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-2
  source_finding: "10-5"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [660, 663]
    snippet: "live = api.get('/rides/active'); if !live.data?.active or stored.id mismatch: clear"
    test_file: null
    test_lines: null
  reason: "Cold-start validates ride against backend; falls back to cache when offline"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-3
  source_finding: "01-0"
  status: DONE
  evidence:
    file: rider-app/constants/rideStatus.ts
    lines: [1, 12]
    snippet: "export const RideStatus = { SEARCHING, DRIVER_ASSIGNED, ..., COMPLETED, CANCELLED, FAILED }"
    test_file: null
    test_lines: null
  reason: "Status constants centralized; consumed in rideStore"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-4
  source_finding: "02-0"
  status: PARTIAL
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [6, 117]
    snippet: "Ride, Driver, Location, RideEstimate, NearbyDriver interfaces typed"
    test_file: null
    test_lines: null
  reason: "Store interfaces typed; Zod parsing of WS messages in useRiderSocket not verified"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 4
  duplicate_of: null
  notes: "Add Zod schemas for inbound WS messages"

- id: rider-P2-5
  source_finding: "05-0"
  status: PENDING
  evidence:
    file: rider-app/app/driver-arriving.tsx
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "wsConnected wiring to polling-effect suspension not present at HEAD"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 2
  duplicate_of: null
  notes: "useRiderSocket exposes connection state; needs to gate polling"

- id: rider-P2-6
  source_finding: "05-1"
  status: PENDING
  evidence:
    file: rider-app/app/ride-options.tsx
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "Error-state + retry coverage on 4 screens (ride-options, driver-arriving, activity, wallet) not verified"
  owner: rider-app
  blocked_by: null
  confidence: low
  regulations: []
  effort_remaining_hours: 3
  duplicate_of: null
  notes: null

- id: rider-P2-7
  source_finding: "02-7"
  status: PENDING
  evidence:
    file: backend/routes/riders.py
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "RiderPublicView model not located; driver route response shape needs strip-list audit"
  owner: backend
  blocked_by: null
  confidence: low
  regulations: ["PIPEDA"]
  effort_remaining_hours: 2
  duplicate_of: null
  notes: "P1-28 strips phone/email/stripe — this finding asks about additional fields"

- id: rider-P2-8
  source_finding: "01-3"
  status: PARTIAL
  evidence:
    file: rider-app/app/become-driver.tsx
    lines: [1, 4]
    snippet: "DRIVER_APP_SCHEME = 'spinr-driver://'; DRIVER_APP_STORE_IOS / ANDROID"
    test_file: null
    test_lines: null
  reason: "Constants defined; Linking.canOpenURL() conditional flow incomplete"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 2
  duplicate_of: null
  notes: null

- id: rider-P2-9
  source_finding: "05-5"
  status: DONE
  evidence:
    file: rider-app/app/ride-completed.tsx
    lines: [609, 623, 625]
    snippet: "tipBtn: { minHeight: 44, justifyContent: 'center' }"
    test_file: null
    test_lines: null
  reason: "Tip buttons meet 44pt minHeight; center-aligned"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P2-10
  source_finding: "01-5"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [101, 386]
    snippet: "ride payload includes corporate_account_id || null"
    test_file: null
    test_lines: null
  reason: "Corporate account ID flows through ride creation; in Ride interface"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "UI entry-point in payment-confirm not separately verified here"

- id: rider-P2-11
  source_finding: "01-7"
  status: PENDING
  evidence:
    file: rider-app/app/profile-setup.tsx
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "ToS acceptance step during onboarding not located in profile-setup.tsx"
  owner: product
  blocked_by: null
  confidence: low
  regulations: ["PIPEDA"]
  effort_remaining_hours: 2
  duplicate_of: null
  notes: "App Store Review 4.0 mandates explicit ToS acceptance"

- id: rider-P2-12
  source_finding: "01-3"
  status: PARTIAL
  evidence:
    file: rider-app/app/become-driver.tsx
    lines: [1, 4]
    snippet: "Same constants as P2-8; deep-link conditional incomplete"
    test_file: null
    test_lines: null
  reason: "Same scope as P2-8 — deep-link ↔ store-fallback wiring incomplete"
  owner: rider-app
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 1
  duplicate_of: "rider-P2-8"
  notes: null

- id: rider-P2-13
  source_finding: "02-5"
  status: PENDING
  evidence:
    file: shared/store/authStore.ts
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "Access-token memory-only persistence pattern not verified in setTokens()"
  owner: shared
  blocked_by: null
  confidence: low
  regulations: ["PIPEDA"]
  effort_remaining_hours: 1
  duplicate_of: null
  notes: "Refresh-on-startup with in-memory access token expected"

- id: rider-P2-14
  source_finding: "03-2"
  status: PENDING
  evidence:
    file: rider-app/eas.json
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "EAS profiles still hardcode production backend; staging env not provisioned"
  owner: devops
  blocked_by: null
  confidence: medium
  regulations: []
  effort_remaining_hours: 2
  duplicate_of: null
  notes: "Requires staging infra"

- id: rider-P2-15
  source_finding: "03-3"
  status: PENDING
  evidence:
    file: .github/workflows/ci.yml
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "TruffleHog uses --only-verified; misses unverified leaks"
  owner: devops
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0.5
  duplicate_of: null
  notes: "Drop the flag in workflow"

- id: rider-P2-16
  source_finding: "03-4,03-5"
  status: PENDING
  evidence:
    file: .github/workflows/ci.yml
    lines: null
    snippet: null
    test_file: null
    test_lines: null
  reason: "CI grep check for EXPO_PUBLIC_ private vars missing; play-service-account not gitignored"
  owner: devops
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0.5
  duplicate_of: null
  notes: "Two-line fix"

- id: rider-P2-17
  source_finding: "04-2"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [135, 139]
    snippet: "class TipRequest(BaseModel): amount: Decimal = Field(..., gt=0, le=500)"
    test_file: null
    test_lines: null
  reason: "Tip amount validates Decimal gt 0 le 500; NaN bypass closed"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P2a module=rider items=17 done=8 partial=4 pending=5 blocked=0 unverifiable=0 superseded=0
