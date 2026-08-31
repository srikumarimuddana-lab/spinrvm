/**
 * dashboard/users/page.tsx's ban/suspend confirmation dialog -- the
 * moderation-reason requiredness check, used at both the confirm
 * button's `disabled` prop and its `onClick` guard, per ACTION_ITEMS.md
 * B39 (broader-sweep candidate: admin-dashboard #9, ban/suspend reason).
 * Pure extraction, byte-for-byte equivalent of the check it replaces --
 * no behavior change.
 *
 * Not a zod schema -- a single non-empty-after-trim check has no
 * meaningful shape for zod's parsing machinery to add value over.
 */

/** Mirrors the dialog's `!moderationReason.trim()` check (inverted). */
export function isModerationReasonValid(reason: string): boolean {
  return !!reason.trim();
}
