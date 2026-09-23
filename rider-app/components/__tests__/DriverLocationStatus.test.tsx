import React from 'react';
import { act, render } from '@testing-library/react-native';
import { DriverLocationStatus } from '../DriverLocationStatus';

jest.mock('@shared/components/Text', () => ({ Text: require('react-native').Text }));

afterEach(() => jest.useRealTimers());
it('shows stale sensor age even when no new position arrives and clears on a fresh fix', () => {
  jest.useFakeTimers();
  const view = render(<DriverLocationStatus capturedAt={new Date().toISOString()} color="black" />);
  expect(view.queryByText(/Location updates delayed/)).toBeNull();
  act(() => jest.advanceTimersByTime(25_000));
  expect(view.getByText(/Location updates delayed/)).toBeTruthy();
  view.rerender(<DriverLocationStatus capturedAt={new Date().toISOString()} color="black" />);
  expect(view.queryByText(/Location updates delayed/)).toBeNull();
});
it('does not label a missing timestamp as a live position', () => {
  expect(render(<DriverLocationStatus color="black" />).getByText('Waiting for driver location')).toBeTruthy();
});
