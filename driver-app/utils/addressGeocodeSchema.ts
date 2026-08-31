/**
 * app/driver/addresses.tsx's `handleAddAddress` and
 * app/driver/destination-mode.tsx's `handleSave` -- both validate a
 * saved-address form the same way (address fields required; the
 * geocoded result must resolve) per ACTION_ITEMS.md B39 (broader-sweep
 * candidate: driver-app #6 and final item, "saved-address + geocode-
 * failure gates -- same shape, likely one shared extraction"). Shared
 * predicates live here; each screen keeps its own toast-copy
 * construction at the call site, since `addresses.tsx` uses literal
 * English strings and `destination-mode.tsx` uses this app's i18n
 * `t(...)` keys -- forcing those two copy sources into one shared
 * function would either lose `destination-mode.tsx`'s i18n or fabricate
 * i18n keys `addresses.tsx` never had, either of which is a behavior
 * change, not a pure extraction. Each helper below is a byte-for-byte
 * equivalent of the check it replaces -- see the PR description for the
 * before/after per call site.
 */

/** Mirrors `addresses.tsx handleAddAddress`'s `!newAddress.name.trim() || !newAddress.address.trim()` check (inverted). */
export function isAddressNameAndAddressValid(name: string, address: string): boolean {
  return !!(name.trim() && address.trim());
}

/** Mirrors `destination-mode.tsx handleSave`'s `!trimmed` check (inverted), where `trimmed = addressInput.trim()`. */
export function isAddressInputValid(addressInput: string): boolean {
  return !!addressInput.trim();
}

/**
 * Mirrors both screens' `!coords` check (inverted) on the result of
 * `geocodeAddress(...)` -- a null result means the address couldn't be
 * located.
 */
export function isGeocodeResultValid(coords: { lat: number; lng: number } | null): boolean {
  return !!coords;
}
