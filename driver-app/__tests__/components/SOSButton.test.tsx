/**
 * shared/components/SOSButton.tsx localization coverage, driver-app side
 * (P1 tracker #14).
 *
 * SOSButton has no i18n instance of its own — driver-app injects
 * `useLanguageStore().t` (see driver-app/app/driver/(tabs)/index.tsx). This
 * exercises the real component against driver-app's own translate() /
 * en.json / fr.json (the `sos.*` keys added alongside this fix), which the
 * equivalent rider-app test can't cover since rider-app has its own,
 * separate locale files.
 *
 * Despite driver-app/jest.config.js's generic `^@shared/(.*)$`
 * moduleNameMapper pointing at driver-app/__mocks__/@shared/ (which has no
 * components/ stub), babel-plugin-module-resolver (driver-app/babel.config.js)
 * rewrites `@shared/...` import specifiers to a relative path at transform
 * time, before that mapper ever sees them — so `@shared/components/SOSButton`
 * resolves to the real shared/components/SOSButton.tsx under Jest, same as
 * it does at runtime. Verified empirically: `require.resolve('@shared/components/SafetyShield')`
 * returns the real shared/ path, not a __mocks__ one.
 */
import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { SOSButton } from '@shared/components/SOSButton';
import { translate } from '../../i18n';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: undefined, lng: undefined }),
}));

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Alert, 'alert');
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('SOSButton localization (driver-app locale data)', () => {
  it('renders French accessibility strings from driver-app/i18n/fr.json', () => {
    const t = (key: string) => translate('fr', key);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} t={t} />,
    );
    expect(getByLabelText("SOS d'urgence")).toBeTruthy();
  });

  it('renders English accessibility strings from driver-app/i18n/en.json', () => {
    const t = (key: string) => translate('en', key);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} t={t} />,
    );
    expect(getByLabelText('Emergency SOS')).toBeTruthy();
  });

  it('shows the French success alert using driver-app translations after a held press', async () => {
    const t = (key: string) => translate('fr', key);
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} t={t} />,
    );

    const button = getByLabelText("SOS d'urgence");
    fireEvent(button, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(1200); // SOS_HOLD_MS
    });

    await waitFor(() => expect(onTrigger).toHaveBeenCalled());
    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith(
        "🚨 Alerte d'urgence envoyée",
        expect.stringContaining('Voulez-vous appeler le 911'),
        expect.any(Array),
      ),
    );
    // driver-app's suite (many more transitive mocks via jest.setup.js —
    // firebase, secure-store, etc.) is measurably slower to transform/run
    // on a cold cache than rider-app's equivalent test; the assertions
    // above are the actual coverage, this just gives them room to finish.
  }, 15000);
});
