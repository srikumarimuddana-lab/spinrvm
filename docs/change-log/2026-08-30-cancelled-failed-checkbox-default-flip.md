# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Caught mid-conversation: the operator asked "are we sure we need to check Include cancelled/failed bookings?" before re-running Legacy Booking Import for Step 3 of the rider-import walkthrough — investigating that question surfaced a real, already-documented incident this default caused once, and that my own prior instructions in this session almost caused it to repeat. |

## 1. Issue / gap identified

`LegacyBookingImport.tsx`'s "Include cancelled/failed bookings" checkbox defaulted to **checked**, even though every real production run of this batch has actually been scoped to **completed rides only** in practice. On 2026-08-29, an operator committed with this left at its old default and imported 918 unwanted cancelled/failed rows, requiring a manual SQL rollback (`docs/runbooks/legacy-booking-import-2026-08-22-batch.md` §8.1–8.2 — already documented before this change). Earlier in this same session, before checking the runbook, I told the operator to leave the checkbox checked for a re-run — repeating the same mistake the prior incident already made, caught only because the operator asked to double check first.

## 2. Root cause

The checkbox's default was set to match the *feature's* own default policy decision (2026-08-20: cancelled/failed import should be supported and is regulatory-motivated — PIPEDA/SK Transportation Act retention rules require GPS+timestamps for cancelled trips too). That policy decision is correct and unchanged. But the checkbox's default was never revisited against how the tool is *actually operated* in practice — every real batch run since has had an explicit "completed rides only" instruction, making the checked-by-default the wrong starting point for the common case, not the feature itself.

## 3. Fix / remediation

Flipped the checkbox's initial `useState` value from `true` to `false`. The underlying cancelled/failed import path, its validation, and its regulatory justification are all unchanged and fully intact — an operator who explicitly wants cancelled/failed rows imported still checks the box; it's opt-in now instead of opt-out.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one component's initial state.** Grepped for `includeCancelledFailed` — only used within `LegacyBookingImport.tsx` itself (the checkbox, and the value passed to `adminValidateBookingImport`/`adminCommitBookingImport`). No other component reads or depends on this default.
- **No backend change** — `services/booking_import_service.py`'s `include_cancelled_failed` parameter and its default (`True` at the function-signature level, for callers other than this UI) are untouched. This is purely which value the admin-dashboard checkbox starts at.
- **No existing tests reference the default** — grepped for `includeCancelledFailed`/"Include cancelled" across the admin-dashboard test tree; none found, so nothing to update.
- `npx tsc --noEmit` clean; `npm run build` — real production build, succeeded.

## 5. User-experience effect

- **Internal admin only.** Before: an operator opening this tool starts with cancelled/failed import silently enabled, matching neither the tool's actual operating history nor (based on this session) even careful operators' expectations. After: starts disabled — an operator who wants the broader scope makes an active choice to enable it, same one click either way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | `includeCancelledFailed` initial state: `true` → `false`; comment updated to reference the 2026-08-29 incident | Match the checkbox's default to how the tool is actually run in practice, after it already caused one incident |

## 7. Before / after

```tsx
// Before
const [includeCancelledFailed, setIncludeCancelledFailed] = useState(true);
```

```tsx
// After
const [includeCancelledFailed, setIncludeCancelledFailed] = useState(false);
```

## 8. Rollback plan

`git-revert-safe` — a pure UI default change, no data written by this diff itself.

## 9. Verification performed

- [x] Confirmed the 2026-08-29 incident directly from `docs/runbooks/legacy-booking-import-2026-08-22-batch.md` §8.1–8.2 (918 rows imported unintentionally, manually rolled back via SQL) rather than assuming.
- [x] Confirmed via direct production SQL that the batch currently holds zero `status='cancelled'` rows from this import (the 17 `cancelled` rows in production predate this batch and were untouched by it) — consistent with the documented rollback.
- [x] Grepped for every other reference to `includeCancelledFailed` — confirmed isolated to this one component.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` — real production build, succeeded.

## What was NOT verified

- Not run against a live re-import in this session — the operator's next click (Step 3 of the current rider-import walkthrough) will exercise this default for real; I'll verify the resulting rows via direct SQL afterward, same as every other step this session.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one component, no other consumers)
- [x] No silent behavior change to the underlying import logic — only the UI's starting checkbox state changed; the feature itself, its validation, and its regulatory justification are unchanged
