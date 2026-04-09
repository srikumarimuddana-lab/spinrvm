# Frontend (Rider App) Testing Knowledge Document
## Spinr - React Native / Expo Rider Application

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Framework** | Jest 29 + jest-expo |
| **Component testing** | @testing-library/react-native |
| **Language** | TypeScript |
| **Test location** | `frontend/__tests__/` |
| **Config file** | `frontend/jest.config.js` |
| **Setup file** | `frontend/jest.setup.js` |
| **Run command** | `cd frontend && yarn test` |
| **Run with coverage** | `yarn test --coverage` |
| **Run in watch mode** | `yarn test:watch` |
| **Run single file** | `yarn test __tests__/store/rideStore.test.ts` |

---

## 2. Test Architecture

```
frontend/
├── jest.config.js          # Jest config: preset, mocks, ignore patterns
├── jest.setup.js           # Global mocks: SecureStore, Firebase, AsyncStorage, Cache
├── __mocks__/
│   └── @shared/
│       └── cache.js        # Mock for shared cache module
├── __tests__/
│   └── store/
│       ├── authStore.test.ts   # 13 tests - Authentication flows
│       └── rideStore.test.ts   # 20 tests - Ride booking flows
```

### Mock Strategy
The rider app depends on native modules and external services that must be mocked:

| Mock | Why |
|------|-----|
| `expo-secure-store` | Native encrypted storage (not available in Node.js) |
| `@react-native-async-storage/async-storage` | Native key-value storage |
| `firebase/auth` | Firebase phone authentication |
| `./config/firebaseConfig` | Firebase app initialization |
| `@shared/cache` | Cross-app caching utility (lives outside project root) |
| `../../api/client` | API HTTP client (mocked per test to control responses) |

---

## 3. Test Files - Detailed Breakdown

### 3.1 `authStore.test.ts` - Authentication Store (13 Tests)

**Source file:** `frontend/store/authStore.ts`

**Application Flow:**
```
App opens → Initialize auth → Check Firebase state
  → Logged in?  → Fetch user profile → Set token → Ready
  → Logged out? → Show login screen

Login: Enter phone → Send OTP → Verify OTP → Firebase signs in
  → New user? → Create profile screen → POST /users/profile
  → Has driver account? → Fetch driver profile → Enable driver mode toggle
```

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **Initial State** | `should have correct initial state` | Store starts with null user, null token, no loading, no error | App fresh launch |
| **Create Profile** | `should create profile successfully` | POST to `/users/profile` stores returned user data, clears loading | New rider filling out name/email after OTP |
| | `should handle profile creation error` | Error like "Phone already registered" is stored, loading cleared, error thrown | Duplicate phone number attempt |
| **Register Driver** | `should register driver and update user role` | POST to `/drivers/register`, user role changes to "driver", `is_driver=true`, driver mode ON | Rider upgrading to driver account |
| **Toggle Driver Mode** | `should toggle driver mode on when driver data exists` | Switches `isDriverMode` from false to true | Rider tapping "Switch to Driver" |
| | `should toggle driver mode off` | Switches `isDriverMode` from true to false | Driver switching back to rider view |
| **Update Driver Status** | `should update driver online status` | POST to `/drivers/status?is_online=true`, local state updated | Driver going online to accept rides |
| **Logout** | `should clear all auth state` | User, driver, token, driverMode all cleared | User tapping logout |
| **Clear Error** | `should clear error state` | Error reset to null | User dismissing error toast |

**Cross-reference with Backend APIs:**

| Frontend Action | Backend Endpoint | Backend Test File |
|----------------|------------------|-------------------|
| Create profile | `POST /api/v1/users/profile` | `test_auth.py` |
| Register driver | `POST /api/v1/drivers/register` | `test_drivers.py` → TestDriverRegistration |
| Update driver status | `POST /api/v1/drivers/status` | `test_drivers.py` → TestDriverAvailability |
| Logout | Firebase `signOut()` | `test_auth.py` → TestFirebaseIntegration |

---

### 3.2 `rideStore.test.ts` - Ride Booking Store (20 Tests)

**Source file:** `frontend/store/rideStore.ts`

**Application Flow:**
```
Rider opens app → Select pickup (GPS or search) → Select dropoff
  → Add optional stops → Fetch ride estimates (per vehicle type)
  → Select vehicle type → Choose payment method → Create ride
  → Wait for driver match → Driver assigned → Track driver on map
  → Driver arrives → OTP verification → Trip in progress
  → Trip complete → Rate driver → Tip (optional)
```

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **Set Pickup / Dropoff** | `should set pickup location` | Pickup stored with address, lat, lng | Rider taps map or selects from search |
| | `should set dropoff location` | Dropoff stored correctly | Rider searches for destination |
| | `should clear pickup when set to null` | Pickup reset to null | Rider clears the pickup field |
| **Stops Management** | `should add a stop` | Stop added to array with correct data | Rider adds intermediate stop |
| | `should add multiple stops` | Multiple stops accumulate in order | "Add another stop" tapped multiple times |
| | `should remove a stop by index` | Correct stop removed, others shift | Rider removes a stop |
| | `should update a stop at specific index` | In-place update works | Rider edits an existing stop address |
| **Fetch Estimates** | `should not fetch if pickup is missing` | No API call made | Only dropoff selected |
| | `should not fetch if dropoff is missing` | No API call made | Only pickup selected |
| | `should fetch estimates when both set` | POST `/rides/estimate` with correct lat/lng, estimates stored | Both locations selected, estimates screen shown |
| | `should handle estimate fetch error` | Error stored, loading cleared | Network timeout or backend error |
| **Create Ride** | `should throw if missing ride details` | Error thrown if pickup/dropoff/vehicle missing | Coding safeguard |
| | `should create a ride successfully` | POST `/rides` with all params, ride stored as currentRide | Rider taps "Confirm Ride" |
| **Cancel Ride** | `should do nothing if no current ride` | No API call made | Safety check |
| | `should cancel current ride` | POST `/rides/{id}/cancel`, currentRide and currentDriver cleared | Rider cancels before driver arrives |
| **Clear Ride** | `should reset all ride-related state` | All ride fields reset (pickup, dropoff, stops, estimates, ride, driver) | Ride completed or back to home screen |
| **Recent Searches** | `should add a recent search` | Location added to recents list | Rider selects a destination |
| | `should avoid duplicate addresses` | Same address not added twice | Rider picks same place again |
| | `should keep max 10 recent searches` | List capped at 10, oldest removed | History doesn't grow unbounded |
| | `should clear recent searches` | Empty list after clear | Rider clears search history |
| **Select Vehicle** | `should set selected vehicle` | Vehicle type stored | Rider picks "Standard" or "XL" |
| **Clear Error** | `should clear error state` | Error reset | Dismiss error |
| **Rate Ride** | `should call rate endpoint` | POST `/rides/{id}/rate` with rating, comment, tip | Rider rates after completion |
| | `should default tip to 0` | Tip defaults to 0 when not provided | Rider rates without tipping |

**Cross-reference with Backend APIs:**

| Frontend Action | Backend Endpoint | Backend Test File |
|----------------|------------------|-------------------|
| Fetch estimates | `POST /api/v1/rides/estimate` | `test_rides.py` → TestFareCalculation |
| Create ride | `POST /api/v1/rides` | `test_rides.py` → TestRideCreation |
| Cancel ride | `POST /api/v1/rides/{id}/cancel` | `test_rides.py` → TestRideEndpoints |
| Rate ride | `POST /api/v1/rides/{id}/rate` | `test_rides.py` → TestRideRatings |

---

## 4. End-to-End Ride Booking Flow - Test Coverage Map

This maps the complete rider journey to which tests cover each step:

```
Step 1: Open app, authenticate
  └── authStore.test.ts → "initial state", "create profile"

Step 2: Set pickup location
  └── rideStore.test.ts → "should set pickup location"

Step 3: Set dropoff location
  └── rideStore.test.ts → "should set dropoff location"

Step 4: (Optional) Add stops
  └── rideStore.test.ts → "stops management" (4 tests)

Step 5: View ride estimates
  └── rideStore.test.ts → "fetchEstimates" (4 tests)
  └── Backend: test_rides.py → TestFareCalculation (5 tests)

Step 6: Select vehicle type
  └── rideStore.test.ts → "should set selected vehicle"

Step 7: Confirm ride
  └── rideStore.test.ts → "createRide" (2 tests)
  └── Backend: test_rides.py → TestRideCreation

Step 8: Wait for driver / Cancel
  └── rideStore.test.ts → "cancelRide" (2 tests)

Step 9: Trip completes
  └── Backend: test_rides.py → TestRideStatusUpdates

Step 10: Rate and tip
  └── rideStore.test.ts → "rateRide" (2 tests)
  └── Backend: test_rides.py → TestRideRatings
```

---

## 5. How to Add New Frontend Tests

### Test a new store function:
```typescript
// frontend/__tests__/store/rideStore.test.ts
describe('newFunction', () => {
  it('should do something', async () => {
    // 1. Mock API response
    api.post.mockResolvedValueOnce({ data: { ... } });
    
    // 2. Set up initial state
    useRideStore.setState({ pickup: { address: 'Test', lat: 50, lng: -104 } });
    
    // 3. Call the function
    await useRideStore.getState().newFunction();
    
    // 4. Assert state changed
    expect(useRideStore.getState().someField).toBe(expectedValue);
    
    // 5. Assert API was called correctly
    expect(api.post).toHaveBeenCalledWith('/expected/endpoint', expectedBody);
  });
});
```

### Test a new component:
```typescript
// frontend/__tests__/components/SomeComponent.test.tsx
import { render, fireEvent } from '@testing-library/react-native';
import SomeComponent from '../../components/SomeComponent';

describe('SomeComponent', () => {
  it('should render correctly', () => {
    const { getByText } = render(<SomeComponent />);
    expect(getByText('Expected Text')).toBeTruthy();
  });

  it('should handle button press', () => {
    const onPress = jest.fn();
    const { getByText } = render(<SomeComponent onPress={onPress} />);
    fireEvent.press(getByText('Button'));
    expect(onPress).toHaveBeenCalled();
  });
});
```

---

## 6. Gaps & Future Tests to Add

| Area | What's Missing | Priority |
|------|---------------|----------|
| `documentStore` | Document upload, requirements fetching, cache behavior | High |
| `driverStore` (frontend) | Driver-side ride acceptance from rider app perspective | Medium |
| `api/client.ts` | URL resolution, auth header injection, error parsing | High |
| `api/upload.ts` | File upload with FormData | Medium |
| `AppMap` component | Map rendering, marker placement | Low |
| Screen components | Login screen, ride booking screen, rate screen | Medium |
