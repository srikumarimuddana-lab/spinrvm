# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (local worktree, not yet pushed — branch `worktree-agent-aa1c04b57896f4ea9`) |
| Related issue or gap ID | `docs/audit/2026-08-19-legacy-migration-data-quality-audit.md`, "Not fixed — driver-app display" section |

This entry covers **four findings**, each its own commit (task's "one logical
change per commit" rule) but sharing one log per this session's instruction.
Findings 1–3 fully fixed. Finding 4 is a partial UI-only mitigation — see its
section for the blocker analysis.

---

## Finding 1 — `ActivityView.tsx` silently swallowed an earnings-fetch failure

### 1. Issue / gap identified
`ActivityView.tsx`'s `loadData` wrapped its entire fetch flow in a bare
`try { ... } catch {}`. On a fetch failure the screen still rendered a
fully-formed "$0.00" earnings breakdown — indistinguishable from a genuine
zero balance — reachable by **any** driver on a transient 503/network blip,
not just legacy-imported drivers.

### 2. Root cause
Two compounding bugs, both now fixed:
- The catch block itself discarded the error with no logging, no state change.
- Worse: the *initial* combined load path used `Promise.allSettled([fetchEarnings(period), fetchRideHistory(...)])`. `Promise.allSettled` never rejects — it resolves with per-promise status — so even the (mostly useless) outer `catch {}` never fired for that path; a rejected `fetchEarnings` inside `allSettled` was invisible to it twice over.

### 3. Fix / remediation
Each branch of `loadData` now inspects its own fetch result explicitly:
- Pill-change path (`await fetchEarnings(period)` alone): wrapped in its own `try/catch`, `console.error`s the failure, and sets a new `error` state — but only when there's no cached earnings for that period to fall back on (a background refresh failure never blanks numbers already on screen).
- Initial/combined path: destructures `Promise.allSettled`'s per-promise results and checks `.status === 'rejected'` for each, logging and setting `error` the same way for the earnings half; the ride-history half just logs (no full-screen error — the list already degrades to "No Rides Found").
- New `error` state renders a distinct icon/title/retry-button block, matching the **existing** error/retry pattern already established in `driver-app/app/driver/referral.tsx` (`errorState`/`errorTitle`/`errorSub`/`retryBtn`/`retryBtnText`, same shape, same copy tone) rather than inventing a new one — this screen doesn't consume `useTheme()`, so the new styles use its own existing hardcoded palette (`#ef4444`/`#6b7280`/`#9ca3af`/`#374151`) instead of theme tokens.
- The "Your Rides" section header + status filter pills now render whenever loading has finished, **independent** of the earnings error state — a failed earnings fetch no longer also hides a ride history that loaded fine.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to `ActivityView.tsx`.** Grepped for other importers: `driver-app/app/driver/(tabs)/activity.tsx` (the screen that renders it) is the only consumer; `driver-app/lib/androidAuto/carSurface.tsx` and `driver-app/__tests__/components/ActivityView.test.tsx` also reference the name but are, respectively, an unrelated Android Auto surface (not this component) and its own test file (updated in this change).
- No table/state/endpoint changes — this is a pure client-side render/error-handling change. `useDriverStore.fetchEarnings`/`fetchRideHistory` (the store functions) were not modified.
- The existing `earningsByPeriod` per-period cache behavior is unchanged; `error` is a new, independent piece of local state.

### 5. User-experience effect
- **Driver-facing.** A driver who opens the Activity tab during a backend hiccup now sees "Couldn't load your earnings" + a Try Again button instead of a fabricated "$0.00" breakdown. This is a visible change to an already-shipped screen, but only in the failure path — the success path (the overwhelming majority of loads) is pixel-identical to before.
- Not something a driver mid-ride would notice differently — Activity is not part of the active-ride flow.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `driver-app/components/activity/ActivityView.tsx` | Replaced silent `catch {}` with per-branch result inspection + new `error` state + error/retry UI + styles; rides-section header decoupled from the error branch | Stop masking a real fetch failure as a fabricated zero balance |

### 7. Before / after
```tsx
// Before
if (!cached) setLoading(true);
try {
  if (isPeriodChange && hasLoadedRef.current) {
    await fetchEarnings(period);
  } else {
    await Promise.allSettled([
      fetchEarnings(period),
      fetchRideHistory(PAGE_SIZE, 0, false),
    ]);
  }
} catch {}
```
```tsx
// After
if (!cached) {
  setLoading(true);
  setError(false);
}
if (isPeriodChange && hasLoadedRef.current) {
  try {
    await fetchEarnings(period);
    setError(false);
  } catch (err) {
    console.error('[DriverActivity] earnings fetch failed:', err);
    if (!cached) setError(true);
  }
} else {
  const [earningsResult, historyResult] = await Promise.allSettled([
    fetchEarnings(period),
    fetchRideHistory(PAGE_SIZE, 0, false),
  ]);
  if (earningsResult.status === 'rejected') {
    console.error('[DriverActivity] earnings fetch failed:', earningsResult.reason);
    if (!cached) setError(true);
  } else {
    setError(false);
  }
  if (historyResult.status === 'rejected') {
    console.error('[DriverActivity] ride history fetch failed:', historyResult.reason);
  }
}
```

### 8. Rollback plan
No feature flag — pure client-side rendering logic with no server dependency,
no data migration, no wallet/state-machine involvement. Rollback is
`git revert` of the commit (or redeploy the previous app build via EAS); no
live data is touched by this change, so a plain revert is a complete and
sufficient rollback here (unlike the money/ride-state cases CLAUDE.md warns
`git revert` alone is insufficient for).

### 9. Verification performed
- [x] `tsc --noEmit` on the full driver-app project — 0 errors, both before and after each commit in this session
- [x] `eslint` on the touched files — 0 errors/warnings introduced (one pre-existing unrelated warning in the test file)
- [x] Existing `__tests__/components/ActivityView.test.tsx` suite (8 tests, all pre-existing) — all pass, including "keeps ride history visible when earnings loading fails" which now exercises the new error-state code path
- [x] Full driver-app test suite (`npx jest`) — 70 suites / 596 tests, all pass
- [ ] **No production build (`expo export` / EAS build) was run** — this is a `tsc --noEmit` + Jest + ESLint verification only; see "What was NOT verified" below
- [ ] No manual device/simulator run — no simulator available in this environment

---

## Finding 2 — Client-derived "Fare" line omitted `total_cancel_fees` and clamped to $0.00

### 1. Issue / gap identified
The client computed `totalEarnings − tips − incentives − bonuses − tax` for
the "Fare" breakdown row, but backend's `total_earnings` also includes
`total_cancel_fees` (confirmed by reading `backend/routes/drivers/earnings.py`
— `_total_with_extras = stats["total_earnings"] + total_incentives +
total_cancel_fees + total_tax + total_bonuses`). Any ride with a
cancellation/no-show fee therefore inflated the displayed Fare by that
amount, and the old formula's `Math.max(..., 0)` clamped any
negative/wrong result straight to a plausible-but-wrong "$0.00".

### 2. Root cause
The client-side formula was written before `total_cancel_fees` existed as a
component of `total_earnings`, or was written without checking the backend's
actual composition — it never subtracted it. `EarningsSummary.total_cancel_fees`
(shared/store/driverStore.ts) was already being fetched and shown as its own
row further down; only the Fare-derivation math missed it.

### 3. Fix / remediation
Added `totalCancelFees` to the subtraction. Because the corrected formula
should now reconcile to ≥ 0 in the normal case, a hard `Math.max(x, 0)` clamp
would once again mask a genuine future drift — so the fix distinguishes
floating-point rounding noise (JS float subtraction across several money
values can leave a sub-cent artifact like `-1e-13`) from a real mismatch:
anything more negative than half a cent renders as **"Doesn't match total"**
(styled distinctly, in the existing warning color `#f59e0b`) instead of a
dollar figure, plus a `console.warn` with the full component breakdown for
follow-up — never silently clamped to zero.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to the same `fareEarnings` computation inside `ActivityView.tsx`** — no other file reads or derives this value; `total_cancel_fees` itself is already displayed as its own "Referral"-adjacent row elsewhere in the same card (unaffected, unchanged).
- In the normal/reconciling case (the overwhelming majority — most rides have no cancel fee, so `totalCancelFees` is `0` and the formula is unchanged), the displayed Fare value is **numerically identical to before**. The only behavior change is for rides that *do* carry a cancel fee, where Fare now correctly decreases by that amount (it was previously overstated).
- No backend/table changes.

### 5. User-experience effect
- **Driver-facing.** A driver viewing a period that includes a cancelled/no-show ride with a fee now sees a lower (correct) Fare figure than before, with Total Earned unchanged (Total Earned was already correct — only the Fare sub-line was wrong). This is a numeric correction to an already-shipped screen; the affected population is bounded to rides with `cancellation_fee_driver > 0`.
- In the rare/pathological case where components genuinely don't reconcile (a data-quality bug elsewhere, not expected in normal operation post-fix), the driver now sees "Doesn't match total" instead of a wrong-but-plausible $0.00 — a clearer signal that something needs investigating, not a UX regression.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `driver-app/components/activity/ActivityView.tsx` | Added `totalCancelFees` to the Fare subtraction; replaced the hard `Math.max(x, 0)` clamp with a rounding-tolerant mismatch check that renders a distinct "Doesn't match total" state instead of clamping to $0.00; added a `console.warn` on mismatch | Match the backend's actual `total_earnings` composition; stop masking a real discrepancy as a fake zero |
| `driver-app/__tests__/components/ActivityView.test.tsx` | Added two tests: cancel-fee subtraction, and mismatch-renders-as-flagged-not-zero | Regression coverage for a money-adjacent client computation |

### 7. Before / after
```tsx
// Before
const totalTax = parseMoney(shownEarnings?.total_tax);
const fareEarnings = Math.max(totalEarnings - totalTips - totalIncentives - totalBonuses - totalTax, 0);
// ... rendered unconditionally as ${toMoney(fareEarnings)}
```
```tsx
// After
const totalTax = parseMoney(shownEarnings?.total_tax);
const totalCancelFees = parseMoney(shownEarnings?.total_cancel_fees);
const fareEarningsRaw = totalEarnings - totalTips - totalIncentives - totalBonuses - totalTax - totalCancelFees;
const fareMismatch = fareEarningsRaw < -0.005; // beyond float rounding noise
const fareEarnings = fareMismatch ? 0 : Math.max(fareEarningsRaw, 0);
// ... renders "Doesn't match total" when fareMismatch, else ${toMoney(fareEarnings)}
```

### 8. Rollback plan
Same as Finding 1 — pure client-side computation/render change, no server or
data dependency. `git revert` of the commit is sufficient; nothing here
touches a live Stripe charge, wallet delta, or ride-state row (the underlying
`total_cancel_fees` figure itself is unchanged — this only fixes how the
client re-derives "Fare" from numbers the backend already sends correctly).

### 9. Verification performed
- [x] `tsc --noEmit` — 0 errors
- [x] `eslint` on touched files — 0 errors/warnings introduced
- [x] New regression tests (cancel-fee subtraction case: `total_earnings=120, total_cancel_fees=20` → Fare renders `$100.00`; deliberately-inconsistent fixture → renders "Doesn't match total", not a dollar amount) — both pass
- [x] Full existing `ActivityView.test.tsx` suite (10 tests after the additions) — all pass; full driver-app suite (596 tests) — all pass
- [ ] No production build run; no manual device/simulator run (see below)

---

## Finding 3 — Profile screen's Vehicle card had no blank-field fallback

### 1. Issue / gap identified
Every other field on the driver Profile screen (Phone, Email, License Plate)
falls back to `'N/A'` when blank. The Vehicle card's
`{vehicle_color} {vehicle_make} {vehicle_model}` row didn't, so a legacy
driver with unpopulated vehicle data saw a blank or space-only row that
reads as a broken screen.

### 2. Root cause
The row was written as a plain string interpolation without ever handling
the all-blank (or partially-blank) case, unlike the surrounding fields on
the same screen, which were written with `|| 'N/A'`.

### 3. Fix / remediation
`[driverData?.vehicle_color, driverData?.vehicle_make, driverData?.vehicle_model].filter(Boolean).join(' ') || 'N/A'`
— matches the **existing** pattern already used verbatim in
`driver-app/components/dashboard/DriverIdlePanel.tsx` (confirmed by grep,
not invented), plus the `'N/A'` fallback already used by every sibling field
on this same screen. `filter(Boolean)` also fixes the secondary issue of a
run of bare spaces when only some of the three fields are populated.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated.** Grepped for other consumers of this exact
  `vehicle_color`/`vehicle_make`/`vehicle_model` combination — only
  `DriverIdlePanel.tsx` (already using the correct pattern, unmodified) and
  `vehicle-info.tsx` (a different, already-fallback-safe rendering, also
  unmodified) exist; no shared component was touched.
- For a driver with all three fields populated (the normal case), the
  rendered text is identical to before.

### 5. User-experience effect
- **Driver-facing**, Profile tab only. A driver with populated vehicle data
  sees no change. A driver with blank/partial vehicle data now sees "N/A" (or
  the populated subset) instead of a blank-looking row — this only affects
  drivers who already have a visibly broken row today, so it is a pure
  improvement with no regression path for anyone else.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx` | Vehicle card row now uses `filter(Boolean).join(' ') \|\| 'N/A'` instead of raw string interpolation | Match the fallback convention every other field on this screen already uses |

### 7. Before / after
```tsx
// Before
<Text style={styles.cardValue}>
  {driverData?.vehicle_color} {driverData?.vehicle_make} {driverData?.vehicle_model}
</Text>
```
```tsx
// After
<Text style={styles.cardValue}>
  {[driverData?.vehicle_color, driverData?.vehicle_make, driverData?.vehicle_model]
    .filter(Boolean)
    .join(' ') || 'N/A'}
</Text>
```

### 8. Rollback plan
Pure presentation change, no data/server dependency. `git revert` is
sufficient.

### 9. Verification performed
- [x] `tsc --noEmit` — 0 errors
- [x] `eslint` — 0 errors/warnings
- [ ] No existing test file covers `profile.tsx` (`driver-app/__tests__` has
      no profile screen test) — no regression test was added for this small
      a change given the no-test-file precedent on this screen; flagged here
      rather than silently skipped
- [ ] No production build run; no manual device/simulator run

---

## Finding 4 — Documents screen: legacy import gap indistinguishable from a genuine missing upload (PARTIAL)

### 1. Issue / gap identified
A legacy-imported driver whose old-app document *images* were never part of
the export (filenames only, no bytes, per the migration audit) sees the
exact same "Missing" badge / "UPLOAD" button as a driver who genuinely never
uploaded anything — even though the legacy driver is already approved and
actively driving.

### 2. Root cause
`documents.tsx`'s per-requirement status derivation (`getDocStatus`) has no
concept of "this requirement's absence is explained by a known migration
gap" — it only ever compares against `documents` rows fetched from
`/drivers/documents`, which is empty for an affected legacy driver exactly
as it would be for a genuinely-non-compliant one.

### 3. Fix / remediation — and why this is only a partial fix
Per the task's instruction, I checked whether `legacy_import_metadata` is
**already available client-side** before concluding this was blocked on a
backend change from another track, rather than assuming it wasn't:

- `backend/migrations/221_drivers_bulk_import_fields.sql` adds
  `drivers.legacy_import_metadata JSONB NOT NULL DEFAULT '{}'::jsonb`.
- `GET /drivers/me` (`backend/routes/drivers/profile.py`) calls
  `db_supabase.get_rows("drivers", ...)` with no restrictive `columns=`
  (defaults to `select("*")`), and its strip-list
  `_STRIP_FROM_SELF_RESPONSE = {"stripe_account_id", "bank_account", "fcm_token", "sin"}`
  does **not** include `legacy_import_metadata`.
- `shared/store/authStore.ts`'s `fetchDriverProfile()` calls exactly this
  endpoint and stores the full response as `driver` (typed `Driver`, which
  has a `[key: string]: unknown` index signature covering this field).

**Conclusion: the data is already reachable client-side today** — this is
not blocked on the other track's backend work the task anticipated might be
needed. I implemented the real fix (not just a UI-only mitigation):
`documents.tsx` now reads `driver.legacy_import_metadata`, and when it's a
non-empty object **and** the driver has zero documents on file at all (a
real migration gap affects every requirement equally, unlike a genuine
one-off missing upload), shows an additional info banner above the
requirement list explaining the gap without implying negligence, and
reassuring the driver their approved status is unaffected.

**Why this is still logged as "partial"**: I deliberately did **not** touch
the existing per-requirement "Missing"/"UPLOAD REQUIRED" badge logic itself
(`getDocStatus`, `renderStatusBadge`) — only added a new, purely additive
banner above it. A more thorough fix would also soften the per-row copy for
this specific case, but that touches logic shared across every requirement
row (mandatory/optional, expiry, rejection-reason display) and I judged
changing that logic itself to be outside a conservative, additive-only
change for a live-tested screen — see Risk section below.

**A genuine caveat on the data source**: the `legacy_import_metadata` column's
own SQL comment says it is "scoped to admin/compliance use only." It is not
actually filtered out of the self-serve `/drivers/me` response — this fix
consumes an existing overexposure rather than creating one, and I limited
the client-side usage to a presence check only (`Object.keys(...).length > 0`)
— nothing from inside the object is ever rendered to the driver. This
overexposure (if it's considered a bug at all — it may be intentional and
the comment stale) is a backend-side finding outside this track's scope; I
did not touch any backend file per the task's strict scope boundary.

### 4. Risk & impact on existing functionality
- **Blast radius: isolated to `documents.tsx`.** Grepped for other
  navigators to this screen — only `profile.tsx`'s two "Manage"/card
  `router.push('/documents')` calls; no shared component imports this
  screen.
- **Additive only**: the new banner is a new `View` inserted between the
  existing info box and the requirements map; no existing state variable,
  status-derivation function, or badge/style was modified. The banner
  itself is invisible for every driver except a legacy-imported one with
  literally zero documents on file — the majority-case render (any driver
  with ≥ 1 document, or any non-legacy driver) is pixel-identical to before.
- No `useAuthStore` field or shape was added — `driver` was already a field
  on the store; I only started reading one more of its already-fetched
  properties in this one screen.

### 5. User-experience effect
- **Driver-facing**, Documents screen only, and only for the specific
  population of legacy-imported drivers with zero documents on file (a
  narrow, already-known-gap segment per the migration audit). Everyone else
  sees no change.
- Not mid-session-disruptive — Documents is not part of the active-ride flow.

### 6. Files modified
| File path | What changed | Why |
|---|---|---|
| `driver-app/app/documents.tsx` | Reads `driver` from `useAuthStore`; computes `isLegacyImportedDriver` / `showLegacyDocsGapNotice`; renders an additive info banner when both are true; adds two new styles | Distinguish a known migration gap from a genuine missing upload, using data already reachable client-side |

### 7. Before / after
```tsx
// Before
const { fetchDriverProfile } = useAuthStore();
...
<View style={styles.infoBox}>...</View>
{requirements.map((req) => { ... })}
```
```tsx
// After
const { fetchDriverProfile, driver } = useAuthStore();
...
const legacyMeta = driver?.legacy_import_metadata as Record<string, unknown> | undefined;
const isLegacyImportedDriver = !!legacyMeta && Object.keys(legacyMeta).length > 0;
const showLegacyDocsGapNotice = isLegacyImportedDriver && documents.length === 0;
...
<View style={styles.infoBox}>...</View>
{showLegacyDocsGapNotice && (
  <View style={styles.legacyInfoBox}>
    <Text style={styles.legacyInfoText}>
      Your documents from your previous Spinr account weren't part of this
      transfer — that's a data-migration gap, not a sign anything is missing
      from your file. You're still an approved, active driver. Re-upload
      below whenever it's convenient, or contact support with any questions.
    </Text>
  </View>
)}
{requirements.map((req) => { ... })}
```

### 8. Rollback plan
Pure additive UI change reading an already-fetched field — no migration, no
flag needed. `git revert` of the commit is sufficient and complete.

### 9. Verification performed
- [x] `tsc --noEmit` — 0 errors (confirms `driver?.legacy_import_metadata`
      typechecks against the `Driver` interface's index signature)
- [x] `eslint` — 0 errors/warnings
- [ ] No existing test file covers `documents.tsx` — no regression test
      exists for this screen at all (pre-existing gap, not introduced here);
      not added given the screen has zero test scaffolding to extend
- [ ] No production build run; no manual device/simulator run
- [ ] **Not verified against a real legacy-imported driver record** — I
      confirmed the data path by reading migration 221, `profile.py`, and
      `authStore.ts`, but did not query a live Supabase `drivers` row to
      confirm a real legacy driver's `legacy_import_metadata` is in fact
      non-empty JSON in production today (no live Supabase access this
      session, consistent with the parent audit's own "What was NOT
      verified" note)

---

## Cross-cutting: what was NOT verified (all four findings)

- **No simulator or physical device was used at any point in this session.**
  Every claim about rendered output above is based on reading the JSX,
  `tsc --noEmit` typechecking, ESLint, and Jest component-test assertions
  (`@testing-library/react-native` queries against the rendered tree) — none
  of that is a visual/screenshot check. This repo has **no automated
  visual/snapshot regression tooling** for the driver-app (a standing gap,
  same one noted in this session's earlier consent-notice work and in the
  parent audit doc) — state it explicitly rather than implying a screenshot
  happened.
- **No production build was run.** Verification for all four findings was
  `tsc --noEmit` (full project, 0 errors after every commit) + `eslint` on
  touched files (0 new errors/warnings) + `npx jest` (full suite: 70 suites,
  596 tests, all pass after Findings 1–2's changes and additions) — not
  `expo export`, not an EAS build. Per CLAUDE.md, a passing dev-equivalent
  check is explicitly **not** equivalent to a real production build for a
  `driver-app` change; this is stated as a boundary, not implied as covered.
- **Finding 4's data-availability conclusion (`legacy_import_metadata`
  reaches `/drivers/me` unfiltered) is based on static code reading**
  (migration SQL + `profile.py` + `authStore.ts`), not a live query against
  a real legacy-imported driver's row. No live Supabase access this session.
- Findings 3 and 4 touch screens (`profile.tsx`, `documents.tsx`) that have
  **zero existing test coverage** in `driver-app/__tests__` — no regression
  test was added for either, consistent with the pre-existing absence of
  test scaffolding for those screens rather than a gap introduced by this
  change. Findings 1 and 2 (`ActivityView.tsx`) did have an existing test
  file, which was extended with 2 new tests for Finding 2 and exercises the
  Finding 1 code path via an existing test.
- Coverage is bounded to what this task's four listed findings describe —
  not a broader sweep of the driver-app for other similar issues.
