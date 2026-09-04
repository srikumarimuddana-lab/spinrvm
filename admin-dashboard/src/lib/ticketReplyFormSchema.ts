/**
 * dashboard/support-tickets/tickets/[id]/page.tsx's `sendReply` and
 * `sendNote` -- validation extracted from the two inline requiredness
 * checks, per ACTION_ITEMS.md B39 (support-tickets area -- not part of
 * the original 21-candidate broader sweep; found during B39's ADR
 * freshness check). Pure extraction, byte-for-byte equivalent of the
 * checks they replace -- no behavior change.
 *
 * `isReplyContentValid` mirrors `sendReply`'s own guard exactly: it
 * takes the DOMPurify-sanitized draft run through the page's local
 * `toText()` helper (the same value the guard itself checks,
 * `toText(html).trim()`), not the raw editor draft. The "Send reply"
 * button's own `disabled` prop separately checks `!toText(reply).trim()`
 * against the *unsanitized* draft -- a pre-existing, non-identical
 * duplicate (not introduced by this extraction) left untouched, same
 * discipline prior B39 steps used for partial-overlap `disabled` props:
 * folding it into this predicate would be a behavior change, not a pure
 * extraction.
 *
 * `isNoteContentValid` mirrors `sendNote`'s exact-duplicate guard
 * (`!note.trim()`, identical to its own button's `disabled` prop) --
 * both call sites now share this one predicate, same as prior B39 steps'
 * exact-duplicate consolidations (e.g. step 25's moderation-reason
 * check).
 */

/** Mirrors `sendReply`'s `!toText(html).trim()` check (inverted), given
 *  the already-`toText()`-converted, sanitized reply draft. */
export function isReplyContentValid(sanitizedPlainText: string): boolean {
  return sanitizedPlainText.trim().length > 0;
}

/** Mirrors `sendNote`'s `!note.trim()` check (inverted). */
export function isNoteContentValid(note: string): boolean {
  return note.trim().length > 0;
}
