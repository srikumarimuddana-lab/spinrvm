# Spinr Deployment Guide

> **Living Document** — Update this file whenever deployment procedures change.
> Last updated: 2026-03-26

## Infrastructure Overview

| Component | Hosting | URL |
|-----------|---------|-----|
| Backend API | Fly.io | `https://<app>.fly.dev` |
| Rider App | Expo EAS (iOS/Android) | App Store / Play Store |
| Driver App | Expo EAS (iOS/Android) | App Store / Play Store |
| Admin Dashboard | Vercel | `https://<project>.vercel.app` |
| Database | Supabase | Supabase dashboard |

---

## Backend Deployment (Fly.io)

### Config Files
- `fly.toml` — Fly.io app configuration
- `Dockerfile` — Container build
- `Procfile` — Process command

### Prerequisites
```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Login
fly auth login
```

### Deploy Steps
```bash
# 1. Ensure tests pass
cd backend && python -m pytest -v

# 2. Deploy
fly deploy

# 3. Verify
fly status
fly logs -n 20
```

### Environment Variables
Set via Fly.io secrets:
```bash
fly secrets set JWT_SECRET=<value>
fly secrets set SUPABASE_URL=<value>
fly secrets set SUPABASE_KEY=<value>
fly secrets set STRIPE_SECRET_KEY=<value>
fly secrets set TWILIO_ACCOUNT_SID=<value>
fly secrets set TWILIO_AUTH_TOKEN=<value>
fly secrets set SENTRY_DSN=<value>
fly secrets set GOOGLE_API_KEY=<value>
```

### Rollback
```bash
fly releases
fly deploy --image <previous-image>
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
EXPO_PUBLIC_API_URL=https://<backend>.fly.dev
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
NEXT_PUBLIC_API_URL=https://<backend>.fly.dev
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
- **Fly.io Metrics**: CPU, memory, request count
- **Vercel Analytics**: Page load times, errors
