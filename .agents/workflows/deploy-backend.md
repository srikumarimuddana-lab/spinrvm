---
description: Backend deployment workflow for the Spinr API on Railway
---

# Backend Deployment Workflow

Reference `.agents/roles/devops-engineer.md` for infrastructure details.

## Step 1: Pre-Deployment Checks
// turbo
```bash
cd backend && python -m pytest -v --tb=short
```
- [ ] All tests pass
- [ ] No critical security issues (run `.agents/workflows/security-audit.md` first if unsure)

## Step 2: Verify Configuration
- [ ] `railway.json` has the correct builder and Dockerfile path
- [ ] `Dockerfile` is up to date
- [ ] All required environment variables are set on Railway:
  ```bash
  railway variables --service backend
  ```
- [ ] Environment matches production requirements

## Step 3: Build and Deploy
```bash
railway up --service backend
```

## Step 4: Post-Deployment Verification
```bash
# Check deployment status
railway status

# Verify health endpoint
curl -s https://<your-app>.up.railway.app/api/v1/health

# Check recent logs for errors
railway logs --service backend
```

- [ ] App is running
- [ ] Health endpoint returns 200
- [ ] No error spikes in logs
- [ ] Sentry shows no new errors

## Step 5: Smoke Test
Test critical endpoints:
1. Auth: Can users log in?
2. Rides: Can a ride be requested?
3. Payments: Can a fare be calculated?

## Step 6: Update Documentation
- [ ] Update deployment date in `.agents/docs/deployment-guide.md`
- [ ] Note any config changes made
- [ ] Update version if applicable

## Rollback Procedure
If issues are found after deployment, roll back via the Railway dashboard:

1. Open the Railway project → backend service → **Deployments** tab.
2. Find the last known-good deployment.
3. Click the overflow menu → **Redeploy**.

CLI alternative:
```bash
railway redeploy --service backend
```
