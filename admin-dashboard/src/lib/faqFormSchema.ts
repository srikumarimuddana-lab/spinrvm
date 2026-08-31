/**
 * dashboard/faqs/page.tsx's `handleSave` -- validation extracted from
 * the inline question/answer requiredness check per ACTION_ITEMS.md B39
 * (broader-sweep candidate: admin-dashboard #11, faqs). Pure extraction,
 * byte-for-byte equivalent of the check it replaces -- no behavior
 * change.
 *
 * Not a zod schema -- a single combined non-empty-after-trim check has
 * no meaningful shape for zod's parsing machinery to add value over.
 */

/** Mirrors `handleSave`'s `!form.question.trim() || !form.answer.trim()` check (inverted). */
export function isFaqFormValid(question: string, answer: string): boolean {
  return !!question.trim() && !!answer.trim();
}
