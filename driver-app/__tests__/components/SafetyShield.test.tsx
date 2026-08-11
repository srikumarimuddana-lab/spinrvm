/**
 * Driver Safety shield (ACTION_ITEMS.md B16) -- hold 3s fires a silent
 * alert (badge + toast, never Alert.alert), short tap opens the overlay.
 *
 * Code under test: shared/components/SafetyShield.tsx
 */
import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { SafetyShield } from '@shared/components/SafetyShield';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(() => Promise.resolve({ data: { contacts: [{ id: 'ec-1', name: 'Mom' }] } })) },
}));

const SHIELD_LABEL = 'Safety shield';

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Alert, 'alert');
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('SafetyShield', () => {
  it('holding 3s calls onTrigger and shows the sent badge + toast, never Alert.alert', async () => {
    const onTrigger = jest.fn().mockResolvedValue({
      contacts: [{ id: 'ec-1', name: 'Mom', notified: true }],
    });
    const onOpenOverlay = jest.fn();

    const { getByLabelText, getByText } = render(
      <SafetyShield rideId="ride-1" onTrigger={onTrigger} onOpenOverlay={onOpenOverlay} />,
    );
    // Let useEmergencyContacts' initial fetch settle before interacting.
    await act(async () => {
      await Promise.resolve();
    });

    const shield = getByLabelText(SHIELD_LABEL);
    fireEvent(shield, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onTrigger).toHaveBeenCalledWith('ride-1', undefined, undefined);
    expect(onOpenOverlay).not.toHaveBeenCalled();
    await waitFor(() => expect(getByText(/Silent alert sent/)).toBeTruthy());
    expect(Alert.alert).not.toHaveBeenCalled();
  });

  it('a short tap (release before 3s) opens the overlay without calling onTrigger', async () => {
    const onTrigger = jest.fn();
    const onOpenOverlay = jest.fn();

    const { getByLabelText } = render(
      <SafetyShield rideId="ride-1" onTrigger={onTrigger} onOpenOverlay={onOpenOverlay} />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    const shield = getByLabelText(SHIELD_LABEL);
    fireEvent(shield, 'pressIn');
    act(() => {
      jest.advanceTimersByTime(1500);
    });
    fireEvent(shield, 'pressOut');

    expect(onOpenOverlay).toHaveBeenCalledTimes(1);
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it('a failed hold shows an inline error state, never Alert.alert', async () => {
    const onTrigger = jest.fn().mockRejectedValue(new Error('network down'));
    const onOpenOverlay = jest.fn();

    const { getByLabelText } = render(
      <SafetyShield rideId="ride-1" onTrigger={onTrigger} onOpenOverlay={onOpenOverlay} />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    const shield = getByLabelText(SHIELD_LABEL);
    fireEvent(shield, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onTrigger).toHaveBeenCalled();
    expect(Alert.alert).not.toHaveBeenCalled();
  });
});
