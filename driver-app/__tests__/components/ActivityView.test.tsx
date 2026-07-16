import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import ActivityView from '../../components/activity/ActivityView';
import { useDriverStore } from '../../store/driverStore';

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#FF3B30',
      primaryDark: '#D32F2F',
      background: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceLight: '#F5F5F5',
      text: '#1A1A1A',
      textDim: '#666666',
      border: '#E5E7EB',
      success: '#10B981',
      danger: '#DC2626',
    },
  }),
}));

jest.mock('@expo/vector-icons', () => ({
  Ionicons: () => null,
  MaterialCommunityIcons: () => null,
  FontAwesome5: () => null,
}));

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock('@react-navigation/native', () => {
  const React = require('react');
  return {
    useFocusEffect: (effect: () => void | (() => void)) => {
      React.useEffect(() => effect(), [effect]);
    },
  };
});

jest.mock('../../store/driverStore', () => ({
  useDriverStore: jest.fn(),
}));

const mockUseDriverStore = useDriverStore as unknown as jest.Mock;

const makeRide = (overrides = {}) => ({
  id: 'ride-001',
  status: 'completed',
  ride_completed_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
  distance_km: 5.2,
  duration_minutes: 18,
  total_earned: 12,
  ...overrides,
});

const makeStore = (overrides = {}) => ({
  earnings: {
    total_earnings: 12,
    total_tips: 0,
    total_incentives: 0,
    total_tax: 0,
    total_rides: 1,
    total_distance_km: 5.2,
    total_duration_minutes: 18,
    average_per_ride: 12,
  },
  rideHistory: [makeRide()],
  historyTotal: 1,
  fetchEarnings: jest.fn().mockResolvedValue(undefined),
  fetchRideHistory: jest.fn().mockResolvedValue(undefined),
  fetchDriverBalance: jest.fn().mockResolvedValue(undefined),
  ...overrides,
});

let mockStore: ReturnType<typeof makeStore>;

describe('ActivityView', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStore = makeStore();
    mockUseDriverStore.mockReturnValue(mockStore);
    // ActivityView reads the per-period earnings cache via getState(); the
    // closure returns whatever mockStore is set to (including per-test overrides).
    (mockUseDriverStore as unknown as { getState: () => unknown }).getState = () => mockStore;
  });

  it('keeps ride history visible when earnings loading fails', async () => {
    mockStore.fetchEarnings.mockRejectedValueOnce(new Error('earnings unavailable'));

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    expect(getByText('456 Elm Ave')).toBeTruthy();
    expect(mockStore.fetchRideHistory).toHaveBeenCalledWith(50, 0, false);
  });

  it('loads the next page without changing the working ScrollView flow', async () => {
    mockStore = makeStore({ historyTotal: 2 });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    fireEvent.press(getByText('All Time'));

    await waitFor(() => expect(getByText('Load more rides')).toBeTruthy());
    fireEvent.press(getByText('Load more rides'));

    await waitFor(() => expect(mockStore.fetchRideHistory).toHaveBeenCalledWith(50, 1, true));
  });

  it('hides load more when the current period has no matching rides', async () => {
    mockStore = makeStore({
      historyTotal: 20,
      rideHistory: [makeRide({ ride_completed_at: '2024-01-01T12:00:00.000Z' })],
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText, queryByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('No Rides Found')).toBeTruthy());
    expect(queryByText('Load more rides')).toBeNull();
  });

  it('hides load more when the active period already filtered the loaded page', async () => {
    const thisWeek = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    mockStore = makeStore({
      historyTotal: 20,
      rideHistory: [
        makeRide({ id: 'ride-this-week', ride_completed_at: thisWeek, pickup_address: 'Weekly Pickup' }),
        makeRide({ id: 'ride-old', ride_completed_at: '2024-01-01T12:00:00.000Z', pickup_address: 'Old Pickup' }),
      ],
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText, queryByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('No Rides Found')).toBeTruthy());
    fireEvent.press(getByText('This Week'));

    await waitFor(() => expect(getByText('Weekly Pickup')).toBeTruthy());
    expect(queryByText('Old Pickup')).toBeNull();
    expect(queryByText('Load more rides')).toBeNull();
  });

  it('shows load more for a month when period stats say more rides exist', async () => {
    const thisMonth = new Date();
    thisMonth.setDate(Math.max(1, thisMonth.getDate() - 10));
    mockStore = makeStore({
      earnings: {
        ...makeStore().earnings,
        total_rides: 86,
      },
      historyTotal: 120,
      rideHistory: [
        makeRide({
          id: 'ride-this-month',
          ride_completed_at: thisMonth.toISOString(),
          pickup_address: 'Monthly Pickup',
        }),
        makeRide({ id: 'ride-old', ride_completed_at: '2024-01-01T12:00:00.000Z' }),
      ],
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('No Rides Found')).toBeTruthy());
    fireEvent.press(getByText('This Month'));

    await waitFor(() => expect(getByText('Monthly Pickup')).toBeTruthy());
    expect(getByText('Load more rides')).toBeTruthy();
  });

  it('refetches only earnings on a period change (list re-filters client-side)', async () => {
    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    mockStore.fetchRideHistory.mockClear();

    fireEvent.press(getByText('This Week'));

    await waitFor(() => expect(mockStore.fetchEarnings).toHaveBeenLastCalledWith('week'));
    // The ride-history endpoint is period-independent and the list filters
    // client-side, so a pill change must NOT refetch history — that churn
    // replaced the rideHistory array and re-rendered the whole FlatList on
    // every pill tap.
    expect(mockStore.fetchRideHistory).not.toHaveBeenCalled();
  });
});
