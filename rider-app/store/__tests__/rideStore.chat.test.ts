/**
 * P2-13: Chat E2E — rider-side store (R7)
 *
 * Pins the chat slice of rideStore:
 *   - addChatMessage: deduplicates by id (WS + REST may both deliver)
 *   - addChatMessage: preserves order (appends)
 *   - setChatMessages: replaces full list
 *   - clearRide: resets chatMessages to []
 *   - ChatMessage type has ride_id, timestamp, and strict sender union
 *
 * Code under test: rider-app/store/rideStore.ts::addChatMessage
 */

import { useRideStore } from '../rideStore';

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

  it('ChatMessage carries ride_id and timestamp fields', () => {
    const msg = _msg('m1', 'check fields');
    useRideStore.getState().addChatMessage(msg);
    const stored = useRideStore.getState().chatMessages[0];
    expect(stored.ride_id).toBe('ride-001');
    expect(stored.timestamp).toBeTruthy();
    expect(typeof stored.timestamp).toBe('string');
  });

  it('sender is preserved correctly for both roles', () => {
    useRideStore.getState().addChatMessage(_msg('r1', 'from rider', 'rider'));
    useRideStore.getState().addChatMessage(_msg('d1', 'from driver', 'driver'));
    const [r, d] = useRideStore.getState().chatMessages;
    expect(r.sender).toBe('rider');
    expect(d.sender).toBe('driver');
  });

  it('setChatMessages with multiple messages updates the full list', () => {
    const batch = [_msg('b1', 'one'), _msg('b2', 'two'), _msg('b3', 'three')];
    useRideStore.getState().setChatMessages(batch);
    expect(useRideStore.getState().chatMessages).toHaveLength(3);
  });

  it('deduplication does not affect messages with distinct ids', () => {
    for (let i = 0; i < 5; i++) {
      useRideStore.getState().addChatMessage(_msg(`m${i}`, `msg ${i}`));
    }
    expect(useRideStore.getState().chatMessages).toHaveLength(5);
  });
});
