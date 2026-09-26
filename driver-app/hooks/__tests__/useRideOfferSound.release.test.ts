/**
 * useRideOfferSound — releasing the player when the tone is stopped in the
 * background (Android).
 *
 * expo-audio's Android native side marks every player that is playing when the
 * app backgrounds as `isPaused` and replays each one when the app returns. A JS
 * pause() never clears that flag, so a tone caught mid-clip resumed on return
 * even after its offer had expired. Releasing the player (remove()) takes it
 * out of that native resume list.
 *
 * The release is deliberately narrow: Android only, and only when the app is
 * not in front, so an in-app accept/decline keeps the warm player exactly as
 * before and iOS is untouched.
 *
 * Code under test: driver-app/hooks/useRideOfferSound.ts
 */
import { act, renderHook } from '@testing-library/react-native';
import { AppState, Platform } from 'react-native';

const mockMakePlayer = () => ({
  seekTo: jest.fn(() => Promise.resolve()),
  play: jest.fn(),
  pause: jest.fn(),
  remove: jest.fn(),
});
let mockPlayers: ReturnType<typeof mockMakePlayer>[] = [];
const mockCreateAudioPlayer = jest.fn((_source?: unknown) => {
  const p = mockMakePlayer();
  mockPlayers.push(p);
  return p;
});
jest.mock('expo-audio', () => ({
  createAudioPlayer: (source: unknown) => mockCreateAudioPlayer(source),
  setAudioModeAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: { getState: () => ({ soundEffects: true }) },
}));
jest.mock('../../lib/androidAuto/carOfferRing', () => ({
  isCarRingOwner: () => false,
  subscribeCarRingOwner: () => () => undefined,
}));

// eslint-disable-next-line import/first -- must follow the jest.mock() calls above
import { setOfferSoundUrl, useRideOfferSound } from '../useRideOfferSound';

const URL_A = 'https://example.test/offer-a.mp3';
const URL_B = 'https://example.test/offer-b.mp3';

const setEnv = (os: 'android' | 'ios', appState: string) => {
  Object.defineProperty(Platform, 'OS', { value: os, configurable: true });
  Object.defineProperty(AppState, 'currentState', { value: appState, configurable: true });
};

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
};

beforeEach(() => {
  jest.useFakeTimers();
  mockPlayers = [];
  mockCreateAudioPlayer.mockClear();
  // Reset the module-level player: a different URL each time forces a fresh one.
  setOfferSoundUrl(null);
  setOfferSoundUrl(URL_A); // creates exactly one player: mockPlayers[0]
});

afterEach(() => {
  jest.useRealTimers();
});

describe('Android, app not in front', () => {
  it('releases the player on stop() so the OS cannot resume the tone on return', async () => {
    setEnv('android', 'background');
    const { result, unmount } = renderHook(() => useRideOfferSound());
    act(() => result.current.play());
    await flush();
    const player = mockPlayers[0];
    expect(player.play).toHaveBeenCalled();

    act(() => result.current.stop());
    expect(player.pause).toHaveBeenCalled();
    expect(player.remove).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('rebuilds the SAME (admin-uploaded) sound for the next offer, not the bundled one', async () => {
    setEnv('android', 'background');
    const { result, unmount } = renderHook(() => useRideOfferSound());
    act(() => result.current.play());
    await flush();
    act(() => result.current.stop());
    mockCreateAudioPlayer.mockClear();

    act(() => result.current.play());
    await flush();
    expect(mockCreateAudioPlayer).toHaveBeenCalledWith({ uri: URL_A });
    unmount();
  });

  it('follows a changed sound URL after a release', async () => {
    setEnv('android', 'background');
    const { result, unmount } = renderHook(() => useRideOfferSound());
    act(() => result.current.play());
    await flush();
    act(() => result.current.stop());
    mockCreateAudioPlayer.mockClear();

    setOfferSoundUrl(URL_B);
    expect(mockCreateAudioPlayer).toHaveBeenCalledWith({ uri: URL_B });
    act(() => result.current.play());
    await flush();
    expect(mockPlayers[mockPlayers.length - 1].play).toHaveBeenCalled();
    unmount();
  });

  it('a stop() with no player yet does not throw', () => {
    setEnv('android', 'background');
    setOfferSoundUrl(null); // no remote player; bundled one is created lazily
    const { result, unmount } = renderHook(() => useRideOfferSound());
    expect(() => act(() => result.current.stop())).not.toThrow();
    unmount();
  });
});

describe('everything else is unchanged', () => {
  it('Android with the app in front keeps the warm player (in-app accept/decline)', async () => {
    setEnv('android', 'active');
    const { result, unmount } = renderHook(() => useRideOfferSound());
    act(() => result.current.play());
    await flush();
    const player = mockPlayers[0];

    act(() => result.current.stop());
    expect(player.pause).toHaveBeenCalled();
    expect(player.remove).not.toHaveBeenCalled();
    unmount();
  });

  it('iOS never releases the player, even in the background', async () => {
    setEnv('ios', 'background');
    const { result, unmount } = renderHook(() => useRideOfferSound());
    act(() => result.current.play());
    await flush();
    const player = mockPlayers[0];

    act(() => result.current.stop());
    expect(player.pause).toHaveBeenCalled();
    expect(player.remove).not.toHaveBeenCalled();
    unmount();
  });
});
