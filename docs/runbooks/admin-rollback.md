# Admin Dashboard — Rollback Runbook

**Applies to:** `admin-dashboard` (Vercel deployment)
**Who runs this:** On-call engineer or release owner
**Trigger:** Bad deploy detected via Sentry spike, error-rate alert, or manual QA failure

---

## 1 · Instant rollback via Vercel UI (< 2 min)

1. Open **Vercel → spinr-admin → Deployments**
2. Find the last known-good deployment (look for the green ✓ "Ready" badge before the bad one)
3. Click the **⋯** menu on that deployment → **Promote to Production**
4. Confirm. Vercel re-aliases the `spinrvm.vercel.app` / custom domain instantly — no rebuild required.
5. Verify: open the admin dashboard in an incognito tab and confirm the issue is gone.

> Instant rollback does **not** touch the database. If the bad deploy wrote schema changes or data migrations, see §4.

---

## 2 · Rollback via GitHub (if Vercel UI is unavailable)

```bash
# Find the last good commit SHA from git log
git log --oneline admin-dashboard/ | head -10

# Force-push the known-good SHA to main
# ⚠ Requires bypass of branch protection — get a second approval first
git push origin <good-sha>:refs/heads/main --force-with-lease
```

Vercel auto-deploys on push to `main`; the new deploy replaces production.

---

## 3 · Rollback checklist

- [ ] Slack `#eng-incidents`: "Rolling back admin to `<sha>` — reason: `<one line>`"
- [ ] Vercel Promote to Production complete — confirm green "Ready"
- [ ] Spot-check 3 admin pages (Dashboard, Rides, Drivers) in incognito
- [ ] Sentry error rate returning to baseline (check 5-min window)
- [ ] Slack `#eng-incidents`: "Rollback complete — admin stable"
- [ ] File a post-mortem issue within 24 h

---

## 4 · Database migrations that shipped with the bad deploy

If the bad deploy included a `backend/migrations/NN_*.sql` file that already ran on production:

1. **Do not delete or modify the migration file.** Migrations are append-only.
2. Write a new `NN+1_rollback_<description>.sql` that reverses the DDL change (drop column, revert index, etc.).
3. Apply via `python -m backend.scripts.run_migrations` after the rollback deploy is stable.
4. If data was corrupted, follow the **PITR restore runbook** at `docs/runbooks/pitr-restore.md`.

---

## 5 · Branch protection requirements (GitHub)

> These settings must be manually configured in GitHub → Settings → Branches → `main`.

| Setting | Required value |
|---------|---------------|
| Require a pull request before merging | ✅ enabled |
| Required approvals | ≥ 1 |
| Dismiss stale reviews on new push | ✅ enabled |
| Require status checks to pass before merging | ✅ enabled |
| Required status checks | `Security gates summary` (from `security-gates.yml`) |
| Require branches to be up to date | ✅ enabled |
| Restrict who can push to matching branches | Engineering leads only |
| Allow force pushes | ❌ disabled (except emergencies with approval) |

The `Security gates summary` check is the `summary` job in `.github/workflows/security-gates.yml`, which depends on all G1–G5 gates. Once all gates are flipped from `continue-on-error: true` to `false`, this check blocks any PR that introduces a HIGH+ CVE, secret, or SAST finding.
