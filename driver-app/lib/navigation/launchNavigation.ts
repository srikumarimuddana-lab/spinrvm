/**
 * Hands turn-by-turn navigation to the driver's chosen maps app.
 *
 * Extracted from ActiveRidePanel's inline `openMapsNavigation` when the
 * auto-launch effect needed the same behaviour — deliberately extracted rather
 * than copied, because a second copy is exactly the fork that CLAUDE.md's
 * pre-implementation review rule exists to prevent. The panel's manual Navigate
 * button and the automatic hand-off run this one implementation, so a fix to
 * either reaches both.
 *
 * (A third, older launcher still lives at `hooks/useDriverDashboard.ts`'s
 * `openNavigation`, reached through a different prop. Collapsing it is a
 * separate change — but note it already used the Android intent this module
 * now uses, and was closer to correct than the panel's copy was.)
 */
import { Linking, Platform } from 'react-native';
import type { NavApp } from '../../store/navStore';

/**
 * The Google Maps web URL — the universal last resort. It works on every device
 * whether or not a native app is installed: with Google Maps present it opens
 * there, without it (or in Expo Go) it opens the browser. Note this lands on a
 * route *preview*, not turn-by-turn, which is why it is the fallback and never
 * the first choice.
 */
export function googleWebUrlFor(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}

/** The phone's own maps app: Apple Maps on iOS, Google Maps on Android. */
export function platformDefaultUrlFor(lat: number, lng: number, ios: boolean): string {
  return ios
    ? `http://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`
    : `google.navigation:q=${lat},${lng}`;
}

/**
 * Where a given preference should actually send the driver, per platform.
 *
 * The same app needs different URLs on each OS, which the panel's original
 * inline copy did not account for — it emitted the iOS scheme on both. Mirrors
 * `lib/androidAuto/carRoute.ts`'s `buildHandoffUrl`, which had this right:
 *   - google → iOS `comgooglemaps://`, Android `google.navigation:` intent
 *   - waze   → iOS `waze://`, Android the `waze.com/ul` universal link
 *   - default→ Apple Maps on iOS, the Google navigation intent on Android
 *
 * Waze on Android is a universal link rather than `waze://` on purpose: API 30+
 * package-visibility filtering hides unlisted packages, and app.config.ts
 * declares `LSApplicationQueriesSchemes` for iOS but no Android `<queries>`, so
 * the scheme link would resolve to nothing even with Waze installed.
 */
export function targetUrlFor(
  navApp: NavApp,
  lat: number,
  lng: number,
  ios: boolean = Platform.OS === 'ios',
): string {
  if (navApp === 'waze') {
    return ios
      ? `waze://?ll=${lat},${lng}&navigate=yes`
      : `https://waze.com/ul?ll=${lat},${lng}&navigate=yes`;
  }
  if (navApp === 'google') {
    return ios
      ? `comgooglemaps://?daddr=${lat},${lng}&directionsmode=driving`
      : `google.navigation:q=${lat},${lng}`;
  }
  return platformDefaultUrlFor(lat, lng, ios);
}

/**
 * Try each URL in turn, stopping at the first that opens. Duplicates are
 * skipped — on Android the platform default and the web fallback can be the
 * same string, and retrying a URL that just rejected only delays the warning.
 */
async function openFirst(urls: string[], navApp: NavApp): Promise<void> {
  const tried = new Set<string>();
  for (const url of urls) {
    if (tried.has(url)) continue;
    tried.add(url);
    try {
      await Linking.openURL(url);
      return;
    } catch {
      // No handler for this one — fall through to the next.
    }
  }
  // Every candidate failed. Recoverable: the driver can still tap Navigate
  // again or open their maps app by hand, so this warns rather than surfacing
  // an error. The rejections themselves are deliberately NOT logged — React
  // Native puts the failing URL in the message, and that URL carries the ride's
  // raw pickup or dropoff coordinates, which CLAUDE.md's PIPEDA rules bar from
  // logs. The chosen app is the diagnostic that matters and is not personal.
  console.warn(`[launchNavigation] no maps handler available (pref: ${navApp})`);
}

/**
 * Open driving directions to `lat`/`lng` in the driver's preferred app, falling
 * back when that app isn't installed.
 */
export async function launchNavigation(navApp: NavApp, lat: number, lng: number): Promise<void> {
  const ios = Platform.OS === 'ios';
  const target = targetUrlFor(navApp, lat, lng, ios);
  const candidates: string[] = [];

  // canOpenURL is only consulted on iOS, where app.config.ts whitelists the two
  // third-party schemes in LSApplicationQueriesSchemes and it therefore answers
  // truthfully. On Android it does not: package-visibility filtering (API 30+)
  // returns false for an installed app that `<queries>` doesn't declare, so
  // gating on it there would send every Waze driver to Google Maps. Android
  // instead opens the intent directly and lets the rejection pick the next
  // candidate — the same approach lib/androidAuto/register.ts already uses,
  // whose comment notes that `google.navigation:` throws with no Maps app.
  if (ios && navApp !== 'default') {
    let installed = false;
    try {
      installed = await Linking.canOpenURL(target);
    } catch {
      installed = false;
    }
    if (installed) candidates.push(target);
    candidates.push(platformDefaultUrlFor(lat, lng, ios));
  } else {
    candidates.push(target);
  }
  candidates.push(googleWebUrlFor(lat, lng));

  await openFirst(candidates, navApp);
}
