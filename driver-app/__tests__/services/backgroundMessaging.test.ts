/**
 * Tests for the in-process dispatch channel added to
 * services/backgroundMessaging.ts.
 *
 * The rule this file exists to defend: with NO subscriber — every phone-only
 * launch, which is nearly all of them — the handler must behave exactly as it
 * did before the channel existed. The Android Auto car session is the only
 * subscriber, and it only exists while a head unit is connected.
 */
const mockSetBackgroundMessageHandler = jest.fn();
jest.mock('@shared/services/firebase', () => ({
  setBackgroundMessageHandler: (h: unknown) => mockSetBackgroundMessageHandler(h),
  getAppCheckToken: jest.fn(() => Promise.resolve(null)),
  initFirebaseServices: jest.fn(() => Promise.resolve()),
}));

const mockSetItem = jest.fn<Promise<void>, [string, string]>(() => Promise.resolve());
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    setItem: (k: string, v: string) => mockSetItem(k, v),
    removeItem: jest.fn(() => Promise.resolve()),
  },
}));

jest.mock('../../utils/backgroundLocation', () => ({
  getBackgroundAuthToken: jest.fn(() => Promise.resolve(null)),
}));

// Platform.OS is 'ios' under jest-expo's default preset, which is the branch
// that skips Notifee — keeping this suite on the persist + republish path.
jest.mock('@notifee/react-native', () => ({ default: { onBackgroundEvent: jest.fn() } }), {
  virtual: true,
});

// eslint-disable-next-line import/first -- must follow the jest.mock() calls above
import {
  PENDING_OFFER_KEY,
  registerBackgroundMessageHandlers,
  subscribeBackgroundDispatch,
  type BackgroundDispatchEvent,
} from '../../services/backgroundMessaging';

type Handler = (m: { data: Record<string, string> }) => Promise<void>;

const handler = (): Handler => mockSetBackgroundMessageHandler.mock.calls[0][0] as Handler;

const OFFER = {
  type: 'new_ride_assignment',
  ride_id: 'r1',
  pickup_address: '1 Main St',
  dropoff_address: '2 Side Rd',
  fare: '14.50',
  countdown_seconds: '20',
};

beforeAll(() => {
  // Registration is once-per-process by design (`registered` flag), so grab the
  // handler here and let every case drive it directly.
  registerBackgroundMessageHandlers();
});

beforeEach(() => {
  mockSetItem.mockClear();
  jest.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe('with no subscriber (every phone-only launch)', () => {
  it('still persists a ride offer under the shared key', async () => {
    await handler()({ data: OFFER });

    expect(mockSetItem).toHaveBeenCalledTimes(1);
    const [key, body] = mockSetItem.mock.calls[0];
    expect(key).toBe(PENDING_OFFER_KEY);
    expect(JSON.parse(body)).toMatchObject({ ride_id: 'r1', fare: 14.5 });
  });

  it('does nothing at all for a cancellation', async () => {
    await handler()({ data: { type: 'ride_cancelled', ride_id: 'r1' } });
    expect(mockSetItem).not.toHaveBeenCalled();
  });

  it('ignores unrelated message types', async () => {
    await handler()({ data: { type: 'chat_message', ride_id: 'r1' } });
    expect(mockSetItem).not.toHaveBeenCalled();
  });
});

describe('with the car session subscribed', () => {
  let seen: BackgroundDispatchEvent[];
  let unsubscribe: () => void;

  beforeEach(() => {
    seen = [];
    unsubscribe = subscribeBackgroundDispatch((e) => seen.push(e));
  });
  afterEach(() => unsubscribe());

  it('republishes an offer with the SAME payload it persisted', async () => {
    await handler()({ data: OFFER });

    expect(seen).toHaveLength(1);
    expect(seen[0].type).toBe('new_ride_assignment');
    const [, body] = mockSetItem.mock.calls[0];
    // One payload shape, not two that can drift apart.
    expect((seen[0] as { offer: unknown }).offer).toEqual(JSON.parse(body));
  });

  it('republishes a cancellation', async () => {
    await handler()({ data: { type: 'ride_cancelled', ride_id: 'r9' } });
    expect(seen).toEqual([{ type: 'ride_cancelled', ride_id: 'r9' }]);
  });

  it('republishes AFTER the durable write, so a bad subscriber costs nothing', async () => {
    unsubscribe();
    subscribeBackgroundDispatch(() => {
      throw new Error('car session blew up');
    });

    await expect(handler()({ data: OFFER })).resolves.toBeUndefined();
    expect(mockSetItem).toHaveBeenCalledTimes(1);
  });

  it('a cancellation with no ride_id is not republished', async () => {
    await handler()({ data: { type: 'ride_cancelled' } });
    expect(seen).toHaveLength(0);
  });

  it('unsubscribing restores the no-subscriber behaviour exactly', async () => {
    unsubscribe();
    await handler()({ data: OFFER });
    expect(seen).toHaveLength(0);
    expect(mockSetItem).toHaveBeenCalledTimes(1);
  });
});
