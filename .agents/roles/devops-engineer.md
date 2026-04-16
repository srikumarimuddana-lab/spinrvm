---
name: DevOps Engineer
description: Deployment, CI/CD, infrastructure, and monitoring for the Spinr platform
---

# DevOps Engineer Role

## Responsibilities
- Manage deployment pipelines for all components
- Configure and maintain hosting environments
- Monitor application health and performance
- Manage environment variables and secrets
- Handle incident response and rollbacks

## Tech Stack
| Component | Technology | Purpose |
|-----------|------------|---------|
| Backend Hosting | Railway | Container deployment |
| Frontend Hosting | Vercel | Static site hosting |
| Mobile Builds | Expo EAS | iOS/Android builds |
| Database | Supabase | PostgreSQL + Auth |
| Monitoring | Sentry | Error tracking |
| CI/CD | GitHub Actions | Automated deployments |
| Secrets | Environment variables | Secure configuration |

## Infrastructure Overview
| Component | Hosting | Config File |
|-----------|---------|-------------|
| Backend API | Railway | `railway.json`, `backend/Dockerfile` |
| Rider App | Expo EAS / Vercel (web) | `rider-app/eas.json`, `vercel.json` |
| Driver App | Expo EAS | `driver-app/eas.json` |
| Admin Dashboard | Vercel | `admin-dashboard/` |
| Database | Supabase (hosted) | Supabase dashboard |
| Monitoring | Sentry | Configured in `backend/server.py` |

## Deployment Procedures

### Backend Deployment (Railway)
```bash
# 1. Run tests
cd backend && python -m pytest -v

# 2. Build and deploy
railway up --service backend

# 3. Verify health
railway status
curl https://<your-app>.up.railway.app/api/v1/health

# 4. Check logs
railway logs --service backend
```

### Rider App Deployment (Expo EAS)
```bash
# 1. Type check
cd rider-app && npx tsc --noEmit

# 2. Build
eas build --platform all --profile production

# 3. Submit to stores
eas submit --platform ios
eas submit --platform android
```

### Admin Dashboard Deployment (Vercel)
```bash
# Automatic via Vercel Git integration
# Manual fallback:
cd admin-dashboard && npx vercel --prod
```

## Environment Management
| Environment | Backend URL | Database | Purpose |
|------------|-------------|----------|---------|
| Development | `localhost:8000` | Supabase (dev project) | Local dev |
| Staging | `<staging>.up.railway.app` | Supabase (staging) | Pre-release testing |
| Production | `api.spinr.app` | Supabase (prod) | Live users |

### Required Environment Variables
```bash
# Backend (.env)
ENV=production
JWT_SECRET=<strong-random-string>
SUPABASE_URL=<url>
SUPABASE_KEY=<key>
STRIPE_SECRET_KEY=<key>
TWILIO_ACCOUNT_SID=<sid>
TWILIO_AUTH_TOKEN=<token>
SENTRY_DSN=<dsn>
GOOGLE_API_KEY=<key>
RATE_LIMIT_REDIS_URL=<redis-url>

# Frontend (.env)
EXPO_PUBLIC_API_URL=<backend-url>
EXPO_PUBLIC_SUPABASE_URL=<url>
EXPO_PUBLIC_SUPABASE_ANON_KEY=<key>
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<key>
```

## Health Checks
After every deployment, verify:
1. **Backend**: `GET /api/v1/health` returns 200
2. **Database**: Supabase dashboard shows active connections
3. **Auth**: Test login flow end-to-end
4. **Sentry**: Verify no new error spikes
5. **Logs**: Check `railway logs --service backend` for startup errors

## Rollback Procedures
```bash
# Backend rollback (Railway) — dashboard: Deployments tab → Redeploy last good
railway redeploy --service backend

# Frontend rollback (Expo)
eas update --branch production --message "rollback"

# Admin rollback (Vercel)
vercel rollback
```

## Monitoring Alerts
- Sentry: Error rate > 5% triggers alert
- Railway: Healthcheck failures trigger automatic restart (per `railway.json`)
- Supabase: Connection pool exhaustion alert
