# 05 — DevOps / Deployment / Infra Audit

> **Read time:** ~15 min
> **Audience:** SRE, DevOps, Eng Lead
> **Scope:** Fly.io, Render, Vercel, Docker, EAS, secrets, CI/CD

---

## Executive verdict

Pipeline (CI/CD) quality is **A−** — one of the strongest areas of the repo. Infrastructure config (**C+**) is the weak link, primarily because of one setting that silently halts ride-lifecycle jobs.

---

## P0 findings

### P0-D1 — `min_machines_running = 0` halts background jobs

**Evidence:** `fly.toml` has `auto_stop_machines = true` + `min_machines_running = 0`.

**Impact:** After Fly's idle timeout, the machine stops. The five background loops (surge, scheduled-rides dispatcher, payment retry, document expiry, subscription expiry) **stop forever** until an HTTP request wakes the machine. Real consequences:
- Scheduled rides (booked for tomorrow) **never dispatch** if the machine is cold at the scheduled minute.
- Surge pricing becomes stale.
- Failed Stripe payments never retry.
- Expired documents never alert the driver — a suspended driver keeps accepting rides.
- Cold-start latency (~2–5s) on first request after idle → rider sees "no drivers available".

**Fix (S):**
```toml
# fly.toml
[http_service]
  auto_stop_machines = "off"
  auto_start_machines = true
  min_machines_running = 1
```
**And** split worker out (see backend audit P1-B8). Worker has its own app with `min_machines_running = 1`, never scales to 0, never serves HTTP traffic.

---

### P0-D2 — No disaster-recovery / backup verification

**Evidence:** No `backups/` folder, no runbook in `docs/`, no automated backup verification.

**Impact:** Supabase free/pro takes daily snapshots, but:
- No tested restore procedure. "Backups exist" ≠ "We can recover."
- No PITR policy documented.
- No export to a separate cloud (region/vendor lock-in).

**Fix (M):**
1. Enable Supabase PITR (Pro plan).
2. Schedule daily `pg_dump` of schema+data to S3/R2 in a different region. Encrypt at rest with customer-managed key.
3. Quarterly **restore drill** — document `RESTORE_RUNBOOK.md` with exact steps; run it on staging every quarter; record RTO/RPO numbers.

---

## P1 findings

### P1-D3 — Secrets management is flat

**Evidence:** Fly secrets (`fly secrets set …`). No rotation process, no vault, no audit trail per secret.

**Fix (M):**
- Migrate to Doppler / 1Password-Connect / HashiCorp Vault.
- Define rotation cadence: JWT secret (quarterly), Stripe keys (yearly or on incident), Supabase service role (quarterly), Twilio (yearly).
- Every secret access writes to audit log.

---

### P1-D4 — No staging environment parity

**Evidence:** Only one Fly app referenced in config.

**Impact:** Changes go straight to prod. No "canary" for risky migrations.

**Fix (M):**
- Create `spinr-backend-staging` (Fly), separate Supabase project, separate Stripe test keys.
- CI deploys `main` → staging automatically; prod deploy is a manual approval on a `release/*` branch.
- DB migration runs on staging → soak for 24h → apply to prod.

---

### P1-D5 — Docker image `latest` tag risk

**Evidence:** `backend/Dockerfile` likely tags `latest` on each push.

**Impact:** Rollbacks become lossy if `latest` is overwritten. Two deploys racing clobber each other.

**Fix (S):**
- Tag every image with Git SHA and `YYYYMMDD-HHMM`.
- `latest` becomes a pointer, never the source of truth.
- Fly machine references SHA; rollback = redeploy previous SHA.

---

### P1-D6 — EAS build profiles and OTA channel hygiene

**Evidence:** Both mobile apps have `expo-updates` installed. EAS channels not fully documented.

**Impact:** OTA update could ship a breaking JS bundle to a native binary that doesn't support it (protocol mismatch) → white screen app.

**Fix (S):**
- Define channels: `development`, `preview`, `production`.
- Tie channel to release branch: `main` → preview; tagged release → production.
- Enable **runtime version** pinning (`runtimeVersion.policy = "appVersion"` in `app.json`) so OTA can't cross native-version boundaries.
- Staged rollout to 10% → 50% → 100% via EAS.

---

### P1-D7 — Multiple competing deploy targets (Fly + Render) unclear

**Evidence:** Both `fly.toml` and `render.yaml` exist. No docs declare which is canonical.

**Fix (S):** Pick one primary. Delete or clearly archive the other. Update `DEPLOYMENT.md` to state "Canonical deploy: Fly.io (yyz region). Render config retained as cold DR only."

---

### P1-D8 — CI doesn't gate on coverage or type-check

**Evidence:** `.github/workflows/ci.yml` runs tests but (likely) accepts any pass/fail.

**Fix (S):**
- Fail build when backend pytest coverage <80% (target already stated).
- Fail build on `pyright --strict` (backend) and `tsc --noEmit` (frontend) errors.
- Fail build on new `# type: ignore` or `@ts-ignore` without justification comment.

---

### P1-D9 — No dependency license audit

**Fix (S):** Add `pip-licenses` (backend) and `license-checker` (frontend) to CI; fail on GPL/AGPL in runtime deps.

---

### P1-D10 — No SBOM / provenance

**Fix (M):** Generate SBOM (`cyclonedx-bom`) in CI; attach to release artifacts. Optionally sign via Cosign/Sigstore.

---

### P1-D11 — Fly regions

**Evidence:** `fly.toml` pins `yyz` (Toronto). Good for CA. But no multi-region failover.

**Impact:** yyz outage (Fly has had them) = full product outage.

**Fix (M):** Add `ord` (Chicago) or `sea` (Seattle) as warm standby. Supabase is regional anyway, so secondary region mainly helps request routing and static caching — document this trade-off.

---

## P2 findings

### P2-D12 — No cost-monitoring budget alerts

Fly/Supabase/Stripe/Twilio all have spending APIs. Set alerts at 50%/80%/100% of monthly budget.

### P2-D13 — Build cache not warmed

CI rebuilds RN iOS pods from scratch each time. Add Metro + CocoaPods cache.

### P2-D14 — No chaos testing

Once worker split is done, add a monthly "kill worker" drill that proves primary API stays healthy.

### P2-D15 — `.dockerignore` audit

Verify `.dockerignore` excludes `uploads/`, `tests/`, `.git`, `docs/`.

### P2-D16 — Dependabot grouping

Dependabot config (5 ecosystems) is good; consider adding `ignore` for major Expo SDK jumps until manually tested.

---

## P3 findings

- **D17** — Post-deploy smoke test is present in CI but disabled. Re-enable with 3 key checks: `/health`, OTP send (test number), Stripe test PaymentIntent.
- **D18** — Slack failure notifier good; add PagerDuty for P0 outage signal.

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| D1 min_machines=0 | P0 | S |
| D2 backup/DR | P0 | M |
| D3 secrets mgmt | P1 | M |
| D4 staging parity | P1 | M |
| D5 image tagging | P1 | S |
| D6 EAS channels | P1 | S |
| D7 deploy target | P1 | S |
| D8 coverage gate | P1 | S |
| D9 license audit | P1 | S |
| D10 SBOM | P1 | M |
| D11 multi-region | P1 | M |
| D12–D16 | P2 | S–M |
| D17–D18 | P3 | S |

---

*Continue to → [06_TESTING_OBSERVABILITY.md](./06_TESTING_OBSERVABILITY.md)*
