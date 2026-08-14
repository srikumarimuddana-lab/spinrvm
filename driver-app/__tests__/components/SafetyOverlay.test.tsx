/**
 * Driver Safety overlay (ACTION_ITEMS.md B16) -- 911 / alert-contacts /
 * share-trip-link / close, with a hold-to-confirm silent alert and a
 * sent-confirmation view listing per-contact status.
 *
 * Code under test: shared/components/SafetyOverlay.tsx
 */
import React from 'react';
import { Alert, Linking } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { SafetyOverlay } from '@shared/components/SafetyOverlay';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

// react-native's real <Modal> pulls RCTModalHostViewNativeComponent's
// flow-typed native spec through babel-plugin-codegen, which this repo's
// jest transform can't parse (same class of issue ActiveRidePanel.test.tsx
// documents for Modal-based children -- it sidesteps by mocking the child
// components instead). SafetyOverlay's own layout is what's under test, so
// stub Modal to a simple conditional passthrough of its children.
jest.mock('react-native/Libraries/Modal/Modal', () => {
  const ReactActual = jest.requireActual('react');
  const RN = jest.requireActual('react-native');
  return {
    __esModule: true,
    default: ({ visible, children }: { visible: boolean; children: React.ReactNode }) =>
      visible ? ReactActual.createElement(RN.View, null, children) : null,
  };
});

jest.mock('expo-location', () => ({
  Accuracy: { High: 6 },
  getCurrentPositionAsync: jest.fn().mockResolvedValue({ coords: { latitude: 52.1, longitude: -106.6 } }),
  getLastKnownPositionAsync: jest.fn().mockResolvedValue(null),
  reverseGeocodeAsync: jest.fn().mockResolvedValue([{ street: 'Victoria Ave', city: 'Regina' }]),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn((url: string) =>
      url.includes('/emergency-contacts')
        ? Promise.resolve({ data: { contacts: [{ id: 'ec-1', name: 'Mom' }] } })
        : Promise.resolve({ data: { success: true, share_url: 'https://track.spinr.ca/tok' } }),
    ),
  },
}));

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Alert, 'alert');
  jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('SafetyOverlay', () => {
  it('renders the discreet-mode banner, location, and all four action rows', async () => {
    const { getByText, getByLabelText } = render(
      <SafetyOverlay visible rideId="ride-1" onClose={jest.fn()} onTrigger={jest.fn()} />,
    );
    await flush();

    expect(getByText(/Discreet mode on/)).toBeTruthy();
    expect(getByText('🛡 Safety')).toBeTruthy();
    await waitFor(() => expect(getByText('Victoria Ave, Regina')).toBeTruthy());
    expect(getByLabelText('Alert emergency contacts')).toBeTruthy();
    expect(getByLabelText('Call 911')).toBeTruthy();
    expect(getByLabelText('Share live trip link')).toBeTruthy();
    expect(getByLabelText("I'm safe, close")).toBeTruthy();
  });

  it('tapping Call 911 opens the dialer and never shows Alert.alert', async () => {
    const { getByLabelText } = render(
      <SafetyOverlay visible rideId="ride-1" onClose={jest.fn()} onTrigger={jest.fn()} />,
    );
    await flush();

    fireEvent.press(getByLabelText('Call 911'));

    expect(Linking.openURL).toHaveBeenCalledWith('tel:911');
    expect(Alert.alert).not.toHaveBeenCalled();
  });

  it('holding Alert Emergency Contacts for 3s calls onTrigger and shows the sent-confirmation view', async () => {
    const onTrigger = jest.fn().mockResolvedValue({
      contacts: [{ id: 'ec-1', name: 'Mom', notified: true }],
    });
    const { getByLabelText, getByText } = render(
      <SafetyOverlay visible rideId="ride-1" onClose={jest.fn()} onTrigger={onTrigger} />,
    );
    await flush();

    const holdBtn = getByLabelText('Alert emergency contacts');
    fireEvent(holdBtn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onTrigger).toHaveBeenCalledWith('ride-1', 52.1, -106.6);
    await waitFor(() => expect(getByText('Alert Sent Silently')).toBeTruthy());
    expect(getByText('Mom')).toBeTruthy();
    expect(getByText('✓ Notified')).toBeTruthy();
    expect(getByText('Spinr Safety Team')).toBeTruthy();
    expect(Alert.alert).not.toHaveBeenCalled();
  });

  it("tapping I'm Safe — Close calls onClose", async () => {
    const onClose = jest.fn();
    const { getByLabelText } = render(
      <SafetyOverlay visible rideId="ride-1" onClose={onClose} onTrigger={jest.fn()} />,
    );
    await flush();

    fireEvent.press(getByLabelText("I'm safe, close"));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('a failed hold does not show Alert.alert', async () => {
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const { getByLabelText } = render(
      <SafetyOverlay visible rideId="ride-1" onClose={jest.fn()} onTrigger={onTrigger} />,
    );
    await flush();

    const holdBtn = getByLabelText('Alert emergency contacts');
    fireEvent(holdBtn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onTrigger).toHaveBeenCalled();
    expect(Alert.alert).not.toHaveBeenCalled();
  });
});
