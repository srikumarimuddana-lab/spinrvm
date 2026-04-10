# Backend Testing Knowledge Document
## Spinr - Python/FastAPI Backend

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Framework** | pytest 8.0+ with pytest-asyncio |
| **Language** | Python 3.11 |
| **Coverage tool** | pytest-cov (target: 80%) |
| **Test location** | `backend/tests/` |
| **Config file** | `backend/pytest.ini` |
| **Run command** | `cd backend && pytest` |
| **Run with coverage** | `pytest --cov=. --cov-report=term-missing` |
| **Run single file** | `pytest tests/test_auth.py -v` |
| **Run by marker** | `pytest -m unit` or `pytest -m "not slow"` |

---

## 2. Test Architecture

```
backend/
├── pytest.ini              # Config: async mode, coverage, markers, env vars
├── tests/
│   ├── conftest.py         # Shared fixtures (mock Supabase, Firebase, SMS, JWT)
│   ├── test_auth.py        # Authentication & authorization tests
│   ├── test_rides.py       # Ride lifecycle tests
│   ├── test_drivers.py     # Driver management tests
│   ├── test_db.py          # Database layer tests
│   ├── test_features.py    # Support tickets, FAQs, notifications
│   ├── test_documents.py   # Document upload & verification
│   ├── test_admin_stats.py # Admin dashboard statistics
│   ├── test_sms.py         # SMS/Twilio integration
│   └── verify_db.py        # Database connectivity verification
```

### Shared Fixtures (`conftest.py`)
All tests share these mock fixtures to avoid hitting real services:

| Fixture | Purpose |
|---------|---------|
| `mock_supabase_client` | Mocks all Supabase table queries (select, eq, insert, update, delete) with chainable responses |
| `mock_firebase_admin` | Mocks Firebase Admin SDK for token verification |
| `mock_sms_service` | Mocks Twilio SMS sending |
| `sample_user` | Standard test user data (rider role, Saskatchewan phone) |
| `sample_driver` | Standard test driver data (with vehicle, rating, location) |
| `sample_ride` | Standard test ride data (pickup, dropoff, fare, status) |
| `auth_headers` | Pre-built `Authorization: Bearer <token>` headers |

---

## 3. Test Files - Detailed Breakdown

### 3.1 `test_auth.py` - Authentication & Authorization

**Application Flow:** Phone OTP login → Firebase verification → JWT token → User session

| Test Class | Test Case | What It Verifies | Related API |
|------------|-----------|------------------|-------------|
| **TestOTPCreation** | `test_generate_otp_format` | OTP is exactly 6 digits | `POST /api/auth/send-otp` |
| | `test_generate_otp_randomness` | Two OTPs are not identical | |
| | `test_generate_otp_range` | OTP is between 100000-999999 | |
| **TestJWTTokenHandling** | `test_create_jwt_token` | JWT contains user_id and role claims | Internal `create_token()` |
| | `test_create_jwt_token_with_session` | Session ID embedded in JWT | |
| | `test_verify_jwt_token_valid` | Valid JWT decodes correctly | Internal `verify_token()` |
| | `test_verify_jwt_token_invalid` | Invalid JWT raises error | |
| | `test_verify_jwt_token_expired` | Expired JWT is rejected | |
| | `test_verify_jwt_token_wrong_algorithm` | Wrong algorithm JWT is rejected | |
| **TestGetCurrentUser** | (multiple) | Extracts user from Bearer token | Middleware |
| **TestAdminUserVerification** | (multiple) | Admin role check for protected routes | Middleware |
| **TestFirebaseIntegration** | (multiple) | Firebase token verification flow | `POST /api/auth/verify-firebase` |
| **TestAuthEndpoints** | `test_send_otp_success` | OTP sent via Twilio | `POST /api/auth/send-otp` |
| | `test_send_otp_missing_phone` | Returns 422 if phone missing | |
| | `test_send_otp_invalid_phone_format` | Rejects invalid phone format | |
| | `test_verify_otp_success` | Valid OTP returns JWT + user | `POST /api/auth/verify-otp` |
| | `test_verify_otp_missing_fields` | Returns error if fields missing | |
| **TestSessionManagement** | `test_session_id_in_token` | Session ID tracked in JWT | |
| **TestPasswordlessAuth** | (multiple) | Passwordless login flow | |
| **TestTokenRefresh** | `test_token_refresh_with_valid_session` | Token refresh returns new JWT | `POST /api/auth/refresh` |

**User Flow Diagram:**
```
User enters phone → Send OTP → User enters OTP → Verify OTP
  → New user? → Create profile → Return JWT
  → Existing user? → Return JWT + user data
```

---

### 3.2 `test_rides.py` - Ride Lifecycle

**Application Flow:** Select locations → Get estimates → Create ride → Driver assigned → Pickup → In progress → Complete → Rate

| Test Class | Test Case | What It Verifies | Related API |
|------------|-----------|------------------|-------------|
| **TestRideCreation** | (multiple) | Ride creation with pickup/dropoff, vehicle type, payment method | `POST /api/v1/rides` |
| **TestRideStatusUpdates** | (multiple) | Status transitions: searching → driver_assigned → driver_arrived → in_progress → completed | `POST /api/v1/rides/{id}/start`, `/complete` |
| **TestFareCalculation** | `test_calculate_distance` | Haversine formula returns correct km | Internal |
| | `test_calculate_distance_same_point` | Same coordinates = 0 km | |
| | `test_calculate_base_fare` | Base fare applied correctly | |
| | `test_fare_with_distance` | Per-km rate applied | |
| | `test_fare_with_time` | Per-minute rate applied | |
| **TestRideMatching** | (multiple) | Nearest available driver matched to ride | Internal matching logic |
| **TestRideHistory** | (multiple) | Paginated ride history for user | `GET /api/v1/rides/history` |
| **TestRideRatings** | (multiple) | 1-5 star rating with comment and tip | `POST /api/v1/rides/{id}/rate` |
| **TestScheduledRides** | (multiple) | Future ride scheduling | `POST /api/v1/rides` (scheduled) |
| **TestRideEndpoints** | `test_create_ride_endpoint` | HTTP endpoint creates ride | `POST /api/v1/rides` |
| | `test_get_ride_endpoint` | Fetch single ride details | `GET /api/v1/rides/{id}` |
| | `test_get_user_rides_endpoint` | Fetch user's rides | `GET /api/v1/rides` |
| | `test_cancel_ride_endpoint` | Cancel a ride | `POST /api/v1/rides/{id}/cancel` |
| **TestRideSharing** | `test_generate_share_token` | Generates shareable trip link | |
| | `test_share_trip_data_structure` | Shared data has correct fields | |
| **TestRideDisputes** | (multiple) | Dispute creation and resolution | `POST /api/admin/disputes` |

**Ride Status Flow:**
```
searching → driver_assigned → driver_arrived → in_progress → completed
                                                           → cancelled (at any point)
```

**Fare Calculation Formula:**
```
total_fare = base_fare + (distance_km × per_km_rate) + (duration_min × per_min_rate) + booking_fee
           × surge_multiplier (if active)
```

---

### 3.3 `test_drivers.py` - Driver Management

**Application Flow:** Register as driver → Upload documents → Get verified → Go online → Accept rides

| Test Class | Test Case | What It Verifies | Related API |
|------------|-----------|------------------|-------------|
| **TestDriverRegistration** | (multiple) | Driver signup with vehicle info, license plate | `POST /api/v1/drivers/register` |
| **TestDriverAvailability** | (multiple) | Online/offline toggle, availability by area | `POST /api/v1/drivers/status` |
| **TestDriverLocation** | (multiple) | GPS location updates, nearby driver queries | `PUT /api/v1/drivers/location` |
| **TestDriverDocuments** | (multiple) | Document upload, review, expiry checking | `POST /api/v1/drivers/documents` |
| **TestDriverStats** | (multiple) | Rating calculation, ride count, earnings | `GET /api/v1/drivers/earnings` |
| | `test_calculate_driver_rating` | Average rating computation | |
| **TestDriverEndpoints** | `test_get_driver_profile` | Fetch driver profile | `GET /api/v1/drivers/me` |
| | `test_update_driver_availability` | Set online status | `POST /api/v1/drivers/status` |
| | `test_get_nearby_drivers_admin` | Admin nearby driver query | `GET /api/admin/drivers/nearby` |
| **TestDriverVehicle** | (multiple) | Vehicle info validation | |

**Driver Lifecycle:**
```
Register → Upload docs → Pending review → Verified → Can go online → Accept rides
                                        → Rejected → Re-upload docs
```

---

### 3.4 `test_db.py` - Database Layer

**Purpose:** Tests the Supabase database abstraction layer to ensure all CRUD operations work correctly.

| Test Class | What It Verifies |
|------------|------------------|
| **TestMockCursor** | Cursor initialization for DB queries |
| **TestCollection** | Collection find/filter operations |
| **TestDBWrapper** | All required collections exist (users, drivers, rides, etc.) |
| **TestUserCollection** | User CRUD: create, find by phone, update profile |
| **TestDriverCollection** | Driver CRUD: create, update status, find by location |
| **TestRideCollection** | Ride CRUD: create, update status, query by user/driver |
| **TestOTPRecordOperations** | OTP storage, retrieval, expiry, cleanup |
| **TestDatabaseSupabaseFunctions** | Raw Supabase RPC calls, PostGIS queries |
| **TestUtilityFunctions** | `serialize_for_api()`, `single_row_from_res()`, `rows_from_res()` |
| **TestAsyncHelpers** | Async database helper functions |

---

### 3.5 `test_features.py` - Platform Features

| Test Class | What It Verifies | Related API |
|------------|------------------|-------------|
| **TestSupportTickets** | Create, update, reply, close tickets | `/api/admin/tickets` |
| **TestFAQs** | FAQ CRUD operations | `/api/admin/faqs` |
| **TestSurgePricing** | Surge multiplier calculations | `/api/admin/service-areas/{id}/surge` |
| **TestNotifications** | FCM push notification sending | `/api/admin/cloud-messaging/send` |
| **TestServiceAreas** | Geofence creation, point-in-polygon checks | `/api/admin/service-areas` |
| **TestSavedAddresses** | User saved places (home, work, etc.) | `/api/v1/addresses` |
| **TestEmergencyContacts** | Emergency contact management | `/api/v1/users/emergency-contacts` |
| **TestCorporateAccounts** | Corporate account CRUD, employee rides | `/api/admin/corporate-accounts` |

---

### 3.6 `test_documents.py` - Document Verification

| Test Class | What It Verifies | Related API |
|------------|------------------|-------------|
| **TestDocumentRequirements** | Configurable doc requirements per vehicle type | `GET /api/v1/drivers/document-requirements` |
| **TestDriverDocuments** | Upload, list, delete driver documents | `POST /api/v1/drivers/documents` |
| **TestDocumentExpiry** | Expiry date tracking, renewal alerts | Internal |
| **TestDocumentFileStorage** | Cloud storage integration (Cloudinary) | Internal |
| **TestDocumentValidation** | File type (PDF, JPEG), size (max 10MB), extension validation | Internal |
| **TestDocumentEndpoints** | HTTP endpoint integration tests | `/api/v1/drivers/documents/*` |
| **TestDocumentRegressions** | WebSocket route registration check | Regression |

---

### 3.7 `test_admin_stats.py` - Admin Dashboard Statistics

| Test Class | What It Verifies | Related API |
|------------|------------------|-------------|
| **TestAdminStats** | Total rides, completed, cancelled, active rides count; total/online drivers; total users; total earnings; admin earnings; tips | `GET /api/admin/stats` |

---

### 3.8 `test_sms.py` - SMS/Twilio Integration

| Test Class | Test Case | What It Verifies |
|------------|-----------|------------------|
| **TestSMSService** | (multiple) | SMS service initialization, OTP message sending |
| | `test_send_otp_sms_format` | OTP message contains the code and is well-formatted |
| **TestTwilioIntegration** | `test_twilio_client_initialization` | Twilio client uses correct credentials |
| | `test_twilio_message_creation_params` | Message has correct to/from/body |
| **TestSMSValidation** | `test_validate_phone_number_valid` | Canadian phone numbers accepted (+1306, +1639, etc.) |
| | `test_validate_phone_number_invalid` | Invalid formats rejected |
| | `test_validate_message_length` | Message under 160 chars for SMS |
| **TestSMSRetry** | `test_sms_retry_on_failure` | Retries on Twilio failure |
| | `test_sms_retry_exhausted` | Gives up after max retries |

---

## 4. How to Add New Backend Tests

### Step 1: Create test file
```python
# tests/test_new_feature.py
import pytest
from unittest.mock import MagicMock, AsyncMock

class TestNewFeature:
    def test_basic_case(self, mock_supabase_client):
        # Use shared fixtures from conftest.py
        pass
    
    @pytest.mark.asyncio
    async def test_async_case(self, mock_supabase_client):
        # For async functions
        pass
```

### Step 2: Use markers for categorization
```python
@pytest.mark.unit        # Fast, isolated tests
@pytest.mark.integration # Tests that touch multiple layers
@pytest.mark.slow        # Tests that take > 1 second
@pytest.mark.e2e         # End-to-end tests
```

### Step 3: Run and verify
```bash
pytest tests/test_new_feature.py -v              # Run new tests
pytest --cov=. --cov-report=term-missing -v      # Check coverage
```

---

## 5. Environment Variables for Testing

Set in `pytest.ini` automatically:
```
SUPABASE_URL=https://test.supabase.co
SUPABASE_SERVICE_ROLE_KEY=test_key
SECRET_KEY=test-secret-key
ENV=test
```
