/**
 * Regression: the Safety panel must actually close.
 *
 * Reported from a device — the panel stayed on screen no matter what was
 * tapped, and survived navigation to the next screen. Cause: it used
 * react-native <Modal>, which in this app lays out as a flex sibling of the
 * navigator rather than overlaying. Neither of the app's other root overlays
 * uses Modal (ConfirmSheet uses a gorhom BottomSheet, Toast uses
 * position:'absolute'), and this one now uses an absolute-fill overlay too.
 *
 * These pin the three ways out: the X, "I'm safe — close", and the dimmed
 * backdrop. Plus the invariant that made it un-dismissable — nothing renders
 * at all when visible is false.
 */
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { SafetySheet } from '@shared/components/SafetySheet';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 34, left: 0, right: 0 }),
}));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn().mockResolvedValue({ data: {} }) },
}));
jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: 50.4, lng: -104.6 }),
}));

const props = {
  rideId: 'ride-1',
  onTrigger: jest.fn().mockResolvedValue(undefined),
};

beforeEach(() => jest.clearAllMocks());

// SafetySheet's useSafetyPanelConfig hook kicks off an async
// Promise.allSettled fetch on mount. Without flushing it here, its `.then`
// continuation can resolve after RTL's implicit afterEach unmount, calling
// setState on an already-unmounted tree outside act() -- a leaked update
// that intermittently corrupted a later test file in the same Jest worker
// (observed: rideOptionsScreen.test.tsx timing out under CI's leaner
// scheduling). Flushing the pending microtask queue inside act() while the
// component is still mounted discharges it here instead.
async function flushPendingEffects() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('SafetySheet dismissal', () => {
  it('renders nothing at all when not visible', async () => {
    const { toJSON } = render(
      <SafetySheet visible={false} onClose={jest.fn()} {...props} />,
    );
    await flushPendingEffects();
    // A Modal left a mounted-but-hidden tree behind; this must be truly gone,
    // otherwise it keeps occupying layout on the next screen.
    expect(toJSON()).toBeNull();
  });

  it('closes from the X', async () => {
    const onClose = jest.fn();
    const { getAllByLabelText } = render(
      <SafetySheet visible onClose={onClose} {...props} />,
    );
    await flushPendingEffects();
    // Backdrop and X share the label; the X is the last one rendered.
    const targets = getAllByLabelText('Close safety options');
    fireEvent.press(targets[targets.length - 1]);
    expect(onClose).toHaveBeenCalled();
  });

  it("closes from \"I'm safe — close\"", async () => {
    const onClose = jest.fn();
    const { getByLabelText } = render(
      <SafetySheet visible onClose={onClose} {...props} />,
    );
    await flushPendingEffects();
    fireEvent.press(getByLabelText("I'm safe, close"));
    expect(onClose).toHaveBeenCalled();
  });

  it('closes when the dimmed backdrop is tapped', async () => {
    const onClose = jest.fn();
    const { getAllByLabelText } = render(
      <SafetySheet visible onClose={onClose} {...props} />,
    );
    await flushPendingEffects();
    // First match is the full-screen backdrop pressable behind the sheet.
    fireEvent.press(getAllByLabelText('Close safety options')[0]);
    expect(onClose).toHaveBeenCalled();
  });
});
