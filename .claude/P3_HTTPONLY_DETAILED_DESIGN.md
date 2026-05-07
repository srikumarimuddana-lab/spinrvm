# P3: HttpOnly Token Storage — Detailed Implementation Guide

**Objective**: Move JWT tokens from localStorage (XSS-vulnerable) to HTTP-only cookies (JavaScript-inaccessible).

**Success Criteria**:
1. No JWT tokens in response JSON
2. No JWT tokens in localStorage
3. Cookies sent automatically via `withCredentials: true`
4. 401 on missing cookie → auto-refresh → retry succeeds
5. Logout clears both auth + refresh cookies

---

## Architecture: Old vs New

### BEFORE (Current — Vulnerable)

```
┌──────────────────────┐
│   Client (Browser)   │
│  ┌────────────────┐  │
│  │  localStorage  │  │
│  │  auth_token=.. │◄─┼─── Vulnerable to XSS
│  └────────────────┘  │
│         │            │
│  ┌──────▼──────────┐ │
│  │ Axios client    │ │
│  │ Authorization   │ │
│  │ Bearer <token>  │ │
│  └─────────────────┘ │
└───────────────┬──────┘
                │
         ┌──────▼──────────┐
         │   Backend API   │
         │  1. Verify JWT  │
         │  2. Process req │
         │  3. Return data │
         └─────────────────┘
```

**Problem**: JavaScript can read localStorage. XSS attack steals token.

### AFTER (New — Secure)

```
┌──────────────────────┐
│   Client (Browser)   │
│  ┌────────────────┐  │
│  │ HTTP-only      │  │
│  │ Cookie storage │◄─┼─── JavaScript cannot read
│  │ (browser only) │  │
│  └────────────────┘  │
│         │            │
│  ┌──────▼──────────┐ │
│  │ Axios client    │ │
│  │ withCredentials │ │
│  │ Cookie auto-   │ │
│  │ attached        │ │
│  └─────────────────┘ │
└───────────────┬──────┘
                │
         ┌──────▼──────────────┐
         │   Backend API       │
         │  1. Read cookie     │
         │  2. Verify JWT      │
         │  3. Check if expiry │
         │     < 5 min → issue │
         │     new token       │
         │  4. Process request │
         │  5. Set-Cookie hdr  │
         └─────────────────────┘
```

**Benefit**: JavaScript cannot read HTTP-only cookies. XSS cannot steal token. Browser auto-attaches cookie on each request.

---

## Step 1: Backend Cookie Manager

### File: `backend/utils/cookie_manager.py` (NEW)

```python
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Response
from backend.core.config import settings


class CookieManager:
    """
    Centralized cookie management for auth tokens.
    All Set-Cookie headers go through this class for consistency.
    """

    @staticmethod
    def set_auth_cookie(
        response: Response,
        token: str,
        ttl_minutes: int = 15
    ) -> None:
        """
        Set HTTP-only auth token cookie.
        
        Args:
            response: FastAPI Response object
            token: JWT access token
            ttl_minutes: Time-to-live (default 15 min for rider/driver, 60 min for admin)
        """
        response.set_cookie(
            key="auth_token",
            value=token,
            max_age=ttl_minutes * 60,  # Convert to seconds
            expires=datetime.utcnow() + timedelta(minutes=ttl_minutes),
            httponly=True,  # ← JavaScript cannot access
            secure=settings.ENVIRONMENT == "production",  # HTTPS only in prod
            samesite="Strict",  # Prevent CSRF token leaks
            domain=settings.COOKIE_DOMAIN,  # Cross-subdomain if applicable
            path="/"  # Available to all routes
        )

    @staticmethod
    def set_refresh_cookie(
        response: Response,
        token: str,
        ttl_days: int = 30
    ) -> None:
        """
        Set HTTP-only refresh token cookie.
        Longer TTL, used only for token rotation.
        """
        response.set_cookie(
            key="refresh_token",
            value=token,
            max_age=ttl_days * 86400,
            expires=datetime.utcnow() + timedelta(days=ttl_days),
            httponly=True,
            secure=settings.ENVIRONMENT == "production",
            samesite="Strict",
            domain=settings.COOKIE_DOMAIN,
            path="/"
        )

    @staticmethod
    def clear_auth_cookie(response: Response) -> None:
        """Clear auth token cookie (logout)."""
        response.delete_cookie(
            key="auth_token",
            domain=settings.COOKIE_DOMAIN,
            path="/"
        )

    @staticmethod
    def clear_refresh_cookie(response: Response) -> None:
        """Clear refresh token cookie (force logout)."""
        response.delete_cookie(
            key="refresh_token",
            domain=settings.COOKIE_DOMAIN,
            path="/"
        )

    @staticmethod
    def clear_all_cookies(response: Response) -> None:
        """Clear both cookies (logout-all)."""
        CookieManager.clear_auth_cookie(response)
        CookieManager.clear_refresh_cookie(response)
```

---

## Step 2: Config Updates

### File: `backend/core/config.py`

**ADD** (around line 150, after existing JWT settings):

```python
# ──── Cookie Settings ────
COOKIE_SECURE: bool = Field(
    default=True,
    description="Set-Cookie Secure flag (HTTPS only). False only in dev."
)

COOKIE_HTTPONLY: bool = Field(
    default=True,
    description="Set-Cookie HttpOnly flag (JavaScript cannot read)."
)

COOKIE_SAMESITE: str = Field(
    default="Strict",
    description="Set-Cookie SameSite attribute (Strict|Lax|None)."
)

COOKIE_DOMAIN: str = Field(
    default=".spinrvm.ca",
    description="Set-Cookie Domain attribute. Use .spinrvm.ca for subdomains."
)

COOKIE_PATH: str = Field(
    default="/",
    description="Set-Cookie Path attribute."
)
```

---

## Step 3: Update Auth Endpoints

### File: `backend/routes/auth.py`

**FIND AND REPLACE** (around login endpoint):

```python
# BEFORE (OLD)
@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest) -> JSONResponse:
    # ... validation logic ...
    
    tokens = _mint_tokens(user_id)
    return JSONResponse({
        "status": "ok",
        "access_token": tokens["access"],  # ← Token in response!
        "refresh_token": tokens["refresh"],  # ← Token in response!
        "user": user_data
    })


# AFTER (NEW)
@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest) -> JSONResponse:
    # ... validation logic ...
    
    tokens = _mint_tokens(user_id)
    
    # Set HTTP-only cookies
    from backend.utils.cookie_manager import CookieManager
    CookieManager.set_auth_cookie(response, tokens["access"], ttl_minutes=15)
    CookieManager.set_refresh_cookie(response, tokens["refresh"], ttl_days=30)
    
    # Return user data ONLY (no tokens in response)
    return JSONResponse({
        "status": "ok",
        "user": user_data
    })
```

**FIND AND REPLACE** (around refresh endpoint):

```python
# BEFORE (OLD)
@router.post("/refresh")
async def refresh_access_token(request: Request, response: Response, body: RefreshRequest) -> JSONResponse:
    # ... validate refresh token from body ...
    
    new_access = _mint_access_token(user_id)
    return JSONResponse({
        "status": "ok",
        "access_token": new_access  # ← Token in response!
    })


# AFTER (NEW)
@router.post("/refresh")
async def refresh_access_token(request: Request, response: Response, body: RefreshRequest) -> JSONResponse:
    # Extract refresh token from cookie
    refresh_token = request.cookies.get("refresh_token")
    
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh_token cookie")
    
    # Validate refresh token
    user_id = verify_refresh_token(refresh_token)  # Your existing function
    
    # Check if auth token is near expiry (< 5 min left)
    auth_token = request.cookies.get("auth_token")
    if auth_token:
        try:
            payload = jwt.decode(auth_token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
            expiry = payload.get("exp", 0)
            time_left = expiry - time.time()
            
            if time_left > 300:  # > 5 minutes
                # Token still valid, no need to refresh
                return JSONResponse({"status": "ok"})
        except jwt.ExpiredSignatureError:
            pass  # Token expired, issue new one below
    
    # Issue new auth token
    new_access = _mint_access_token(user_id)
    
    from backend.utils.cookie_manager import CookieManager
    CookieManager.set_auth_cookie(response, new_access, ttl_minutes=15)
    
    return JSONResponse({"status": "ok"})
```

**FIND AND REPLACE** (around logout endpoint):

```python
# BEFORE (OLD)
@router.post("/logout")
async def logout(request: Request, response: Response) -> JSONResponse:
    user_id = get_current_user_id(request)
    
    # Revoke token in DB (optional)
    revoke_refresh_token(user_id)
    
    return JSONResponse({"status": "ok"})


# AFTER (NEW)
@router.post("/logout")
async def logout(request: Request, response: Response) -> JSONResponse:
    user_id = get_current_user_id(request)
    
    # Revoke token in DB
    revoke_refresh_token(user_id)
    
    # Clear cookies
    from backend.utils.cookie_manager import CookieManager
    CookieManager.clear_all_cookies(response)
    
    return JSONResponse({"status": "ok"})
```

---

## Step 4: Admin Auth Routes

### File: `backend/routes/admin/auth.py`

**Same pattern as rider/driver auth**, but with admin-specific TTL:

```python
# BEFORE (OLD)
@router.post("/admin/auth/login")
async def admin_login(request: Request, response: Response, body: AdminLoginRequest) -> JSONResponse:
    # ... validate email + password + MFA ...
    
    token = _mint_admin_access_token(admin_id)
    return JSONResponse({
        "status": "ok",
        "access_token": token  # ← Remove this
    })


# AFTER (NEW)
@router.post("/admin/auth/login")
async def admin_login(request: Request, response: Response, body: AdminLoginRequest) -> JSONResponse:
    # ... validate email + password + MFA ...
    
    token = _mint_admin_access_token(admin_id)
    
    from backend.utils.cookie_manager import CookieManager
    CookieManager.set_auth_cookie(response, token, ttl_minutes=60)  # ← 1 hour for admin
    
    return JSONResponse({
        "status": "ok",
        "admin": admin_data
    })
```

---

## Step 5: Middleware — CORS + Cookie Support

### File: `backend/core/middleware.py`

**ADD OR UPDATE** (around CORS setup):

```python
from fastapi.middleware.cors import CORSMiddleware

# In your app setup (backend/server.py or similar):
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Admin dev
        "http://localhost:8081",  # Rider app dev
        "http://localhost:8082",  # Driver app dev
        "https://admin.spinrvm.ca",
        "https://spinr.ca",  # If mobile uses web view
    ],
    allow_credentials=True,  # ← CRITICAL: Allow credentials with cookies
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    expose_headers=["X-Total-Count"],  # Pagination
    max_age=3600,  # Preflight cache
)
```

---

## Step 6: Axios Client — withCredentials

### File: `rider-app/utils/apiClient.ts`

**REPLACE ENTIRE FILE**:

```typescript
import axios, { AxiosError } from 'axios'
import { apiErrorBoundary } from '@spinr/shared/hooks'
import store from '../store'

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://localhost:8000'

const apiClient = axios.create({
  baseURL: BACKEND_URL,
  timeout: 10000,
  withCredentials: true,  // ← AUTO-SEND COOKIES
  headers: {
    'Content-Type': 'application/json',
    // Remove: Authorization: `Bearer ${token}`
  },
})

/**
 * Response interceptor: Handle 401 (expired auth token)
 * Trigger auto-refresh + retry
 */
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config

    // If 401 and we haven't already tried refreshing
    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest.headers['X-Retry-Attempted']
    ) {
      try {
        // POST /auth/refresh
        // Browser auto-sends refresh_token cookie
        await apiClient.post('/auth/refresh')

        // Mark as attempted (prevent infinite loop)
        originalRequest.headers['X-Retry-Attempted'] = 'true'

        // Retry original request
        // Browser auto-sends new auth_token cookie
        return apiClient.request(originalRequest)
      } catch (refreshError) {
        // Refresh failed: token invalid, force logout
        store.dispatch({ type: 'LOGOUT' })
        // Redirect to login
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default apiClient
```

---

## Step 7: Remove localStorage Token Sync

### File: `rider-app/hooks/useAuth.ts`

**BEFORE**:
```typescript
function useAuth() {
  useEffect(() => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      setUser(decodeToken(token))
    }
  }, [])

  return { user, login, logout }
}
```

**AFTER**:
```typescript
function useAuth() {
  // Remove localStorage sync
  // Token is now in HTTP-only cookie
  // On mount, the API will auto-restore session via cookie

  useEffect(() => {
    // Optional: Fetch current user from /auth/me to restore session
    apiClient.get('/auth/me')
      .then(res => setUser(res.data.user))
      .catch(() => setUser(null))  // Not logged in
  }, [])

  return { user, login, logout }
}
```

### File: `rider-app/store/rideStore.ts`

**REMOVE ALL** lines like:
```typescript
// Delete these:
const token = localStorage.getItem('auth_token')
const user = decodeJWT(token)
const isLoggedIn = !!localStorage.getItem('auth_token')
```

Replace with:
```typescript
// Use Redux/Zustand store instead
const user = useSelector(state => state.auth.user)
const isLoggedIn = !!user
```

---

## Step 8: Same for Driver & Admin

### File: `driver-app/utils/apiClient.ts`
**Copy `rider-app/utils/apiClient.ts` exactly**

### File: `driver-app/hooks/useAuth.ts`
**Copy `rider-app/hooks/useAuth.ts` exactly**

### File: `admin-dashboard/lib/api.ts`

**Same pattern**:
```typescript
const adminClient = axios.create({
  baseURL: BACKEND_URL,
  withCredentials: true,  // ← CRITICAL
})

// Same 401 interceptor
```

---

## Step 9: Testing

### Backend Unit Test

**File**: `backend/tests/test_cookie_auth.py` (NEW)

```python
import pytest
from fastapi.testclient import TestClient
from backend.server import app

client = TestClient(app)


@pytest.mark.unit
def test_login_sets_httponly_cookie():
    """Verify login response includes Set-Cookie header with HttpOnly flag."""
    response = client.post('/auth/login', json={
        'phone': '+16475551234',
        'otp': '123456'
    })
    
    assert response.status_code == 200
    
    # Check Set-Cookie header
    set_cookie = response.headers.get('set-cookie', '')
    assert 'auth_token=' in set_cookie
    assert 'HttpOnly' in set_cookie
    assert 'Secure' in set_cookie  # In production
    assert 'SameSite=Strict' in set_cookie
    
    # Verify no token in response body
    assert 'access_token' not in response.json()


@pytest.mark.unit
def test_401_on_missing_auth_cookie():
    """Verify protected endpoint returns 401 without auth_token cookie."""
    response = client.get('/rides')
    assert response.status_code == 401


@pytest.mark.unit
def test_refresh_updates_auth_cookie(mock_auth_user):
    """Verify /auth/refresh issues new auth_token cookie."""
    # Login first (sets cookies)
    login_response = client.post('/auth/login', json={
        'phone': '+16475551234',
        'otp': '123456'
    })
    assert login_response.status_code == 200
    
    # Get cookies from login response
    cookies = client.cookies
    
    # Call refresh
    refresh_response = client.post('/auth/refresh')
    assert refresh_response.status_code == 200
    
    # Verify new cookie issued
    new_cookie = refresh_response.headers.get('set-cookie', '')
    assert 'auth_token=' in new_cookie


@pytest.mark.unit
def test_logout_clears_cookies():
    """Verify logout response includes Set-Cookie with max_age=0."""
    # Login
    client.post('/auth/login', json={
        'phone': '+16475551234',
        'otp': '123456'
    })
    
    # Logout
    logout_response = client.post('/auth/logout')
    assert logout_response.status_code == 200
    
    # Verify cookie cleared (max_age=0 or path deleted)
    set_cookie = logout_response.headers.get('set-cookie', '')
    assert 'auth_token=' in set_cookie
    # FastAPI's delete_cookie sets max_age=0 or expires in past
```

### Frontend Integration Test

**File**: `rider-app/__tests__/auth.integration.ts` (NEW)

```typescript
import { apiClient } from '@spinr/utils/apiClient'
import { login, logout } from '@spinr/utils/auth'

describe('HttpOnly Cookie Auth', () => {
  it('login should not store token in localStorage', async () => {
    // Mock backend response
    fetchMock.post('/auth/login', { user: { id: '123' } })

    await login('+16475551234', '123456')

    // Verify localStorage is empty
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })

  it('401 should trigger auto-refresh and retry', async () => {
    // First call returns 401
    fetchMock.get('/rides', { status: 401 }, { overwriteRoutes: false })
    // Refresh succeeds
    fetchMock.post('/auth/refresh', { status: 200 })
    // Retry succeeds
    fetchMock.get('/rides', { rides: [] })

    const response = await apiClient.get('/rides')

    expect(response.status).toBe(200)
    expect(response.data.rides).toEqual([])
    
    // Verify refresh was called
    expect(fetchMock.calls().filter(c => c[0] === '/auth/refresh')).toHaveLength(1)
  })

  it('logout should clear cookies', async () => {
    // Login first
    fetchMock.post('/auth/login', { user: { id: '123' } })
    await login('+16475551234', '123456')

    // Logout
    fetchMock.post('/auth/logout', { status: 200 })
    await logout()

    // Next request without cookie should fail
    fetchMock.get('/rides', { status: 401 })
    const response = await apiClient.get('/rides')
    
    expect(response.status).toBe(401)
  })
})
```

---

## Step 10: Staging Verification Checklist

### Browser (Admin Dashboard)

```bash
# Login to admin.spinrvm-staging.ca
# Open DevTools → Application → Cookies
```

**Verify**:
- [ ] `auth_token` cookie exists
- [ ] `auth_token` has HttpOnly ✅ (no "Http" column if it's writable from JS)
- [ ] `auth_token` has Secure ✅
- [ ] `auth_token` has SameSite=Strict ✅
- [ ] No Authorization header in requests (Network tab)
- [ ] Response has Set-Cookie headers with auth_token

### Mobile (Rider App)

**Test on actual device**:
```bash
# Deploy to EAS preview
eas update --channel preview --platform ios
eas update --channel preview --platform android

# On device:
# 1. Login
# 2. Request ride
# 3. Verify request succeeds (phone number, address filled in)
# 4. Kill app
# 5. Restart app
# 6. Should still be logged in (cookie persisted)
# 7. Logout
# 8. Restart app
# 9. Should see login screen (cookie cleared)
```

---

## Rollback Procedure

### If Cookies Fail in Staging

**Option 1: Quick Revert**
```bash
git revert <commit>
git push origin main

# Backend auto-deploys
# Old localStorage flow still works
```

**Option 2: Feature Flag** (Graceful)
```python
# backend/core/config.py
USE_HTTPONLY_COOKIES = os.getenv('USE_HTTPONLY_COOKIES', 'false') == 'true'

# In login endpoint:
if settings.USE_HTTPONLY_COOKIES:
    CookieManager.set_auth_cookie(...)
else:
    return JSONResponse({'access_token': token})  # Old way
```

Then toggle env var if needed.

---

## Summary: All Files Changed

### Backend (9 files)
1. ✅ `backend/utils/cookie_manager.py` (NEW)
2. ✅ `backend/core/config.py` (ADD COOKIE_* settings)
3. ✅ `backend/core/middleware.py` (CORS: allow_credentials=True)
4. ✅ `backend/routes/auth.py` (login, refresh, logout)
5. ✅ `backend/routes/admin/auth.py` (admin login, logout)
6. ✅ `backend/tests/test_cookie_auth.py` (NEW)

### Frontend (7 files across 3 apps)
7. ✅ `rider-app/utils/apiClient.ts` (withCredentials + 401 handler)
8. ✅ `rider-app/hooks/useAuth.ts` (remove localStorage sync)
9. ✅ `rider-app/__tests__/auth.integration.ts` (NEW)
10. ✅ `driver-app/utils/apiClient.ts` (copy from rider-app)
11. ✅ `driver-app/hooks/useAuth.ts` (copy from rider-app)
12. ✅ `admin-dashboard/lib/api.ts` (same pattern)
13. ✅ `admin-dashboard/__tests__/auth.ts` (NEW)

**Total touched**: ~13–15 files

---

## Security Review Checklist

- [ ] No JWT tokens in response JSON
- [ ] Cookies have HttpOnly flag ✅
- [ ] Cookies have Secure flag (HTTPS only in prod) ✅
- [ ] Cookies have SameSite=Strict ✅
- [ ] CORS allows credentials ✅
- [ ] No tokens in browser console (F12) ✅
- [ ] localStorage.getItem('auth_token') returns null ✅
- [ ] 401 triggers auto-refresh ✅
- [ ] Logout clears both cookies ✅
- [ ] Mobile device can login and maintain session ✅

