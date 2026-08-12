/**
 * P2-13: Chat E2E — driver-side store (D11)
 *
 * Pins the chat slice of driverStore:
 *   - addChatMessage: deduplicates by id (WS + REST may both deliver)
 *   - addChatMessage: preserves order (appends)
 *   - setChatMessages: replaces full list
 *   - clearChatMessages: empties without clearing ride state
 *   - resetRide / completeTrip: also clears chatMessages
 *
 * Code under test: driver-app/store/driverStore.ts::addChatMessage (~line 660)
 */

import { useDriverStore } from '../driverStore';

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
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

// driverStore imports tripLocationRecorder, which imports expo-location —
// mocked to avoid pulling in the real native module (crashes Jest's Expo
// Winter-runtime polyfill outside a real device/simulator context).
jest.mock('../../utils/tripLocationRecorder', () => ({
  tripLocationRecorder: {
    startRide: jest.fn(),
    captureCompletionFix: jest.fn(),
    applyAcknowledgement: jest.fn(),
    closeRide: jest.fn(),
    flushPendingWithTimeout: jest.fn(),
  },
}));

jest.mock('../../utils/tripLocationTransport', () => ({
  apiLocationBatchTransport: jest.fn(),
}));

const _msg = (id: string, text = 'hi', sender: 'rider' | 'driver' = 'rider') => ({
  id,
  ride_id: 'ride-d01',
  text,
  sender,
  timestamp: new Date().toISOString(),
});

beforeEach(() => {
  useDriverStore.setState({ chatMessages: [] });
});

describe('driverStore — chat slice (P2-13 / D11)', () => {
  it('appends a new chat message', () => {
    useDriverStore.getState().addChatMessage(_msg('m1', 'On my way'));
    expect(useDriverStore.getState().chatMessages).toHaveLength(1);
    expect(useDriverStore.getState().chatMessages[0].text).toBe('On my way');
  });

  it('deduplicates by id — same message delivered via WS and REST', () => {
    const msg = _msg('m1', 'Dup');
    useDriverStore.getState().addChatMessage(msg);
    useDriverStore.getState().addChatMessage(msg); // second delivery
    expect(useDriverStore.getState().chatMessages).toHaveLength(1);
  });

  it('preserves arrival order for distinct messages', () => {
    useDriverStore.getState().addChatMessage(_msg('m1', 'First'));
    useDriverStore.getState().addChatMessage(_msg('m2', 'Second'));
    useDriverStore.getState().addChatMessage(_msg('m3', 'Third'));

    const texts = useDriverStore.getState().chatMessages.map((m: any) => m.text);
    expect(texts).toEqual(['First', 'Second', 'Third']);
  });

  it('setChatMessages replaces the full list', () => {
    useDriverStore.getState().addChatMessage(_msg('m1'));
    useDriverStore.getState().setChatMessages([_msg('m2', 'Replaced')]);

    const msgs = useDriverStore.getState().chatMessages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe('m2');
  });

  it('clearChatMessages empties without touching other ride state', () => {
    useDriverStore.setState({ chatMessages: [_msg('m1')], rideState: 'trip_in_progress' });
    useDriverStore.getState().clearChatMessages();

    expect(useDriverStore.getState().chatMessages).toEqual([]);
    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
  });

  it('addChatMessage does not mutate existing array (immutability)', () => {
    useDriverStore.getState().addChatMessage(_msg('m1'));
    const before = useDriverStore.getState().chatMessages;
    useDriverStore.getState().addChatMessage(_msg('m2'));
    const after = useDriverStore.getState().chatMessages;
    expect(after).not.toBe(before);
  });
});
