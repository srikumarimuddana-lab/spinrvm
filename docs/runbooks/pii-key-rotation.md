# Runbook — pgsodium PII Key Rotation

**Owner:** `infra` + `compliance` · **Cadence:** Annual (min.) + on suspected compromise
**Closes:** DV-7 (PII key rotation cadence undocumented) · **Regs:** SOC2 CC6.1, PIPEDA

---

## When to Rotate

| Trigger | Urgency |
|---|---|
| Annual schedule (calendar reminder on Dec 1 each year) | Planned, ≤ 30 days |
| Suspected key compromise (leaked logs, departed-employee access, vendor breach) | Emergency, ≤ 24 h |
| SOC2 audit finding "rotation cadence not evidenced" | Planned, ≤ 90 days |
| Supabase platform key-material advisory | Per advisory urgency |

---

## Prerequisites

- Supabase project admin access (`infra` lead)
- `backend/.env` write access (rotation may touch `PII_KEY_ID` or similar)
- A maintenance window (~30 min during low-traffic period — Sun 03:00 UTC)
- `compliance` sign-off on pre-rotation evidence snapshot

---

## Steps

### 1. Pre-rotation evidence (T-7 days)
- [ ] Snapshot current `pgsodium.valid_key` list via `SELECT * FROM pgsodium.valid_key` and archive to `reports/compliance/key-rotation/YYYY-MM-DD-pre.csv`
- [ ] Confirm PITR window covers T-0 + 7 days (min.)
- [ ] Notify `#engineering` channel of rotation window

### 2. Provision new key (T-0)
```sql
-- In Supabase SQL editor, as admin:
SELECT pgsodium.create_key('aead-det', 'drivers_pii_key_vN');  -- N = current + 1
```
- [ ] Capture new key UUID from output → record in rotation log

### 3. Re-encrypt rows (T-0 to T+maintenance-window-end)
- Run the re-encryption helper:
```python
# backend/scripts/rotate_pii_key.py (create if absent)
# - Iterates drivers table with `key_version < current_version`
# - Decrypts each row with old key, re-encrypts with new key
# - Updates `key_version` column
# - Batched in 1,000-row chunks; emits progress log
```
- [ ] Run in dry-run mode first against staging snapshot
- [ ] Confirm no row-count mismatch pre/post

### 4. Revoke old key (T+7 days)
- [ ] Verify `key_version` distribution: `SELECT key_version, count(*) FROM drivers GROUP BY key_version` — no rows on old version
- [ ] Run `SELECT pgsodium.disable_key('<old-key-uuid>')`
- [ ] Do NOT hard-delete the old key for 30 days (PITR safety)

### 5. Post-rotation evidence (T+7 days)
- [ ] Snapshot new `pgsodium.valid_key` list → archive to `reports/compliance/key-rotation/YYYY-MM-DD-post.csv`
- [ ] Attach rotation-log row with: trigger, start time, end time, rows re-encrypted, old-key uuid, new-key uuid, executor name
- [ ] File SOC2 evidence: `reports/compliance/soc2-evidence/key-rotation-YYYY.md`

---

## Rollback

If step 3 fails mid-batch:
1. Stop the re-encryption job.
2. The rows with `key_version = new` are readable (new key not yet revoked).
3. Rows with `key_version = old` are readable (old key still active).
4. Investigate root cause before resuming; do NOT revoke old key until all rows migrated.

If a row becomes unreadable post-rotation (hash/payload mismatch), restore from
PITR using `docs/runbooks/pitr-restore.md`.

---

## Failure Modes to Watch

- **Supabase query timeout** on large tables → use smaller batch size (100 rows) and parallel workers with row-range locks
- **Partial re-encryption after crash** → `key_version` column prevents double-encryption; job is resumable
- **Secrets-engine sync** if using Vault — rotate the Vault reference to the new key UUID

---

## SLAs

- **Planned rotation**: complete within 30 days of calendar trigger
- **Emergency rotation**: complete within 24 h of trigger
- **Post-rotation evidence**: filed to `reports/compliance/` within 7 days

---

## Recent Rotations

| Date | Trigger | Old key UUID | New key UUID | Rows re-encrypted | Executor |
|---|---|---|---|---:|---|
| — | Initial creation (pre-rotation history not tracked) | — | — | — | — |

**First rotation due:** 2026-12-01 (annual) or before launch, whichever is earlier.
