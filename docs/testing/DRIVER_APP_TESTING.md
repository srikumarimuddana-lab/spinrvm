# Driver App Testing Knowledge Document
## Spinr - React Native / Expo Driver Application

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Framework** | Jest 29 + jest-expo |
| **Component testing** | @testing-library/react-native |
| **Language** | TypeScript |
| **Test location** | `driver-app/__tests__/` |
| **Config file** | `driver-app/jest.config.js` |
| **Setup file** | `driver-app/jest.setup.js` |
| **Run command** | `cd driver-app && yarn test` |
| **Run with coverage** | `yarn test --coverage` |
| **Run in watch mode** | `yarn test:watch` |
| **Run single file** | `yarn test __tests__/store/driverStore.test.ts` |

---

## 2. Test Architecture

```
driver-app/
├── jest.config.js          # Jest config: preset, module mappings
├── jest.setup.js           # Global mocks: Firebase, SecureStore, AsyncStorage
├── __mocks__/
│   └── @shared/
│       ├── api/
│       │   └── client.js   # Mock API client (get, post, put, patch, delete)
│       └── config/
│           └── spinr.config.js  # Mock app config (countdown seconds, etc.)
├── __tests__/
│   └── store/
│       └── driverStore.test.ts  # 20 tests - Driver ride lifecycle
```

### Mock Strategy

| Mock | Why |
|------|-----|
| `@shared/api/client` | Shared API client (lives in `../shared/`, mocked via moduleNameMapper) |
| `@shared/config/spinr.config` | App configuration (ride offer countdown = 15s) |
| `expo-secure-store` | Native encrypted storage |
| `@react-native-async-storage/async-storage` | Native key-value storage |
| `@react-native-firebase/*` | Firebase native modules (messaging, crashlytics) |

---

## 3. Driver Ride State Machine

The driver app operates on a **state machine** that governs the entire ride lifecycle. This is the core logic being tested.

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
              ┌──────────┐    ride offer     ┌─────────────────┐
              │   idle   │ ───────────────▶  │  ride_offered   │
              └──────────┘                   │  (15s countdown)│
                    ▲                        └────────┬────────┘
                    │                          accept │  │ decline/timeout
                    │                                 ▼  │
                    │                    ┌─────────────────────────┐
                    │                    │  navigating_to_pickup   │
                    │                    └────────────┬────────────┘
                    │                                 │ arrive
                    │                                 ▼
                    │                    ┌─────────────────────────┐
                    │                    │   arrived_at_pickup     │
                    │                    │   (verify OTP)          │
                    │                    └────────────┬────────────┘
                    │                                 │ OTP verified
                    │                                 ▼
                    │                    ┌─────────────────────────┐
                    │   resetRideState   │   trip_in_progress      │
                    │◀───────────────────│                         │
                    │                    └────────────┬────────────┘
                    │                                 │ complete
                    │                                 ▼
                    │                    ┌─────────────────────────┐
                    └────────────────────│   trip_completed        │
                       after rating      │   (show fare summary)   │
                                        └─────────────────────────┘
```

---

## 4. Test File - Detailed Breakdown

### `driverStore.test.ts` - Driver Store (20 Tests)

**Source file:** `driver-app/store/driverStore.ts`

#### 4.1 Ride Offer & Countdown (4 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **setIncomingRide** | `should set incoming ride and change state to ride_offered` | State changes to `ride_offered`, countdown starts at 15s, ride data stored | WebSocket pushes new ride offer to driver |
| | `should reset to idle when set to null` | State resets to `idle`, countdown=0, ride cleared | Offer manually dismissed |
| **setCountdown** | `should update countdown seconds` | Countdown decrements correctly | Timer ticking down |
| | `should auto-decline when countdown reaches 0 during ride_offered` | Calls `POST /drivers/rides/{id}/decline` automatically | Driver didn't respond in 15 seconds |

**Business Rule:** Drivers get 15 seconds to accept a ride. If they don't respond, the ride is automatically declined and offered to the next driver.

---

#### 4.2 Ride Accept / Decline (3 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **acceptRide** | `should accept ride and transition to navigating_to_pickup` | POST `/drivers/rides/{id}/accept`, clears incoming ride, countdown=0, fetches active ride | Driver taps "Accept" |
| | `should handle accept error` | Error like "Ride already accepted" stored | Another driver accepted first |
| **declineRide** | `should decline ride and reset state` | POST `/drivers/rides/{id}/decline`, state=idle, incoming=null | Driver taps "Decline" |

**Cross-reference with Backend:**

| Driver Action | Backend Endpoint | Backend Test |
|---------------|------------------|--------------|
| Accept ride | `POST /api/v1/drivers/rides/{id}/accept` | `test_rides.py` → TestRideStatusUpdates |
| Decline ride | `POST /api/v1/drivers/rides/{id}/decline` | `test_rides.py` → TestRideMatching |

---

#### 4.3 Ride Completion & Cancellation (2 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **completeRide** | `should complete ride and transition to trip_completed` | POST `/drivers/rides/{id}/complete`, state=trip_completed, completed ride data stored, activeRide cleared | Driver taps "Complete Ride" at dropoff |
| **cancelRide** | `should cancel ride and reset state` | POST with URL-encoded reason, state=idle, all ride data cleared | Driver cancels (rider not found, emergency, etc.) |

---

#### 4.4 Active Ride Sync (4 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **fetchActiveRide** | `driver_assigned → navigating_to_pickup` | Correct state mapping from backend status | App resumes, syncs current ride state |
| | `driver_arrived → arrived_at_pickup` | Correct state mapping | Driver at pickup location |
| | `in_progress → trip_in_progress` | Correct state mapping | Trip is ongoing |
| | `no ride → clears activeRide` | Sets activeRide to null | No active ride on server |

**Why this matters:** If the driver app crashes or restarts, `fetchActiveRide()` re-syncs the state machine from the backend so the driver picks up where they left off.

**Backend status → Frontend state mapping:**
```
driver_assigned  →  navigating_to_pickup
driver_accepted  →  navigating_to_pickup
driver_arrived   →  arrived_at_pickup
in_progress      →  trip_in_progress
(no ride)        →  idle
```

---

#### 4.5 State Reset (1 test)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **resetRideState** | `should reset all ride-related state` | rideState=idle, all ride fields null, countdown=0, error cleared | After rating, return to dashboard |

---

#### 4.6 Earnings & History (2 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **fetchEarnings** | `should fetch earnings for a period` | GET `/drivers/earnings?period=day`, earnings data stored | Driver views today's earnings |
| **fetchRideHistory** | `should fetch ride history with pagination` | GET `/drivers/rides/history?limit=10&offset=0`, rides array + total count stored | Driver scrolls through past rides |

**Cross-reference with Backend:**

| Driver Action | Backend Endpoint | Backend Test |
|---------------|------------------|--------------|
| View earnings | `GET /api/v1/drivers/earnings` | `test_drivers.py` → TestDriverStats |
| View history | `GET /api/v1/drivers/rides/history` | `test_rides.py` → TestRideHistory |

---

#### 4.7 Bank Account / Payouts (2 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **fetchBankAccount** | `should fetch bank account` | GET `/drivers/bank-account`, hasBankAccount flag + bank details stored | Driver opens payout settings |
| | `should delete bank account` | DELETE `/drivers/bank-account`, hasBankAccount=false, bankAccount=null | Driver removes bank account |

---

#### 4.8 Rider Rating & Error (2 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **rateRider** | `should submit rider rating` | POST `/drivers/rides/{id}/rate-rider` with rating + comment | Driver rates the passenger after trip |
| **clearError** | `should clear error` | Error reset to null | User dismisses error message |

---

## 5. End-to-End Driver Ride Flow - Test Coverage Map

```
Step 1: Driver goes online
  └── Frontend authStore: "updateDriverStatus"
  └── Backend: test_drivers.py → TestDriverAvailability

Step 2: Ride offer arrives via WebSocket
  └── driverStore.test.ts → "setIncomingRide" (2 tests)

Step 3: 15-second countdown
  └── driverStore.test.ts → "setCountdown" (2 tests)

Step 4a: Driver accepts
  └── driverStore.test.ts → "acceptRide" (2 tests)
  └── Backend: test_rides.py → TestRideStatusUpdates

Step 4b: Driver declines (or timeout)
  └── driverStore.test.ts → "declineRide" (1 test)
  └── driverStore.test.ts → "setCountdown auto-decline" (1 test)

Step 5: Navigate to pickup
  └── driverStore.test.ts → "fetchActiveRide driver_assigned" (1 test)

Step 6: Arrive at pickup, verify OTP
  └── driverStore.test.ts → "fetchActiveRide driver_arrived" (1 test)
  └── Backend: test_rides.py → TestRideStatusUpdates

Step 7: Trip in progress
  └── driverStore.test.ts → "fetchActiveRide in_progress" (1 test)

Step 8: Complete trip
  └── driverStore.test.ts → "completeRide" (1 test)
  └── Backend: test_rides.py → TestRideStatusUpdates

Step 9: Rate rider
  └── driverStore.test.ts → "rateRider" (1 test)
  └── Backend: test_rides.py → TestRideRatings

Step 10: View earnings
  └── driverStore.test.ts → "fetchEarnings" (1 test)
  └── Backend: test_drivers.py → TestDriverStats

Step 11: Return to idle
  └── driverStore.test.ts → "resetRideState" (1 test)
```

---

## 6. Gaps & Future Tests to Add

| Area | What's Missing | Priority |
|------|---------------|----------|
| `arriveAtPickup` | Haversine distance validation (100m radius check) | High |
| `verifyOTP` | OTP verification flow + state transition | High |
| `startRide` | Start ride state transition | High |
| `documentStore` | Document upload, requirements fetching | Medium |
| `languageStore` | Language switching, AsyncStorage persistence | Low |
| `useDriverDashboard` hook (location) | Location tracking, permission handling, batch upload | High |
| `useDriverDashboard` hook (WebSocket) | WebSocket connection, reconnection, message handling | High |
| `ActiveRidePanel` component | UI rendering for each ride state | Medium |
| Payout flow | `setBankAccount`, `requestPayout`, `fetchPayoutHistory` | Medium |
| T4A tax documents | `fetchT4ASummaries`, `fetchT4ADetails` | Low |
