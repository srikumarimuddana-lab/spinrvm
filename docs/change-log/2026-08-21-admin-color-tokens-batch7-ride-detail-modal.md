# Change Impact & Risk Log — #2816 Batch 7, sub-batch 39: ride-detail-modal flag/complaint/cancellation signals (partial)

**Issue/gap identified**: `ride-detail-modal.tsx` (a large ~1150-line component, already carrying a documented `STATUS_META` categorical exclusion from an earlier pass) used hardcoded Tailwind colors for genuine severity signals in its flags/complaints/cancellation section: the load-error text, the rider/driver flag-count severity badges (≥2 flags = worse tier), the complaint "open" status badge, the near-ban warning notice, the Flag/Complaint/Force-Cancel action buttons, and the "Cancellation" card's border/header accent.

**Root cause**: Predates the semantic-token system introduced for #2816. This is a large, frequently-touched file, so #2816 coverage here is incremental across sub-batches.

**Fix/remediation**: Converted:
- Load-error text → `text-destructive`.
- Rider/driver flag-count badges (≥2 flags → destructive, else → warning) — a genuine 2-tier severity signal.
- Complaint "open"/other status badge → warning/success.
- Near-ban warning notice (background + text) → destructive tokens.
- Flag Rider / Flag Driver / Force Cancel outline buttons → destructive tokens; Raise Complaint outline button → warning tokens.
- "Cancellation" card border and header accent → destructive tokens.

This is a **partial** pass — a scan of this file found 72 raw-color matches at the start of this sub-batch; the remaining 64 (per post-fix `eslint`) are mostly deliberate exclusions already reasoned about in earlier sessions: the rider-card (blue) / driver-card (emerald) branding theme, the pickup/dropoff dot colors (blue/red, a fixed UI convention), the money-breakdown categorical differentiation (driver/platform/incentives — emerald/violet/amber, 3+ arbitrary categories per the established money-category-differentiation exclusion), the star-rating amber convention, the dispatch-offer-outcome categorical map (accepted/declined/expired/preempted), and the promo/tip inline figures (single-column decorative accents, not alert-style signals). The already-documented `STATUS_META` hero-badge block was untouched.

**Risk & impact on existing functionality**: Pure CSS class-name substitution across ~10 JSX locations — no logic, props, or conditional rendering changed. `--success`/`--warning`/`--destructive` are pre-existing tokens already used elsewhere in this same file. Blast radius: isolated to `ride-detail-modal.tsx`; this component is the shared ride-detail dialog used by `rides/page.tsx`, `promotions/page.tsx`, and other pages that open it — verified none of those callers depend on the specific class values changed here (they only pass `rideId`/`open`/`onClose` props).

**User experience effect**: Internal-admin-only surface (opened from multiple admin pages via the shared ride-detail dialog). Visually equivalent in both themes — same hue family, now theme-aware via tokens.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx` | Load-error text, flag-count severity badges, complaint status badge, near-ban notice, Flag/Complaint/Force-Cancel buttons, Cancellation card accent → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<span className={`... ${ride.rider_flag_count >= 2 ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"}`}>
// after
<span className={`... ${ride.rider_flag_count >= 2 ? "bg-destructive/15 text-destructive" : "bg-warning/15 text-warning"}`}>
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 64 warnings (down from 72; remaining are the documented categorical/decorative exclusions discussed above).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap). The remaining ~64 raw-color occurrences in this file (branding themes, money-category differentiation, dispatch-offer-outcome map) were reviewed but left untouched as established exclusions rather than force-converted.
