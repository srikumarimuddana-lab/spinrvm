/**
 * "No drivers available right now" sheet host.
 *
 * Pins:
 *   - hidden until a prompt is raised
 *   - Try again restores the trip, wipes the old quote and opens ride-options
 *     (which fetches a fresh one) — it never books
 *   - Schedule for later does the same and asks ride-options to open its picker
 *   - Not now just dismisses
 *
 * ConfirmSheet is stubbed (same stub as driverArrivingScreen.test.tsx), so the
 * bottom-sheet chrome, animation and layout are NOT covered here — only the
 * copy and the button wiring. rider-app has no visual-regression tooling.
 *
 * Code under test: rider-app/components/NoDriversSheetHost.tsx
 */
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { NoDriversSheetHost } from '../NoDriversSheetHost';
import { useNoDriversStore } from '../../store/noDriversStore';

jest.mock('../ConfirmSheet', () => (props: any) => {
  const { View, Text, TouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <Text>{props.title}</Text>
      <Text>{props.message}</Text>
      {(props.buttons || []).map((b: any, i: number) => (
        <TouchableOpacity
          key={i}
          onPress={async () => { await b.onPress?.(); props.onClose(); }}
        >
          <Text>{b.text}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
});

jest.mock('@shared/api/client', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ push: mockPush, replace: jest.fn() }) }));

const mockRideActions = {
  currentRide: null as any,
  pickup: null as any,
  dropoff: null as any,
  clearRide: jest.fn(),
  setPickup: jest.fn(),
  setDropoff: jest.fn(),
  clearStops: jest.fn(),
  setScheduledTime: jest.fn(),
  clearEstimates: jest.fn(),
  createRide: jest.fn(),
};
jest.mock('../../store/rideStore', () => ({
  useRideStore: { getState: () => mockRideActions },
}));

const PROMPT = {
  rideId: 'ride-1',
  pickup: { address: '100 Queen St', lat: 52.13, lng: -106.67 },
  dropoff: { address: '200 King St', lat: 52.12, lng: -106.65 },
};

beforeEach(() => {
  jest.clearAllMocks();
  useNoDriversStore.setState({ enabled: true, prompt: null, _shownRideId: null, _openScheduleOnArrival: false });
});

function raise() {
  act(() => { useNoDriversStore.getState().show(PROMPT); });
}

describe('NoDriversSheetHost', () => {
  it('renders nothing until a prompt is raised', () => {
    const { queryByText } = render(<NoDriversSheetHost />);
    expect(queryByText('No drivers available right now')).toBeNull();
  });

  it('shows the copy and three actions', () => {
    const { getByText } = render(<NoDriversSheetHost />);
    raise();
    getByText('No drivers available right now');
    getByText('Try again');
    getByText('Schedule for later');
    getByText('Not now');
  });

  it('Try again wipes the old quote and opens ride-options without booking', async () => {
    const { getByText } = render(<NoDriversSheetHost />);
    raise();
    await act(async () => { fireEvent.press(getByText('Try again')); });

    expect(mockRideActions.setPickup).toHaveBeenCalledWith(PROMPT.pickup);
    expect(mockRideActions.setDropoff).toHaveBeenCalledWith(PROMPT.dropoff);
    expect(mockRideActions.clearEstimates).toHaveBeenCalled();
    expect(mockRideActions.createRide).not.toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith('/ride-options');
    expect(useNoDriversStore.getState()._openScheduleOnArrival).toBe(false);
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });

  it('Schedule for later opens ride-options with the picker requested', async () => {
    const { getByText } = render(<NoDriversSheetHost />);
    raise();
    await act(async () => { fireEvent.press(getByText('Schedule for later')); });

    expect(mockRideActions.clearEstimates).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith('/ride-options');
    expect(useNoDriversStore.getState()._openScheduleOnArrival).toBe(true);
  });

  it('Not now only dismisses', async () => {
    const { getByText } = render(<NoDriversSheetHost />);
    raise();
    await act(async () => { fireEvent.press(getByText('Not now')); });

    expect(mockPush).not.toHaveBeenCalled();
    expect(mockRideActions.clearEstimates).not.toHaveBeenCalled();
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });
});
