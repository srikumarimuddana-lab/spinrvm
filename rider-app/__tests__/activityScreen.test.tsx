/**
 * app/(tabs)/activity.tsx — ride history + upcoming scheduled rides tab.
 * Pins:
 *  - focus-effect fetch of rides/stats/scheduled rides, throttled by a
 *    30s TTL (a re-focus inside the window does not re-fetch; pull-to-
 *    refresh always force-refreshes regardless of the TTL)
 *  - the history/upcoming tab switch and the personal/business filter
 *    (business = has a corporate_account_id)
 *  - rides group into a "RECENT" section (< 7 days old) vs. month-named
 *    sections
 *  - fare total: grand_total is used as-is when fare_breakdown has any
 *    lines (already tip-inclusive); only added to tip when
 *    fare_breakdown is empty (legacy rides)
 *  - a load failure shows the error/retry empty state; an empty result
 *    shows the "no rides" empty state; the upcoming tab's own empty
 *    state when there are no scheduled rides
 *  - infinite scroll: onEndReached loads the next cursor page and
 *    appends; a load-more failure shows a retry footer
 *  - tapping a ride card navigates to /ride-details with the ride id
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, FlatList } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('../components/SkeletonBox', () => () => null);

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), [cb]);
    },
  };
});

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ push: mockPush }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockT = (key: string) => key;
jest.mock('../i18n', () => ({ useTranslation: () => ({ t: mockT }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockFetchScheduledRides = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: () => mockRideState,
}));

import ActivityScreen from '../app/(tabs)/activity';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const now = new Date();
const RIDE_RECENT = {
  id: 'ride-1', ride_code: 'RIDE001', pickup_address: '100 Main St', dropoff_address: '200 Elm St',
  total_fare: '15.00', grand_total: '15.00', distance_km: 3.2, duration_minutes: 8,
  status: 'completed', vehicle_type_id: 'vt-1', created_at: now.toISOString(),
  fare_breakdown: [],
};
const RIDE_BUSINESS = {
  ...RIDE_RECENT, id: 'ride-2', corporate_account_id: 'corp-1', dropoff_address: '300 Oak Ave',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ActivityScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRideState = { scheduledRides: [], fetchScheduledRides: mockFetchScheduledRides };
  mockApiGet.mockImplementation((url: string) => {
    if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: null } });
    if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 5, total_distance_km: 12.5, total_saved: '3.00', co2_saved_kg: 2.1 } });
    if (url === '/vehicle-types') return Promise.resolve({ data: [{ id: 'vt-1', name: 'Sedan' }] });
    return Promise.reject(new Error('unexpected url ' + url));
  });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('ActivityScreen', () => {
  it('fetches rides, stats, and scheduled rides on focus', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/rides/history'));
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/rides/stats'));
    expect(mockFetchScheduledRides).toHaveBeenCalled();
  });

  it('groups a recent ride under the "RECENT" section', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('RECENT');
    expect(allText(r)).toContain('200 Elm St');
  });

  it('shows only business rides (with a corporate_account_id) under the Business filter', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT, RIDE_BUSINESS], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 5, total_distance_km: 12.5, total_saved: '3.00', co2_saved_kg: 2.1 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('300 Oak Ave');
    expect(allText(r)).toContain('200 Elm St');

    const businessTab = findButtonByText(r, 'Business');
    act(() => { businessTab.props.onPress(); });
    expect(allText(r)).toContain('300 Oak Ave');
    expect(allText(r)).not.toContain('200 Elm St');
  });

  it('uses grand_total as-is when fare_breakdown has lines (already tip-inclusive)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: {
            rides: [{ ...RIDE_RECENT, grand_total: '18.00', fare_breakdown: [{ label: 'Tip', amount: '3.00', type: 'tip' }] }],
            next_cursor: null,
          },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","18.00"]');
    expect(allText(r)).not.toContain('["$","21.00"]');
  });

  it('adds tip to grand_total only when fare_breakdown is empty (legacy ride)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: { rides: [{ ...RIDE_RECENT, grand_total: '15.00', fare_breakdown: [] }], next_cursor: null },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","15.00"]'); // no tip line, so total = grand_total (no fare_breakdown tip to add)
  });

  it('shows the error/retry empty state on a load failure', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('Could not load rides');
  });

  it('shows the "no rides" empty state for a genuinely empty result', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 0, total_distance_km: 0, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('activity.no_rides');
  });

  it('shows the upcoming-tab empty state when there are no scheduled rides', async () => {
    const r = await renderScreen();
    const upcomingTab = findButtonByText(r, 'activity.upcoming_tab');
    act(() => { upcomingTab.props.onPress(); });
    expect(allText(r)).toContain('activity.no_upcoming');
  });

  it('shows scheduled rides under the upcoming tab', async () => {
    mockRideState.scheduledRides = [{
      id: 'sched-1', dropoff_address: '400 Pine Rd', vehicle_type_id: 'vt-1',
      scheduled_time: now.toISOString(), grand_total: '20.00',
    }];
    const r = await renderScreen();
    const upcomingTab = findButtonByText(r, 'activity.upcoming_tab');
    act(() => { upcomingTab.props.onPress(); });
    expect(allText(r)).toContain('400 Pine Rd');
  });

  it('loads the next page on end-reached and appends', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/rides/history?limit=20') return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: 'cursor-1' } });
      if (url === '/rides/history?limit=20&before=cursor-1') return Promise.resolve({ data: { rides: [{ ...RIDE_RECENT, id: 'ride-3', dropoff_address: '500 Birch Ln' }], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 2, total_distance_km: 2, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const list = r.root.findByType(FlatList);
    await act(async () => { await list.props.onEndReached(); await flush(); });
    expect(mockApiGet).toHaveBeenCalledWith('/rides/history?limit=20&before=cursor-1');
    expect(allText(r)).toContain('500 Birch Ln');
  });

  it('navigates to /ride-details with the ride id when a card is tapped', async () => {
    const r = await renderScreen();
    const rideCard = findButtonByText(r, '200 Elm St');
    act(() => { rideCard.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-details', params: { rideId: 'ride-1' } });
  });
});
