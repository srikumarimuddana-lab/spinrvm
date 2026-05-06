/**
 * P2-13: Chat E2E — rider-side store (R7)
 *
 * Pins the chat slice of rideStore:
 *   - addChatMessage: deduplicates by id (WS + REST may both deliver)
 *   - addChatMessage: preserves order (appends)
 *   - setChatMessages: replaces full list
 *   - clearRide: resets chatMessages to []
 *
 * Code under test: rider-app/store/rideStore.ts::addChatMessage (~line 540)
 */

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
  Alert: { alert: jest.fn() },
  Linking: { openURL: jest.fn() },
  NativeModules: {},
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: null, token: null })) },
}));

import { useRideStore } from '../rideStore';

const _msg = (id: string, text = 'hi', sender: 'rider' | 'driver' = 'rider') => ({
  id,
  ride_id: 'ride-001',
  text,
  sender,
  timestamp: new Date().toISOString(),
});

beforeEach(() => {
  useRideStore.setState({ chatMessages: [] });
});

describe('rideStore — chat slice (P2-13 / R7)', () => {
  it('appends a new chat message', () => {
    useRideStore.getState().addChatMessage(_msg('m1', 'Hello'));
    expect(useRideStore.getState().chatMessages).toHaveLength(1);
    expect(useRideStore.getState().chatMessages[0].text).toBe('Hello');
  });

  it('deduplicates by id — same message delivered via WS and REST', () => {
    const msg = _msg('m1', 'Dup');
    useRideStore.getState().addChatMessage(msg);
    useRideStore.getState().addChatMessage(msg); // second delivery
    expect(useRideStore.getState().chatMessages).toHaveLength(1);
  });

  it('preserves arrival order for distinct messages', () => {
    useRideStore.getState().addChatMessage(_msg('m1', 'First'));
    useRideStore.getState().addChatMessage(_msg('m2', 'Second'));
    useRideStore.getState().addChatMessage(_msg('m3', 'Third'));

    const texts = useRideStore.getState().chatMessages.map((m: any) => m.text);
    expect(texts).toEqual(['First', 'Second', 'Third']);
  });

  it('setChatMessages replaces the full list', () => {
    useRideStore.getState().addChatMessage(_msg('m1'));
    useRideStore.getState().setChatMessages([_msg('m2', 'Replaced')]);

    const msgs = useRideStore.getState().chatMessages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe('m2');
  });

  it('clearRide resets chatMessages to empty', () => {
    useRideStore.getState().addChatMessage(_msg('m1'));
    useRideStore.getState().clearRide();
    expect(useRideStore.getState().chatMessages).toEqual([]);
  });

  it('addChatMessage does not mutate existing array (immutability)', () => {
    useRideStore.getState().addChatMessage(_msg('m1'));
    const before = useRideStore.getState().chatMessages;
    useRideStore.getState().addChatMessage(_msg('m2'));
    const after = useRideStore.getState().chatMessages;
    expect(after).not.toBe(before);
  });
});
