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

jest.mock('expo-router/react-navigation', () => {
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
    elapsed_days: 1,
  },
  rideHistory: [makeRide()],
  historyTotal: 1,
  fetchEarnings: jest.fn().mockResolvedValue(undefined),
  fetchRideHistory: jest.fn().mockResolvedValue(undefined),
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
    // Must land within the current calendar month but NOT on today's date
    // (the default 'today' period must exclude it so the initial render can
    // assert "No Rides Found"). The old approach subtracted a fixed 10-day
    // offset from today's day-of-month and clamped negative/zero results to
    // day 1 via Math.max(1, ...) — but that clamp collapses the fixture back
    // onto "today" itself whenever today IS the 1st, and still lands
    // suspiciously close to "today" on the 2nd-10th. Anchor to day 1 of the
    // month instead (never negative/invalid, preserving the same safety
    // intent as the old Math.max(1, ...) clamp) and nudge forward one day
    // only in the single case where day 1 would collide with today.
    const today = new Date();
    const thisMonth = new Date(
      today.getFullYear(),
      today.getMonth(),
      1,
      today.getHours(),
      today.getMinutes(),
      today.getSeconds()
    );
    if (thisMonth.getDate() === today.getDate()) {
      thisMonth.setDate(thisMonth.getDate() + 1);
    }
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

  // Business decision 2026-08-13 (docs/change-log/2026-08-13-blended-
  // lifetime-earnings.md): superseded A30 Finding 4's driver/rider-facing
  // "Imported" badge and Finding 3's "not counted here" explainer. A
  // migrated ride now renders identically to any other ride in the driver
  // app; the distinction stays in the backend/admin portal only.
  it('never shows an imported/legacy badge or explainer on a ride card', async () => {
    mockStore = makeStore({
      rideHistory: [
        makeRide({
          id: 'ride-legacy',
          pickup_address: 'Old App Pickup',
          legacy_import_metadata: { batch: '20260726', source: 'legacy_mongo_booking_import' },
        }),
      ],
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText, queryByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('Old App Pickup')).toBeTruthy());
    expect(queryByText('Imported from your previous account')).toBeNull();
    expect(queryByText(/from your previous account/)).toBeNull();
    expect(queryByText(/imported/i)).toBeNull();
  });

  // Business decision 2026-08-13 (A32/A33, docs/change-log/2026-08-13-
  // blended-lifetime-earnings.md): the blend moved from the frontend
  // (Spinr total + a separate previous_app_paid_total figure) to the
  // backend (/drivers/earnings sums every completed ride, legacy included,
  // per period). The component now just displays whatever total_earnings
  // and total_rides it's given — no more client-side "Previously Paid" math.
  it('renders the blended total/average/per-day stats /drivers/earnings now returns', async () => {
    mockStore = makeStore({
      earnings: {
        total_earnings: 150,
        total_tips: 0,
        total_incentives: 0,
        total_tax: 0,
        total_rides: 5,
        total_distance_km: 25,
        total_duration_minutes: 600,
        average_per_ride: 30,
        elapsed_days: 10,
      },
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText, getAllByText } = render(<ActivityView />);
    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());

    // Total Earned is whatever the backend returns — no client-side add-on.
    // (Fare shows the same $150.00 here since tips/incentives/bonus/tax are
    // all 0 in this fixture, so both the hero total and the Fare row match.)
    await waitFor(() => expect(getAllByText('$150.00').length).toBeGreaterThan(0));
    // Avg per Trip = total_earnings / total_rides = 150 / 5.
    expect(getByText('$30.00')).toBeTruthy();
    // Avg Trips/Day = total_rides / elapsed_days = 5 / 10.
    expect(getByText('0.5')).toBeTruthy();
    // Avg KM/Day = total_distance_km / elapsed_days = 25 / 10.
    expect(getByText('2.5 km')).toBeTruthy();
    // Total/Avg Online Time = total_duration_minutes (600 = 10h) and per-day (60min = 1h).
    expect(getByText('10h')).toBeTruthy();
    expect(getByText('1h')).toBeTruthy();
  });

  // Audit finding 2 (docs/audit/2026-08-19-legacy-migration-data-quality-
  // audit.md, "Not fixed — driver-app display"): the backend's total_earnings
  // includes total_cancel_fees (backend/routes/drivers/earnings.py's
  // _total_with_extras), so the client-computed "Fare" line must subtract it
  // too or a ride with a cancel fee silently inflates Fare.
  it('subtracts total_cancel_fees from the Fare line, matching the backend total composition', async () => {
    mockStore = makeStore({
      earnings: {
        total_earnings: 120, // fare(100) + cancel_fees(20)
        total_tips: 0,
        total_incentives: 0,
        total_bonuses: 0,
        total_tax: 0,
        total_cancel_fees: 20,
        total_rides: 5,
        total_distance_km: 25,
        total_duration_minutes: 600,
        elapsed_days: 10,
      },
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText } = render(<ActivityView />);
    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());

    // Total Earned is the raw backend figure ($120); Fare is $120 - $20 cancel
    // fees = $100 — before this fix, the missing subtraction left Fare at $120,
    // silently overstating the fare component by the cancel-fee amount.
    expect(getByText('$100.00')).toBeTruthy();
  });

  it('signals a discrepancy instead of clamping to a fabricated $0.00 when the Fare components do not reconcile', async () => {
    mockStore = makeStore({
      earnings: {
        // Deliberately inconsistent: components sum to more than total_earnings
        // (a data-quality bug, not a real-world case once cancel fees are
        // subtracted correctly) — must not render as a plausible "$0.00".
        total_earnings: 50,
        total_tips: 40,
        total_incentives: 0,
        total_bonuses: 0,
        total_tax: 0,
        total_cancel_fees: 20,
        total_rides: 1,
        total_distance_km: 5,
        total_duration_minutes: 20,
        elapsed_days: 1,
      },
    });
    mockUseDriverStore.mockReturnValue(mockStore);

    const { getByText } = render(<ActivityView />);
    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());

    // The Fare row itself must read as a flagged discrepancy, not a dollar
    // amount at all (let alone a fabricated-looking "$0.00") — the other
    // breakdown rows (Bonus/Referral/Tax) are legitimately $0.00 in this
    // fixture, so this only asserts on the Fare row's own text.
    expect(getByText("Doesn't match total")).toBeTruthy();
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
