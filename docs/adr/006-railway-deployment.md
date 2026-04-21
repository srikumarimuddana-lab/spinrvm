# ADR-006: Railway as the primary hosting platform

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

Spinr needed a hosting platform for the FastAPI backend that could be operational quickly, support auto-deploy from GitHub, and offer Canadian data residency for PIPEDA compliance. The team has no DevOps/SRE capacity — zero Kubernetes or Terraform experience — and the initial runway assumption is Saskatchewan-scale load (hundreds of concurrent users, not millions).

Key requirements:
- Auto-deploy from `main` with zero manual steps
- Persistent WebSocket support (not serverless)
- Environment variable management without secrets in code
- Horizontal scaling (multiple replicas) for the rolling-deploy safety net
- No DBA or infra overhead
- Canadian (or at minimum US) data residency

Alternatives considered:

| Option | Rejected because |
|--------|-----------------|
| AWS ECS / Fargate | Significant operational overhead; IAM, VPC, load balancer config required |
| Fly.io | No Canadian region at time of decision; persistent WebSocket support is possible but less mature |
| Render | Good fit, retained as fallback; Canadian region not available; cold-start on free tier |
| Heroku | Removed free tier; higher cost per dyno; slower deploy pipeline |
| Google Cloud Run | Serverless model; WebSocket connections time out after 60 minutes without special config |
| Self-hosted VPS | Requires firewall, TLS, backup, and on-call responsibility |

---

## Decision

Deploy the FastAPI backend on **Railway** as the primary host, with **Render** as a documented fallback in `docs/runbooks/`.

Key implementation details:
- `railway.json` at the repo root declares: Dockerfile build path (`backend/Dockerfile`), start command (`uvicorn ... --workers 4`), health check path (`/health`, 300s timeout), restart policy (`ON_FAILURE`, max 3 retries), 2 replicas, and `sleepApplication: false`.
- Auto-deploy is triggered on every push to `main`. Railway performs a rolling deploy: new replicas must pass `/health` before old replicas are terminated.
- All secrets (Supabase keys, JWT_SECRET, Firebase SA JSON, Twilio, Stripe) are set as Railway environment variables — never in `.env` files committed to git.
- `app_settings` in Supabase holds Stripe keys, Twilio credentials, and Google Maps keys — these can be rotated without a Railway redeploy.
- The backend process starts with `--workers 4` inside a single Railway service. WebSocket fan-out across the 4 workers (and across the 2 replicas) uses the `spinr:ws:dispatch` Redis pub/sub channel.
- Render fallback procedure: update `BACKEND_URL` in Vercel env vars and Expo EAS secrets; no code changes required.

**Vercel** hosts the admin dashboard (Next.js). **Expo EAS** manages mobile builds and OTA updates. These are out of scope for this ADR.

---

## Consequences

**Positive:**
- Zero-config auto-deploy from `main`; no CI/CD pipeline required for deployment (GitHub Actions handles testing and image signing separately).
- Railway's managed Redis add-on means no separate Redis hosting needed for production.
- Rolling deploy with health checks means a bad deploy that fails `/health` does not take down the running service.
- `sleepApplication: false` ensures the backend is always warm; no cold-start latency for the first request after idle periods.
- 2 replicas provide redundancy; a single replica crash does not cause downtime.

**Negative / trade-offs:**
- Railway is a single vendor for the backend runtime. A Railway outage (which has occurred historically) takes down the API entirely. The Render fallback requires a manual cutover (DNS / env var update) with ~15-minute RTO.
- The `--workers 4` model means 4 processes × 7 background loops = 28 loop instances across 2 replicas. Each loop must use atomic DB claims or idempotency keys to avoid double-processing. This constraint increases implementation complexity for new background tasks.
- Railway pricing is usage-based; a traffic spike (e.g., viral media coverage) could generate an unexpected bill. Mitigation: Railway usage alerts set at 2× baseline monthly cost.
- WebSocket connections are load-balanced across 2 replicas by Railway's proxy; a rider and their matched driver may connect to different replicas. This is handled correctly by Redis pub/sub fan-out, but any outage of the Redis add-on causes WebSocket events to fail silently within a replica boundary.
