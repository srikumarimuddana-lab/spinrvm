/**
 * app/driver/upcoming.tsx — driver "Upcoming trips" list. Pins:
 *  - renders inside the shared ScreenHeader (owns the top safe-area inset and
 *    the back button — the driver stack sets headerShown: false, so without
 *    it the title sat under the status bar with no way back but a swipe)
 *  - GET /drivers/rides/upcoming on focus; each ride shows date, time
 *    (no seconds) and pickup/drop-off
 *  - empty list shows only the empty message
 *  - a failed load shows the error + retry, NOT the empty message too
 *  - retry re-fetches
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Text, TouchableOpacity } from 'react-native';

import UpcomingRidesScreen from '../../app/driver/upcoming';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('expo-router', () => {
  const React = require('react');
  return { useFocusEffect: (cb: () => void) => React.useEffect(cb, [cb]) };
});

const mockHeaderTitles: string[] = [];
jest.mock('../../components/ScreenHeader', () => ({
  ScreenHeader: ({ title }: { title: string }) => {
    mockHeaderTitles.push(title);
    return null;
  },
}));

jest.mock('../../components/SafeRefreshControl', () => ({ __esModule: true, default: () => null }));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', text: '#111', textSecondary: '#666',
  border: '#E5E7EB', error: '#EF4444', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

async function renderScreen() {
  let r: TestRenderer.ReactTestRenderer | null = null;
  await act(async () => {
    r = TestRenderer.create(<UpcomingRidesScreen />);
    await flush();
  });
  return r!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => ([] as unknown[]).concat(t.props.children).join('')).join(' | ');
}

beforeEach(() => {
  mockApiGet.mockReset();
  mockHeaderTitles.length = 0;
});

it('renders the shared header with the screen title', async () => {
  mockApiGet.mockResolvedValue({ data: { rides: [] } });
  await renderScreen();
  expect(mockHeaderTitles).toContain('Upcoming trips');
});

it('lists rides with date, time without seconds, and both addresses', async () => {
  mockApiGet.mockResolvedValue({
    data: {
      rides: [{
        id: 'r1', status: 'driver_accepted', scheduled_time: '2030-01-15T21:30:00Z',
        pickup_address: '123 Main St', dropoff_address: 'Airport',
      }],
    },
  });
  const r = await renderScreen();
  expect(mockApiGet).toHaveBeenCalledWith('/drivers/rides/upcoming');
  const text = allText(r);
  expect(text).toContain('123 Main St');
  expect(text).toContain('Airport');
  expect(text).not.toContain('No upcoming scheduled trips.');
  const d = new Date('2030-01-15T21:30:00Z');
  expect(text).toContain(d.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' }));
  expect(text).not.toMatch(/:\d\d:\d\d/);
});

it('shows only the empty message when there are no rides', async () => {
  mockApiGet.mockResolvedValue({ data: { rides: [] } });
  const r = await renderScreen();
  const text = allText(r);
  expect(text).toContain('No upcoming scheduled trips.');
  expect(text).not.toContain('Could not load upcoming trips.');
});

it('shows the error with a retry, not the empty message, when loading fails', async () => {
  mockApiGet.mockRejectedValueOnce(new Error('boom'));
  const r = await renderScreen();
  const text = allText(r);
  expect(text).toContain('Could not load upcoming trips.');
  expect(text).not.toContain('No upcoming scheduled trips.');

  mockApiGet.mockResolvedValueOnce({
    data: { rides: [{ id: 'r2', scheduled_time: '2030-01-15T21:30:00Z', pickup_address: 'Home', dropoff_address: 'Work' }] },
  });
  const retry = r.root.findAllByType(TouchableOpacity).find((b) => b.props.accessibilityRole === 'button');
  await act(async () => {
    retry!.props.onPress();
    await flush();
  });
  expect(mockApiGet).toHaveBeenCalledTimes(2);
  expect(allText(r)).toContain('Home');
  expect(allText(r)).not.toContain('Could not load upcoming trips.');
});
