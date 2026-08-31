/**
 * app/driver/(tabs)/profile.tsx's `handleSaveProfile` -- validation
 * extracted from the two sequential inline checks (all fields required;
 * email must match the screen's own email-shape regex), plus the
 * fields-required check hand-duplicated at the save button's `disabled`
 * prop and its style expression, per ACTION_ITEMS.md B39 (broader-sweep
 * candidate: driver-app #4, email regex). Each helper below is a
 * byte-for-byte equivalent of the check it replaces -- see the PR
 * description for the before/after per call site.
 */

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Mirrors `handleSaveProfile`'s
 * `!editFirstName.trim() || !editLastName.trim() || !editEmail.trim() || !editGender`
 * check (inverted) -- also hand-duplicated at the save button's
 * `disabled` prop and its style expression; this is the exact-duplicate
 * case those two call sites now share with `handleSaveProfile`.
 */
export function isProfileFieldsComplete(firstName: string, lastName: string, email: string, gender: string): boolean {
  return !!(firstName.trim() && lastName.trim() && email.trim() && gender);
}

/** Mirrors `handleSaveProfile`'s `!EMAIL_REGEX.test(editEmail)` check (inverted). Untrimmed, matching the original call site. */
export function isProfileEmailFormatValid(email: string): boolean {
  return EMAIL_REGEX.test(email);
}

/**
 * Runs the same checks as `handleSaveProfile`'s old inline validation
 * block, in the same order, and returns the same `{ title, message }`
 * pair the original passed to `showToast('error', ...)` for the first
 * failing check, or null if both pass.
 */
export function getProfileFormError(
  firstName: string,
  lastName: string,
  email: string,
  gender: string,
): { title: string; message: string } | null {
  if (!isProfileFieldsComplete(firstName, lastName, email, gender)) {
    return { title: 'Missing Info', message: 'Please fill in all fields' };
  }
  if (!isProfileEmailFormatValid(email)) {
    return { title: 'Invalid Email', message: 'Please enter a valid email address' };
  }
  return null;
}
