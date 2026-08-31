/**
 * dashboard/venues/page.tsx's venue-editor form -- the venue-name
 * requiredness check, currently enforced only via the Save button's
 * `disabled` prop (there is no separate inline validation block with its
 * own error message) per ACTION_ITEMS.md B39 (broader-sweep candidate:
 * admin-dashboard #10, venues). Pure extraction, byte-for-byte
 * equivalent of the check it replaces -- no behavior change.
 *
 * Not a zod schema -- a single non-empty-after-trim check has no
 * meaningful shape for zod's parsing machinery to add value over.
 *
 * Out of scope for this step: this page also uses raw `alert`/`confirm`
 * instead of the toast pattern used elsewhere in admin-dashboard (flagged
 * separately in the same broader sweep) -- that is a UX-pattern finding,
 * not a validation-logic gap, and isn't touched here.
 */

/** Mirrors the Save button's `!d.name.trim()` disabled-guard check (inverted). */
export function isVenueNameValid(name: string): boolean {
  return !!name.trim();
}
