/**
 * shared/components/SOSButton.tsx localization coverage (P1 tracker #14).
 *
 * SOSButton is rendered by both rider-app and driver-app (5 call sites
 * total: rider-app/app/driver-arrived.tsx, driver-arriving.tsx,
 * ride-in-progress.tsx x2, (tabs)/index.tsx, and
 * driver-app/app/driver/(tabs)/index.tsx). It has no i18n instance of its
 * own — each app injects its own `t` prop — so this test exercises the
 * component directly against rider-app's real translation data (en-CA /
 * fr-CA) via `translate()` from rider-app/i18n, without going through the
 * zustand store/AsyncStorage hydration.
 *
 * This file is run from rider-app's jest config, whose moduleNameMapper
 * resolves `@shared/*` to the real shared/ source (driver-app's config
 * redirects most of `@shared/*` to manual mocks under
 * driver-app/__mocks__/@shared/, which has no SOSButton stub — rider-app is
 * the correct place to test the real component).
 */
import fs from 'fs';
import path from 'path';
import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { SOSButton } from '@shared/components/SOSButton';
import { t as translateKey } from '../i18n';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: undefined, lng: undefined }),
}));

// rider-app/i18n's translate() doesn't touch AsyncStorage, but the module
// also exports the AsyncStorage-backed useLanguageStore/getStoredLanguage,
// so the import still needs a working native-module stub under Jest.
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Alert, 'alert');
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('SOSButton localization', () => {
  it('defaults to English when no t prop is passed (back-compat for any un-migrated caller)', () => {
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} />,
    );
    expect(getByLabelText('Emergency SOS')).toBeTruthy();
  });

  it('renders French accessibility strings when given rider-app\'s fr-CA translator', () => {
    const t = (key: string) => translateKey('fr', key);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} t={t} />,
    );
    expect(getByLabelText('SOS d’urgence')).toBeTruthy();
  });

  it('renders English accessibility strings when given rider-app\'s en-CA translator', () => {
    const t = (key: string) => translateKey('en', key);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} t={t} />,
    );
    expect(getByLabelText('Emergency SOS')).toBeTruthy();
  });

  it('shows the French success alert after a held press triggers the backend call', async () => {
    const t = (key: string) => translateKey('fr', key);
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} t={t} />,
    );

    const button = getByLabelText('SOS d’urgence');
    fireEvent(button, 'pressIn');
    jest.advanceTimersByTime(1200); // SOS_HOLD_MS

    await waitFor(() => expect(onTrigger).toHaveBeenCalled());
    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith(
        '🚨 Alerte d’urgence envoyée',
        expect.stringContaining('Voulez-vous appeler le 911'),
        expect.any(Array),
      ),
    );
  });

  it('shows the French failure alert when every backend attempt rejects', async () => {
    const t = (key: string) => translateKey('fr', key);
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} t={t} />,
    );

    const button = getByLabelText('SOS d’urgence');
    fireEvent(button, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(1200); // SOS_HOLD_MS
    });
    // Two retries at 1s / 2s backoff (3 total attempts) before failure.
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(2000);
    });

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith(
        '⚠️ Alerte non envoyée',
        expect.stringContaining('Impossible de joindre Spinr'),
        expect.any(Array),
      ),
    );
  });

  it('has no hardcoded user-facing English strings outside the DEFAULT_STRINGS fallback map', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '../../shared/components/SOSButton.tsx'),
      'utf8',
    );
    // The fallback map is the ONLY place hardcoded English copy is allowed
    // to live (it's what un-migrated callers render). Strip it out, then
    // scan the rest of the component for strings that should have gone
    // through translate() instead.
    // Comments legitimately reference the English words in prose (e.g. "the
    // 'Dismiss' button intentionally does NOT reset..."); only *code* has to
    // be translate()-only, so strip line comments before scanning too.
    const withoutDefaults = source
      .replace(/const DEFAULT_STRINGS[\s\S]*?\n};\n/, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    // Sanity: the strip actually removed the map entries (defaultT() below
    // still legitimately references the DEFAULT_STRINGS *identifier*, so we
    // can't assert on that name — assert an actual entry is gone instead).
    expect(withoutDefaults).not.toContain("'sos.button_label': 'Emergency SOS'");

    const forbiddenPhrases = [
      'Emergency SOS',
      'Emergency Alert Sent',
      "Call 911",
      "I'm OK",
      'Alert Not Sent',
      'Could not reach Spinr',
      'Retry Now',
      'Dismiss',
      'No Active Ride',
      'Hold...',
      'Sending…',
      'Alert Sent',
      'FAILED',
      'Hold for 1.2 seconds',
      'Tap to retry',
      'Emergency alert sent',
      'Sending emergency alert',
      'Emergency alert failed',
      'double tap to retry',
      'Double tap to send SOS alert',
    ];
    for (const phrase of forbiddenPhrases) {
      expect(withoutDefaults).not.toContain(phrase);
    }
  });
});
