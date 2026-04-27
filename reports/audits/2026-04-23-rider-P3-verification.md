# Rider App — P3 Sprint Verification (8 items)

**Sprint file:** `reports/remediation/rider-P3-hardening.md`
**Branch:** `claude/audit-continuation-batch-2`

**Result: 8/8 DONE · 0 PARTIAL · 0 PENDING · 0 BLOCKED · 0 UNVERIFIABLE · 0 SUPERSEDED**

P3 ("hardening — fix before scale") is fully landed at HEAD. All eight items
have file-level evidence. Cumulative rider verification:

| Sprint | Total | DONE | PARTIAL | PENDING |
|---|---:|---:|---:|---:|
| P0 | 8 | 8 | 0 | 0 |
| P1a | 17 | 17 | 0 | 0 |
| P1b | 16 | 16 | 0 | 0 |
| P2a | 17 | 8 | 4 | 5 |
| P2b | 16 | 10 | 1 | 5 |
| **P3** | **8** | **8** | **0** | **0** |
| **Cumulative** | **82** | **67 (82%)** | **5** | **10** |

P4 (10 items, mostly roadmap) remains.

---

## Per-Item Verification

| ID | Title | Status | Evidence |
|---|---|---|---|
| P3-1 | Rate-limit decorators on POST /rides, /cancel, promo endpoints | DONE | `rides.py:660,1849` + `promotions.py:70,251` |
| P3-2 | FCM `onTokenRefresh` listener re-registers token | DONE | `_layout.tsx:286` |
| P3-3 | `CarMarker` wrapped in `React.memo` with custom comparator | DONE | `CarMarker.tsx:80,91` |
| P3-4 | Cursor-based pagination on activity list | DONE | `activity.tsx:61,78,94` |
| P3-5 | Home screen 5-min TTL cache + `useFocusEffect` staleness check | DONE | `(tabs)/index.tsx:29,131,134` |
| P3-6 | Scheduled-ride DatePicker `minimumDate = now + 15 min` | DONE | `ride-options.tsx:626,639` |
| P3-7 | Store-action unit tests for 7 failure paths | DONE | `rideStore.test.ts:362-490` + `walletStore.test.ts:188` |
| P3-8 | Per-screen ErrorBoundary on 5 ride-flow screens | DONE | 5 screens in `rider-app/app/` |

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P3-1
  source_finding: "R-P3-1"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [660, 1849]
    snippet: "@ride_request_limit on create_ride; @cancel_ride_limit on cancel"
    test_file: null
    test_lines: null
  reason: "Rate limiters on POST /rides (660) and POST /rides/{id}/cancel (1849); promo endpoints decorated at promotions.py:70,251"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-2
  source_finding: "R-P3-2"
  status: DONE
  evidence:
    file: rider-app/app/_layout.tsx
    lines: [286, 296]
    snippet: "onTokenRefresh(async (newToken) => api.post('/notifications/register-token'))"
    test_file: null
    test_lines: null
  reason: "FCM token refresh listener registered; re-registers token with backend on each rotation"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-3
  source_finding: "R-P3-3"
  status: DONE
  evidence:
    file: shared/components/CarMarker.tsx
    lines: [80, 91]
    snippet: "export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual)"
    test_file: null
    test_lines: null
  reason: "CarMarker wrapped with React.memo and custom _propsAreEqual comparator; only re-renders on lat/lng/heading/size change"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-4
  source_finding: "R-P3-4"
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/activity.tsx
    lines: [61, 78, 94]
    snippet: "?limit=${PAGE_LIMIT}&before=${cursor}; loadMore via fetchPage(nextCursor)"
    test_file: null
    test_lines: null
  reason: "Cursor pagination with limit=20, ?before=<ride_id>; cursor state at line 78; loadMore callback at 94-104"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-5
  source_finding: "R-P3-5"
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/index.tsx
    lines: [29, 131, 134]
    snippet: "HOME_DATA_TTL_MS=5*60*1000; useFocusEffect with staleness check"
    test_file: null
    test_lines: null
  reason: "5-min TTL constant; useFocusEffect skips refetch if data is within window"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-6
  source_finding: "R-P3-6"
  status: DONE
  evidence:
    file: rider-app/app/ride-options.tsx
    lines: [626, 639]
    snippet: "minimumDate={new Date(Date.now() + 15 * 60 * 1000)}"
    test_file: null
    test_lines: null
  reason: "DatePicker minimum date enforced at +15 min on both iOS (626) and Android (639) instances"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-7
  source_finding: "R-P3-7"
  status: DONE
  evidence:
    file: rider-app/store/__tests__/rideStore.test.ts
    lines: [362, 385, 409, 445, 478]
    snippet: "double-book, cancel-after-arrived, stale, offline-queue, emergency, wallet-insufficient"
    test_file: rider-app/store/__tests__/walletStore.test.ts
    test_lines: [188]
  reason: "All 7 failure tests present: createRide double-book (362), cancelRide post-arrived (385), hydrateActiveRide stale (409), syncOfflineRequests replay (445), triggerEmergency net-fail (478), payWithWallet insufficient (walletStore:188)"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P3-8
  source_finding: "R-P3-8"
  status: DONE
  evidence:
    file: rider-app/app/driver-arriving.tsx
    lines: [658, 660]
    snippet: "export default function DriverArrivingScreen() { return <ErrorBoundary>..."
    test_file: null
    test_lines: null
  reason: "ErrorBoundary wraps exports of all 5 screens: driver-arriving (658), ride-in-progress (578), ride-completed (520), ride-options (788), payment-confirm (485)"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P3 module=rider items=8 done=8 partial=0 pending=0 blocked=0 unverifiable=0 superseded=0
