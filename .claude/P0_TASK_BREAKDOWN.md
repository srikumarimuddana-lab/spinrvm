# P0 Implementation Task Breakdown

**Master Task**: `P0 Sprint: Merge PR #240 and execute phases 2-3`

---

## PHASE 1: Merge Complete Fixes (PR #240) — ~30 minutes

### P1-1: Verify PR #240 CI Status
- **Owner**: QA
- **Effort**: 5 min
- **Steps**:
  1. `gh pr checks 240` → all green (backend-test, driver-app-test, admin-test, coverage, security)
  2. Known issue: G4b yarn audit (Expo @xmldom/xmldom upstream, non-blocking) OK to ignore
  3. If any new failures: investigate via `.claude/context/sprint-current.md` for known causes

### P1-2: Merge PR #240 to main
- **Owner**: Merge gate keeper
- **Effort**: 5 min
- **Steps**:
  1. `gh pr merge 240 --squash` (one commit for clarity)
  2. Verify merge completes without conflicts
  3. `git checkout main && git pull origin main`

### P1-3: Trigger Auto-Deploy to Railway
- **Owner**: DevOps monitor
- **Effort**: 10 min (wait for deploy)
- **Steps**:
  1. GitHub Action should auto-trigger on main merge
  2. Monitor Railway dashboard for deployment status
  3. Verify backend is live: `curl https://spinr-api.railway.app/health`

### P1-4: Smoke Test All Three Surfaces
- **Owner**: QA
- **Effort**: 10 min
- **Devices**: One driver phone, one rider phone, one browser (admin)
- **Test Plan**:
  - **Rider App**: Login → Request ride → Complete → Rate (verify no PGRST204 crash)
  - **Driver App**: Login → Accept ride → Navigate → Complete (verify no GPS lag/OOM)
  - **Admin Dashboard**: Login → View analytics (verify auth works with new routing)
  
**Verification**:
```bash
# Backend health
curl https://spinr-api.railway.app/health
# Should return 200 { "status": "ok" }
```

**Done**: Phase 1 complete when all 4 checks pass ✅

---

## PHASE 2: Payment Idempotency — ~1 day

### P2-1: Implement Backend Idempotency Cache
- **Owner**: Backend engineer
- **Effort**: 2 hours
- **File**: `backend/routes/rides.py`, `process_payment()` function
- **Changes**:
  ```python
  @router.post("/rides/{ride_id}/rate")
  async def rate_ride(ride_id: str, request: Request, body: RateRequest):
      idempotency_key = request.headers.get("Idempotency-Key")
      
      if not idempotency_key:
          raise HTTPException(status_code=400, detail="Idempotency-Key required")
      
      # Check cache
      cached = redis_client.get(f"payment:{idempotency_key}")
      if cached:
          return json.loads(cached)
      
      # Process payment
      result = process_payment(...)
      
      # Cache for 24h
      redis_client.setex(f"payment:{idempotency_key}", 86400, json.dumps(result))
      return result
  ```
- **Testing**: Unit test in `backend/tests/test_idempotency.py`
- **Verification**: 
  - `pytest backend/tests/test_idempotency.py -v`
  - Verify same key returns cached result
  - Verify different key charges separately

### P2-2: Update Frontend to Send Idempotency-Key
- **Owner**: Frontend engineer (rider-app)
- **Effort**: 1 hour
- **Files**:
  - `rider-app/utils/attemptRidePayment.ts`
  - `rider-app/app/ride-completed.tsx`
- **Changes**:
  ```typescript
  // rider-app/utils/attemptRidePayment.ts
  export async function attemptRidePayment(
    rideId: string,
    tip: Decimal,
    attempt: number = 1
  ) {
    const idempotencyKey = `ride_${rideId}_attempt_${attempt}`
    
    return await apiClient.post(
      `/rides/${rideId}/rate`,
      { rating, tip, text },
      {
        headers: {
          'Idempotency-Key': idempotencyKey
        }
      }
    )
  }
  ```
- **Verification**: 
  - Manual test: submit payment, network fails, retry → charge only once

### P2-3: Write Integration Tests
- **Owner**: Test engineer
- **Effort**: 1.5 hours
- **Files**:
  - `backend/tests/test_idempotency.py` (new)
  - `rider-app/__tests__/payment.integration.ts` (add tests)
- **Test Cases**:
  - ✅ Duplicate key returns cached result
  - ✅ Different key charges separately
  - ✅ Cache expires after 24h
  - ✅ Network timeout + retry uses same key

### P2-4: Staging Deployment
- **Owner**: QA
- **Effort**: 1 hour
- **Steps**:
  1. Deploy backend changes to staging
  2. Rider app connects to staging backend
  3. Run full payment flow: login → request → complete → rate with tip
  4. Verify payment successful and receipt shows correct amount
  5. Monitor logs for duplicate charge warnings (should be zero)

**Done**: Phase 2 complete when:
- ✅ All idempotency tests pass (backend + frontend)
- ✅ Staging smoke test succeeds
- ✅ No duplicate charges in logs

---

## PHASE 3: HttpOnly Token Storage — ~3–4 days

### P3-1: Backend Cookie Middleware
- **Owner**: Backend security lead
- **Effort**: 4 hours
- **Files**:
  - `backend/core/middleware.py` (new: add cookie middleware)
  - `backend/utils/cookie_manager.py` (new)
  - `backend/core/config.py` (add COOKIE_* settings)

**Implementation** (see detailed design below):
```python
# backend/utils/cookie_manager.py (new file)
class CookieManager:
    def set_auth_cookie(self, response: Response, token: str, ttl_minutes: int = 15):
        response.set_cookie(
            key="auth_token",
            value=token,
            max_age=ttl_minutes * 60,
            httponly=True,
            secure=settings.ENVIRONMENT == "production",
            samesite="Strict",
            domain=settings.COOKIE_DOMAIN
        )
    
    def clear_auth_cookie(self, response: Response):
        response.delete_cookie("auth_token")
    # ... refresh_token methods
```

**Verification**:
```bash
pytest backend/tests/test_cookie_auth.py -v
# Should pass: test_login_sets_httponly_cookies, test_401_on_missing_cookie, etc.
```

### P3-2: Update Auth Endpoints
- **Owner**: Backend engineer
- **Effort**: 3 hours
- **Files**:
  - `backend/routes/auth.py` (login, refresh, logout)
  - `backend/routes/admin/auth.py` (admin login, logout)

**Changes**:
- `POST /auth/login` → set auth + refresh cookies (remove JWT from response body)
- `POST /auth/refresh` → update auth cookie if near expiry
- `POST /auth/logout` → clear cookies + revoke token
- Same for admin routes

**Verification**: 
- Unit tests in `backend/tests/test_auth.py`
- Verify Set-Cookie headers in response
- Verify no tokens in response JSON

### P3-3: Frontend: Rider App
- **Owner**: Frontend engineer
- **Effort**: 2.5 hours
- **Files**:
  - `rider-app/utils/apiClient.ts` (enable credentials, remove localStorage)
  - `rider-app/hooks/useAuth.ts` (remove token sync)
  - `rider-app/store/rideStore.ts` (remove token getters)

**Changes**:
```typescript
// rider-app/utils/apiClient.ts
const client = axios.create({
  baseURL: BACKEND_URL,
  withCredentials: true,  // ← Auto-send cookies
})

// Remove: localStorage.setItem('auth_token', token)

// 401 interceptor
client.interceptors.response.use(
  res => res,
  async err => {
    if (err.response?.status === 401) {
      try {
        await apiClient.post('/auth/refresh')  // Cookie auto-sent
        return apiClient.request(err.config)
      } catch (e) {
        dispatch(logout())
      }
    }
    throw err
  }
)
```

**Verification**:
```bash
# Dev: run app, login, check browser Network tab
# No Authorization header in requests ✅
# Response headers show Set-Cookie ✅
# Next request has Cookie header ✅
```

### P3-4: Frontend: Driver App
- **Owner**: Frontend engineer
- **Effort**: 2.5 hours
- **Files**: Same as rider app, different directory
  - `driver-app/utils/apiClient.ts`
  - `driver-app/hooks/useAuth.ts`
  - `driver-app/store/driverStore.ts`

### P3-5: Frontend: Admin Dashboard
- **Owner**: Frontend engineer
- **Effort**: 1.5 hours
- **Files**:
  - `admin-dashboard/lib/api.ts`
  - `admin-dashboard/hooks/useAuth.ts`

### P3-6: Write Cookie Auth Tests
- **Owner**: Test engineer
- **Effort**: 3 hours
- **Files**:
  - `backend/tests/test_cookie_auth.py` (new)
  - `rider-app/__tests__/auth.integration.ts` (new)
  - `driver-app/__tests__/auth.integration.ts` (new)

**Test Cases**:
- ✅ Login sets HttpOnly cookie
- ✅ Refresh updates auth cookie
- ✅ 401 triggers auto-refresh
- ✅ Logout clears cookies
- ✅ No tokens in localStorage

### P3-7: Staging: Backend + Admin Dashboard
- **Owner**: QA
- **Effort**: 2 hours
- **Steps**:
  1. Deploy backend + admin-dashboard to staging
  2. Login to admin console via browser
  3. Open DevTools → Application → Cookies
  4. Verify `auth_token` has HttpOnly ✅, Secure ✅, SameSite=Strict ✅
  5. Make an authenticated request (e.g., GET /analytics)
  6. Verify request includes Cookie header with `auth_token`
  7. Logout and verify cookies cleared

### P3-8: Staging: Rider App
- **Owner**: QA
- **Effort**: 1.5 hours
- **Steps**:
  1. Build rider-app with staging backend URL
  2. Deploy via EAS update to preview channel
  3. Test on actual device (iOS + Android):
     - Login → token in secure cookie ✅
     - Make authenticated request (GET /profile) → succeeds ✅
     - Kill app, restart → still logged in ✅
     - Logout → cookie cleared ✅
  4. Test token rotation:
     - Capture auth cookie expiry time
     - Wait near expiry
     - Make request → auto-refresh triggers ✅

### P3-9: Staging: Driver App
- **Owner**: QA
- **Effort**: 1.5 hours
- **Steps**: Same as rider app

### P3-10: Production Rollout
- **Owner**: Release manager
- **Effort**: 2 hours
- **Sequence**:
  1. Deploy backend changes (Railway auto-deploy on main merge)
  2. Deploy admin-dashboard (Vercel)
  3. Deploy rider-app (EAS update to production)
  4. Deploy driver-app (EAS update to production)
  5. Monitor logs for 401 errors (should be zero for existing users)
  6. If issues: rollback via revert commit + EAS update

**Monitoring**:
```bash
# Check for auth errors
curl https://api-spinr.spinr.ca/sentry/logs?type=auth_error&hours=1

# Verify no 401s in prod
curl https://api-spinr.spinr.ca/metrics?metric=http_401&hours=1
```

**Done**: Phase 3 complete when:
- ✅ All auth tests pass (backend + frontend)
- ✅ Staging smoke test succeeds on all three surfaces
- ✅ Production rollout complete with zero auth errors
- ✅ Tokens in HTTP-only cookies across all apps

---

## Dependency Graph

```
Phase 1 (Merge PR #240)
    ↓
Phase 2 (Idempotency) ← can run in parallel with:
Phase 3 (HttpOnly tokens) ← Backend (P3-1 to P3-6) can start immediately
                           ← Frontend apps (P3-3 to P3-5) must wait for backend deployed to staging
```

**Critical Path**: Phase 1 → Phase 3 (4 days). Phase 2 can overlap.

---

## Rollback Plan

### If Phase 1 Fails (PR #240)
- Revert merge: `git revert <commit>`
- Fix issues on branch, retry PR

### If Phase 2 Fails (Idempotency)
- Disable idempotency check: remove middleware
- API still works without it (just lose duplicate protection)
- Retry implementation on separate PR

### If Phase 3 Fails (HttpOnly Tokens)
**Before Production**:
- Keep staging branch separate; don't merge to main
- Revert backend changes, keep old localStorage flow

**After Production Rollout**:
- Short-term: Revert backend Set-Cookie headers
  - Apps auto-fallback to older localStorage token storage
  - Users remain logged in (token still valid in localStorage)
  - Immediate recovery (< 5 min)
  
- Investigate: Why cookie flow failed (CORS? Mobile? 3DS?)
  - Add detailed logging to cookie middleware
  - Test in staging with failure scenarios
  - Retry with fix

---

## Success Metrics

### Phase 1
- [ ] PR #240 merged to main
- [ ] Railway deploy successful
- [ ] All three surfaces smoke test green (login → action → complete)
- [ ] Zero PGRST204 errors in logs (rating crash fixed)
- [ ] Zero location throttle warnings (GPS OOM fixed)
- [ ] Emergency contacts receive SMS on SOS trigger (SOS fixed)

### Phase 2
- [ ] Idempotency tests pass (100%)
- [ ] Staging payment flow succeeds
- [ ] Duplicate payment scenarios return cached result
- [ ] Zero duplicate charges in payment logs

### Phase 3
- [ ] All auth tests pass (100%)
- [ ] Browser DevTools shows HttpOnly cookie
- [ ] Mobile device login succeeds without localStorage
- [ ] Auto-refresh on 401 works seamlessly
- [ ] Zero auth failures in production logs after rollout
- [ ] User sessions uninterrupted across token rotation

---

## Ownership & Timeline

| Phase | Owner | Start | Duration | End |
|-------|-------|-------|----------|-----|
| 1 | QA + DevOps | Tomorrow | 0.5 hours | Tomorrow EOD |
| 2 | Backend + Frontend | Tomorrow PM | 1 day | Day 2 EOD |
| 3 | Security lead + Full stack | Day 2 | 3–4 days | Day 5–6 |

**Overall Timeline**: 5–6 days from merge to all 6 P0s resolved and in production.

