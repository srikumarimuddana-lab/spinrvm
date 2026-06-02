/**
 * Google Places API session-token helper.
 *
 * One token per "user typing session" — N autocomplete requests + 1 details
 * lookup grouped for Google Places API (New) session pricing. Regenerate the
 * token after closing a session (i.e. after a successful place details call)
 * to start the next session.
 *
 * Not cryptographically sensitive — Google only requires opacity, not entropy.
 *
 * Lives in @shared/utils so rider-app, driver-app, and any future surface
 * can import the same generator. Previously duplicated in both app trees.
 */
export function newPlacesSessionToken(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
