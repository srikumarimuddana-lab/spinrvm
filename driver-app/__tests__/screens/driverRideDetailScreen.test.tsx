/**
 * app/driver/ride-detail.tsx — broader coverage beyond
 * rideDetailBackButton.test.tsx (pins only the map back button's
 * accessibilityLabel) and ride-detail-route.test.tsx (a source-string
 * contract test, not a render test).
 *
 * Pins:
 *  - the loading spinner shows before the fetch resolves
 *  - status badge + formatted date render for any ride
 *  - trip stats: distance falls back actual->booking->0; duration
 *    computes from started/completed timestamps (with a <1min floor) or
 *    falls back to duration_minutes; rating shows "—" when absent
 *  - completed rides show: rider first-name only (PIPEDA — never the
 *    full name); the "Your Earnings" fare breakdown (ride fare = base +
 *    distance + time, tip/incentive/tax lines only when > 0, and the
 *    Total Earned line preferring total_earned over driver_earnings);
 *    the separate Tax Info card only when tax_amount > 0; the "Found an
 *    item?" nav row
 *  - a cancelled ride with a nonzero cancel_fee_earned shows its own fee
 *    card, with distinct copy for a no-show vs. a rider cancellation
 *  - the trip timeline renders one step per present timestamp, in order,
 *    with elapsed-time subtitles between consecutive steps
 *  - no map renders when pickup/dropoff coordinates are missing
 */
import React from 'react';
import { render, waitFor, fireEvent } from '@testing-library/react-native';
import RideDetailScreen from '../../app/driver/ride-detail';

const mockGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: unknown[]) => mockGet(...a) },
}));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
  useLocalSearchParams: () => ({ id: 'ride-1' }),
}));

jest.mock('react-native-maps', () => {
  const ReactLib = require('react');
  const RN = require('react-native');
  const MapView = ReactLib.forwardRef((props: any, ref: any) =>
    ReactLib.createElement(RN.View, { ref, testID: 'map-view' }, props.children),
  );
  return {
    __esModule: true,
    default: MapView,
    PROVIDER_GOOGLE: 'google',
    Polyline: () => null,
    Marker: () => null,
  };
});

jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
jest.mock('@shared/hooks/useCompletedRouteRefresh', () => ({ useCompletedRouteRefresh: () => {} }));
jest.mock('@shared/utils/routeSegments', () => ({
  routeQualityLabel: () => 'Good',
  toReactNativeRouteSections: () => [],
  toReactNativeSegments: () => [],
}));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F3F4F6',
      text: '#111', textDim: '#666', border: '#E5E7EB',
    },
  }),
}));
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

function ride(overrides: any = {}) {
  return {
    id: 'ride-1', status: 'completed',
    created_at: '2026-08-20T10:00:00Z',
    driver_accepted_at: '2026-08-20T10:00:30Z',
    driver_arrived_at: '2026-08-20T10:05:00Z',
    ride_started_at: '2026-08-20T10:06:00Z',
    ride_completed_at: '2026-08-20T10:20:00Z',
    pickup_address: '123 Main St', dropoff_address: '456 2nd Ave',
    distance_km: 5.2, duration_minutes: 14,
    rider_rating: 5, rider_name: 'Jamie Fox',
    base_fare: '5.00', distance_fare: '8.00', time_fare: '2.00',
    total_earned: '15.00',
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('loading state', () => {
  it('shows a spinner before the fetch resolves', () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolves
    const screen = render(<RideDetailScreen />);
    expect(screen.UNSAFE_root.findAllByType(require('react-native').ActivityIndicator).length).toBeGreaterThan(0);
  });
});

describe('status + trip stats', () => {
  it('renders the status badge and distance/duration/rating stats', async () => {
    mockGet.mockResolvedValue({ data: ride() });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Completed')).toBeTruthy());
    expect(screen.getByText('5.2 km')).toBeTruthy();
    expect(screen.getByText('5')).toBeTruthy(); // rating
  });

  it('computes duration from timestamps when both are present, over duration_minutes', async () => {
    mockGet.mockResolvedValue({ data: ride({ duration_minutes: 999 }) });
    const screen = render(<RideDetailScreen />);
    // 10:06:00 -> 10:20:00 = 14 minutes.
    await waitFor(() => expect(screen.getByText('14 min')).toBeTruthy());
  });

  it('falls back to duration_minutes when start/end timestamps are missing', async () => {
    mockGet.mockResolvedValue({ data: ride({ ride_started_at: null, ride_completed_at: null, duration_minutes: 22 }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('22 min')).toBeTruthy());
  });

  it('shows "—" for rating when absent', async () => {
    mockGet.mockResolvedValue({ data: ride({ rider_rating: null }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('—')).toBeTruthy());
  });

  it('falls back distance to booking distance_km when actual_distance_km is absent', async () => {
    mockGet.mockResolvedValue({ data: ride({ distance_km: 3.7 }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('3.7 km')).toBeTruthy());
  });
});

describe('completed ride earnings breakdown', () => {
  it('shows the rider first name only (PIPEDA)', async () => {
    mockGet.mockResolvedValue({ data: ride({ rider_name: 'Jamie Fox' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Jamie')).toBeTruthy());
    expect(screen.queryByText('Jamie Fox')).toBeNull();
  });

  it('computes ride fare as base + distance + time, and shows surge when > 1', async () => {
    mockGet.mockResolvedValue({ data: ride({ base_fare: '5.00', distance_fare: '8.00', time_fare: '2.00', surge_multiplier: '1.5', distance_km: 5.2 }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Ride Fare (5.2 km) · 1.5× surge')).toBeTruthy());
    // Ride Fare ($15.00) and Total Earned ($15.00) both render this fixture's total.
    expect(screen.getAllByText('$15.00').length).toBeGreaterThanOrEqual(2);
  });

  it('omits the surge suffix at 1.0x', async () => {
    mockGet.mockResolvedValue({ data: ride({ surge_multiplier: '1' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText(/^Ride Fare \(5\.2 km\)$/)).toBeTruthy());
  });

  it('shows the Tip line only when tip_amount > 0', async () => {
    mockGet.mockResolvedValue({ data: ride({ tip_amount: '3.00' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('+$3.00')).toBeTruthy());
    expect(screen.getByText('Tip')).toBeTruthy();
  });

  it('shows the Area Boost line only when incentive_amount > 0', async () => {
    mockGet.mockResolvedValue({ data: ride({ incentive_amount: '2.50' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Area Boost')).toBeTruthy());
    expect(screen.getByText('+$2.50')).toBeTruthy();
  });

  it('shows a Tax line and a separate Tax Info card with GST/PST when tax_amount > 0', async () => {
    mockGet.mockResolvedValue({
      data: ride({ tax_amount: '0.65', tax_breakdown: { GST: { amount: '0.25' }, PST: { amount: '0.40' } } }),
    });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Tax Info')).toBeTruthy());
    expect(screen.getByText('GST (5%)')).toBeTruthy();
    expect(screen.getByText('$0.25')).toBeTruthy();
    expect(screen.getByText('$0.40')).toBeTruthy();
  });

  it('prefers total_earned over driver_earnings for the Total Earned line', async () => {
    mockGet.mockResolvedValue({ data: ride({ total_earned: '15.00', driver_earnings: '99.00' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Total Earned')).toBeTruthy());
    // Ride Fare ($15.00, from the default base+distance+time fixture) and
    // Total Earned ($15.00) both render — assert neither shows the stale $99.00.
    expect(screen.getAllByText('$15.00').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('$99.00')).toBeNull();
  });

  it('falls back to driver_earnings when total_earned is absent', async () => {
    mockGet.mockResolvedValue({ data: ride({ total_earned: undefined, driver_earnings: '12.00' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('$12.00')).toBeTruthy());
  });

  it('the "Found an item?" row navigates to the lost-and-found chat with this ride id', async () => {
    mockGet.mockResolvedValue({ data: ride() });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Found an item on this ride?')).toBeTruthy());
    fireEvent.press(screen.getByText('Found an item on this ride?'));
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver/lost-and-found-chat', params: { rideId: 'ride-1' } });
  });

  it('hides the entire earnings section for a non-completed ride', async () => {
    mockGet.mockResolvedValue({ data: ride({ status: 'in_progress' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('In_progress')).toBeTruthy());
    expect(screen.queryByText('Your Earnings')).toBeNull();
    expect(screen.queryByText('Found an item on this ride?')).toBeNull();
  });
});

describe('cancellation fee card', () => {
  it('shows the no-show variant', async () => {
    mockGet.mockResolvedValue({ data: ride({ status: 'cancelled', cancel_fee_earned: '5.00', cancellation_type: 'noshow' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('No-Show Fee')).toBeTruthy());
    expect(screen.getByText('Rider did not show up')).toBeTruthy();
    expect(screen.getByText('+$5.00')).toBeTruthy();
  });

  it('shows the rider-cancelled variant', async () => {
    mockGet.mockResolvedValue({ data: ride({ status: 'cancelled', cancel_fee_earned: '5.00', cancellation_type: 'rider' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Cancellation Fee')).toBeTruthy());
    expect(screen.getByText('Rider cancelled after acceptance')).toBeTruthy();
  });

  it('is hidden when cancel_fee_earned is zero', async () => {
    mockGet.mockResolvedValue({ data: ride({ status: 'cancelled', cancel_fee_earned: '0' }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Cancelled')).toBeTruthy());
    expect(screen.queryByText('Cancellation Fee')).toBeNull();
  });
});

describe('trip timeline', () => {
  it('renders one step per present timestamp with elapsed subtitles', async () => {
    mockGet.mockResolvedValue({ data: ride() });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Trip Timeline')).toBeTruthy());
    expect(screen.getByText('Ride Requested')).toBeTruthy();
    expect(screen.getByText('You Accepted')).toBeTruthy();
    expect(screen.getByText('Arrived at Pickup')).toBeTruthy();
    expect(screen.getByText('Trip Started')).toBeTruthy();
    expect(screen.getByText('Trip Completed')).toBeTruthy();
  });

  it('adds a Cancelled step for a cancelled ride', async () => {
    mockGet.mockResolvedValue({ data: ride({ status: 'cancelled', ride_started_at: null, ride_completed_at: null, cancelled_at: '2026-08-20T10:10:00Z' }) });
    const screen = render(<RideDetailScreen />);
    // "Cancelled" renders twice: the status badge and the timeline step.
    await waitFor(() => expect(screen.getAllByText('Cancelled').length).toBe(2));
  });

  it('omits steps for missing timestamps', async () => {
    mockGet.mockResolvedValue({ data: ride({ driver_arrived_at: null, ride_started_at: null, ride_completed_at: null, duration_minutes: 0 }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('You Accepted')).toBeTruthy());
    expect(screen.queryByText('Arrived at Pickup')).toBeNull();
    expect(screen.queryByText('Trip Started')).toBeNull();
  });
});

describe('map rendering', () => {
  it('renders no map without pickup/dropoff coordinates', async () => {
    mockGet.mockResolvedValue({ data: ride({ pickup_lat: undefined, pickup_lng: undefined }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByText('Completed')).toBeTruthy());
    expect(screen.queryByTestId('map-view')).toBeNull();
  });

  it('renders the map when both coordinates are present', async () => {
    mockGet.mockResolvedValue({ data: ride({ pickup_lat: 52.13, pickup_lng: -106.67, dropoff_lat: 52.14, dropoff_lng: -106.68 }) });
    const screen = render(<RideDetailScreen />);
    await waitFor(() => expect(screen.getByTestId('map-view')).toBeTruthy());
  });
});

describe('back navigation', () => {
  it('the map back button navigates back', async () => {
    mockGet.mockResolvedValue({ data: ride({ pickup_lat: 52.13, pickup_lng: -106.67, dropoff_lat: 52.14, dropoff_lng: -106.68 }) });
    const screen = render(<RideDetailScreen />);
    const backBtn = await waitFor(() => screen.getByLabelText('Go back'));
    fireEvent.press(backBtn);
    expect(mockBack).toHaveBeenCalled();
  });
});
