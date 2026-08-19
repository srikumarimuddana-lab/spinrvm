/**
 * Regression: the Safety panel must NOT render next to the SOS button.
 *
 * The button lives inside `styles.sosButton` on the home map —
 * position:'absolute', left:20, bottom:'47%', inside the map overlay tree.
 * Rendering the sheet's Modal there made it inherit that container's geometry
 * instead of escaping it: the sheet started 20pt from the left and ran off the
 * right edge by the same 20pt, and the container growing to fit the sheet
 * pushed the SOS button up into the status bar.
 *
 * The fix is structural — the sheet is root-mounted by SafetySheetHost and the
 * button only flips store state. These tests pin that split so nobody
 * "simplifies" the sheet back into RiderSOS and silently reintroduces it.
 */
import React from 'react';
import { Modal } from 'react-native';
import { fireEvent, render, act } from '@testing-library/react-native';
import { RiderSOS } from '../components/RiderSOS';
import { useSafetySheetStore } from '../store/safetySheetStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('@shared/utils/sosLocation', () => ({
  getSOSLocation: jest.fn().mockResolvedValue({ lat: 50.4, lng: -104.6 }),
}));

beforeEach(() => {
  jest.useFakeTimers();
  useSafetySheetStore.setState({ visible: false, rideId: undefined });
});
afterEach(() => jest.useRealTimers());

describe('RiderSOS', () => {
  it('renders no Modal of its own — the sheet is root-mounted', () => {
    const { UNSAFE_queryAllByType } = render(
      <RiderSOS rideId="ride-1" onTrigger={jest.fn().mockResolvedValue(undefined)} />,
    );

    // A Modal here means the sheet is back inside the map's positioned
    // overlay — the exact shape of the original layout bug.
    expect(UNSAFE_queryAllByType(Modal)).toHaveLength(0);
  });

  it('opens the shared store on a tap instead of mounting the sheet locally', async () => {
    const { getByLabelText } = render(
      <RiderSOS rideId="ride-42" onTrigger={jest.fn().mockResolvedValue(undefined)} />,
    );

    // Grab the node once: the accessibility label changes as the button
    // transitions state, so re-querying by label mid-gesture is unreliable.
    const btn = getByLabelText('Emergency SOS');
    fireEvent(btn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(200); // released before the 1.2s hold
    });
    fireEvent(btn, 'pressOut');

    const state = useSafetySheetStore.getState();
    expect(state.visible).toBe(true);
    // The ride id has to travel with it — the host has no other way to know.
    expect(state.rideId).toBe('ride-42');
  });

  it('still sends the alert on a full hold, without opening the panel', async () => {
    const onTrigger = jest.fn().mockResolvedValue(undefined);
    const { getByLabelText } = render(<RiderSOS rideId="ride-1" onTrigger={onTrigger} />);

    const btn = getByLabelText('Emergency SOS');
    fireEvent(btn, 'pressIn');
    await act(async () => {
      jest.advanceTimersByTime(1200);
    });
    fireEvent(btn, 'pressOut');

    expect(onTrigger).toHaveBeenCalledTimes(1);
    expect(useSafetySheetStore.getState().visible).toBe(false);
  });
});
