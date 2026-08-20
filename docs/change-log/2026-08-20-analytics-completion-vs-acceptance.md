# Change Impact & Risk Log — "Acceptance rate" renamed to completion rate

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Operational Analytics review, finding P1-2 and P2-avg |

## 1. Issue / gap identified

Three related mislabels on the Driver Acceptance tab:

1. **"Acceptance Rate" was `completed / assigned`** — a completion rate. A
   driver who accepts every offer but whose riders cancel scored badly and
   could be flagged a low performer for something outside their control. The
   real acceptance rate (`accepted / offered`, from the append-only
   `ride_offers` ledger) already existed at `/analytics/driver-offer-stats`
   and is now a sibling tab on the same page — two adjacent tabs, same word,
   different numbers, one of them wrong.
2. **"Avg Acceptance Rate" averaged over every registered driver**, including
   those with no rides in the window (rate 0), while the card beside it
   (`low_performer_count`) required `total_rides >= 5`. Two adjacent cards,
   two different populations. A fleet of 200 registered with 20 active at
   ~90% displayed as ~9%.
3. **"Total Active Drivers" was every driver row**, online or not, with rides
   or not.

## 2. Root cause

(1) is a naming error that hardened into a policy input — the low-performer
threshold is applied to it. (2) and (3) are denominator errors: the summary
was computed over the driver table rather than over the drivers the metric
is about.

## 3. Fix / remediation

- Per-driver `completion_rate` is now the primary field, correctly named.
  `acceptance_rate` is retained as a **deprecated alias with the same value**
  so an un-updated client keeps working (additive over destructive).
- `sort_by` accepts `completion_rate` (new default) and still accepts
  `acceptance_rate`, normalising it to the same column.
- `avg_completion_rate_active` averages only drivers with ≥1 ride — the
  figure an operator means. `avg_completion_rate_all` keeps the old
  all-driver meaning, correctly labelled, and `avg_acceptance_rate` aliases
  it so no consumer silently changes meaning.
- `drivers_with_rides` added alongside `total_drivers`; the UI card is
  relabelled "Drivers" with "N with rides this period" beneath.
- The payload states `metric: "completion_rate"` and
  `true_acceptance_source: "/api/admin/analytics/driver-offer-stats"`, so a
  future consumer cannot mislabel it by accident.
- UI: tab renamed "Driver Completion", column "Completion Rate", card
  "Low completion", plus a one-line note pointing at the Dispatch Offers tab
  for true acceptance.

## 4. Risk & impact on existing functionality

**Blast radius: one endpoint, one page.** `getDriverAcceptanceRates` has a
single consumer (`analytics/page.tsx`), verified by grep. The endpoint is
read-only; no ride state, money, dispatch, or background-loop path is
involved. No migration, no schema change.

**Backward compatibility is deliberate, not incidental.** Every renamed field
keeps its old spelling as an alias with an unchanged value, and the four
pre-existing tests that assert on `acceptance_rate` / `avg_acceptance_rate`
pass **unmodified** — which is the evidence that an un-updated client is
unaffected. A test asserts the alias and the new field never diverge.

**Behaviour that genuinely changed:** the default `sort_by` value is now
`completion_rate` rather than `acceptance_rate`. Both resolve to the same
column, so the returned ordering is identical.

**The low-performer rule now reads `completion_rate`** (falling back to the
alias). Same threshold, same population, same count — the rename does not
move anyone in or out of the flagged set. Covered by a test.

## 5. User-experience effect

**Internal admin only.** Nothing rider-, driver-, or corporate-facing;
nothing visible mid-session to anyone using the apps.

Admins will see: a tab renamed Driver Acceptance → Driver Completion, a
column renamed, and — most visibly — the "Avg Acceptance Rate" card change
value, because it now excludes idle drivers. On a fleet with many registered
but inactive drivers this number will jump substantially. That is the
correction, but it will look like a data change to anyone who has been
watching the old figure, so it is worth flagging to whoever reads this
dashboard daily.

This matters beyond labelling: the low-performer flag feeds driver
performance conversations. Calling a completion rate "acceptance" invites
holding a driver responsible for rider-initiated cancellations.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/analytics.py` | `completion_rate` primary + alias; two averages; `drivers_with_rides`; `metric`/`true_acceptance_source`; sort-key normalisation | Name the metric honestly; fix the denominators |
| `backend/tests/test_admin_analytics_coverage.py` | +7 tests | Alias parity, active-vs-all average, both sort spellings |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | Labels, column, card population, pointer to Dispatch Offers | Match the corrected metric |
| `admin-dashboard/src/lib/api/analytics-payouts.ts` | Sort union gains `completion_rate` | Type the new default |

## 7. Before / after

```python
# Before — a completion rate published as "acceptance", averaged over
# every registered driver including idle ones
acceptance_rate = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
avg_acceptance = round(sum(r["acceptance_rate"] for r in result) / len(result), 1) if result else 0
return {"total_drivers": len(result), "avg_acceptance_rate": avg_acceptance, ...}
```

```python
# After — named for what it is, averaged over the drivers it describes
completion_rate = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
active = [r for r in result if r["total_rides"] > 0]
avg_active = round(sum(r["completion_rate"] for r in active) / len(active), 1) if active else 0
return {
    "total_drivers": total, "drivers_with_rides": len(active),
    "avg_completion_rate_active": avg_active, "avg_completion_rate_all": avg_all,
    "avg_acceptance_rate": avg_all,          # deprecated alias, unchanged meaning
    "metric": "completion_rate",
    "true_acceptance_source": "/api/admin/analytics/driver-offer-stats", ...
}
```

## 8. Rollback plan

`git revert` is sufficient and complete. No migration, no schema change, no
write path, no persisted state, no live data touched — the endpoint is
read-only and the change is confined to field naming, one denominator, and
UI labels. Because every old field name is retained as an alias, a partial
rollback (frontend only, or backend only) also works: an old frontend against
the new backend reads the aliases, and a new frontend against an old backend
falls back via `d.completion_rate ?? d.acceptance_rate`.

No feature flag: flagging this would mean keeping a mislabelled metric
selectable, which is the thing being fixed.

## 9. Verification performed

- [x] Automated tests — **101 passed** (94 prior + 7 new). The four pre-existing driver-acceptance tests pass **unmodified**, which is the backward-compatibility evidence.
- [x] New tests cover: alias parity with the new field, `metric`/`true_acceptance_source` declaration, active-vs-all average divergence (90% vs 9% on a 1-active/9-idle fixture), `drivers_with_rides` distinct from `total_drivers`, both sort spellings, and that the low-performer count is unchanged by the rename.
- [x] **Full backend suite run earlier on this branch: 12599 passed, 8 skipped, 1 xfailed** — confirming the `fare_service` import added in the previous commit introduced no cycle or regression anywhere.
- [x] **Real production build run** — `npm run build`, exit 0. `tsc --noEmit` exit 0.
- [x] `ruff check` + `ruff format` clean.
- [x] Blast-radius grep — `getDriverAcceptanceRates`, `driver-acceptance`, `acceptance_rate` across `*.ts`/`*.tsx`/`*.py`. Single frontend consumer; all remaining `acceptance_rate` reads are the intentional fallbacks and the alias.

## 10. What was NOT verified

- **Nothing was rendered.** The relabelled tab, column, and cards are covered by type-check and build only. No repo visual-regression tooling exists for admin-dashboard (standing gap).
- **The "Avg Completion Rate" jump was not observed against real data.** The claim that it will rise substantially is arithmetic from the fixture, not a measurement — the production ratio of registered to active drivers was not checked, so the size of the visible change is unknown.
- **No consumer outside this repo was checked.** If anything external (a script, a saved report, a BI tool) reads `avg_acceptance_rate` expecting the all-driver figure, it still gets exactly that — but if something reads the per-driver `acceptance_rate` expecting *true* acceptance, it was already wrong and remains wrong under the alias. Only the in-repo consumer was audited.
- **The deprecated aliases have no removal date.** They are commented as deprecated but nothing enforces or schedules their removal; they will linger until someone acts.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Visible behaviour change (the average moving) documented in §5 rather than shipped silently
