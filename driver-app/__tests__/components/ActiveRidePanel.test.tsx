import React from 'react';
import { Animated, AppState, Linking } from 'react-native';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ActiveRidePanel } from '../../components/dashboard/ActiveRidePanel';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { _resetAutoNavClaimForTest } from '../../lib/navigation/autoNavigate';

// react-native-safe-area-context's `useSafeAreaInsets` throws if no provider is
// in the tree. Wrap every render with a deterministic SafeAreaProvider.
const initialMetrics = {
  frame: { x: 0, y: 0, width: 360, height: 800 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};
const renderWithSafeArea = (ui: React.ReactElement) =>
  render(
    <SafeAreaProvider initialMetrics={initialMetrics}>{ui}</SafeAreaProvider>,
  );

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: {
    theme: {
      colors: {
        primary: '#FF3B30',
        primaryDark: '#D32F2F',
        background: '#FFFFFF',
        surface: '#FFFFFF',
        surfaceLight: '#F5F5F5',
        text: '#1A1A1A',
        textDim: '#666666',
        border: '#E5E7EB',
        accent: '#FF3B30',
        accentDim: '#D32F2F',
        danger: '#DC2626',
        gold: '#FFD700',
      },
    },
    rideOffer: { countdownSeconds: 15 },
  },
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

// Mock the router like TripCompletedPanel.test.tsx does — importing the real
// expo-router drags the whole react-navigation tree through babel, which
// fails on react-native's flow-typed internals under jest.
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));

// Modal-based children pull react-native's flow-typed native specs through
// babel codegen, which the jest transform can't parse. The panel's own
// layout is what's under test, so stub them out.
jest.mock('../../components/CancelReasonSheet', () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock('../../components/AlertDialog', () => ({
  showAlert: jest.fn(),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

// Driver's saved navigation preferences — mutate these per test.
// `mockAutoNavigate` defaults OFF here even though production defaults it ON,
// so the manual-button cases below assert on their own Linking calls and
// nothing else. The auto-launch suite at the bottom opts back in.
let mockNavApp = 'default';
let mockAutoNavigate = false;
let mockNavPrefsLoaded = true;
jest.mock('../../store/navStore', () => ({
  useNavStore: () => ({
    navApp: mockNavApp,
    autoNavigate: mockAutoNavigate,
    isLoaded: mockNavPrefsLoaded,
    loadNavApp: jest.fn(),
  }),
}));

jest.mock('../../hooks/useToast', () => ({
  showToast: jest.fn(),
}));

const mockRide = {
  id: 'ride-001',
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
  pickup_lat: 52.1333,
  pickup_lng: -106.6667,
  dropoff_lat: 52.15,
  dropoff_lng: -106.65,
  total_fare: 15.0,
  driver_earnings: 15.0,
  distance_km: 2.5,
  duration_minutes: 10,
  status: 'driver_assigned',
};

const mockRider = {
  first_name: 'Jane',
  last_name: 'Doe',
  rating: 4.8,
  phone: '+15061234567',
};

const defaultProps = {
  rideState: 'navigating_to_pickup' as const,
  ride: mockRide,
  rider: mockRider,
  driverLocation: null,
  isLoading: false,
  otpInput: '',
  setOtpInput: jest.fn(),
  onVerifyOTP: jest.fn(),
  onNavigate: jest.fn(),
  onArriveAtPickup: jest.fn(),
  onStartRide: jest.fn(),
  onCompleteRide: jest.fn(),
  onCancelRide: jest.fn(),
  routeEtaMinutes: null,
  routeDistanceKm: null,
  slideUpAnim: new Animated.Value(0),
  fadeAnim: new Animated.Value(1),
  distanceToPickup: null,
};

describe('ActiveRidePanel', () => {
  it('renders without crashing', () => {
    const { toJSON } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(toJSON()).not.toBeNull();
  });

  it('shows rider name', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Jane D.')).toBeTruthy();
  });

  it('shows earnings amount', () => {
    const { getAllByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    // Earnings appears in both the status pill and the trip info row
    expect(getAllByText('$15.00').length).toBeGreaterThan(0);
  });

  // rides.driver_earnings is fare-only (the bonus is claimed at completion),
  // so the panel adds the projected bonus itself — otherwise the headline drops
  // to the bare fare the moment the driver accepts the offer.
  describe('incentive bonus', () => {
    it('adds the bonus to the headline and shows the split', () => {
      const { getAllByText, getByText } = renderWithSafeArea(
        <ActiveRidePanel {...defaultProps} totalBonus={5} />,
      );
      expect(getAllByText('$20.00').length).toBeGreaterThan(0);
      expect(getByText('$15.00 + $5.00 bonus')).toBeTruthy();
    });

    it('announces the bonus to screen readers', () => {
      const { getByLabelText } = renderWithSafeArea(
        <ActiveRidePanel {...defaultProps} totalBonus={5} />,
      );
      expect(
        getByLabelText(/earnings \$20\.00, including \$5\.00 bonus/),
      ).toBeTruthy();
    });

    it('shows no bonus line when there is no incentive', () => {
      const { queryByText, getAllByText } = renderWithSafeArea(
        <ActiveRidePanel {...defaultProps} totalBonus={null} />,
      );
      expect(getAllByText('$15.00').length).toBeGreaterThan(0);
      expect(queryByText(/bonus/)).toBeNull();
    });

    it('shows no bonus line for a zero bonus', () => {
      const { queryByText } = renderWithSafeArea(
        <ActiveRidePanel {...defaultProps} totalBonus={0} />,
      );
      expect(queryByText(/bonus/)).toBeNull();
    });
  });

  it('shows pickup address', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('123 Main St')).toBeTruthy();
  });

  it('shows cancel ride option during navigating_to_pickup', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Cancel Ride')).toBeTruthy();
  });

  it('renders the draggable grab area expanded by default', () => {
    const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    // Label flips to "Expand ride details" when collapsed; expanded is the default.
    expect(getByLabelText('Collapse ride details')).toBeTruthy();
  });

  it('reports its expanded state to onExpandedChange so the map can reserve mapPadding', () => {
    // index.tsx's follow-camera effect trusts this callback to know how much
    // of the screen the sheet occupies (see its onExpandedChange comment) —
    // pin that it fires true on mount (the sheet's real default state).
    const onExpandedChange = jest.fn();
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} onExpandedChange={onExpandedChange} />);
    expect(onExpandedChange).toHaveBeenCalledWith(true);
  });

  it('renders nothing when ride is null', () => {
    // Render inside SafeAreaProvider (the panel calls useSafeAreaInsets before
    // its early-return); assert the panel produced no panel-specific output by
    // checking that fields populated from a non-null ride are absent.
    const { queryByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} ride={null} />);
    expect(queryByText('Jane D.')).toBeNull();
    expect(queryByText('Cancel Ride')).toBeNull();
  });

  describe('navigation app preference', () => {
    let openURL: jest.SpyInstance;
    let canOpenURL: jest.SpyInstance;
    beforeEach(() => {
      openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
      // Default: the chosen app IS installed.
      canOpenURL = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(true as never);
    });
    afterEach(() => {
      mockNavApp = 'default';
      openURL.mockRestore();
      canOpenURL.mockRestore();
    });

    it('launches Waze deep link when the driver chose Waze and it is installed', async () => {
      mockNavApp = 'waze';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() =>
        expect(openURL).toHaveBeenCalledWith('waze://?ll=52.1333,-106.6667&navigate=yes'),
      );
    });

    it('launches the Google Maps app deep link when chosen and installed', async () => {
      mockNavApp = 'google';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() =>
        expect(openURL).toHaveBeenCalledWith('comgooglemaps://?daddr=52.1333,-106.6667&directionsmode=driving'),
      );
    });

    it('falls back to the default maps app when the chosen app is NOT installed', async () => {
      mockNavApp = 'waze';
      canOpenURL.mockResolvedValue(false as never); // Waze not installed
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() => expect(openURL).toHaveBeenCalled());
      // Never the unhandled waze:// scheme — falls back to a real maps URL.
      for (const call of openURL.mock.calls) {
        expect((call[0] as string).startsWith('waze://')).toBe(false);
      }
    });

    it('does not use Waze/Google deep links when the driver left it on Default', async () => {
      mockNavApp = 'default';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() => expect(openURL).toHaveBeenCalled());
      const calledWith = openURL.mock.calls[0][0] as string;
      expect(calledWith.startsWith('waze://')).toBe(false);
      expect(calledWith.startsWith('comgooglemaps://')).toBe(false);
    });
  });
});


it('shows booked pickup and does not count early arrival as waiting', () => {
  jest.useFakeTimers();
  const booked = new Date(Date.now() + 10 * 60 * 1000).toISOString();
  const view = renderWithSafeArea(<ActiveRidePanel {...defaultProps} rideState="arrived_at_pickup" ride={{...mockRide, is_scheduled:true, scheduled_time:booked} as any} />);
  expect(view.getByText(/^Scheduled pickup:/)).toBeTruthy();
  act(() => { jest.advanceTimersByTime(60 * 1000); });
  expect(view.getByText('0s')).toBeTruthy();
  view.unmount();
  jest.useRealTimers();
});

describe('automatic navigation hand-off', () => {
  let openURL: jest.SpyInstance;
  let canOpenURL: jest.SpyInstance;

  beforeEach(() => {
    _resetAutoNavClaimForTest();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValue(null);
    (AsyncStorage.setItem as jest.Mock).mockResolvedValue(undefined);
    openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
    canOpenURL = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(true as never);
    mockAutoNavigate = true;
    mockNavPrefsLoaded = true;
    (AppState as any).currentState = 'active';
  });

  afterEach(() => {
    mockNavApp = 'default';
    mockAutoNavigate = false;
    mockNavPrefsLoaded = true;
    (AppState as any).currentState = 'active';
    openURL.mockRestore();
    canOpenURL.mockRestore();
  });

  it('launches to the pickup when the panel opens on an accepted ride', async () => {
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await waitFor(() => expect(openURL).toHaveBeenCalled());
    expect(openURL.mock.calls[0][0]).toContain('52.1333,-106.6667');
  });

  it('launches to the dropoff once the trip starts', async () => {
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} rideState="trip_in_progress" />);
    await waitFor(() => expect(openURL).toHaveBeenCalled());
    expect(openURL.mock.calls[0][0]).toContain('52.15,-106.65');
  });

  it('honours the driver\'s chosen app', async () => {
    mockNavApp = 'waze';
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await waitFor(() =>
      expect(openURL).toHaveBeenCalledWith('waze://?ll=52.1333,-106.6667&navigate=yes'),
    );
  });

  it('routes to the road-snapped pickup when the rider pinned an unreachable spot', async () => {
    // pickup_nav_lat/lng (migration 133) is the pin snapped to the nearest
    // drivable road. Sending the driver to the raw pin can mean the middle of a
    // mall. The manual Navigate button already prefers it; so must this.
    renderWithSafeArea(
      <ActiveRidePanel
        {...defaultProps}
        ride={{ ...mockRide, pickup_nav_lat: 52.14, pickup_nav_lng: -106.68 } as any}
      />,
    );
    await waitFor(() => expect(openURL).toHaveBeenCalled());
    expect(openURL.mock.calls[0][0]).toContain('52.14,-106.68');
  });

  it('does nothing when the driver turned auto-navigate off', async () => {
    mockAutoNavigate = false;
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
  });

  it('waits for the stored preferences before launching', async () => {
    // Firing before AsyncStorage resolves would send a Waze driver to Apple
    // Maps, and would fire at all for a driver who had opted out.
    mockNavPrefsLoaded = false;
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
  });

  it('stays put while the driver waits at the pickup', async () => {
    // arrived_at_pickup is the one active phase with no hand-off — the driver
    // is parked and needs the OTP keypad, not a maps app on top of it.
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} rideState="arrived_at_pickup" />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
  });

  it('stays off the phone screen when the accept came from the car head unit', async () => {
    // lib/androidAuto/register.ts calls acceptRide() on this same singleton
    // store straight off the head unit, so this effect can run with the phone
    // locked in the driver's pocket. Launching then would throw the phone into
    // Maps mid-drive for an accept that never touched it.
    (AppState as any).currentState = 'background';
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
    // The leg is still spent, so returning to the phone later can't fire it.
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      '@spinr_auto_nav_launched',
      'ride-001:pickup',
    );
  });

  it('does not retroactively launch when the toggle is switched on mid-leg', async () => {
    // The toggle's own copy promises "when you accept a ride and when the trip
    // starts" — it takes effect from the next transition, not the current one.
    mockAutoNavigate = false;
    const view = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();

    mockAutoNavigate = true;
    view.rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <ActiveRidePanel {...defaultProps} routeEtaMinutes={7} />
      </SafeAreaProvider>,
    );
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
  });

  it('does not launch for a ride cancelled during the claim round-trip', async () => {
    // resetRideState() drops this panel out of the tree on a cancellation. If
    // that lands inside the AsyncStorage round-trip, the hand-off must not
    // still fire for a ride that no longer exists.
    let releaseClaim: (v: string | null) => void = () => {};
    (AsyncStorage.getItem as jest.Mock).mockReturnValue(
      new Promise((resolve) => { releaseClaim = resolve; }),
    );
    const view = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    view.unmount();
    await act(async () => { releaseClaim(null); });
    expect(openURL).not.toHaveBeenCalled();
  });

  it('does not launch again when the same leg re-renders', async () => {
    const view = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await waitFor(() => expect(openURL).toHaveBeenCalledTimes(1));
    view.rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <ActiveRidePanel {...defaultProps} routeEtaMinutes={4} />
      </SafeAreaProvider>,
    );
    await act(async () => {});
    expect(openURL).toHaveBeenCalledTimes(1);
  });

  it('does not re-launch on a cold start mid-leg', async () => {
    // The process was killed while the driver was in Maps; reopening Spinr
    // remounts the panel. The durable marker is what stops the remount from
    // throwing them straight back out.
    (AsyncStorage.getItem as jest.Mock).mockResolvedValue('ride-001:pickup');
    renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();
  });

  it('holds off until the ride payload carries coordinates', async () => {
    // acceptRide flips the phase and only then fetches the ride, so the panel
    // can render a beat before pickup_lat/lng exist. Navigating to (0,0) then
    // would be worse than waiting.
    const view = renderWithSafeArea(
      <ActiveRidePanel {...defaultProps} ride={{ ...mockRide, pickup_lat: undefined, pickup_lng: undefined } as any} />,
    );
    await act(async () => {});
    expect(openURL).not.toHaveBeenCalled();

    view.rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <ActiveRidePanel {...defaultProps} />
      </SafeAreaProvider>,
    );
    await waitFor(() => expect(openURL).toHaveBeenCalled());
  });
});
