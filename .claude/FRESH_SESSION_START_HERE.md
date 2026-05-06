# Fresh Session: P0 Sprint Phase 3 Implementation

**Date**: 2026-05-01  
**Status**: Ready to implement HttpOnly token storage (14 files remaining)  
**Branch**: `feature/httponly-tokens` (pushed to GitHub, ready to code)  
**Context**: Full (100% available in fresh session)

---

## ⚡ Quick Start (Next 30 Seconds)

```bash
cd C:/Users/TabUsrDskOff111/Documents/Spinrvm/spinrvm
git checkout feature/httponly-tokens
git pull origin feature/httponly-tokens
```

---

## 📖 Everything You Need

### Design Documents (Read First)
- **`.claude/P3_HTTPONLY_DETAILED_DESIGN.md`** ← Copy-paste code for all 14 files (Sections "Step 2–9")
- `.claude/P0_ARCHITECTURE_BLUEPRINT.md` — overview of all 6 P0s
- `.claude/P0_TASK_BREAKDOWN.md` — detailed tasks
- `.claude/project_p0_execution_status.md` — progress tracking

### What's Already Done ✅
- ✅ `backend/utils/cookie_manager.py` (cookie utility class)
- ✅ All architecture designed
- ✅ All code documented (ready to copy-paste)
- ✅ Branch created + pushed to GitHub

### What You're Doing Now
Implement the remaining **14 files** using the exact code in `P3_HTTPONLY_DETAILED_DESIGN.md`.

**Order** (follow this exactly):
1. `backend/core/config.py` — Add COOKIE_* settings (see Step 2)
2. `backend/core/middleware.py` — Update CORS (Step 2)
3. `backend/routes/auth.py` — Login/refresh/logout (Step 3)
4. `backend/routes/admin/auth.py` — Admin endpoints (Step 3)
5. `backend/tests/test_cookie_auth.py` — Backend tests (Step 9)
6. `rider-app/utils/apiClient.ts` — Axios client (Step 6)
7. `rider-app/hooks/useAuth.ts` — Remove localStorage (Step 7)
8. `rider-app/__tests__/auth.integration.ts` — Frontend tests (Step 9)
9. `driver-app/utils/apiClient.ts` — Copy from rider-app (Step 8)
10. `driver-app/hooks/useAuth.ts` — Copy from rider-app (Step 8)
11. `admin-dashboard/lib/api.ts` — Admin client (Step 8)
12. `admin-dashboard/__tests__/auth.ts` — Admin tests (Step 9)
13. Remove `localStorage.getItem('auth_token')` from all stores
14. Test locally: `pytest backend/tests/test_cookie_auth.py -v`

---

## 🎯 Success Checklist

**After Implementation**:
- [ ] All 14 files created/modified
- [ ] Backend tests pass: `pytest backend/tests/test_cookie_auth.py -v`
- [ ] No linting errors: `ruff check backend/`
- [ ] No type errors: `mypy backend/routes/auth.py`
- [ ] Commit with message: `feat(p3): implement httponly token storage`
- [ ] Push to GitHub: `git push origin feature/httponly-tokens`

**After Merge to Main**:
- [ ] Deploy to staging
- [ ] Browser DevTools: Cookies tab shows `auth_token` with HttpOnly ✅
- [ ] Mobile: Login → token in secure storage, not localStorage
- [ ] 401 → auto-refresh → retry succeeds
- [ ] Logout clears cookies

---

## 🚀 Status Board

### Phase 1 (Complete ✅)
- PR #240 merged
- Rating crash, GPS OOM, SOS fixes live in production

### Phase 2 (Pending)
- Branch: `feature/payment-idempotency` (created, not started)
- Will start after Phase 3 is staging-tested

### Phase 3 (NOW) 
- Branch: `feature/httponly-tokens` (current, 10% complete)
- **Your task**: Complete remaining 14 files
- **Estimated time**: 2–3 hours for coding + testing
- **After**: Merge to main + staging test (same day)

---

## 📝 References

**Key Files You'll Edit**:
- `backend/core/config.py` — settings
- `backend/routes/auth.py` — main auth logic
- `rider-app/utils/apiClient.ts` — token transport
- `rider-app/hooks/useAuth.ts` — session management

**All code is in**: `.claude/P3_HTTPONLY_DETAILED_DESIGN.md` (search for "Step 2", "Step 3", etc.)

**Memory tracking**: `.claude/project_p0_execution_status.md` (update after you finish)

---

## ❓ If You Get Stuck

1. **Config errors**: Check `backend/core/config.py` — COOKIE_DOMAIN might need adjustment
2. **CORS errors**: Verify `allow_credentials=True` in middleware
3. **Cookie not sent**: Check `withCredentials: true` in axios client
4. **401 loops**: Verify 401 interceptor checks for `X-Retry-Attempted` header

All troubleshooting in `P3_HTTPONLY_DETAILED_DESIGN.md` "Risk Assessment" section.

---

## 🎬 Go!

Open `.claude/P3_HTTPONLY_DETAILED_DESIGN.md` **Step 2** and start copying code into `backend/core/config.py`.

**You've got this.** 💪

