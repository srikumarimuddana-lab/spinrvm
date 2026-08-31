# Change Impact & Risk Log — legacy non-Canadian-number review tab

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session `01JxLGWa57rNuFXF2sgJnZnN`) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (read-only counting/listing surface; no writes) |
| PR / commit link | branch `claude/driver-count-mismatch-legacy-dgw9xw` |
| Related issue or gap ID | Follow-up to §9b of `2026-08-31-legacy-driver-count-segmentation.md` (importer country-filter asymmetry) |

## 1. Issue / gap identified

`booking_import_service.py:126` filters its rows on the legacy export's own `country_code` because
that export is a **shared, multi-tenant SaaS database**, not Spinr-only
(`docs/audit/2026-08-14-mongodb-legacy-extract-audit.md` finding 1).
`build_mongo_driver_import_plan` never read that column, so other tenants' driver rows were imported
alongside Spinr's and are indistinguishable in the admin UI.

## 2. Root cause

An importer asymmetry, recorded but not acted on in the previous change's §9b. `country_code` is not
stored on the rows already held, so it cannot be filtered on retroactively.

## 3. Fix / remediation

A **review queue**, not a filter or a cleanup: a new "Non-Canadian number" tab on the drivers page
listing legacy-imported rows whose phone is not a Canadian number, so an admin can look at them.

Deliberately **not** done: no rows deleted, no rows mutated, no stored flag, no importer behaviour
change, and no reduction of any fleet count. Flagging is inferred, so it must not act on its own.

**Measured against production before shipping**: 11 rows flagged — **0 verified, 0 ever assigned a
ride, and 0 organic (non-imported) signups**. The heuristic currently mislabels no real driver.

Corroboration these are genuinely other-tenant rows rather than Canadians with a foreign mobile:
- Every disposable-email (`yopmail.com`) account in the imported population falls inside the set.
- Every structurally-malformed phone falls inside the set.
- 6 of the 10 distinct flagged area codes (`700, 736, 750, 797, 981, 991`) are **not assignable NANP
  codes at all** — the shape of a foreign number squeezed into ten digits.

Because the email and malformed-phone signals are strict subsets, the predicate is **phone-only**:
identical result, no join onto `users`, no email PII touched.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to admin read paths.** No write path, no dispatch, no money, no migration.

- The predicate is scoped to legacy-imported rows (`legacy_import_metadata` non-empty), so an
  organic signup with a foreign mobile is never flagged — asserted by test and confirmed against
  production (0 organic rows flagged).
- `total` and `onboarded_total` are unchanged; `legacy_review` deliberately **overlaps** them. A
  heuristic must not silently shrink the fleet count.
- **The one real regression risk, caught in self-review**: the tab wiring initially replaced
  `opts.onboarding_complete = statusFilter !== "legacy_incomplete"` with a conditional that sent
  *nothing* for the `legacy_incomplete` tab — which would have shown all 910 drivers on a tab meant
  to show 600 shells. Fixed before commit; the tab→request mapping is now pinned by an explicit
  truth table.
- `filters["id"]` is now claimed by three id-set filters (`pre_launch`, `onboarding_complete`,
  `legacy_review`). They accumulate into one include/exclude pair, as before; a test pins the
  `legacy_review` × `onboarding_complete` intersection.

**False-positive direction is the safe one:** a Canadian NPA missing from the list produces a
spurious *review* flag, never a missed real driver and never a block.

## 5. User-experience effect

- **Internal admin only.** No rider, driver or corporate surface. No copy or notification changes.
- Not visible mid-session to anyone using the rider or driver app.
- An admin gains one tab, labelled **"Non-Canadian number"** — chosen to state the criterion rather
  than assert a verdict ("Test"/"Foreign" would over-claim from an inferred signal).
- All other tabs are unchanged; flagged rows still appear in them.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `CANADIAN_AREA_CODES`, `is_suspect_legacy_import_row()`, paginated `fetch_suspect_legacy_driver_ids()` | Reader lives with the writer, mirroring the two existing classifiers |
| `backend/routes/admin/drivers.py` | `legacy_review` filter on `admin_get_drivers`; `legacy_review` stat key | Serve the tab |
| `admin-dashboard/src/lib/api/drivers.ts` | `legacy_review` opt + stat type | Wire it through |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | New tab, count, request mapping | The tab itself |
| `backend/tests/*` | Classifier, paginated fetch, filter, intersection and stats tests | Pin the behaviour |

## 7. Before / after

```tsx
// Before — one tab was special-cased; everything else got onboarding_complete=true
opts.onboarding_complete = statusFilter !== "legacy_incomplete";
```

```tsx
// After — the review tab is exempt too (some flagged rows ARE shells, and
// sending onboarding_complete=true would hide them from their own queue),
// while legacy_incomplete keeps its original `false`.
if (statusFilter === "legacy_incomplete") opts.onboarding_complete = false;
else if (statusFilter !== "legacy_review") opts.onboarding_complete = true;
if (statusFilter === "legacy_review") opts.legacy_review = true;
```

## 8. Rollback plan

**Nothing is written, so there is no data to unwind.** No migration, no column, no row mutated.

1. **Frontend-only, no backend deploy** — remove the `legacy_review` entry from `STATUS_TABS`. The
   tab disappears; the backend keys stay and are inert if unread.
2. **Full revert** — `git revert` the commit. Safe at any time: every key added is additive and no
   existing key changed meaning.

No feature flag: an internal-admin, read-only tab whose rollback is deleting one array entry.

## 9. Verification performed

- **Measured against production, read-only**: 11 flagged / 0 verified / 0 driven / 0 organic, plus
  the corroborating email, malformed-phone and unassignable-NPA overlaps quoted in §3.
- **Predicate exercised directly** — 12 cases including both critical negatives (organic signup with
  a US number, organic with an unassignable code) and the Canadian codes `250`/`368` that an earlier
  draft of the list wrongly omitted. All pass.
- **Tab→request truth table** executed for all five tab classes after the regression fix in §4.
- **Dual-import parity guard** (`test_dual_import_parity.py`'s own `_violations` logic, replicated):
  PASS across all 138 guarded files.
- `ruff check` / `ruff format --check` clean on every touched file (`routes/admin/drivers.py` still
  reports its 4 pre-existing `B904`s in untouched Stripe handlers).

### 9a. What was NOT verified

- **`pytest` was not run** — the network policy still blocks PyPI, so the new tests are unexecuted
  and statically checked only. CI is the confirmation.
- **`npm run build` was not run** — npm registry blocked. The TSX change was reviewed by diff.
- **No screenshot.** `admin-dashboard` has no active visual-regression coverage
  (`ACTION_ITEMS.md` B38 — zero committed baselines, job skips itself).
- **The heuristic is a heuristic.** It infers from area code because the authoritative
  `country_code` was never stored. It is currently exact against production (0 false positives), but
  that is a measurement today, not a guarantee — which is why the tab reviews rather than acts.

### 9b. Correction to the previous change log

`2026-08-31-legacy-driver-count-segmentation.md` §9c states ruff was "not implicated" in stripping
the dual-import fallback branch. **That was wrong**, and it recurred here. Reproduced directly: the
PostToolUse hook's `ruff check --fix` removes a fallback-branch import while the name is still
**unused in the file** — i.e. when imports are added before the code that consumes them (F401). The
earlier isolated repro missed it because the names were already used there. Working fix: add the
consuming code first, imports last, then re-run the hook's own commands and re-read the file.

## 10. Sign-off

- [x] Rollback plan is concrete — one array entry, no backend deploy.
- [x] Blast radius stated, not assumed — admin read paths only; counts deliberately unchanged.
- [x] No silent behaviour change — the one accidental change (the `legacy_incomplete` tab) was
      caught in self-review and fixed before commit.
- [ ] **Release gates outstanding**: backend tests and an `admin-dashboard` production build must
      pass in CI (§9a).
