/**
 * Shared turn-by-turn launcher — the one implementation behind both the manual
 * Navigate button and the automatic hand-off.
 *
 * The per-platform cases matter more than they look. The panel's original
 * inline copy emitted the iOS scheme on both platforms, which on Android meant
 * a Waze-preferring driver was silently sent to Google Maps and a "start
 * navigation" tap landed on a route preview rather than turn-by-turn. Now that
 * the hand-off fires twice a ride without anyone tapping anything, that is the
 * difference between the feature working and the feature being wrong on every
 * Android leg.
 */
import { Linking, Platform } from 'react-native';
import {
  launchNavigation,
  targetUrlFor,
  googleWebUrlFor,
  platformDefaultUrlFor,
} from '../../lib/navigation/launchNavigation';

const LAT = 52.1333;
const LNG = -106.6667;

let openURL: jest.SpyInstance;
let canOpenURL: jest.SpyInstance;
const originalOS = Platform.OS;

beforeEach(() => {
  openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
  canOpenURL = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(true as never);
});
afterEach(() => {
  (Platform as any).OS = originalOS;
  openURL.mockRestore();
  canOpenURL.mockRestore();
});

describe('targetUrlFor', () => {
  it('uses the iOS schemes on iOS', () => {
    expect(targetUrlFor('waze', LAT, LNG, true)).toBe('waze://?ll=52.1333,-106.6667&navigate=yes');
    expect(targetUrlFor('google', LAT, LNG, true)).toBe(
      'comgooglemaps://?daddr=52.1333,-106.6667&directionsmode=driving',
    );
    expect(targetUrlFor('default', LAT, LNG, true)).toContain('maps.apple.com');
  });

  it('uses the Android intent and the Waze universal link on Android', () => {
    // waze:// would resolve to nothing on API 30+ — app.config.ts declares
    // LSApplicationQueriesSchemes for iOS but no Android <queries>.
    expect(targetUrlFor('waze', LAT, LNG, false)).toBe(
      'https://waze.com/ul?ll=52.1333,-106.6667&navigate=yes',
    );
    expect(targetUrlFor('google', LAT, LNG, false)).toBe('google.navigation:q=52.1333,-106.6667');
    expect(targetUrlFor('default', LAT, LNG, false)).toBe('google.navigation:q=52.1333,-106.6667');
  });

  it('matches the car hand-off builder, which had the split right first', () => {
    // lib/androidAuto/carRoute.ts buildHandoffUrl — same app, same platforms.
    expect(targetUrlFor('google', 52.2, -106.6, false)).toBe('google.navigation:q=52.2,-106.6');
    expect(targetUrlFor('google', 52.2, -106.6, true)).toBe(
      'comgooglemaps://?daddr=52.2,-106.6&directionsmode=driving',
    );
  });
});

describe('launchNavigation on iOS', () => {
  beforeEach(() => { (Platform as any).OS = 'ios'; });

  it('opens the chosen app when it is installed', async () => {
    await launchNavigation('waze', LAT, LNG);
    expect(openURL).toHaveBeenCalledWith('waze://?ll=52.1333,-106.6667&navigate=yes');
  });

  it('never leaves a dead deep link when the chosen app is missing', async () => {
    canOpenURL.mockResolvedValue(false as never);
    await launchNavigation('waze', LAT, LNG);
    expect(openURL).toHaveBeenCalled();
    for (const call of openURL.mock.calls) {
      expect((call[0] as string).startsWith('waze://')).toBe(false);
    }
  });

  it('falls back when canOpenURL itself throws', async () => {
    canOpenURL.mockRejectedValue(new Error('scheme not whitelisted'));
    await launchNavigation('google', LAT, LNG);
    expect(openURL).toHaveBeenCalled();
    for (const call of openURL.mock.calls) {
      expect((call[0] as string).startsWith('comgooglemaps://')).toBe(false);
    }
  });

  it('uses no app-specific scheme on Default', async () => {
    await launchNavigation('default', LAT, LNG);
    const url = openURL.mock.calls[0][0] as string;
    expect(url.startsWith('waze://')).toBe(false);
    expect(url.startsWith('comgooglemaps://')).toBe(false);
  });

  it('retries on the universal web URL when Apple Maps rejects', async () => {
    openURL.mockRejectedValueOnce(new Error('no handler'));
    await launchNavigation('default', LAT, LNG);
    expect(openURL).toHaveBeenLastCalledWith(googleWebUrlFor(LAT, LNG));
  });
});

describe('launchNavigation on Android', () => {
  beforeEach(() => { (Platform as any).OS = 'android'; });

  it('sends a Waze driver to Waze, not to Google Maps', async () => {
    // The regression this guards: canOpenURL('waze://') is false on API 30+
    // even with Waze installed, so gating on it dumped the driver into the
    // Google web URL on every single leg.
    await launchNavigation('waze', LAT, LNG);
    expect(openURL).toHaveBeenCalledWith('https://waze.com/ul?ll=52.1333,-106.6667&navigate=yes');
  });

  it('does not consult canOpenURL at all', async () => {
    await launchNavigation('waze', LAT, LNG);
    expect(canOpenURL).not.toHaveBeenCalled();
  });

  it('starts turn-by-turn rather than a route preview on Default', async () => {
    await launchNavigation('default', LAT, LNG);
    expect(openURL).toHaveBeenCalledWith('google.navigation:q=52.1333,-106.6667');
  });

  it('falls back to the web URL when the navigation intent throws', async () => {
    openURL.mockRejectedValueOnce(new Error('no Maps app'));
    await launchNavigation('default', LAT, LNG);
    expect(openURL).toHaveBeenLastCalledWith(googleWebUrlFor(LAT, LNG));
  });

  it('does not retry an identical URL twice', async () => {
    // platformDefaultUrlFor and googleWebUrlFor can coincide; a repeat can only
    // fail the same way and just delays the warning.
    openURL.mockRejectedValue(new Error('no handler'));
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    await launchNavigation('default', LAT, LNG);
    const urls = openURL.mock.calls.map((c) => c[0] as string);
    expect(new Set(urls).size).toBe(urls.length);
    warn.mockRestore();
  });

  it('warns instead of rejecting when nothing can handle a maps URL', async () => {
    openURL.mockRejectedValue(new Error('no handler'));
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    await expect(launchNavigation('default', LAT, LNG)).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalled();
    // Never the destination coordinates — PIPEDA bars them from logs.
    expect(String(warn.mock.calls[0][0])).not.toContain('52.1333');
    warn.mockRestore();
  });
});

describe('platformDefaultUrlFor', () => {
  it('is Apple Maps on iOS and the navigation intent on Android', () => {
    expect(platformDefaultUrlFor(LAT, LNG, true)).toContain('maps.apple.com');
    expect(platformDefaultUrlFor(LAT, LNG, false)).toBe('google.navigation:q=52.1333,-106.6667');
  });
});
