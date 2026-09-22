/**
 * Hands turn-by-turn navigation to the driver's chosen maps app.
 *
 * Lifted verbatim out of ActiveRidePanel's inline `openMapsNavigation` when the
 * auto-launch effect needed the same behaviour — deliberately extracted rather
 * than copied, because a second copy is exactly the fork that CLAUDE.md's
 * pre-implementation review rule exists to prevent. Behaviour is unchanged; the
 * panel's manual Navigate button and the automatic hand-off now run the one
 * implementation, so a fix to either reaches both.
 *
 * (A third, older launcher still lives at `hooks/useDriverDashboard.ts`'s
 * `openNavigation` — it hardcodes `google.navigation:` and ignores the driver's
 * saved preference. Left alone here: it is reached through a different prop
 * (`onNavigate`) and collapsing it is a separate change.)
 */
import { Linking, Platform } from 'react-native';
import type { NavApp } from '../../store/navStore';

/**
 * The Google Maps web URL is the universal fallback — it works on every device
 * whether or not a native app is installed. On devices WITH the Google Maps app
 * it auto-redirects; without it (or in Expo Go) it opens the browser, which
 * still provides turn-by-turn.
 */
export function googleWebUrlFor(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}

/**
 * The phone's built-in maps app — Apple Maps on iOS, Google Maps web on Android.
 * Always present, always gives driving directions, so it is what an uninstalled
 * choice falls back to.
 */
export function defaultUrlFor(lat: number, lng: number): string {
  const appleUrl = `http://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`;
  return Platform.OS === 'ios' ? appleUrl : googleWebUrlFor(lat, lng);
}

/** Deep link for an explicitly-chosen app, or null when the driver left it on Default. */
export function deepLinkFor(navApp: NavApp, lat: number, lng: number): string | null {
  if (navApp === 'waze') return `waze://?ll=${lat},${lng}&navigate=yes`;
  if (navApp === 'google') return `comgooglemaps://?daddr=${lat},${lng}&directionsmode=driving`;
  return null;
}

/**
 * Open driving directions to `lat`/`lng` in the driver's preferred app, falling
 * back when that app isn't installed.
 *
 * canOpenURL is the reliable cross-platform check: on iOS the `waze` and
 * `comgooglemaps` schemes are whitelisted in app.config's
 * LSApplicationQueriesSchemes, so canOpenURL returns false (not a system error)
 * when the app is absent; on Android it reflects installed intents.
 */
export async function launchNavigation(navApp: NavApp, lat: number, lng: number): Promise<void> {
  const googleWebUrl = googleWebUrlFor(lat, lng);
  const defaultUrl = defaultUrlFor(lat, lng);
  // Both openURL calls can reject (no handler for the scheme at all). The web
  // URL is the last resort, so a failure there is a dead end for this tap —
  // recoverable, because the driver can still tap Navigate again or open their
  // maps app by hand, so it warns rather than surfacing an error to them.
  //
  // The rejection itself is deliberately NOT logged: React Native puts the
  // failing URL in the message, and that URL carries the ride's raw pickup or
  // dropoff coordinates, which CLAUDE.md's PIPEDA rules bar from logs outright.
  // The driver's chosen app is the diagnostic that actually matters and is not
  // personal information.
  const openDefault = () =>
    Linking.openURL(defaultUrl).catch(() =>
      Linking.openURL(googleWebUrl).catch(() =>
        console.warn(`[launchNavigation] no maps handler available (pref: ${navApp})`),
      ),
    );

  const appUrl = deepLinkFor(navApp, lat, lng);
  if (!appUrl) {
    // 'default' — use the platform's native maps app.
    openDefault();
    return;
  }

  try {
    if (await Linking.canOpenURL(appUrl)) {
      await Linking.openURL(appUrl);
      return;
    }
  } catch {
    // canOpenURL/openURL threw — fall through to the default maps app.
  }
  openDefault();
}
