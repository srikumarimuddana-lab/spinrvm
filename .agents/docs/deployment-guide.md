# Spinr Deployment Guide

> **Living Document** — Update this file whenever deployment procedures change.
> Last updated: 2026-04-16

## Infrastructure Overview

| Component | Hosting | URL |
|-----------|---------|-----|
| Backend API | Railway | `https://<app>.up.railway.app` |
| Rider App | Expo EAS (iOS/Android) | App Store / Play Store |
| Driver App | Expo EAS (iOS/Android) | App Store / Play Store |
| Admin Dashboard | Vercel | `https://<project>.vercel.app` |
| Database | Supabase | Supabase dashboard |

---

## Backend Deployment (Railway)

### Config Files
- `railway.json` — Railway build + deploy configuration
- `backend/Dockerfile` — Container build
- `backend/requirements.txt` — Python dependencies

### Prerequisites
```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Link the project (one-time)
railway link
```

### Deploy Steps
```bash
# 1. Ensure tests pass
cd backend && python -m pytest -v

# 2. Deploy
railway up --service backend

# 3. Verify
railway status
railway logs --service backend
```

### Environment Variables
Set via Railway CLI or dashboard:
```bash
railway variables set JWT_SECRET=<value> --service backend
railway variables set SUPABASE_URL=<value> --service backend
railway variables set SUPABASE_KEY=<value> --service backend
railway variables set STRIPE_SECRET_KEY=<value> --service backend
railway variables set TWILIO_ACCOUNT_SID=<value> --service backend
railway variables set TWILIO_AUTH_TOKEN=<value> --service backend
railway variables set SENTRY_DSN=<value> --service backend
railway variables set GOOGLE_API_KEY=<value> --service backend
railway variables set RATE_LIMIT_REDIS_URL=<redis-url> --service backend
```

### Rollback
Railway dashboard → backend service → **Deployments** tab → select last
known-good deployment → **Redeploy**. CLI alternative:
```bash
railway redeploy --service backend
```

---

## Rider App / Driver App (Expo EAS)

### Prerequisites
```bash
# Install EAS CLI
npm install -g eas-cli

# Login
eas login
```

### Config Files
- `app.config.ts` — App configuration
- `eas.json` — EAS build profiles

### Build & Deploy
```bash
cd rider-app

# Development build
eas build --platform all --profile development

# Production build
eas build --platform all --profile production

# Submit to stores
eas submit --platform ios
eas submit --platform android
```

### OTA Updates
```bash
# Push over-the-air update (no store submission needed)
eas update --branch production --message "description of update"
```

### Environment Variables
Set in `app.config.ts` or `.env`:
```
EXPO_PUBLIC_API_URL=https://<backend>.up.railway.app
EXPO_PUBLIC_SUPABASE_URL=<supabase-url>
EXPO_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<key>
```

---

## Admin Dashboard (Vercel)

### Prerequisites
```bash
npm install -g vercel
vercel login
```

### Deploy
```bash
cd admin-dashboard

# Preview deployment
vercel

# Production deployment
vercel --prod
```

### Auto-deployment
If Vercel Git integration is connected, pushes to `main` auto-deploy.

### Environment Variables
Set in Vercel dashboard:
```
NEXT_PUBLIC_API_URL=https://<backend>.up.railway.app
NEXT_PUBLIC_SUPABASE_URL=<supabase-url>
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
```

---

## Health Check Endpoints
| Component | Check | Expected |
|-----------|-------|----------|
| Backend | `GET /api/v1/health` | 200 OK |
| Supabase | Supabase dashboard | Green status |
| Vercel | Admin dashboard URL | Page loads |

## Monitoring
- **Sentry**: Error tracking — configured in `backend/server.py`
- **Railway Metrics**: CPU, memory, request count (Railway dashboard → Observability)
- **Vercel Analytics**: Page load times, errors
