# Change Impact & Risk Log — #2816 Batch 7, sub-batch 40: drivers list/detail signals + categorical documentation (partial)

**Issue/gap identified**: `drivers/page.tsx` (a large ~3500-line file — the main Drivers list + detail slideout) had two undocumented categorical maps generating unaddressed lint warnings (a duplicated 6-state driver-lifecycle status Badge ternary in both the list row and the detail slideout, and a duplicated 7-state `RIDE_STATUS_STYLE` ride-status map), plus several genuine single/binary signals still using hardcoded Tailwind colors: the online/offline badges (×2 duplicates), total earnings figure, Spinr Pass "expired" badge, per-document status badges (Missing/pending/Approved/Re-upload needed), the document-summary line (pending/missing/expired/all-clear), the rejected-photo notice, and the pending-photo-review notice box.

**Root cause**: This file predates the semantic-token system introduced for #2816 and, per an earlier session note, had been treated as a "zero-conversion" file — that assessment covered only a narrower check and missed both the undocumented categorical maps and several genuine convertible signals actually present here.

**Fix/remediation**:
- Documented the driver-lifecycle-status Badge ternary (6 states: deleted/active/needs_review/suspended/banned/pending, 5 hues) with per-branch `eslint-disable-next-line` comments, in both the list-row and detail-slideout copies — matching the convention from `driver-action-bar.tsx`'s `STATUS_CONFIG` (sub-batch 35) and `driver-timeline.tsx`'s `EVENT_CONFIG` (sub-batch 34).
- Documented the duplicated `RIDE_STATUS_STYLE` 7-state ride-status map with an `eslint-disable`/`eslint-enable` block, matching `lib/utils.ts`'s `statusColor()` and `ride-ui-helpers.tsx`'s `STATUS_CONFIG`.
- Converted genuine signals: online/offline badges (×2) → success tokens; total-earnings figure → success; "Pass Expired" badge → destructive; per-document status badges (Missing/Re-upload needed → destructive, pending/expiry-not-recorded → warning, Approved → success); document-summary line (pending → warning, missing/expired → destructive, all-clear → success); rejected-photo notice text → destructive; pending-photo-review notice box (background/border/text) → warning tokens.

This is a **partial** pass — a scan of this file found 112 raw-color matches at the start of this sub-batch; the remaining 151 warnings (post-fix `eslint`, line-count differs from the earlier match-count metric) include the solid-fill Approve/Reject photo buttons (fixed shades, not the `--success` token — left per the established dark-mode contrast-risk policy), the star-rating amber convention, and substantial unreviewed sections of this very large file (the vehicle-history, payouts, and referrals tabs were not read in this sub-batch).

**Risk & impact on existing functionality**: Pure CSS class-name substitution plus comment additions — no logic, props, or conditional rendering changed anywhere in this diff. `--success`/`--warning`/`--destructive` are pre-existing tokens already used elsewhere in this file. Blast radius: isolated to `drivers/page.tsx`; `RIDE_STATUS_STYLE` and the status-badge ternary are both module-local (verified via grep — not exported/imported elsewhere).

**User experience effect**: Internal-admin-only surface (`/dashboard/drivers`). Visually equivalent in both themes for every converted element.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Documented 2 categorical maps; converted online/offline badges, earnings figure, Pass Expired badge, document-status badges/summary, photo notices → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
{matchingDocs.length === 0 && <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[10px]">Missing</Badge>}
{counts.pending > 0 && <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-[10px]">{counts.pending} pending</Badge>}
// after
{matchingDocs.length === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Missing</Badge>}
{counts.pending > 0 && <Badge className="bg-warning/15 text-warning text-[10px]">{counts.pending} pending</Badge>}
```

**Rollback plan**: `git revert` — pure class-name/comment changes, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 151 warnings (down from 172 pre-fix; remaining are the solid-fill button exclusions, star-rating convention, and unreviewed sections of this large file, all deferred to follow-up sub-batches).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap). This is a large file (~3500 lines); the vehicle-history, payouts-summary, and referrals detail tabs were not reviewed in this sub-batch and are flagged for follow-up rather than rushed.
