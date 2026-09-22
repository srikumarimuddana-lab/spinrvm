/**
 * Shared turn-by-turn launcher — the one implementation behind both the manual
 * Navigate button and the automatic hand-off. Extracted out of ActiveRidePanel
 * unchanged, so these cases mirror the panel's existing preference tests and
 * pin the behaviour against drift now that two call sites depend on it.
 */
import { Linking } from 'react-native';
import { launchNavigation, deepLinkFor, googleWebUrlFor } from '../../lib/navigation/launchNavigation';

let openURL: jest.SpyInstance;
let canOpenURL: jest.SpyInstance;

beforeEach(() => {
  openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
  canOpenURL = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(true as never);
});
afterEach(() => {
  openURL.mockRestore();
  canOpenURL.mockRestore();
});

describe('deepLinkFor', () => {
  it('maps each explicit choice to its scheme, and Default to none', () => {
    expect(deepLinkFor('waze', 52.1333, -106.6667)).toBe('waze://?ll=52.1333,-106.6667&navigate=yes');
    expect(deepLinkFor('google', 52.1333, -106.6667)).toBe(
      'comgooglemaps://?daddr=52.1333,-106.6667&directionsmode=driving',
    );
    expect(deepLinkFor('default', 52.1333, -106.6667)).toBeNull();
  });
});

describe('launchNavigation', () => {
  it('opens the chosen app when it is installed', async () => {
    await launchNavigation('waze', 52.1333, -106.6667);
    expect(openURL).toHaveBeenCalledWith('waze://?ll=52.1333,-106.6667&navigate=yes');
  });

  it('never leaves a dead deep link when the chosen app is missing', async () => {
    canOpenURL.mockResolvedValue(false as never);
    await launchNavigation('waze', 52.1333, -106.6667);
    expect(openURL).toHaveBeenCalled();
    for (const call of openURL.mock.calls) {
      expect((call[0] as string).startsWith('waze://')).toBe(false);
    }
  });

  it('falls back when canOpenURL itself throws', async () => {
    canOpenURL.mockRejectedValue(new Error('scheme not whitelisted'));
    await launchNavigation('google', 52.1333, -106.6667);
    expect(openURL).toHaveBeenCalled();
    for (const call of openURL.mock.calls) {
      expect((call[0] as string).startsWith('comgooglemaps://')).toBe(false);
    }
  });

  it('uses no app-specific scheme on Default', async () => {
    await launchNavigation('default', 52.1333, -106.6667);
    const url = openURL.mock.calls[0][0] as string;
    expect(url.startsWith('waze://')).toBe(false);
    expect(url.startsWith('comgooglemaps://')).toBe(false);
  });

  it('retries on the universal web URL when the platform default rejects', async () => {
    openURL.mockRejectedValueOnce(new Error('no handler'));
    await launchNavigation('default', 52.1333, -106.6667);
    expect(openURL).toHaveBeenLastCalledWith(googleWebUrlFor(52.1333, -106.6667));
  });

  it('warns instead of rejecting when no maps handler exists at all', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    openURL.mockRejectedValue(new Error('no handler'));
    await expect(launchNavigation('default', 52.1333, -106.6667)).resolves.toBeUndefined();
    await Promise.resolve();
    warn.mockRestore();
  });
});
