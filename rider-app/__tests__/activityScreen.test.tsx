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
import { TouchableOpacity, Text, FlatList, View } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('../components/SkeletonBox', () => () => null);

const mockUseWindowDimensions = jest.fn(() => ({ width: 800, height: 800, scale: 1, fontScale: 1 }));
jest.mock('react-native/Libraries/Utilities/useWindowDimensions', () => ({
  __esModule: true,
  default: () => mockUseWindowDimensions(),
}));

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
const mockUseTheme = jest.fn(() => ({ colors: COLORS, isDark: false }));
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => mockUseTheme() }));

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
  // mockReturnValue (not mockReturnValueOnce) is needed for these two so the
  // override survives the multiple re-renders a fetch-driven state update
  // triggers — restore the defaults here since restoreAllMocks() doesn't
  // touch a jest.fn()'s configured return value (only jest.spyOn mocks).
  mockUseWindowDimensions.mockReturnValue({ width: 800, height: 800, scale: 1, fontScale: 1 });
  mockUseTheme.mockReturnValue({ colors: COLORS, isDark: false });
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

  it('navigates to /ride-details when a scheduled (upcoming) ride card is tapped', async () => {
    mockRideState.scheduledRides = [{
      id: 'sched-1', dropoff_address: '400 Pine Rd', vehicle_type_id: 'vt-1',
      scheduled_time: now.toISOString(), grand_total: '20.00',
    }];
    const r = await renderScreen();
    const upcomingTab = findButtonByText(r, 'activity.upcoming_tab');
    act(() => { upcomingTab.props.onPress(); });
    const card = findButtonByText(r, '400 Pine Rd');
    act(() => { card.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-details', params: { rideId: 'sched-1' } });
  });

  it('switches back to the history tab from upcoming', async () => {
    const r = await renderScreen();
    const upcomingTab = findButtonByText(r, 'activity.upcoming_tab');
    act(() => { upcomingTab.props.onPress(); });
    expect(allText(r)).toContain('activity.no_upcoming');
    const historyTab = findButtonByText(r, 'activity.history_tab');
    act(() => { historyTab.props.onPress(); });
    expect(allText(r)).toContain('RECENT');
  });

  it('filters to personal rides (no corporate_account_id) under the Personal filter', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT, RIDE_BUSINESS], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 2, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const personalTab = findButtonByText(r, 'Personal');
    act(() => { personalTab.props.onPress(); });
    expect(allText(r)).toContain('200 Elm St');
    expect(allText(r)).not.toContain('300 Oak Ave');
  });

  it('switches the period pill (re-fetches stats, forced) without re-fetching ride history', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    const weekPill = findButtonByText(r, 'This Week');
    act(() => { weekPill.props.onPress(); });
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/rides/stats'));
    expect(mockApiGet).not.toHaveBeenCalledWith(expect.stringContaining('/rides/history'));
  });

  it('pull-to-refresh force-refetches rides, stats, and scheduled rides', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    mockFetchScheduledRides.mockClear();
    const list = r.root.findByType(FlatList);
    await act(async () => { list.props.refreshControl.props.onRefresh(); await flush(); });
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/rides/history'));
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/rides/stats'));
    expect(mockFetchScheduledRides).toHaveBeenCalled();
  });

  it('shows a retry footer and retries when loading the next page fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/rides/history?limit=20') return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: 'cursor-1' } });
      if (url === '/rides/history?limit=20&before=cursor-1') return Promise.reject(new Error('down'));
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const list = r.root.findByType(FlatList);
    await act(async () => { await list.props.onEndReached(); await flush(); });
    expect(allText(r)).toContain('Could not load more rides.');
    const retryBtn = findButtonByText(r, 'Tap to retry');
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/rides/history?limit=20&before=cursor-1') return Promise.resolve({ data: { rides: [{ ...RIDE_RECENT, id: 'ride-3', dropoff_address: '500 Birch Ln' }], next_cursor: null } });
      return Promise.resolve({ data: {} });
    });
    await act(async () => { retryBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('500 Birch Ln');
  });

  it('shows "Yesterday" for a ride created yesterday, and a month/day date for anything older', async () => {
    const exactlyYesterday = new Date(now);
    exactlyYesterday.setDate(exactlyYesterday.getDate() - 1);
    const older = new Date(now.getTime() - 10 * 24 * 60 * 60 * 1000);
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: {
            rides: [
              { ...RIDE_RECENT, id: 'ride-y', created_at: exactlyYesterday.toISOString(), dropoff_address: 'Yesterday Ave' },
              { ...RIDE_RECENT, id: 'ride-o', created_at: older.toISOString(), dropoff_address: 'Old Town Rd' },
            ],
            next_cursor: null,
          },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 2, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Yesterday,');
    // An older ride's section header is its own month name, and its row
    // shows a "Mon D, h:mm" date rather than "Today"/"Yesterday".
    expect(allText(r)).not.toContain('["Today, ');
  });

  it('shows the cancelled status text/color and icon for a cancelled ride', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({ data: { rides: [{ ...RIDE_RECENT, status: 'cancelled' }], next_cursor: null } });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('activity.cancelled');
  });

  it('falls back to the "completed" status text for an unrecognised status', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({ data: { rides: [{ ...RIDE_RECENT, status: 'in_progress' }], next_cursor: null } });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    // getStatusText has no 'in_progress' case, so it falls through to the
    // same "completed" copy as a genuinely completed ride.
    expect(allText(r)).toContain('activity.completed');
  });

  it('falls back to the default status color for an unrecognised status', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({ data: { rides: [{ ...RIDE_RECENT, status: 'refunded' }], next_cursor: null } });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    await expect(renderScreen()).resolves.toBeDefined();
  });

  it('shows the loading skeleton while stats are still loading', async () => {
    let resolveStats: (v: any) => void;
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return new Promise((resolve) => { resolveStats = resolve; });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Trips');
    await act(async () => {
      resolveStats!({ data: { period: 'all', total_rides: 5, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      await flush();
    });
    expect(allText(r)).toContain('Trips');
  });

  it('ignores a stale stats response that resolves after a newer stats fetch already superseded it', async () => {
    let resolveAll: (v: any) => void;
    const allPromise = new Promise((resolve) => { resolveAll = resolve; });
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: null } });
      if (url === '/rides/stats?period=all') return allPromise;
      if (url === '/rides/stats?period=week') {
        return Promise.resolve({ data: { period: 'week', total_rides: 2, total_distance_km: 4, total_saved: '1.00', co2_saved_kg: 0.5 } });
      }
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    // The initial (all-time) stats fetch is still pending when the pill
    // press fires a newer ('week') fetch that supersedes it.
    const weekPill = findButtonByText(r, 'This Week');
    await act(async () => {
      weekPill.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('["$","1.00"]');
    // The stale 'all' response resolving late must not clobber the newer
    // stats, nor spuriously re-toggle the loading state.
    await act(async () => {
      resolveAll!({ data: { period: 'all', total_rides: 99, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      await flush();
    });
    expect(allText(r)).not.toContain('99');
    expect(allText(r)).toContain('["$","1.00"]');
  });

  it('falls back to an empty rides list and null cursor when the history response omits both fields', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: {} });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 0, total_distance_km: 0, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('activity.no_rides');
  });

  it('falls back vehicle-types to an empty map when the response data is not an array', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: null } });
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: null });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    // getVehicleType falls back to 'Standard' since the vehicleTypes map stayed empty.
    expect(allText(r)).toContain('Standard');
  });

  it('does not load more when there is no next cursor', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    const list = r.root.findByType(FlatList);
    await act(async () => { await list.props.onEndReached(); await flush(); });
    expect(mockApiGet).not.toHaveBeenCalledWith(expect.stringContaining('/rides/history'));
  });

  it('does not retry loading more while a prior load-more error is still showing (without an explicit retry)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/rides/history?limit=20') return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: 'cursor-1' } });
      if (url === '/rides/history?limit=20&before=cursor-1') return Promise.reject(new Error('down'));
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const list = r.root.findByType(FlatList);
    await act(async () => { await list.props.onEndReached(); await flush(); });
    expect(allText(r)).toContain('Could not load more rides.');
    mockApiGet.mockClear();
    // A second end-reached (e.g. more scrolling) without tapping retry is a no-op.
    await act(async () => { await list.props.onEndReached(); await flush(); });
    expect(mockApiGet).not.toHaveBeenCalledWith(expect.stringContaining('before=cursor-1'));
  });

  it('shows the loading-more spinner while the next page is in flight', async () => {
    let resolveNext: (v: any) => void;
    const nextPromise = new Promise((resolve) => { resolveNext = resolve; });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/rides/history?limit=20') return Promise.resolve({ data: { rides: [RIDE_RECENT], next_cursor: 'cursor-1' } });
      if (url === '/rides/history?limit=20&before=cursor-1') return nextPromise;
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const list = r.root.findByType(FlatList);
    act(() => { list.props.onEndReached(); });
    await flush();
    expect(allText(r)).toContain('Loading more rides...');
    await act(async () => {
      resolveNext!({ data: { rides: [], next_cursor: null } });
      await flush();
    });
    expect(allText(r)).not.toContain('Loading more rides...');
  });

  it('falls back fare_breakdown-less rides to a $0.00 total when grand_total and total_fare are both missing', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: { rides: [{ ...RIDE_RECENT, fare_breakdown: undefined, grand_total: undefined, total_fare: undefined }], next_cursor: null },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","0.00"]');
  });

  it('falls back to total_fare when grand_total is missing', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: { rides: [{ ...RIDE_RECENT, fare_breakdown: [], grand_total: undefined, total_fare: '9.50' }], next_cursor: null },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","9.50"]');
  });

  it('treats a tip/discount line with a missing amount field as zero', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: {
            rides: [{
              ...RIDE_RECENT,
              fare_breakdown: [{ label: 'Tip', type: 'tip' }, { label: 'Promo', type: 'discount' }],
            }],
            next_cursor: null,
          },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    // Neither the tip nor the discount sub-line renders since both amounts fall back to 0.
    expect(allText(r)).not.toContain('Tip $');
    expect(allText(r)).not.toContain('Saved $');
  });

  it('shows only the "Saved" breakdown line when a ride has a discount but no tip', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: {
            rides: [{ ...RIDE_RECENT, fare_breakdown: [{ label: 'Promo', amount: '-2.00', type: 'discount' }] }],
            next_cursor: null,
          },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Saved $2.00');
    expect(allText(r)).not.toContain('Tip $');
  });

  it('falls back to the ride id and "Unknown destination" when ride_code and dropoff_address are missing', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/rides/history')) {
        return Promise.resolve({
          data: {
            rides: [{ ...RIDE_RECENT, id: 'abcdef1234567890', ride_code: undefined, dropoff_address: undefined, fare_breakdown: [] }],
            next_cursor: null,
          },
        });
      }
      if (url.startsWith('/rides/stats')) return Promise.resolve({ data: { period: 'all', total_rides: 1, total_distance_km: 1, total_saved: '0', co2_saved_kg: 0 } });
      if (url === '/vehicle-types') return Promise.resolve({ data: [] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Unknown destination');
    expect(allText(r)).toContain('ABCDEF12');
  });

  it('falls back scheduled-ride cards to "Unknown destination", "Scheduled", and a $0.00 fare when those fields are missing', async () => {
    mockRideState.scheduledRides = [{ id: 'sched-2', dropoff_address: undefined, vehicle_type_id: 'vt-1', scheduled_time: undefined, grand_total: undefined, total_fare: undefined }];
    const r = await renderScreen();
    const upcomingTab = findButtonByText(r, 'activity.upcoming_tab');
    act(() => { upcomingTab.props.onPress(); });
    expect(allText(r)).toContain('Unknown destination');
    expect(allText(r)).toContain('["Scheduled"," · ","Sedan"]');
    expect(allText(r)).toContain('["$","0.00"]');
  });

  it('uses the compact filter-tab layout (narrower paddings) when the window is under 380px wide', async () => {
    mockUseWindowDimensions.mockReturnValue({ width: 350, height: 800, scale: 1, fontScale: 1 });
    const r = await renderScreen();
    const weekPill = findButtonByText(r, 'This Week');
    const pillStyle = (weekPill.props.style as any[]).find((s) => s && s.borderRadius === 20);
    expect(pillStyle.paddingHorizontal).toBe(10);
    const personalTab = findButtonByText(r, 'Personal');
    const tabStyle = (personalTab.props.style as any[]).find((s) => s && s.borderRadius === 24);
    expect(tabStyle.paddingHorizontal).toBe(14);
    expect(tabStyle.minWidth).toBe(92);
    expect(tabStyle.flexGrow).toBe(1);
  });

  it('falls back the stats card background to colors.surface when colors.surfaceLight is unset', async () => {
    mockUseTheme.mockReturnValue({ colors: { ...COLORS, surfaceLight: undefined } as any, isDark: false });
    const r = await renderScreen();
    const statsCard = r.root.findAllByType(View).find((n) => n.props.style && n.props.style.borderRadius === 16 && n.props.style.padding === 16);
    expect(statsCard!.props.style.backgroundColor).toBe(COLORS.surface);
  });
});
