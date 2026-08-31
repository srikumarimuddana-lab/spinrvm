/**
 * app/driver/settings.tsx's `executeDelete` -- validation extracted from
 * the type-DELETE confirmation check per ACTION_ITEMS.md B39
 * (broader-sweep candidate: driver-app #5, PIPEDA-adjacent account
 * deletion). A byte-for-byte equivalent of the check it replaces -- see
 * the PR description for the before/after at the call site.
 *
 * PIPEDA-adjacent: this gates `DELETE /users/account`, the driver-facing
 * self-serve account-deletion request -- a validation gap here (an
 * un-confirmed deletion silently proceeding) would mean an
 * irreversible-looking action taken without the deliberate typed
 * confirmation the UI promises.
 */

/** Mirrors `executeDelete`'s `deleteInput.trim().toUpperCase() !== 'DELETE'` check (inverted). */
export function isDeleteConfirmationValid(deleteInput: string): boolean {
  return deleteInput.trim().toUpperCase() === 'DELETE';
}
