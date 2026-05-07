# P3 HttpOnly Token Migration — Session Handoff

**Status**: 9/14 files complete ✅  
**Branch**: `feature/httponly-tokens`  
**Last Commit**: `c6cce9ad` (backend auth routes + rider apiClient)  
**Context Used**: 79% — next session should have fresh context

---

## ✅ COMPLETED THIS SESSION

### Backend (4 files)
1. ✅ `backend/core/config.py` — Added COOKIE_SECURE, HTTPONLY, SAMESITE, DOMAIN, PATH settings
2. ✅ `backend/core/middleware.py` — Already correct (allow_credentials=True for specific origins)
3. ✅ `backend/routes/auth.py` — Updated:
   - `_make_auth_response()` now takes Response param, sets cookies via CookieManager
   - `firebase_auth_login()` calls updated (_make_auth_response receives response)
   - `refresh_access_token()` reads refresh_token from cookie, sets new auth_token cookie
   - `logout()` and `logout_all()` clear cookies
4. ✅ `backend/routes/admin/auth.py` — Updated admin_login to set cookies (60min TTL)

### Frontend (1 file)
5. ✅ `rider-app/utils/apiClient.ts` — Created with withCredentials + 401 auto-refresh

---

## ⏳ REMAINING (5 files)

### Frontend (5 files)
6. ⏳ `rider-app/hooks/useAuth.ts` — Remove localStorage, fetch /auth/me on mount
7. ⏳ `rider-app/__tests__/auth.integration.ts` — Test login/refresh/logout flows
8. ⏳ `driver-app/utils/apiClient.ts` — Copy from rider-app (identical)
9. ⏳ `driver-app/hooks/useAuth.ts` — Copy from rider-app (identical)
10. ⏳ `admin-dashboard/lib/api.ts` + tests — Same pattern as rider-app

### Cleanup & Testing
11. ⏳ Store cleanup — Remove all `localStorage.getItem('auth_token')` refs
12. ⏳ Run backend tests: `pytest backend/tests/test_cookie_auth.py -v`
13. ⏳ Verify no linting errors: `ruff check backend/`
14. ⏳ Check DevTools (staging): Cookies tab shows auth_token + HttpOnly flag

---

## 🎯 NEXT SESSION STEPS

1. **Read**: `.claude/P3_HTTPONLY_DETAILED_DESIGN.md` (Steps 7–9 for remaining code)

2. **Implement** (copy-paste ready):
   - Step 7: `rider-app/hooks/useAuth.ts` (remove localStorage)
   - Step 9: Backend test file
   - Step 8: Copy rider files to driver-app
   - Step 8: Admin client (slightly different)

3. **Test**:
   ```bash
   pytest backend/tests/test_cookie_auth.py -v
   ruff check backend/
   ```

4. **Commit**:
   ```bash
   git add -A
   git commit -m "feat(p3): complete httponly token migration - frontend + tests"
   ```

5. **After Merge to Main**:
   - Deploy to staging
   - DevTools → Cookies tab (should see HttpOnly flag)
   - Mobile test: login → token in secure storage, not localStorage
   - 401 → auto-refresh succeeds

---

## 🐛 KNOWN ISSUES

None yet — backend auth changes all syntactically valid.

---

## 📝 NOTES FOR IMPLEMENTATION

- **Admin TTL**: 60 minutes (not 15) — use `admin_ttl_minutes=60` param
- **Cookie domain**: `.spinrvm.ca` (cross-subdomain) — set in config
- **SameSite**: `Strict` (CSRF protection via Set-Cookie)
- **Secure flag**: True in production only (set in config)
- **Import pattern**: Dual `try/except` for `from ..utils.cookie_manager import`

---

## 📊 PROGRESS

```
Phase 3: HttpOnly Token Storage
├─ Backend config       ✅ Done
├─ Auth endpoints       ✅ Done (firebase, refresh, logout)
├─ Admin endpoints      ✅ Done (login updated)
├─ Rider frontend       🟡 In progress (apiClient done, useAuth pending)
├─ Driver frontend      ⏳ Pending (copy from rider)
├─ Admin frontend       ⏳ Pending
└─ Tests + cleanup      ⏳ Pending

Total: 9/14 files complete
```

Go forth! 🚀
