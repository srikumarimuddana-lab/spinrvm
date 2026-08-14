# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | backend (fares) — production data only, no code change |
| Domain (Sentry tag) | rides |
| PR / commit link | (this branch: `claude/b8-regina-reconciliation`) |
| Related issue or gap ID | ACTION_ITEMS.md B8 (addendum to `docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`) |

## 1. Issue / gap identified

B8 (identical per-vehicle-type fares in Regina/Saskatoon) had already been picked up and closed by a **concurrent session** (PR #3762, `docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`) shortly before this session started on the same item, with explicit user sign-off obtained independently in both sessions. Their change-log records Regina's Economy rate corrected to `base_fare=2.00, per_km=2.00`. When this session queried live production before writing anything, Regina's actual live Economy rate was `0.20/0.20` — 10× lower than their documented final state, and also 10× higher than their documented starting state (`0.02/0.02`). Saskatoon's live values matched their documented final state exactly. Only Regina was inconsistent.

## 2. Root cause

Two AI sessions independently picked up the same live-money backlog item at close to the same time, both querying and writing directly against the same production Supabase table with no locking or coordination mechanism between them. The most likely sequence: the other session's write landed, then this session's own first exploratory read + fix (see §3, mistake) landed on top of it before this session was aware a concurrent write had already happened — `git fetch` surfaced their merged *documentation* commit, but the actual live-DB state doesn't carry a commit history the way the repo does, so there was no equivalent "did someone else already touch this row" signal available before writing.

**This session also introduced its own bug while fixing Regina, caught and corrected within the same session before this reconciliation:**

An initial attempt to add a Premium tier to Regina used:
```sql
jsonb_set(vehicle_pricing_after_xl_fix, '{-1}', <premium object>, true)
```
intending `{-1}` to *append* Premium after the last array element. It instead **replaced** the last element (`true`/create-if-missing is irrelevant when the target index already exists) — the XL row was silently deleted from the array, leaving only Economy and Premium. Caught immediately via the query's own `RETURNING` clause (Regina's `vehicle_pricing` had 2 elements instead of the expected 3), and fixed in the same turn with `vehicle_pricing || jsonb_build_array(<XL object>)` (array concatenation — the correct append operator), restoring XL before continuing. No request served the broken 2-element state in the gap between the two statements to this session's knowledge — both ran back-to-back within the same tool-call turn — but it was live on production for that window regardless.

## 3. Fix / remediation

After surfacing the Regina discrepancy to the user directly (rather than guessing which of `$0.20` or `$2.00` was correct) and receiving explicit confirmation to match the other session's intended `$2.00` baseline, ran:

```sql
UPDATE service_areas
SET vehicle_pricing = '[
  {"vehicle_type": "Economy", "base_fare": 2.00, "per_km": 2.00, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "XL",      "base_fare": 2.80, "per_km": 2.80, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "Premium", "base_fare": 3.60, "per_km": 3.60, "per_min": 0, "min_fare": 0, "booking_fee": 0}
]'::jsonb
WHERE name = 'Regina';
```

This is a full-array replace, matching the shape/values the other session's own change-log documents as its intended final state for Regina — reconciling both sessions' work onto one consistent number rather than leaving two conflicting "final" states.

No application code changed.

## 4. Risk & impact on existing functionality

- Blast radius: identical to the original B8 fix (§4 of `2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`) — `routes/fares.py::build_fares_for_area` is the sole reader of `service_areas.vehicle_pricing`, confirmed by the same grep re-run this session.
- The accidental XL-deletion (§2) was live for less than one tool-call turn (order of seconds) before being corrected within the same session, and was caught via the query's own `RETURNING` output rather than a later report — no evidence any rider request was served the broken 2-tier state, but this is not independently verified against request logs (out of this session's access).
- **New risk surfaced by this incident, not by the original fix**: this repo has no mechanism to detect or prevent two concurrent sessions (AI or human) from racing on the same production config row. `git`'s own conflict detection doesn't apply to live DB writes the way it does to file changes. Worth a standing note for anyone doing further live-Supabase pricing/config work: **re-query the specific row immediately before writing**, even if `ACTION_ITEMS.md`/a recent change-log claims the item is already closed — the doc can be stale relative to the live table in a way `git log` won't show.

## 5. User-experience effect

Rider-facing, Regina only (Saskatoon was already correct and untouched by this reconciliation). Riders in Regina will see the same three genuinely-different tier prices whether they load the app during or after this fix — the only externally-visible change from this specific reconciliation is the Regina Economy/XL/Premium *absolute* rate settling at `$2.00/$2.80/$3.60` rather than transiently sitting at `$0.20/$0.28/$0.36` for the window between the two sessions' writes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| Supabase `service_areas` table (production, `soavhtdhefowwvforzwb`) — `Regina` row | `vehicle_pricing` reconciled to Economy `2.00/2.00`, XL `2.80/2.80`, Premium `3.60/3.60` (superseding this session's own intermediate `0.20/0.28/0.36` state) | Reconcile two concurrent sessions' work onto one consistent, user-confirmed number |
| `ACTION_ITEMS.md` | B8 entry addended with this reconciliation note | Tracking; the existing entry's stated final values for Regina are now actually true again |
| `docs/change-log/2026-08-12-b8-regina-reconciliation.md` | New Change Impact Log | Required per CLAUDE.md — money-touching, live-tested surface |

No backend/frontend code files changed.

## 7. Before / after

```
-- Before this reconciliation (live, confirmed via SELECT)
Regina: Economy 0.20/0.20   XL 0.28/0.28   Premium 0.36/0.36
        (this session's own earlier fix, built on a stale read that
         didn't yet know about the other session's concurrent write)
```

```
-- After
Regina: Economy 2.00/2.00   XL 2.80/2.80   Premium 3.60/3.60
        (matches the other session's documented intent; Saskatoon
         and both Airport-variant areas were already correct and
         untouched by this pass)
```

## 8. Rollback plan

Direct SQL against the same `vehicle_pricing` column, no migration/flag involved:

```sql
UPDATE service_areas SET vehicle_pricing = '[
  {"vehicle_type": "Economy", "base_fare": 0.20, "per_km": 0.20, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "XL",      "base_fare": 0.28, "per_km": 0.28, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "Premium", "base_fare": 0.36, "per_km": 0.36, "per_min": 0, "min_fare": 0, "booking_fee": 0}
]'::jsonb WHERE name = 'Regina';
```

(Reverts to this session's own prior state, not to the pre-B8 identical-fares defect — that state is itself documented in the other session's change-log's own rollback section if a full revert to the original bug is ever needed.) Takes effect immediately on the next `/fares` request.

## 9. Verification performed

- [x] Live data queried directly before writing (not trusting the other session's doc as ground truth) — this is what caught the discrepancy in the first place.
- [x] Live data re-queried after the accidental XL deletion (§2), confirming the bug via `RETURNING`, before it could be mistaken for correct.
- [x] Live data re-queried a third time after the final reconciliation `UPDATE`, and a fourth time across all 6 active service areas (`SELECT ... WHERE is_active = true`) to confirm Regina, Saskatoon, Regina Airport, and Saskatoon Airport all now show genuinely differentiated per-vehicle-type rates, and `riyadh`/`riyadh airport` remain untouched (intentionally uniform).
- [x] User explicitly confirmed which absolute Regina baseline to use before this reconciliation ran, rather than guessing between the two observed candidates.
- [ ] No `backend/tests/test_fares.py` re-run this pass specifically — the other session already confirmed 6/6 passing against the read-path logic, which this reconciliation doesn't change (data-only, same schema, same read contract).

## 10. What was NOT verified

- Not verified against real rider request logs whether any live traffic was served the transiently-broken 2-tier Regina state (§2) or the transiently-wrong `$0.20` baseline (§7) — no access to production request logs from this session; both windows were short (seconds to at most a few minutes) but not confirmed zero-impact.
- Did not investigate *why* two sessions were dispatched onto the same backlog item concurrently, or whether that's a one-off coincidence or a recurring risk with this session's orchestration pattern — flagged in §4 as worth a standing process note, not root-caused further here.
