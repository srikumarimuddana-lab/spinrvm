# Runbook — Supabase PITR (Point-In-Time Recovery) Restore

**Owner:** `data` + `infra` · **RTO target:** 4 h · **RPO target:** 5 min
**Regs:** SOC2 A1.2, PIPEDA s.4.7 · **D18 dimension**

---

## When to Invoke

| Trigger | Urgency |
|---|---|
| Accidental mass delete / update | Immediate |
| Data corruption from a deployed bug | Immediate (after revert) |
| Row-level compromise from attacker | Immediate (post-containment) |
| Scheduled DR drill | Planned, quarterly |
| Regulator-ordered data recovery (e.g., restore erroneously-deleted DSAR record) | Per regulator timeline |

---

## Pre-Requisites

- Supabase project admin access (`infra` lead)
- **UNCONFIRMED AND LIKELY FALSE — verify before relying on this runbook.**
  PITR appears to be a separate paid add-on (~$100/mo per 7 days of retention)
  that also requires at least **Small** compute. Pro's base plan includes
  **daily backups with 7-day retention, not PITR**. On Pro + Micro compute
  (the current configuration as of 2026-08-08) this runbook's
  recovery-point assumptions do not hold — a restore goes to the last daily
  snapshot, not to an arbitrary second. Check Dashboard → Database → Backups
  to see which you actually have, and do it now rather than during an incident.
  See `docs/runbooks/capacity-scaling.md` §5.
- Target restore timestamp (how far back, in UTC)
- Sign-off from incident commander on data-loss-window acceptance

---

## Decision Flow

```
                   ┌─────────────────────────┐
                   │  Is data loss localized │
                   │   (single table / rows)?│
                   └───────┬─────────────────┘
                           │ yes                   no
                           ▼                       ▼
             ┌───────────────────────┐   ┌────────────────────────┐
             │ Option A: Branch-     │   │ Option B: Full-project │
             │ restore + selective   │   │ restore (disruptive)   │
             │ INSERT from branch    │   │ — requires maintenance  │
             └───────────────────────┘   │   window                │
                                         └────────────────────────┘
```

**Prefer Option A** unless corruption is widespread.

---

## Option A — Branch-Restore + Selective Merge

### 1. Create a restore branch
- [ ] In Supabase dashboard → `Database` → `Backups` → `Point in Time`
- [ ] Select target timestamp (UTC), create a **branch** restore (not in-place)
- [ ] Wait for branch to become `ready` (typically 5–15 min)

### 2. Verify branch data
- [ ] Connect to branch via `psql` with branch-specific connection string
- [ ] Run `python -m backend.scripts.verify_restore --database-url "<branch connection string>"`
  (or export it as `RESTORE_BRANCH_DATABASE_URL` first) — it reports row counts
  for the core tables (`users`, `drivers`, `rides`, `payouts`, `stripe_disputes`,
  `driver_insurance_periods`, `financial_events`), walks one sample completed
  ride's full lifecycle (ride row → `driver_insurance_periods` → `financial_events`),
  and prints elapsed wall-clock time to feed the RTO measurement below. Exits
  non-zero on any failed check. Read-only — never issues a write statement, and
  refuses to run if the target URL matches this shell's `DATABASE_URL` (see the
  script's own docstring for the full production-URL guard). See
  `backend/scripts/verify_restore.py` and `backend/tests/test_verify_restore_script.py`.
- [ ] Sample rows to confirm expected state beyond what the script checks (e.g.
  spot-check a table/condition specific to the incident under investigation)

### 3. Extract affected rows
```sql
-- Example: recover accidentally-deleted drivers
\o /tmp/restored_drivers.sql
SELECT format(
  'INSERT INTO drivers VALUES (%L, %L, %L, ...)',
  id, user_id, status, ...
)
FROM drivers
WHERE deleted_at > '<incident_timestamp>';
\o
```
- [ ] Review extracted SQL; confirm no unwanted columns

### 4. Merge into production
- [ ] Run extracted SQL against prod with appropriate `ON CONFLICT` clauses
- [ ] Emit audit-log rows for each restored record
- [ ] Verify downstream integrity (foreign-key references still valid)

### 5. Cleanup
- [ ] Delete branch in Supabase dashboard (branches retain billing)
- [ ] File incident evidence: `reports/incidents/YYYY-MM-DD-pitr-restore.md`

---

## Option B — Full-Project In-Place Restore

**CAUTION:** Destructive — overwrites production. Requires maintenance window.

### 1. Pre-restore snapshot
- [ ] Trigger a manual Supabase backup of current state (for forensics)
- [ ] Snapshot Redis state if wallet/session data is critical
- [ ] Notify users via status page: planned maintenance

### 2. Execute restore
- [ ] Supabase dashboard → `Database` → `Backups` → `Restore to timestamp`
- [ ] Confirm the destructive action
- [ ] Expected downtime: 30 min – 2 h

### 3. Post-restore validation
- [ ] Smoke-test critical paths: rider login, driver login, ride creation, payment
- [ ] Diff critical row counts against pre-restore snapshot
- [ ] Rebuild any cached state that PITR cannot restore (Redis, in-flight WS connections)

### 4. Reconcile post-restore window
- [ ] Any writes made between `restore timestamp` and `restore completion` are LOST
- [ ] Flag these transactions from forensic snapshot for manual re-entry if feasible
- [ ] Announce restoration completion + data-loss window to users

---

## Quarterly DR Drill

Execute Option A against a non-critical table in staging every quarter.
Success criteria:
- Branch ready within 15 min
- Data extracted + validated within 30 min — run
  `python -m backend.scripts.verify_restore --database-url "<branch connection string>"`
  as the concrete validation step (see "Verify branch data" above); its
  printed elapsed time is the number to record as the actual measured RTO
  contribution for this step, not an estimate
- Total wall-clock ≤ 2 h (safety margin over 4 h RTO)

Failure → file as P2 in OPEN-ITEMS-TRACKER + re-drill in 30 days.

**Artifact:** `reports/tabletop/YYYY-Q-pitr-drill.md` — record `verify_restore.py`'s
printed elapsed time and overall pass/fail alongside the drill's total wall-clock RTO.

**Status:** this drill has never actually been run — see ACTION_ITEMS.md E7. The
script above exists to make the "verify row counts + a sample ride lifecycle"
step concrete and repeatable; it still requires a human with Supabase org/billing
access to create a scratch project, trigger a real restore, and record a real RTO.

---

## Failure Modes

- **PITR window expired** — target timestamp older than Supabase's retention.
  Check retention tier (Pro = 7 days, Team = 14 days). Escalate to Supabase support.
- **Branch restore hangs** — contact Supabase support; use Option B as fallback.
- **Foreign-key violations** on merge — restore parent tables first (users before drivers).

---

## SLAs

- **RTO (recovery time objective):** 4 h
- **RPO (recovery point objective):** 5 min (Supabase PITR granularity)
- **Incident evidence filed:** ≤ 7 days
- **Regulator notification** (if PII affected): 72 h to OPC per PIPEDA
