/**
 * dashboard/support-tickets/tickets/page.tsx's `create` -- validation
 * extracted from the inline subject-requiredness check, duplicated
 * exactly in the "Create ticket" button's `disabled` prop, per
 * ACTION_ITEMS.md B39 (support-tickets area -- not part of the original
 * 21-candidate broader sweep; found during B39's ADR freshness check).
 * Pure extraction, byte-for-byte equivalent of the check it replaces --
 * no behavior change.
 *
 * Not a zod schema -- a single non-empty-after-trim check has no
 * meaningful shape for zod's parsing machinery to add value over.
 */

/** Mirrors `create`'s `!form.subject.trim()` check (inverted). */
export function isTicketSubjectValid(subject: string): boolean {
  return subject.trim().length > 0;
}
