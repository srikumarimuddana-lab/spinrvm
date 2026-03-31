---
description: Frontend deployment workflow for rider-app, driver-app, and admin-dashboard
---

# Frontend Deployment Workflow

Reference `.agents/roles/devops-engineer.md` for infrastructure details.

## Rider App / Driver App (Expo EAS)

### Step 1: Pre-Deployment Checks
// turbo
```bash
cd rider-app && npx tsc --noEmit 2>&1 | tail -10
```
- [ ] No TypeScript errors
- [ ] No console.log in production code
- [ ] API base URL points to production backend
- [ ] All environment variables set for production

### Step 2: Build
```bash
# Build for both platforms
eas build --platform all --profile production

# Or build for specific platform
eas build --platform ios --profile production
eas build --platform android --profile production
```

### Step 3: Submit to App Stores
```bash
# iOS
eas submit --platform ios

# Android
eas submit --platform android
```

### Step 4: Verify
- [ ] App installs and launches
- [ ] Login flow works
- [ ] Core features functional (ride booking, map, payments)
- [ ] Push notifications working

---

## Admin Dashboard (Vercel)

### Step 1: Pre-Deployment Checks
// turbo
```bash
cd admin-dashboard && npx tsc --noEmit 2>&1 | tail -10
```
- [ ] No TypeScript errors
- [ ] No build errors: `npm run build`
- [ ] API URL points to production backend

### Step 2: Deploy
```bash
# Via Vercel CLI
cd admin-dashboard && npx vercel --prod

# Or via git push (if Vercel Git integration is configured)
git push origin main
```

### Step 3: Verify
- [ ] Dashboard loads at production URL
- [ ] Admin login works
- [ ] CORS allows admin dashboard origin
- [ ] Data displays correctly

---

## Rollback Procedures

### Expo EAS
```bash
# Publish an update that reverts to previous version
eas update --branch production --message "rollback to vX.X.X"
```

### Vercel
```bash
# Rollback via Vercel CLI or dashboard
vercel rollback
```

## Post-Deployment
- [ ] Update `.agents/docs/deployment-guide.md` with deployment date
- [ ] Check Sentry for new errors from deployed version
- [ ] Monitor user reports for 24 hours
