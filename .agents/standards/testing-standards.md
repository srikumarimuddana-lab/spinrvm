---
name: Testing Standards
description: Test coverage requirements and testing patterns for the Spinr project
---

# Testing Standards

## Coverage Requirements
| Component | Minimum | Target |
|-----------|---------|--------|
| Backend Auth (`backend/routes/auth.py`) | 90% | 95% |
| Backend Rides (`backend/routes/rides.py`) | 80% | 90% |
| Backend Payments (`backend/routes/payments.py`) | 90% | 95% |
| Backend Drivers (`backend/routes/drivers.py`) | 80% | 90% |
| Frontend Stores (`*/store/*.ts`) | 70% | 85% |
| Frontend Screens | 50% | 70% |

## What MUST Have Tests (Non-negotiable)
1. Authentication flows (login, OTP, token refresh)
2. Payment processing (fare calc, charge, refund)
3. Ride lifecycle (create, match, start, complete, cancel)
4. Admin operations (user management, analytics)
5. Any security-critical code

## Test Structure

### Backend (pytest)
```
backend/tests/
├── conftest.py              # Shared fixtures
├── test_auth.py             # Auth endpoint tests
├── test_rides.py            # Ride lifecycle tests
├── test_drivers.py          # Driver operation tests
├── test_payments.py         # Payment flow tests
├── test_admin.py            # Admin endpoint tests
├── test_validators.py       # Input validation tests
└── test_rate_limits.py      # Rate limiting tests
```

### Test Naming
```python
# Pattern: test_{action}_{scenario}_{expected_result}
def test_create_ride_success():
def test_create_ride_missing_pickup_returns_400():
def test_create_ride_unauthorized_returns_401():
def test_calculate_fare_zero_distance():
```

### Test Template (Backend)
```python
import pytest
from fastapi.testclient import TestClient
from backend.server import app

client = TestClient(app)

class TestRideEndpoints:
    """Tests for ride-related API endpoints."""

    def test_create_ride_success(self, auth_headers):
        """Verify ride creation with valid data."""
        response = client.post("/api/v1/rides", json={
            "pickup": {"lat": 50.45, "lng": -104.61},
            "dropoff": {"lat": 50.46, "lng": -104.62},
            "vehicle_type": "standard"
        }, headers=auth_headers)
        assert response.status_code == 201
        assert "id" in response.json()

    def test_create_ride_unauthorized(self):
        """Verify 401 for unauthenticated ride creation."""
        response = client.post("/api/v1/rides", json={})
        assert response.status_code == 401

    def test_create_ride_invalid_input(self, auth_headers):
        """Verify 400 for missing required fields."""
        response = client.post("/api/v1/rides", json={},
                             headers=auth_headers)
        assert response.status_code in [400, 422]
```

### Frontend Test Template
```typescript
import { renderHook, act } from '@testing-library/react-hooks';
import { useRideStore } from '../store/rideStore';

describe('RideStore', () => {
  beforeEach(() => {
    useRideStore.getState().reset();
  });

  it('should start with empty rides list', () => {
    const { result } = renderHook(() => useRideStore());
    expect(result.current.rides).toEqual([]);
  });

  it('should handle fetch error gracefully', async () => {
    // Mock API failure
    const { result } = renderHook(() => useRideStore());
    await act(() => result.current.fetchRides());
    expect(result.current.error).toBeDefined();
  });
});
```

## Running Tests
```bash
# Backend — all tests
cd backend && python -m pytest -v

# Backend — specific file
cd backend && python -m pytest tests/test_auth.py -v

# Backend — with coverage
cd backend && python -m pytest --cov=. --cov-report=term-missing

# Frontend
cd rider-app && npm test
cd admin-dashboard && npm test
```

## When Tests Are Missing
If a PR lacks tests for a critical area:
1. **Block the merge** — tests are mandatory
2. Write the missing tests
3. Add the gap to `.agents/docs/architecture.md` under technical debt
