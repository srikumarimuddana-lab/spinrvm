/**
 * Unit tests for services/pendingRideOffer.ts.
 *
 * This is the only path by which an offer that arrived while the app was killed
 * reaches a driver, and it had no coverage at all while it lived inline in
 * useDriverDashboard. The rules that matter: never resurrect an expired offer,
 * never clobber an active ride, and always clear the key so a bad payload can't
 * be retried forever.
 */
const mockGetItem = jest.fn<Promise<string | null>, [string]>();
const mockRemoveItem = jest.fn<Promise<void>, [string]>(() => Promise.resolve());
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: (k: string) => mockGetItem(k),
    removeItem: (k: string) => mockRemoveItem(k),
  },
}));

// The producer's own constant. Mocked because the real module statically pulls
// in Firebase, Notifee and the SQLite location recorder; the literal is kept in
// step by services/backgroundMessaging.ts, which exports it and writes with it.
jest.mock('../../services/backgroundMessaging', () => ({
  PENDING_OFFER_KEY: 'spinr_pending_ride_offer',
}));

const mockSetIncomingRide = jest.fn();
const mockState = { rideState: 'idle', setIncomingRide: mockSetIncomingRide };
jest.mock('../../store/driverStore', () => ({
  useDriverStore: { getState: () => mockState },
}));

import { consumePendingRideOffer } from '../../services/pendingRideOffer';

const offer = (extra: Record<string, unknown> = {}) =>
  JSON.stringify({ ride_id: 'r1', pickup_address: '1 Main St', ...extra });

const inFuture = () => new Date(Date.now() + 30_000).toISOString();
const inPast = () => new Date(Date.now() - 30_000).toISOString();

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  mockState.rideState = 'idle';
});

afterEach(() => jest.restoreAllMocks());

it('surfaces a live offer and fires onOffer', async () => {
  mockGetItem.mockResolvedValue(offer({ offer_expires_at: inFuture() }));
  const onOffer = jest.fn();

  await expect(consumePendingRideOffer({ onOffer })).resolves.toBe(true);

  expect(onOffer).toHaveBeenCalledTimes(1);
  expect(mockSetIncomingRide).toHaveBeenCalledWith(expect.objectContaining({ ride_id: 'r1' }));
  expect(mockRemoveItem).toHaveBeenCalledWith('spinr_pending_ride_offer');
});

it('surfaces an offer with no expiry — the store owns the countdown', async () => {
  mockGetItem.mockResolvedValue(offer());
  await expect(consumePendingRideOffer()).resolves.toBe(true);
  expect(mockSetIncomingRide).toHaveBeenCalled();
});

it('drops an expired offer, and does not buzz for it', async () => {
  mockGetItem.mockResolvedValue(offer({ offer_expires_at: inPast() }));
  const onOffer = jest.fn();

  await expect(consumePendingRideOffer({ onOffer })).resolves.toBe(false);

  expect(onOffer).not.toHaveBeenCalled();
  expect(mockSetIncomingRide).not.toHaveBeenCalled();
  // Still cleared: leaving it would re-offer a dead ride on every resume.
  expect(mockRemoveItem).toHaveBeenCalled();
});

it('never clobbers a ride already under way', async () => {
  mockState.rideState = 'trip_in_progress';
  mockGetItem.mockResolvedValue(offer({ offer_expires_at: inFuture() }));
  const onOffer = jest.fn();

  await expect(consumePendingRideOffer({ onOffer })).resolves.toBe(false);

  expect(mockSetIncomingRide).not.toHaveBeenCalled();
  expect(onOffer).not.toHaveBeenCalled();
});

it('does nothing when there is no stashed offer', async () => {
  mockGetItem.mockResolvedValue(null);
  await expect(consumePendingRideOffer()).resolves.toBe(false);
  expect(mockRemoveItem).not.toHaveBeenCalled();
  expect(mockSetIncomingRide).not.toHaveBeenCalled();
});

it('a corrupt payload is cleared, not retried forever', async () => {
  mockGetItem.mockResolvedValue('{not json');
  await expect(consumePendingRideOffer()).resolves.toBe(false);
  expect(mockRemoveItem).toHaveBeenCalled();
  expect(mockSetIncomingRide).not.toHaveBeenCalled();
});

it('a storage failure resolves false instead of throwing at the caller', async () => {
  // Called from the car's connect path, where a throw would abort the rest of
  // the session bootstrap.
  mockGetItem.mockRejectedValue(new Error('AsyncStorage unavailable'));
  await expect(consumePendingRideOffer()).resolves.toBe(false);
});
