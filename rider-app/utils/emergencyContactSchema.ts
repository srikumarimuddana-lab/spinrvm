/**
 * app/emergency-contacts.tsx's `handleAdd` -- validation extracted from
 * the two sequential inline checks (name required; phone must have at
 * least 10 digits after non-digit stripping) per ACTION_ITEMS.md B39
 * (broader-sweep candidate: rider-app #4, the final item in the entire
 * 21-candidate broader sweep). Each helper below is a byte-for-byte
 * equivalent of the check it replaces -- see the PR description for the
 * before/after per call site.
 *
 * Same validation shape as driver-app's own
 * `emergencyContactSchema.ts` (B39 step 30) -- kept as a separate file
 * since rider-app and driver-app don't share a `utils/` module, but the
 * function names/signatures mirror it exactly for consistency.
 *
 * Safety-critical: emergency contacts are used by the SOS flow to notify
 * someone in a real emergency -- a validation gap here (an unreachable
 * phone number silently saved) has real safety consequence.
 */

/** Mirrors `handleAdd`'s `!trimmedName` check (inverted), where `trimmedName = name.trim()`. */
export function isContactNameValid(name: string): boolean {
  return !!name.trim();
}

/**
 * Mirrors `handleAdd`'s `trimmedPhone.length < 10` check (inverted),
 * where `trimmedPhone = phone.trim().replace(/\D/g, '')`.
 */
export function isContactPhoneValid(phone: string): boolean {
  const trimmedPhone = phone.trim().replace(/\D/g, '');
  return trimmedPhone.length >= 10;
}

/**
 * Runs the same checks as `handleAdd`'s old inline validation block, in
 * the same order, and returns the same `{ title, message }` pair the
 * original passed to `showToast('warning', ...)` for the first failing
 * check, or null if both pass.
 */
export function getEmergencyContactFormError(name: string, phone: string): { title: string; message: string } | null {
  if (!isContactNameValid(name)) {
    return { title: 'Missing Name', message: 'Please enter a contact name.' };
  }
  if (!isContactPhoneValid(phone)) {
    return { title: 'Invalid Phone', message: 'Please enter a valid phone number (at least 10 digits).' };
  }
  return null;
}
