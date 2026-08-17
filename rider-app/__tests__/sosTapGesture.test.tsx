/**
 * SOS tap-vs-hold.
 *
 * The panel is additive: adding tap-to-open must not weaken the panic button.
 * A rider who already knows "hold SOS" must keep getting an immediate alert,
 * and a caller that passes no onTap must behave exactly as before.
 */
import React from 'react';
import { fireEvent, render, act } from '@testing-library/react-native';
import { SOSButton } from '@shared/components/SOSButton';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: 50.4, lng: -104.6 }),
}));

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
});
afterEach(() => jest.useRealTimers());

const HOLD_MS = 1200;

describe('SOSButton tap vs hold', () => {
  it('still fires the alert on a full hold — the panel must not weaken this', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const onTap = jest.fn();
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} onTap={onTap} />,
    );

    const btn = getByLabelText('Emergency SOS');
    fireEvent(btn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(HOLD_MS);
    });
    fireEvent(btn, 'pressOut');

    expect(onTrigger).toHaveBeenCalledTimes(1);
    // A completed hold is NOT a tap — the panel must not open over an alert.
    expect(onTap).not.toHaveBeenCalled();
  });

  it('opens the panel on a short tap without sending an alert', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const onTap = jest.fn();
    const { getByLabelText } = render(
      <SOSButton rideId="ride-1" onTrigger={onTrigger} onTap={onTap} />,
    );

    const btn = getByLabelText('Emergency SOS');
    fireEvent(btn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(200); // released well before the hold elapses
    });
    fireEvent(btn, 'pressOut');

    expect(onTap).toHaveBeenCalledTimes(1);
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it('is byte-identical to the old behaviour when no onTap is supplied', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(<SOSButton rideId="ride-1" onTrigger={onTrigger} />);

    const btn = getByLabelText('Emergency SOS');
    fireEvent(btn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(200);
    });
    fireEvent(btn, 'pressOut');

    // A short tap with no onTap does nothing at all — no alert, no crash.
    expect(onTrigger).not.toHaveBeenCalled();
  });
});
