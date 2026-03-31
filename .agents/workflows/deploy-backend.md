---
description: Backend deployment workflow for the Spinr API on Fly.io
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
- [ ] `fly.toml` has correct app name and region
- [ ] `Dockerfile` is up to date
- [ ] All required environment variables are set on Fly.io:
  ```bash
  fly secrets list --app spinr-backend
  ```
- [ ] Environment matches production requirements

## Step 3: Build and Deploy
```bash
fly deploy --app spinr-backend
```

## Step 4: Post-Deployment Verification
```bash
# Check deployment status
fly status --app spinr-backend

# Verify health endpoint
curl -s https://your-app.fly.dev/api/v1/health

# Check recent logs for errors
fly logs --app spinr-backend -n 50
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
If issues are found after deployment:
```bash
# List recent releases
fly releases --app spinr-backend

# Rollback to previous release
fly deploy --image <previous-image> --app spinr-backend
```
