/**
 * app/manage-cards.tsx's `handleAddCard` -- validation extracted per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: rider-app #3, last two
 * remaining candidates from the 2026-08-31 broader sweep). Pure
 * extraction, byte-for-byte equivalent of the checks it replaces -- no
 * behavior change.
 */

/** Mirrors `!cardDetailsComplete` (inverted to a positive predicate). */
export function isCardDetailsComplete(cardDetailsComplete: boolean): boolean {
  return cardDetailsComplete;
}

/** Mirrors `!cardName.trim()` (inverted to a positive predicate). */
export function isCardholderNameValid(cardName: string): boolean {
  return cardName.trim().length > 0;
}

/**
 * Mirrors `!createPaymentMethod` (inverted) -- Stripe's `createPaymentMethod`
 * function is undefined until the Stripe SDK finishes initializing.
 */
export function isStripeReady(createPaymentMethod: unknown): boolean {
  return !!createPaymentMethod;
}

/**
 * Runs the same checks as the screen's old inline `handleAddCard` block, in
 * the same order (card details, then cardholder name, then Stripe
 * readiness), and returns the same `{ title, message }` toast pair for the
 * first failing check -- or null if every check passes. Drop-in
 * replacement for the three sequential `if` blocks + `showToast(...)` calls.
 */
export function getManageCardsFormError(
  cardDetailsComplete: boolean,
  cardName: string,
  createPaymentMethod: unknown,
): { title: string; message: string } | null {
  if (!isCardDetailsComplete(cardDetailsComplete)) {
    return { title: 'Missing Details', message: 'Please enter complete card details' };
  }
  if (!isCardholderNameValid(cardName)) {
    return { title: 'Missing Name', message: 'Please enter the cardholder name' };
  }
  if (!isStripeReady(createPaymentMethod)) {
    return { title: 'Payments unavailable', message: 'Payment processing is still starting up. Try again in a moment.' };
  }
  return null;
}
