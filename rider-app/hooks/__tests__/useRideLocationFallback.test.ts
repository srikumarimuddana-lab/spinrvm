import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { useRideLocationFallback } from '../useRideLocationFallback';

const mockState = { wsConnected: true, _lastWsDriverPositionAt: 0, fetchRide: jest.fn().mockResolvedValue(undefined) };
jest.mock('../../store/rideStore', () => ({ useRideStore: Object.assign(
  (selector: (state: typeof mockState) => unknown) => selector(mockState), { getState: () => mockState }) }));
let mounted: TestRenderer.ReactTestRenderer;
function Screen() { useRideLocationFallback('ride-1'); return null; }
afterEach(async () => { await act(async () => mounted?.unmount()); jest.useRealTimers(); });

it('polls when the rider socket is connected but no fresh driver positions arrive', async () => {
  jest.useFakeTimers(); mockState.fetchRide.mockClear();
  mockState._lastWsDriverPositionAt = Date.now();
  await act(async () => { mounted = TestRenderer.create(React.createElement(Screen)); });
  expect(mockState.fetchRide).toHaveBeenCalledTimes(1);
  // The socket is healthy and GPS is arriving: no HTTP request at this tick.
  await act(async () => { jest.advanceTimersByTime(9000); mockState._lastWsDriverPositionAt = Date.now(); jest.advanceTimersByTime(6000); });
  expect(mockState.fetchRide).toHaveBeenCalledTimes(1);
  // Still connected, but GPS is now stale: recover from the database.
  await act(async () => jest.advanceTimersByTime(15000));
  expect(mockState.fetchRide).toHaveBeenCalledTimes(2);
});
