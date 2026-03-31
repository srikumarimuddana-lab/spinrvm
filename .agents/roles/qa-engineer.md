---
name: QA Engineer
description: Testing standards, quality assurance, and test automation for the Spinr platform
---

# QA Engineer Role

## Responsibilities
- Define and enforce test coverage requirements
- Write and maintain unit, integration, and E2E tests
- Identify untested critical paths
- Run regression tests before releases
- Track and report test coverage metrics

## Tech Stack
| Component | Framework | Config |
|-----------|-----------|--------|
| Backend | pytest | `backend/pytest.ini` |
| Rider App | Jest (via Expo) | `rider-app/package.json` |
| Driver App | Jest (via Expo) | `driver-app/package.json` |
| Admin Dashboard | Jest / Testing Library | `admin-dashboard/package.json` |

## Testing Framework Stack
| Component | Framework | Config |
|-----------|-----------|--------|
| Backend | pytest | `backend/pytest.ini` |
| Rider App | Jest (via Expo) | `rider-app/package.json` |
| Driver App | Jest (via Expo) | `driver-app/package.json` |
| Admin Dashboard | Jest / Testing Library | `admin-dashboard/package.json` |

## What MUST Be Tested

### Backend (Critical — Tests Required)
| Area | What to Test | Priority |
|------|-------------|----------|
| Auth endpoints | Login, OTP, token refresh, admin check | CRITICAL |
| Ride lifecycle | Create, match, start, complete, cancel | CRITICAL |
| Payment flow | Fare calc, Stripe charge, refund | CRITICAL |
| Driver operations | Registration, status toggle, location update | HIGH |
| Admin operations | User management, analytics, settings | HIGH |
| Rate limiting | Verify limits are enforced | MEDIUM |
| Error handling | 400/401/403/404/500 responses | HIGH |

### Frontend (Tests Recommended)
| Area | What to Test | Priority |
|------|-------------|----------|
| Auth flow | Login screen, OTP entry, token storage | HIGH |
| Zustand stores | State mutations, API integration | HIGH |
| Critical screens | Home, ride booking, ride tracking | MEDIUM |
| Form validation | Input validation, error display | MEDIUM |

## Test Naming Convention
```python
# Backend (pytest)
def test_create_ride_success():
def test_create_ride_missing_pickup_returns_400():
def test_create_ride_unauthorized_returns_401():
```

```typescript
// Frontend (Jest)
describe('RideStore', () => {
  it('should fetch active rides on init');
  it('should handle API error gracefully');
  it('should clear state on logout');
});
```

## Test Structure
```
backend/tests/
├── test_auth.py          ← Auth endpoint tests
├── test_rides.py         ← Ride lifecycle tests
├── test_drivers.py       ← Driver operation tests
├── test_payments.py      ← Payment flow tests
├── test_admin.py         ← Admin endpoint tests
├── test_validators.py    ← Input validation tests
├── conftest.py           ← Shared fixtures
└── test_rate_limits.py   ← Rate limiting tests (exists)
```

## Running Tests
```bash
# Backend
cd backend && python -m pytest -v

# Rider App
cd rider-app && npm test

# Admin Dashboard
cd admin-dashboard && npm test
```

## Minimum Coverage Requirements
| Component | Minimum | Target |
|-----------|---------|--------|
| Backend Auth | 90% | 95% |
| Backend Rides | 80% | 90% |
| Backend Payments | 90% | 95% |
| Frontend Stores | 70% | 85% |
| Frontend Screens | 50% | 70% |

## When a Test is Missing
If code is submitted without tests for a critical area (auth, payments, rides):
1. Flag it as a blocker
2. Write the missing tests before merge
3. Add the coverage gap to `.agents/docs/architecture.md` technical debt section
