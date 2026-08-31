/**
 * app/driver/emergency-contacts.tsx's `handleAdd` -- validation extracted
 * from the two sequential inline checks (name required; phone must have
 * at least 10 digits after non-digit stripping) per ACTION_ITEMS.md B39
 * (broader-sweep candidate: driver-app #2, safety). Each helper below is
 * a byte-for-byte equivalent of the check it replaces -- see the PR
 * description for the before/after per call site.
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
