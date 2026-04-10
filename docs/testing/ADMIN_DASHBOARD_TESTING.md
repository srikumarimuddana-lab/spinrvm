# Admin Dashboard Testing Knowledge Document
## Spinr - Next.js Admin Panel

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Framework** | Vitest 4.1 |
| **Component testing** | @testing-library/react |
| **DOM matchers** | @testing-library/jest-dom |
| **Language** | TypeScript |
| **Test location** | `admin-dashboard/src/__tests__/` |
| **Config file** | `admin-dashboard/vitest.config.ts` |
| **Setup file** | `admin-dashboard/vitest.setup.ts` |
| **Run command** | `cd admin-dashboard && npm test` |
| **Run with coverage** | `npm run test:coverage` |
| **Run in watch mode** | `npm run test:watch` |
| **Run single file** | `npx vitest run src/__tests__/lib/utils.test.ts` |

---

## 2. Test Architecture

```
admin-dashboard/
├── vitest.config.ts        # Vitest config: jsdom environment, path aliases, coverage
├── vitest.setup.ts         # Global setup: jest-dom matchers, localStorage mock, fetch mock
├── src/
│   └── __tests__/
│       ├── lib/
│       │   ├── utils.test.ts       # 14 tests - Utility functions
│       │   └── export-csv.test.ts  #  6 tests - CSV export
│       └── store/
│           └── authStore.test.ts   # 10 tests - Admin authentication
```

### Test Environment Setup (`vitest.setup.ts`)

| Setup | Purpose |
|-------|---------|
| `@testing-library/jest-dom/vitest` | Adds DOM matchers (toBeInTheDocument, toHaveClass, etc.) |
| `localStorage` mock | In-memory localStorage for store persistence testing |
| `global.fetch` mock | Mocked fetch for API call testing |
| `URL.createObjectURL` mock | Mocked for file download testing (CSV export) |

---

## 3. Test Files - Detailed Breakdown

### 3.1 `utils.test.ts` - Utility Functions (14 Tests)

**Source file:** `admin-dashboard/src/lib/utils.ts`

These utility functions are used throughout every admin dashboard page.

#### `cn()` - Tailwind CSS Class Merger (4 tests)

| Test Case | Input | Expected Output | Used In |
|-----------|-------|-----------------|---------|
| `should merge class names` | `cn('px-4', 'py-2')` | `'px-4 py-2'` | Every component |
| `should handle conflicting tailwind classes` | `cn('px-4', 'px-6')` | `'px-6'` (last wins) | Conditional styling |
| `should handle conditional classes` | `cn('base', false && 'hidden', 'extra')` | `'base extra'` | Toggle visibility |
| `should handle undefined/null inputs` | `cn('base', undefined, null)` | `'base'` | Optional props |

#### `formatCurrency()` - CAD Currency Formatting (4 tests)

| Test Case | Input | Expected Output | Used In |
|-----------|-------|-----------------|---------|
| `should format as CAD currency` | `15.5` | Contains `$` and `15.50` | Earnings, fare display, subscription pricing |
| `should format zero` | `0` | Contains `0.00` | Empty earnings |
| `should format large amounts` | `1250.99` | Contains `1,250.99` | Revenue reports |
| `should format negative amounts` | `-10` | Contains `10.00` | Refunds |

**Business context:** Spinr operates in Canada (Saskatchewan-first). All monetary values are in CAD and formatted with `en-CA` locale.

#### `formatDate()` - Date Formatting (4 tests)

| Test Case | Input | Expected Output | Used In |
|-----------|-------|-----------------|---------|
| `should format valid date string` | `'2024-06-15T14:30:00Z'` | Formatted date string | Ride timestamps, user join dates |
| `should return dash for null` | `null` | `'—'` | Missing date fields |
| `should return dash for undefined` | `undefined` | `'—'` | Optional date fields |
| `should handle Date objects` | `new Date('2024-01-15')` | Formatted date string | JavaScript Date inputs |

#### `statusColor()` - Ride/Ticket Status Badge Colors (5 tests)

| Test Case | Input Status | Expected Color | Badge Appears On |
|-----------|-------------|---------------|-----------------|
| `searching` | `'searching'` | `yellow` | Ride list - looking for driver |
| `completed` | `'completed'` | `green` | Ride list - finished rides |
| `cancelled` | `'cancelled'` | `red` | Ride list - cancelled rides |
| `in_progress` | `'in_progress'` | `emerald` | Ride list - active rides |
| `unknown_status` | `'unknown'` | `zinc` (default) | Fallback for new statuses |

**Complete status-to-color mapping used in the app:**
```
searching        → yellow    (looking for driver)
driver_assigned  → blue      (driver matched)
driver_arrived   → indigo    (at pickup)
in_progress      → emerald   (trip active)
completed        → green     (finished)
cancelled        → red       (cancelled)
scheduled        → purple    (future ride)
open             → yellow    (ticket open)
closed           → zinc      (ticket closed)
```

---

### 3.2 `export-csv.test.ts` - CSV Export (6 Tests)

**Source file:** `admin-dashboard/src/lib/export-csv.ts`

**Application Flow:**
```
Admin views a data table (rides, drivers, earnings, etc.)
  → Clicks "Export CSV" button
  → exportToCsv() generates CSV string from data
  → Creates Blob → Triggers browser download
```

| Test Case | What It Verifies | App Scenario |
|-----------|------------------|--------------|
| `should do nothing for empty rows` | No download triggered if data is empty | Empty table export attempt |
| `should create and trigger download` | Creates link element, clicks it, cleans up DOM | Normal export of driver list |
| `should use custom columns` | Only specified columns exported with custom labels | Export with column selection |
| `should handle null and undefined values` | Null/undefined converted to empty string, no crash | Missing data in export |
| `should escape quotes in values` | Double-quotes escaped as `""` per CSV spec | Names with quotes: `John "JD" Doe` |
| `should handle object values` | Objects stringified as JSON | Metadata fields in export |

**Pages using CSV export:**
- Drivers page → Export driver list
- Rides page → Export ride history
- Earnings page → Export financial reports
- Users page → Export rider list

---

### 3.3 `authStore.test.ts` - Admin Authentication (10 Tests)

**Source file:** `admin-dashboard/src/store/authStore.ts`

**Application Flow:**
```
Admin opens dashboard → Check localStorage for saved session
  → Token found? → Validate with backend (GET /api/admin/auth/session)
    → Valid? → Set user + authenticated state → Show dashboard
    → Invalid/expired? → Clear state → Redirect to /login
  → No token? → Show login page

Login: Admin enters email + password → POST /api/admin/auth/login
  → Success → Store token + user in Zustand + localStorage → Redirect to /dashboard
  → Failure → Show error message
```

#### State Management (4 tests)

| Test Group | Test Case | What It Verifies | App Scenario |
|------------|-----------|------------------|--------------|
| **Initial State** | `should start unauthenticated` | user=null, token=null, isAuthenticated=false | Fresh page load |
| **setUser** | `should set user and mark as authenticated` | User stored, isAuthenticated=true, isLoading=false | Successful login |
| | `should clear authentication when user is null` | isAuthenticated=false when user cleared | Session expired |
| **setToken** | `should set auth token` | Token stored correctly | After login API returns JWT |
| | `should clear token` | Token set to null | Logout |
| **setLoading** | `should set loading state` | Toggle isLoading on/off | During API calls |

#### Logout (1 test)

| Test Case | What It Verifies | App Scenario |
|-----------|------------------|--------------|
| `should clear all auth state` | user=null, token=null, isAuthenticated=false, isLoading=false | Admin clicks logout |

#### Session Validation - `checkAuth()` (5 tests)

| Test Case | What It Verifies | App Scenario |
|-----------|------------------|--------------|
| `should set loading false when no token` | Skips API call, just clears loading | Fresh visit, no saved session |
| `should validate token and set user on success` | Calls `/api/admin/auth/session`, sets user + authenticated | Page refresh with valid session |
| `should logout on 401 response` | Clears token + user + isAuthenticated | Token expired on server |
| `should logout on network error` | Clears auth state gracefully | Backend down or network issue |
| `should logout when server says not authenticated` | Handles `{ authenticated: false }` response | Token revoked by another admin |

**Persistence:** The auth store uses Zustand's `persist` middleware to save `token`, `user`, and `isAuthenticated` to `localStorage`. On page reload, `onRehydrateStorage` triggers `checkAuth()` to validate the saved session with the backend.

**Cross-reference with Backend:**

| Admin Action | Backend Endpoint | Backend Test |
|-------------|------------------|--------------|
| Login | `POST /api/admin/auth/login` | `test_auth.py` → TestAuthEndpoints |
| Session check | `GET /api/admin/auth/session` | `test_auth.py` → TestSessionManagement |
| View stats | `GET /api/admin/stats` | `test_admin_stats.py` → TestAdminStats |

---

## 4. Admin Dashboard Pages - Test Coverage Map

Maps each admin page to what's currently tested and what's not:

| Page | Route | Current Coverage | Tested By |
|------|-------|-----------------|-----------|
| Login | `/login` | Auth flow tested | `authStore.test.ts` |
| Dashboard | `/dashboard` | Stats formatting | `utils.test.ts` (formatCurrency) |
| Rides | `/dashboard/rides` | Status badges, date formatting | `utils.test.ts` (statusColor, formatDate) |
| Drivers | `/dashboard/drivers` | Currency formatting, CSV export | `utils.test.ts`, `export-csv.test.ts` |
| Users | `/dashboard/users` | Currency/date formatting | `utils.test.ts` |
| Earnings | `/dashboard/earnings` | Currency formatting, CSV export | `utils.test.ts`, `export-csv.test.ts` |
| Service Areas | `/dashboard/service-areas` | Not yet tested | - |
| Vehicle Types | `/dashboard/vehicle-types` | Not yet tested | - |
| Pricing | `/dashboard/pricing` | Currency formatting | `utils.test.ts` |
| Promotions | `/dashboard/promotions` | Date formatting | `utils.test.ts` |
| Support | `/dashboard/support` | Status badges | `utils.test.ts` |
| Cloud Messaging | `/dashboard/cloud-messaging` | Not yet tested | - |
| Staff | `/dashboard/staff` | Not yet tested | - |
| Settings | `/dashboard/settings` | Not yet tested | - |
| Audit Logs | `/dashboard/audit-logs` | Date formatting | `utils.test.ts` |

---

## 5. How to Add New Admin Dashboard Tests

### Test a utility function:
```typescript
// src/__tests__/lib/myUtil.test.ts
import { describe, it, expect } from 'vitest';
import { myFunction } from '@/lib/myUtil';

describe('myFunction', () => {
  it('should handle normal input', () => {
    expect(myFunction('input')).toBe('expected');
  });

  it('should handle edge case', () => {
    expect(myFunction(null)).toBe('fallback');
  });
});
```

### Test a store:
```typescript
// src/__tests__/store/myStore.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMyStore } from '@/store/myStore';

describe('myStore', () => {
  beforeEach(() => {
    useMyStore.setState({ /* initial state */ });
    vi.clearAllMocks();
  });

  it('should update state on action', async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: 'value' }),
    });

    await useMyStore.getState().fetchData();
    expect(useMyStore.getState().data).toBe('value');
  });
});
```

### Test a React component:
```typescript
// src/__tests__/components/MyComponent.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MyComponent from '@/components/MyComponent';

describe('MyComponent', () => {
  it('should render title', () => {
    render(<MyComponent title="Hello" />);
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });

  it('should handle click', async () => {
    const onClick = vi.fn();
    render(<MyComponent onClick={onClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalled();
  });
});
```

---

## 6. Gaps & Future Tests to Add

| Area | What's Missing | Priority |
|------|---------------|----------|
| `api.ts` request function | Auth header injection, 401 redirect, error parsing | High |
| Login page component | Form validation, submit flow, error display | High |
| Dashboard page | Stats cards rendering with mock data | Medium |
| Ride list component | Filtering, pagination, status badges | Medium |
| Driver management | Driver table, action bar, document review | Medium |
| Service area map | Geofence drawing, area CRUD | Low |
| Sidebar component | Navigation rendering, module-based access control | Low |
| Support tabs | Ticket list, reply flow, dispute resolution | Medium |
| Cloud messaging | Audience selection, message sending | Low |
| Staff management | RBAC, module permissions | Medium |
