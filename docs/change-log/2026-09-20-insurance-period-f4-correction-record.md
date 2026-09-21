# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend (data-only; no code/schema change) |
| Domain (Sentry tag) | safety |
| PR / commit link | none — direct production data correction, no code change |
| Related issue or gap ID | F4 (`docs/audit/2026-09-13-driver-app-mid-ride-process-death.md`); fix shipped in migration 421 |

## 1. Issue / gap identified

One historical `driver_insurance_periods` row (`id = a4daff6c-4bb7-47a6-bf25-107abbc07eda`,
Period 2, driver `483bf09e-459e-4351-8c57-94029228d7c8`) had its `ended_at` stamped with the
wrong timestamp, and the ride that should have opened its own Period 2 interval
(`294ed8e4-e963-487f-beae-c6fd4fbeb034`) has no Period 2 row at all in the regulatory audit
trail.

## 2. Root cause

`record_insurance_period_transition()` (migration 253) de-duplicated on `period` number alone,
not `period + ride_id`. The driver was open on Period 2 for ride `ac38399b-c754-489b-b405-f03f1cb08885`,
which was then cancelled at `2026-09-13 19:46:07.83308+00`. Moments later the driver was
claimed for a second, real ride (`294ed8e4...`) while still nominally "Period 2" — the
transition call saw the same period number and treated it as a no-op instead of closing the
stale interval and opening a fresh one. Effects: (a) the stale row's `ended_at` was later
overwritten to `2026-09-13 19:46:36.595097+00` (the second ride's actual Period-3-start time)
instead of the first ride's real cancellation time, and (b) ride `294ed8e4` never got its own
Period 2 interval logged.

Confirmed as a one-time historical occurrence, not systemic: ran a `LAG() OVER (PARTITION BY
driver_id ORDER BY started_at)` scan across the full `driver_insurance_periods` history
comparing every Period 3 row's `ride_id` against the immediately-preceding Period 2 row's
`ride_id` for the same driver — this is the exact fingerprint of the bug, and it matched only
this one incident.

## 3. Fix / remediation

- **Prospective fix**: migration 421 (applied to production this session) redefined
  `record_insurance_period_transition()` to de-duplicate on `period AND ride_id`, so this
  cannot recur for any ride going forward.
- **Historical record**: inserted one row into `driver_insurance_period_corrections`
  (id `c4644c94-8f44-4bc9-9fe5-7a3f697a4476`) narrowing the stale row's effective end time to
  the ride's true cancellation timestamp, with the full misattribution story (including the
  permanent, structurally-unrepresentable gap for ride `294ed8e4`'s missing Period 2 interval)
  written into the `reason` field. **The original `driver_insurance_periods` row was not
  mutated** — append-only rule respected; this is an additive correction record only, per this
  repo's established pattern (156 prior rows from a 2026-08-27 batch use the same table the
  same way).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** No code changed in this action — only a single new row was
  inserted into `driver_insurance_period_corrections`. That table has no known consumer yet in
  `backend/` (grepped: no route or service reads from it); it exists purely as a
  regulator-facing audit correction ledger. Adding a row does not change dispatch, insurance
  gating (`go_online`), or any live request path.
- No other `driver_insurance_periods` rows share this bug's fingerprint (verified above), so no
  further correction rows are needed for this incident class.
- **Schema gap noted, not fixed here**: `driver_insurance_period_corrections.corrected_by` is a
  hard FK to `users` (rider/driver accounts only — no admin/system identity exists in that
  table). This correction's `corrected_by` reuses the same rider account
  (`71ba3eea-287f-41d8-8e48-9d794ea531e0`) that all 156 prior correction rows use, for
  consistency with the only existing precedent in this table. Whether that account is a
  deliberately-designated internal/ops identity or should be replaced with a proper
  system-account mechanism is unconfirmed and out of scope for this fix — worth a follow-up
  backlog item if this table sees more use.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing surface reads this table
today. Backend-only, audit-trail-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| (none — data-only) | Inserted 1 row into `driver_insurance_period_corrections` in production | Correct the F4 historical timing/attribution gap per the recommendation approved this session |

## 7. Before / after

```
# Before
driver_insurance_periods row a4daff6c-...:
  period=2, ride_id=ac38399b-... (cancelled ride)
  started_at=2026-09-13 19:45:56.358588+00
  ended_at=2026-09-13 19:46:36.595097+00   -- wrong: this is ride 294ed8e4's Period-3-start time
```

```
# After
driver_insurance_period_corrections row c4644c94-...:
  original_period_id=a4daff6c-...
  corrected_started_at=2026-09-13 19:45:56.358588+00   -- unchanged
  corrected_ended_at=2026-09-13 19:46:07.83308+00       -- corrected to ac38399b's real cancellation time
  reason=<full misattribution story, incl. the unrecoverable missing-Period-2 gap for 294ed8e4>
  corrected_by=71ba3eea-287f-41d8-8e48-9d794ea531e0
```

## 8. Rollback plan

`DELETE FROM driver_insurance_period_corrections WHERE id = 'c4644c94-8f44-4bc9-9fe5-7a3f697a4476';`
— safe: this table is additive-only and has no downstream consumer, so removing the row fully
reverts this action with no second deploy and no data-loss risk beyond the correction record
itself.

## 9. Verification performed

- [x] Blast-radius grep performed: confirmed no backend route/service reads
      `driver_insurance_period_corrections` (audit-ledger-only table today).
- [x] Confirmed via `information_schema` that `original_period_id` and `corrected_by` FK
      constraints are satisfied (row inserted successfully, returned via `RETURNING`).
- [x] Confirmed via `LAG()` window-function scan across full `driver_insurance_periods` history
      that this is the only occurrence of this bug's fingerprint.
- [x] Confirmed the source row (`a4daff6c-...`) was not mutated — append-only rule intact.
- [ ] Not tested against staging (there is no staging copy of this production incident data;
      verification was against production read-only queries plus one production insert).
- [ ] No automated test covers `driver_insurance_period_corrections` inserts (none existed
      before this session; not added here since this was a one-off data correction, not a new
      code path).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single `DELETE` by primary key)
- [x] Blast radius is stated, not assumed (isolated, no known consumer)
- [x] No silent behavior change to an already-shipped flow — nothing reads this table yet
