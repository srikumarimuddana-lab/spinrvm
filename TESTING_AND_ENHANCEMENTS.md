# Spinr Testing Strategy & Technical Enhancements

This document provides an overview of the testing infrastructure and technical enhancements implemented for the Spinr ride-sharing platform.

## Table of Contents

1. [Testing Strategy](#testing-strategy)
2. [Technical Enhancements](#technical-enhancements)
3. [CI/CD Pipeline](#cicd-pipeline)
4. [Usage Guide](#usage-guide)

---

## Testing Strategy

### 1. Unit Tests with Pytest (Backend)

**Location:** `spinr/backend/tests/`

The backend test suite provides comprehensive coverage of all core functionality with a target of 80%+ code coverage.

#### Test Files

| File | Description |
|------|-------------|
| `conftest.py` | Shared fixtures and mocks for all tests |
| `test_db.py` | Database layer tests |
| `test_auth.py` | Authentication and authorization tests |
| `test_rides.py` | Ride management and pricing tests |
| `test_drivers.py` | Driver management tests |
| `test_sms.py` | SMS service and Twilio integration tests |
| `test_features.py` | Support tickets, FAQs, notifications tests |
| `test_documents.py` | Document management tests |
| `test_utils.py` | Utility function tests |

#### Running Tests

```bash
cd spinr/backend

# Run all tests
pytest

# Run with coverage report
pytest --cov=backend --cov-report=html

# Run specific test file
pytest tests/test_auth.py

# Run tests by marker
pytest -m unit
pytest -m integration
```

#### Pytest Configuration

Configuration is in `spinr/backend/pytest.ini`:
- Async test support with `asyncio_mode = auto`
- Coverage reporting with 80% threshold
- Test markers for categorization
- Environment variable configuration for tests

### 2. E2E Tests with Detox (Frontend)

**Location:** `spinr/frontend/e2e/`

End-to-end tests for mobile applications using Detox framework.

#### Configuration Files

| File | Description |
|------|-------------|
| `jest.config.js` | Jest test runner configuration |
| `.detoxrc.json` | Detox app and device configurations |
| `init.js` | Test initialization and teardown |
| `auth.test.tsx` | Authentication flow E2E tests |

#### Running E2E Tests

```bash
cd spinr/frontend

# Install dependencies
npm install
npm install -g detox-cli

# Build and test iOS
detox build -c ios.sim.debug
detox test -c ios.sim.debug

# Build and test Android
detox build -c android.emu.debug
detox test -c android.emu.debug
```

#### Test Coverage

The E2E test suite covers:
- OTP login and verification
- Session persistence
- Logout functionality
- Ride booking flow
- Payment processing
- Profile management

---

## Technical Enhancements

### 1. Cloudinary Integration

**Location:** `spinr/backend/utils/cloudinary.py`

Cloudinary integration for image upload, transformation, and management.

#### Features

- Image upload from file or URL
- Automatic image transformation and optimization
- Folder organization
- Tag-based image management
- Mock mode for development without credentials

#### Usage

```python
from backend.utils.cloudinary import init_cloudinary, get_cloudinary_service

# Initialize with credentials
init_cloudinary(
    cloud_name='your-cloud-name',
    api_key='your-api-key',
    api_secret='your-api-secret'
)

# Get service instance
service = get_cloudinary_service()

# Upload image
result = await service.upload_image(
    file_path='/path/to/image.jpg',
    folder='driver_profiles',
    tags=['profile', 'driver']
)

# Get transformed URL
url = service.get_image_url(
    public_id='driver_profiles/image',
    width=200,
    height=200,
    crop='fill'
)
```

#### Environment Variables

```bash
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
```

### 2. Mixpanel/Amplitude Analytics

**Location:** `spinr/backend/utils/analytics.py`

Unified analytics service supporting both Mixpanel and Amplitude.

#### Features

- Dual-provider support (Mixpanel and Amplitude)
- Automatic fallback to mock mode when not configured
- Pre-built methods for common events
- User identification and property tracking
- Group analytics support (Amplitude)

#### Usage

```python
from backend.utils.analytics import init_analytics, get_analytics

# Initialize analytics
init_analytics(
    mixpanel_token='your-mixpanel-token',
    amplitude_api_key='your-amplitude-key'
)

# Get analytics instance
analytics = get_analytics()

# Track events
analytics.track(user_id, 'Ride Requested', {
    'pickup': '123 Main St',
    'destination': '456 Oak Ave',
    'vehicle_type': 'sedan'
})

# Use convenience methods
analytics.track_ride_completed(user_id, {
    'ride_id': 'ride_123',
    'duration_minutes': 15,
    'amount': 25.50
})

analytics.track_signup(user_id, {
    'source': 'referral',
    'phone': '+1234567890'
})
```

#### Environment Variables

```bash
MIXPANEL_TOKEN=your-mixpanel-token
AMPLITUDE_API_KEY=your-amplitude-key
```

### 3. Enhanced Error Handling

**Location:** `spinr/backend/utils/error_handling.py`

Comprehensive error handling framework with standardized error codes and responses.

#### Features

- Hierarchical exception classes
- Standardized error codes (1000-9999)
- Consistent error response format
- Automatic exception-to-HTTP-status mapping
- Request logging integration

#### Error Code Ranges

| Range | Category |
|-------|----------|
| 1000-1999 | Authentication errors |
| 2000-2999 | Validation errors |
| 3000-3999 | Resource errors |
| 4000-4999 | Ride errors |
| 5000-5999 | Driver errors |
| 6000-6999 | Payment errors |
| 9000-9999 | System errors |

#### Usage

```python
from backend.utils.error_handling import (
    SpinrException,
    InvalidTokenException,
    RideNotFoundException,
    PaymentException,
    register_exception_handlers
)

# Raise specific exceptions
raise InvalidTokenException()
raise RideNotFoundException(ride_id='ride_123')
raise PaymentException(message='Card declined')

# Custom exceptions with details
raise SpinrException(
    message='Custom error',
    error_code=ErrorCode.EXTERNAL_SERVICE_ERROR,
    status_code=502,
    details={'service': 'stripe'}
)

# Register handlers with FastAPI app
from fastapi import FastAPI
app = FastAPI()
register_exception_handlers(app)
```

#### Error Response Format

```json
{
  "success": false,
  "error": {
    "code": 4001,
    "message": "Ride not found: ride_123",
    "details": {
      "resource_type": "Ride",
      "resource_id": "ride_123"
    },
    "timestamp": "2024-01-15T10:30:00.000Z"
  }
}
```

---

## CI/CD Pipeline

**Location:** `spinr/.github/workflows/ci.yml`

GitHub Actions workflow for continuous integration and deployment.

### Pipeline Stages

1. **Backend Tests**
   - Python environment setup
   - Dependency installation with caching
   - Pytest execution with coverage
   - Coverage report upload to Codecov

2. **Frontend Tests**
   - Node.js environment setup
   - Dependency installation with caching
   - TypeScript type checking
   - ESLint validation
   - Unit test execution
   - Production build

3. **Admin Dashboard Tests**
   - Similar to frontend tests
   - Separate build artifact

4. **E2E Tests** (main branch only)
   - Detox CLI installation
   - Mobile app build
   - E2E test execution

5. **Deployment**
   - Backend: Render or Fly.io
   - Frontend: Vercel
   - Admin Dashboard: Vercel

6. **Mobile Build** (with `[build]` commit message)
   - EAS Build for iOS and Android

7. **Security Scan**
   - Trivy vulnerability scanning
   - SARIF report upload

### Required Secrets

```bash
# Supabase
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY

# Deployment
RENDER_API_KEY
RENDER_BACKEND_SERVICE_ID
FLY_API_TOKEN
VERCEL_TOKEN
VERCEL_ORG_ID
VERCEL_FRONTEND_PROJECT_ID
VERCEL_ADMIN_PROJECT_ID

# Expo/EAS
EXPO_TOKEN

# Notifications
SLACK_WEBHOOK

# App Configuration
EXPO_PUBLIC_API_URL
```

---

## Usage Guide

### Quick Start for Development

```bash
# Backend
cd spinr/backend
pip install -r requirements.txt
pip install pytest-cov pytest-asyncio pytest-mock
pytest --cov=backend

# Frontend
cd spinr/frontend
npm install
npm test

# E2E Tests
cd spinr/frontend
npm install -g detox-cli
detox test -c ios.sim.debug
```

### Adding New Tests

1. **Backend Unit Tests:**
   - Create new test file in `spinr/backend/tests/`
   - Follow naming convention: `test_*.py`
   - Use fixtures from `conftest.py`
   - Mark tests appropriately: `@pytest.mark.unit`

2. **E2E Tests:**
   - Create new test file in `spinr/frontend/e2e/`
   - Follow naming convention: `*.test.tsx`
   - Use Detox selectors and matchers
   - Test complete user flows

### Adding New Error Types

1. Define error code in `ErrorCode` enum
2. Create exception class inheriting from appropriate base
3. Set default message, error code, and status code
4. Add to appropriate category based on code range

### Configuring Analytics

1. Set environment variables for desired providers
2. Call `init_analytics()` at application startup
3. Use `get_analytics()` to access the service
4. Track events using convenience methods

---

## Summary

This implementation provides:

- **80%+ code coverage** with comprehensive pytest test suite
- **E2E testing** with Detox for mobile applications
- **Automated CI/CD** with GitHub Actions
- **Cloudinary integration** for image management
- **Analytics support** for Mixpanel and Amplitude
- **Robust error handling** with standardized responses

All components are production-ready and include mock modes for development without external service credentials.