# Change Impact & Risk Log — C97 client fallback-path: verify, don't rebuild

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | none (verification + doc correction only — no code changed) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `mvapps/blissful-thompson-u14frt` |
| Related issue or gap ID | ACTION_ITEMS.md C97, recommendation #4 |

**No code was changed by this pass.** This closes out an investigation
("go deeper on C97's client fallback path") that found the requested fix
already shipped, and corrects a tracker entry this session itself wrote
incorrectly two days after the fact.

## 1. Issue / gap identified

The previous session's own PR (#5223, merged 2026-09-11) left C97's status
line reading "the client fallback-path/iOS recommendations (#4, #5, #6)
remain open." That was wrong for #4: PR #5160 ("show a toast for unhandled
foreground FCM notification types," merged 2026-09-09) had already shipped
it — two days *before* the incorrect status line was written. The #5223 pass
didn't re-check the actual client code before writing that line; it should
have.

## 2. Root cause

Process gap in the prior pass: updated ACTION_ITEMS.md's C97 status
prose based on the "recommendations, in leverage order" list's own wording
without re-diffing which of those recommendations already had a merged PR
against them. `git log` on the relevant files would have caught it in one
command.

## 3. Fix / remediation

Verified directly against the current code (`driver-app/hooks/
useDriverDashboard.ts`'s foreground `onForegroundMessage` handler, and
`driver-app/services/backgroundMessaging.ts`) rather than trusting either
the stale tracker line or PR #5160's own commit message at face value:

- **Foreground**: confirmed the final `else if (data?.type)` branch (~line
  2016) calls `showToast(...)` with `remoteMessage.notification.title/body`
  (falling back to a generic string if that block is ever absent) for any
  type outside the 5 explicitly-handled ones. This is recommendation #4,
  done.
- **Background/killed**: confirmed `backgroundMessaging.ts` still only
  branches on `new_ride_assignment`/`ride_cancelled`/`location_health` —
  unchanged by PR #5160, and correctly so. Every other push type carries a
  real FCM `notification` block (per `backend/features.py`'s `is_data_only`
  gate), which Android/iOS display automatically with zero app code running,
  regardless of foreground/background/killed state. Adding a
  background-handler fallback for those types would risk a **duplicate**
  notification (one from the OS automatically, one from a hand-rolled
  fallback), not fix a real gap.
- Updated ACTION_ITEMS.md's C97 section: corrected the status line, and
  marked recommendation #4 done in the "leverage order" list with the exact
  file/line evidence above, replacing the two-days-stale "remain open"
  claim.

## 4. Risk & impact on existing functionality

None — this is a documentation correction. No code, config, or data changed.

## 5. User-experience effect

None.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | C97 status line + recommendation #4 marked done, with a self-correction note | Fix a tracker entry this session itself got wrong |
| `docs/change-log/2026-09-11-c97-client-fallback-verification.md` | New — this file | Record the verification and the correction |

## 7. Before / after

Not applicable — no behavior-changing diff (documentation only).

## 8. Rollback plan

`git-revert-safe` — pure documentation.

## 9. Verification performed

- [x] Re-read the actual current client code on `origin/main`
  (`driver-app/hooks/useDriverDashboard.ts`, `driver-app/services/
  backgroundMessaging.ts`) rather than trusting either the stale tracker
  line or PR #5160's commit message.
- [x] `git log --oneline` on both files to confirm PR #5160 is the most
  recent relevant change and hasn't been reverted or further modified in a
  way that would invalidate its own reasoning.
- [x] Cross-checked PR #5160's own stated reasoning (no background-handler
  change needed) against the actual `is_data_only` gate in
  `backend/features.py` and the actual branch list in
  `backgroundMessaging.ts` — independently confirmed, not just trusted.

## What was NOT verified

- Not tested on a real device — this is a code-reading verification, not a
  device/QA pass. The claim that Android/iOS auto-display a `notification`-
  block FCM message in background/killed state with no app code running is
  standard, well-documented FCM/APNs platform behavior, not something this
  session can confirm empirically without real hardware.
- The forward-looking risk named in the ACTION_ITEMS.md addendum (a future
  data-only push type with no dedicated background handler would be
  silently invisible) is not fixed here — deliberately, since no such type
  exists today and building a guard for a hypothetical future case isn't
  justified by this task (CLAUDE.md's simplicity-first / no-speculative-code
  rule). Named so a future session adding a new data-only type sees the
  warning, not silently fixed.

## 10. Sign-off

- [x] Blast radius is stated, not assumed (zero — no code changed)
- [x] No silent behavior change — nothing changed
- [x] The self-correction is disclosed plainly, not smoothed over
