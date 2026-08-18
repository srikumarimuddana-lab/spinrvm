# Three-Ledger Reconciliation — `bookings` vs `driverearnings` vs `payments`

**Date:** 2026-08-14
**Trigger:** Follow-up to `2026-08-14-mongodb-legacy-extract-audit.md`, requested before any code changes are made against the legacy MongoDB export — confirm whether the three money-bearing collections in the old app (`bookings.csv`, `driverearnings.csv`, `payments.csv`) actually agree with each other before trusting any one of them as a migration source.
**Scope:** Row-level reconciliation across all 224 completed, Canada-linked bookings in the legacy export (the same 224-ride set already live in Supabase — see prior doc, Finding 1, for the filtering method).
**Auditor:** Claude Code, reporting as senior DBA / ride-share financial auditor.
**Status:** Read-only reconciliation. No code or schema changes in this doc.

---

## Executive summary

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **`driverearnings.amount` and `bookings.you_earn` agree perfectly** — 220/220 exact matches (the other 4 bookings have no `driverearnings` row at all, see Finding 4). These two are the same number from the same underlying calculation; either is safe to treat as the driver-earnings source of truth. | **CLEAN** | Exact-match diff, 224 rows |
| 2 | **`payments.amount` is NOT a driver-earnings figure — it's the rider-facing charge.** It matches `bookings.total_amount` in 208/211 rows with a `payments` record (98.6%), not `you_earn` (1/211 match). No conflict once compared against the right field — this was a mis-mapping risk worth closing explicitly before any code assumes `payments.amount` means "what the driver was paid." | **CLEAN, once correctly mapped** | Exact-match diff against both candidate fields |
| 3 | **`payments.tax` is 100% empty** across all 224 completed bookings (0 non-null values). Not usable as a cross-check or backfill source for the GST work in the companion doc. | **P2 — dead field, ignore for tax backfill** | Null count |
| 4 | **13 of 224 bookings have no matching `payments.csv` row at all.** Concentrated, not evenly spread: 9 of the 13 belong to a single driver (`69c54a7fed8044094606bba2`). | **P2 — investigate the concentrated driver before backfill** | Left-join, null count |
| 5 | **⚠️ 35 of 224 rides (20 distinct drivers, $276.59 in driver earnings) show `payments.payout_to_driver = 'pending'` in the old app's own ledger** — i.e., the old app's own records say these specific rides were *not yet paid out* to the driver. This directly bears on, and potentially contradicts, the working assumption from prior conversation that "all migrated drivers were already settled in full under the old app." One of Alexander Gavu's 17 rides is among the 35. | **P0 — verify before implementing "Pending Payout = $0 for all migrated rides" as a blanket rule** | `payout_to_driver` value counts, driver-level breakdown |
| 6 | One booking (`CB5444608`) is a clear data-corruption outlier: `you_earn = $0.712` against `total_amount = $591.47`. Recommend excluding this single row from any backfill/reconciliation math pending manual investigation, rather than importing it as-is. | **P2 — single-row exclusion, not systemic** | Row-level inspection |

---

## Finding 1 — `driverearnings` vs `bookings`: clean

```
driverearnings.amount == bookings.you_earn:  220 / 220 rows that have both   (100%)
```

No diffs beyond the 4 bookings with no `driverearnings` row (see Finding 4's driver-earnings side — these are a subset of the same 13-booking gap, not a new problem). Either field can be trusted interchangeably as the driver-earnings figure for backfill purposes.

## Finding 2 — `payments.amount` is rider-charge, not driver-payout (mapping risk closed)

Initial pass compared `payments.amount` against `bookings.you_earn` and got a near-total mismatch (1/211, wide spread, diffs up to $590). Re-run against `bookings.total_amount` instead:

```
payments.amount == bookings.you_earn:       1 / 211    (0.5%)  ← wrong comparison
payments.amount == bookings.total_amount: 208 / 211    (98.6%) ← correct comparison
```

**Conclusion: `payments.amount` records what the rider was charged, not what the driver earned.** This is exactly the kind of mis-mapping that would silently corrupt a backfill if `payments.csv` were used as a driver-earnings source without this check — flagging explicitly so no future migration script reaches for the wrong field. The 3 residual mismatches (of 211) are minor (small-cent diffs, not systemic) and not investigated further here.

## Finding 3 — `payments.tax` is dead

```
payments.tax non-null count: 0 / 224
```

Every value is empty. This field cannot corroborate or replace `commission_gst_amount`/`payout_gst_amount` from the companion audit — it was apparently never populated in the old app's actual write path, whatever its original intent.

## Finding 4 — 13 bookings with no `payments.csv` row

```
missing payments row: 13 / 224
  9 of 13 belong to driver 69c54a7fed8044094606bba2
  1 belongs to driver 69a6c8baa7a37f75bceeb19f
  1 belongs to driver 69efb5a8468f2ceebf31389f
  1 belongs to driver 6a338e3aca6c0d82beb2ec54
  1 (CB5444608) is the data-corruption outlier in Finding 6
```

The 9-of-13 concentration in one driver suggests a systemic gap for that specific driver/date-range rather than 13 independent random misses — worth a targeted look at that driver's account in the old system (if still accessible) before assuming these 9 rides simply have no payment record anywhere.

## Finding 5 — `payout_to_driver = 'pending'` contradicts the "already fully settled" assumption ⚠️

```
payments.payout_to_driver distribution (n=211 with a payments row):
  completed:  176
  pending:     35
```

This is the most consequential finding in this pass. Prior conversation established a working design assumption — confirmed by the user — that migrated drivers "were already settled in full under the old app," and on that basis the recommended earnings formula was: **Pending Payout = $0, unconditionally, for migrated rides.** The old app's own `payments.payout_to_driver` field says otherwise for a real subset:

```
35 rides, 20 distinct drivers, $276.59 in you_earn value, marked 'pending' — not 'completed' — in the source ledger
Alexander Gavu: 1 of his 17 rides is in this set
```

**This does not necessarily mean $276.59 is currently owed** — 'pending' in the old app's ledger could mean: (a) genuinely unpaid at time of migration (real liability), (b) paid through a channel this specific field doesn't track (e.g. a manual/off-platform settlement that never updated this flag), or (c) a stale/abandoned status on rides that were later reconciled some other way. What it *does* mean is that the blanket "Pending Payout = $0 for all migrated rides" rule cannot be applied uniformly without checking this field first — at minimum, these 35 rides need a specific look (ideally cross-referenced against actual Stripe transfer history, the real ground truth per the earlier earnings-audit conversation) before their Pending Payout is set to $0 by policy.

**Recommendation:** before implementing the earnings/Bonus formula from the prior audit, carve out these 35 rides as a distinct case — verify against real Stripe transfer records (not just this field) whether they were actually paid, and only then decide their Pending Payout value. Do not let a blanket rule silently zero out a potentially real $276.59 driver liability.

## Finding 6 — one clear data-corruption row

```
booking_id: CB5444608
you_earn:    $0.712
total_amount: $591.47
```

A $591 total against $0.71 driver earnings is not a plausible real ride — flagging for exclusion from any backfill or reconciliation arithmetic pending manual investigation (possibly a decimal-placement error in the original export, or an unrelated large transaction misfiled under this booking ID). Not treated as representative of any systemic issue above; isolated.

---

## What this reconciliation does NOT resolve

- Whether the 35 "pending" rides are real outstanding liability — requires Stripe cross-reference, not resolvable from this export alone.
- Why 9 of 13 missing-`payments`-row bookings concentrate on one driver — requires access to the old system directly, not just this CSV snapshot.
- Any code or schema change — this and the companion doc are both audit-only, per explicit request to sequence reconciliation before implementation.

## Recommended next steps

1. **Before any "Pending Payout = $0" logic ships**, resolve Finding 5 — check the 35 flagged rides (20 drivers) against real Stripe transfer records.
2. Investigate the 9-row concentration on driver `69c54a7fed8044094606bba2` (Finding 4) before assuming those rides simply lack payment data.
3. Exclude `CB5444608` (Finding 6) from any backfill math; investigate separately.
4. Once 1–3 are resolved, the implementation PR (separate from this and the companion audit doc) can proceed with: the `payout_gst_amount` backfill from the companion doc, and the earnings/Bonus formula from the earlier conversation — now informed by the "pending" carve-out above rather than a blanket assumption.
