/**
 * useRideOfferSound while Android Auto owns the ride-offer ring.
 *
 * lib/androidAuto/carOfferRing.ts plays the offer tone through the car
 * speakers; the phone's in-app loop must stay silent for that offer so only
 * one tone sounds, and must ring again as soon as the car hands the ring back.
 *
 * Code under test: driver-app/hooks/useRideOfferSound.ts
 */
import { act, renderHook } from '@testing-library/react-native';

const mockPlayer = {
  seekTo: jest.fn(() => Promise.resolve()),
  play: jest.fn(),
  pause: jest.fn(),
};
jest.mock('expo-audio', () => ({
  createAudioPlayer: jest.fn(() => mockPlayer),
  setAudioModeAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: { getState: () => ({ soundEffects: true }) },
}));

let mockCarOwner = false;
let mockOwnerCb: ((owner: boolean) => void) | null = null;
const mockUnsubscribe = jest.fn();
jest.mock('../../lib/androidAuto/carOfferRing', () => ({
  isCarRingOwner: () => mockCarOwner,
  subscribeCarRingOwner: (cb: (owner: boolean) => void) => {
    mockOwnerCb = cb;
    return mockUnsubscribe;
  },
}));

// eslint-disable-next-line import/first -- must follow the jest.mock() calls above
import { setOfferSoundUrl, useRideOfferSound } from '../useRideOfferSound';

const REPLAY_MS = 2_500;

/** Let playOnce's awaited seekTo settle. */
const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
};

beforeAll(() => {
  // A remote URL, so the player comes from the mocked createAudioPlayer rather
  // than the bundled mp3 require.
  setOfferSoundUrl('https://example.test/offer.mp3');
});

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockCarOwner = false;
  mockOwnerCb = null;
});

afterEach(() => {
  jest.useRealTimers();
});

it('rings on the phone when the car does not own the ring', async () => {
  const { result, unmount } = renderHook(() => useRideOfferSound());
  act(() => result.current.play());
  await flush();
  expect(mockPlayer.play).toHaveBeenCalledTimes(1);
  act(() => result.current.stop());
  unmount();
});

it('stays silent for the whole offer while the car owns the ring', async () => {
  mockCarOwner = true;
  const { result, unmount } = renderHook(() => useRideOfferSound());
  act(() => result.current.play());
  await flush();
  for (let i = 0; i < 3; i++) {
    act(() => jest.advanceTimersByTime(REPLAY_MS));
    await flush();
  }
  expect(mockPlayer.play).not.toHaveBeenCalled();
  act(() => result.current.stop());
  unmount();
});

it('rings again on the next replay once the car hands the ring back', async () => {
  mockCarOwner = true;
  const { result, unmount } = renderHook(() => useRideOfferSound());
  act(() => result.current.play());
  await flush();
  expect(mockPlayer.play).not.toHaveBeenCalled();

  mockCarOwner = false; // flag off / car disconnected / car tone failed
  act(() => jest.advanceTimersByTime(REPLAY_MS));
  await flush();
  expect(mockPlayer.play).toHaveBeenCalledTimes(1);
  act(() => result.current.stop());
  unmount();
});

it('pauses a tone already sounding when the car takes the ring mid-offer', async () => {
  const { result, unmount } = renderHook(() => useRideOfferSound());
  act(() => result.current.play());
  await flush();
  mockPlayer.pause.mockClear();

  mockCarOwner = true;
  act(() => mockOwnerCb?.(true));
  expect(mockPlayer.pause).toHaveBeenCalledTimes(1);
  act(() => result.current.stop());
  unmount();
});

it('does not pause when ownership goes back to the phone', () => {
  const { unmount } = renderHook(() => useRideOfferSound());
  act(() => mockOwnerCb?.(false));
  expect(mockPlayer.pause).not.toHaveBeenCalled();
  unmount();
});

it('unsubscribes from car ownership on unmount', () => {
  const { unmount } = renderHook(() => useRideOfferSound());
  unmount();
  expect(mockUnsubscribe).toHaveBeenCalledTimes(1);
});
