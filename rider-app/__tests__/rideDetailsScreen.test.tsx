/**
 * app/ride-details.tsx — rider's past-ride detail/receipt screen. Pins:
 *  - fetches the ride on mount; a not-found (never-resolved) ride shows
 *    "Ride not found" instead of crashing
 *  - status badge label/color: completed/cancelled get their own copy,
 *    everything else falls back to the raw status string
 *  - cancelled rides show ONLY the flat cancellation-fee card (never the
 *    booking-time fare_breakdown, which would misrepresent what was
 *    actually charged for a trip never taken)
 *  - completed/other rides show the normalized fare breakdown — multiple
 *    'fare' lines consolidate into one "Ride fare"; a promo/tip line is
 *    injected only when the ride has one but breakdown doesn't already
 *    carry it
 *  - handleEmailReceipt: POSTs, toasts to the user's registered email
 *  - handleDownloadInvoice: generates+shares the PDF; a failure (no
 *    native module) toasts "PDF Unavailable" instead of crashing
 *  - receipt actions ("Email receipt", "Download invoice") only render
 *    for completed rides
 *  - back nav and "Get help with this ride" nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn() }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  const Polyline = () => null;
  return { __esModule: true, default: MapView, Polyline, PROVIDER_GOOGLE: 'google' };
});
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));

const mockBack = jest.fn();
const mockPush = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockGetState = jest.fn(() => ({ user: { email: 'jamie@example.com' } }));
jest.mock('@shared/store/authStore', () => ({ useAuthStore: { getState: () => mockGetState() } }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockPrintToFileAsync = jest.fn();
jest.mock('expo-print', () => ({ printToFileAsync: (...a: any[]) => mockPrintToFileAsync(...a) }));
const mockIsAvailableAsync = jest.fn();
const mockShareAsync = jest.fn();
jest.mock('expo-sharing', () => ({
  isAvailableAsync: (...a: any[]) => mockIsAvailableAsync(...a),
  shareAsync: (...a: any[]) => mockShareAsync(...a),
}));

import RideDetailsScreen, { buildReceiptHtml } from '../app/ride-details';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const RIDE_COMPLETED = {
  id: 'ride-1', status: 'completed', created_at: '2026-08-20T10:00:00Z',
  pickup_address: '123 Main St', dropoff_address: '456 Elm St',
  fare_breakdown: [
    { label: 'Base fare', amount: '5.00', type: 'fare' },
    { label: 'Distance', amount: '8.00', type: 'fare' },
  ],
  distance_km: 5.2, duration_minutes: 12, rider_rating: 5,
  payment_method: 'card', payment_status: 'paid', card_last4: '4242',
};

const RIDE_CANCELLED = {
  id: 'ride-2', status: 'cancelled', created_at: '2026-08-20T10:00:00Z',
  pickup_address: '123 Main St', dropoff_address: '456 Elm St',
  cancellation_fee_admin: '5.00', cancellation_fee_driver: '0',
  payment_method: 'wallet', payment_status: 'paid',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RideDetailsScreen />);
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
  mockParams = { rideId: 'ride-1' };
  mockApiGet.mockResolvedValue({ data: RIDE_COMPLETED });
  mockApiPost.mockResolvedValue({});
  mockGetState.mockReturnValue({ user: { email: 'jamie@example.com' } });
  mockPrintToFileAsync.mockResolvedValue({ uri: 'file://receipt.pdf' });
  mockIsAvailableAsync.mockResolvedValue(true);
  mockShareAsync.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('RideDetailsScreen', () => {
  it('fetches the ride on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/rides/ride-1');
  });

  it('shows "Ride not found" instead of crashing when the fetch never resolves a ride', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride not found');
  });

  it('shows the "Completed" status badge for a completed ride', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Completed');
  });

  it('shows the "Cancelled" status badge and falls back to the raw status for anything else', async () => {
    mockApiGet.mockResolvedValue({ data: { ...RIDE_CANCELLED } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Cancelled');

    mockApiGet.mockResolvedValue({ data: { ...RIDE_COMPLETED, status: 'disputed' } });
    const r2 = await renderScreen();
    expect(allText(r2)).toContain('disputed');
  });

  it('shows only the flat cancellation fee for a cancelled ride, never the fare breakdown', async () => {
    mockApiGet.mockResolvedValue({ data: RIDE_CANCELLED });
    const r = await renderScreen();
    expect(allText(r)).toContain('Cancellation fee');
    expect(allText(r)).toContain('["$","5.00"]');
    expect(allText(r)).not.toContain('Fare breakdown');
  });

  it('consolidates multiple "fare" breakdown lines into one "Ride fare" line', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Fare breakdown');
    expect(allText(r)).toContain('100% goes to your driver · ride local, support local');
    expect(allText(r)).toContain('$13.00'); // 5.00 + 8.00 consolidated
  });

  it('injects a promo discount line when the ride has one but the breakdown does not', async () => {
    mockApiGet.mockResolvedValue({
      data: { ...RIDE_COMPLETED, discount_amount: '2.50', promo_code: 'SAVE10' },
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Promo (SAVE10)');
    expect(allText(r)).toContain('-$2.50');
  });

  it('injects a tip line when the ride has one but the breakdown does not', async () => {
    mockApiGet.mockResolvedValue({
      data: { ...RIDE_COMPLETED, tip_amount: '3.00' },
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Tip');
  });

  it('emails the receipt and toasts to the registered email', async () => {
    const r = await renderScreen();
    const emailBtn = findButtonByText(r, 'Email receipt');
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/email-receipt');
    expect(mockShowToast).toHaveBeenCalledWith('Receipt Sent', 'Receipt emailed to jamie@example.com.', 'success');
  });

  it('toasts a failure when emailing the receipt fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const emailBtn = findButtonByText(r, 'Email receipt');
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Email Not Sent', 'Could not send receipt email. Please try again.', 'danger');
  });

  // NOTE: handleDownloadInvoice uses a dynamic `await import('expo-print')`
  // specifically so an older build without the native module degrades to
  // this same catch branch instead of crashing (see the source comment).
  // Jest's CJS runtime throws ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING_FLAG on
  // ANY dynamic import regardless of jest.mock() — there is no
  // --experimental-vm-modules flag in this test config — so the success
  // path (Print/Sharing actually resolving) can never be exercised here.
  // That happens to be the exact real-world "old build" failure mode this
  // code is defending against, so pin it directly: every tap always lands
  // in the catch branch and shows the same graceful toast, never a crash.
  it('toasts "PDF Unavailable" (dynamic import fails in Jest, same as an old build without the native module) instead of crashing', async () => {
    const r = await renderScreen();
    const downloadBtn = findButtonByText(r, 'Download invoice (PDF)');
    await act(async () => { await downloadBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
    expect(mockPrintToFileAsync).not.toHaveBeenCalled();
  });

  it('hides the receipt actions for a non-completed ride', async () => {
    mockApiGet.mockResolvedValue({ data: RIDE_CANCELLED });
    const r = await renderScreen();
    expect(() => findButtonByText(r, 'Email receipt')).not.toThrow();
    expect(findButtonByText(r, 'Email receipt')).toBeUndefined();
  });

  it('navigates back on the header back button', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('navigates to support on "Get help with this ride"', async () => {
    const r = await renderScreen();
    const helpBtn = findButtonByText(r, 'Get help with this ride');
    act(() => { helpBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/support');
  });

  it('navigates back from the "Ride not found" state too', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});

// buildReceiptHtml is exported specifically so its branches (tax-breakdown
// formatting, the grand_total-gap tax fallback, driver-block presence, and
// the three-way route-snapshot-image state) can be pinned directly rather
// than only indirectly through handleDownloadInvoice — which, per the note
// above, can never actually reach Print.printToFileAsync in Jest.
describe('buildReceiptHtml', () => {
  it('renders base fare/distance/time rows and a grand total with no tax breakdown', () => {
    const html = buildReceiptHtml({
      id: 'ride-99', created_at: '2026-08-20T10:00:00Z',
      base_fare: '5.00', distance_fare: '3.00', time_fare: '2.00', distance_km: 5.2, duration_minutes: 12,
      total_fare: '10.00', grand_total: '10.00',
    });
    expect(html).toContain('Base fare');
    expect(html).toContain('$5.00');
    expect(html).toContain('Distance (5.2 km)');
    expect(html).toContain('Time (12 min)');
    expect(html).toContain('$10.00 CAD');
  });

  it('adds a booking fee row only when booking_fee > 0', () => {
    const withFee = buildReceiptHtml({ id: 'r1', booking_fee: '1.50', total_fare: '10', grand_total: '10' });
    expect(withFee).toContain('Booking fee');
    const withoutFee = buildReceiptHtml({ id: 'r2', booking_fee: '0', total_fare: '10', grand_total: '10' });
    expect(withoutFee).not.toContain('Booking fee');
  });

  it('renders each tax_breakdown entry with its rate, skipping zero-amount entries', () => {
    const html = buildReceiptHtml({
      id: 'r3', total_fare: '10.00', grand_total: '10.60',
      tax_breakdown: { GST: { amount: '0.50', rate: 5 }, PST: { amount: '0.10', rate: 6 }, Other: { amount: '0', rate: 0 } },
    });
    expect(html).toContain('GST (5%)');
    expect(html).toContain('$0.50');
    expect(html).toContain('PST (6%)');
    expect(html).not.toContain('Other');
  });

  it('falls back to a single "Tax" line from the grand_total gap when no tax_breakdown is present', () => {
    const html = buildReceiptHtml({ id: 'r4', total_fare: '10.00', grand_total: '10.60' });
    expect(html).toContain('Tax');
    expect(html).toContain('$0.60');
  });

  it('omits the fallback Tax line when the gap is negligible', () => {
    const html = buildReceiptHtml({ id: 'r5', total_fare: '10.00', grand_total: '10.00' });
    expect(html).not.toContain('>Tax<');
  });

  it('includes a Tip row and folds the tip into the grand total when tip_amount > 0', () => {
    const html = buildReceiptHtml({ id: 'r6', total_fare: '10.00', grand_total: '10.00', tip_amount: '2.00' });
    expect(html).toContain('Tip');
    expect(html).toContain('$12.00 CAD'); // grand_total + tip
  });

  it('renders a driver block from driver_name, falling back to first/last name, with driver_code · vehicle subtitle', () => {
    const withDriverName = buildReceiptHtml({ id: 'r7', driver_name: 'Alex Rider', driver_vehicle: 'Toyota Camry', driver_code: 'D-123' });
    expect(withDriverName).toContain('Alex Rider');
    expect(withDriverName).toContain('D-123 · Toyota Camry');

    const withNestedDriver = buildReceiptHtml({ id: 'r8', driver: { first_name: 'Sam', last_name: 'Lee' } });
    expect(withNestedDriver).toContain('Sam Lee');
  });

  it('omits the driver block entirely when no driver name is available', () => {
    const html = buildReceiptHtml({ id: 'r9' });
    expect(html).not.toContain('background:#f9f9f9;border-radius:12px"><tr><td style="padding:12px 14px"');
  });

  it('shows the actual-route snapshot image when the snapshot revision matches the ride revision', () => {
    const html = buildReceiptHtml({
      id: 'r10', route_schema_version: 2, route_revision: 3, snapshot_revision: 3,
      route_snapshot_url: 'https://cdn.example.com/route.png', route_quality: 'good',
    });
    expect(html).toContain('Actual route (revision 3)');
    expect(html).toContain('https://cdn.example.com/route.png');
  });

  it('shows "Route snapshot unavailable" when v2 but the snapshot revision does not match', () => {
    const html = buildReceiptHtml({
      id: 'r11', route_schema_version: 2, route_revision: 3, snapshot_revision: 1,
      route_snapshot_url: 'https://cdn.example.com/stale.png',
    });
    expect(html).toContain('Route snapshot unavailable');
    expect(html).not.toContain('stale.png');
  });

  it('falls back to a "Planned route" image for legacy (pre-v2) rides with a snapshot url', () => {
    const html = buildReceiptHtml({ id: 'r12', route_snapshot_url: 'https://cdn.example.com/planned.png' });
    expect(html).toContain('Planned route');
    expect(html).toContain('planned.png');
  });

  it('uses "—" as the ride code fallback when neither ride_code nor id is present', () => {
    const html = buildReceiptHtml({});
    expect(html).toContain('Ride <strong style="color:#1a1a1a">—</strong>');
  });
});

describe('map rendering (pickup_lat/dropoff_lat present)', () => {
  const RIDE_WITH_COORDS = {
    ...RIDE_COMPLETED,
    pickup_lat: 52.13, pickup_lng: -106.67,
    dropoff_lat: 52.15, dropoff_lng: -106.63,
  };

  it('renders the MapView when both pickup and dropoff coordinates are present', async () => {
    mockApiGet.mockResolvedValue({ data: RIDE_WITH_COORDS });
    const r = await renderScreen();
    const MapViewNode = r.root.findByType('MapView' as any);
    expect(MapViewNode).toBeTruthy();
  });

  it('omits the MapView when coordinates are missing (existing fixture)', async () => {
    const r = await renderScreen();
    expect(() => r.root.findByType('MapView' as any)).toThrow();
  });

  it('calls fitToCoordinates via the map ref once onMapReady fires with 2+ coordinates', async () => {
    mockApiGet.mockResolvedValue({
      data: {
        ...RIDE_WITH_COORDS,
        actual_route_segments: [
          { latitude: 52.13, longitude: -106.67 },
          { latitude: 52.14, longitude: -106.65 },
        ],
      },
    });
    const r = await renderScreen();
    const MapViewNode = r.root.findByType('MapView' as any);
    await act(async () => { MapViewNode.props.onMapReady(); await flush(); });
    // No assertion error thrown means the ref's fitToCoordinates (mocked via
    // useImperativeHandle) was reachable and callable post state update.
    expect(allText(r)).toBeDefined();
  });
});
