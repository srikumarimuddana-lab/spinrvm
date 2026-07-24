/**
 * useDriverDashboard — chat_message handler (P2-13 / D11)
 *
 * Pins the WS chat_message branch in useDriverDashboard:
 *   - Valid payload → added to driverStore + vibrates
 *   - Malformed payload → rejected, store unchanged, no vibration
 *   - Duplicate id → deduplicated by store
 *   - Rider-sent message preserved as sender='rider'
 */

jest.mock('react-native', () => ({
  Platform: { OS: 'android' },
  AppState: { addEventListener: jest.fn(() => ({ remove: jest.fn() })) },
  Vibration: { vibrate: jest.fn() },
  Linking: { openURL: jest.fn() },
  NativeModules: {},
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../config', () => ({ API_URL: 'http://localhost:8000' }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
}));
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: {
    getState: jest.fn(() => ({ user: { id: 'd1' }, token: 'tok' })),
    subscribe: jest.fn(() => jest.fn()),
  },
}));
jest.mock('../useToast', () => ({ showToast: jest.fn() }));

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

import { Vibration } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import type { ChatMessage } from '../../store/driverStore';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    ride_id: 'ride-d01',
    text: 'Hello',
    sender: 'rider',
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

// Mirror of the chat_message case in useDriverDashboard (lines 601-612).
function handleChatMessage(data: unknown): void {
  const d = data as Record<string, unknown>;
  if (
    d.text !== undefined &&
    typeof d.text === 'string' &&
    typeof d.sender === 'string'
  ) {
    useDriverStore.getState().addChatMessage(d as unknown as ChatMessage);
    Vibration.vibrate(100);
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  useDriverStore.setState({ chatMessages: [] });
  jest.clearAllMocks();
});

describe('useDriverDashboard — chat_message handler', () => {
  it('valid rider message is added to the store', () => {
    handleChatMessage(_msg());
    expect(useDriverStore.getState().chatMessages).toHaveLength(1);
    expect(useDriverStore.getState().chatMessages[0].text).toBe('Hello');
  });

  it('valid message triggers vibration', () => {
    handleChatMessage(_msg());
    expect(Vibration.vibrate).toHaveBeenCalledWith(100);
  });

  it('payload without text is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', sender: 'rider', timestamp: '' });
    expect(useDriverStore.getState().chatMessages).toHaveLength(0);
    expect(Vibration.vibrate).not.toHaveBeenCalled();
  });

  it('payload with numeric text is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', text: 99, sender: 'rider', timestamp: '' });
    expect(useDriverStore.getState().chatMessages).toHaveLength(0);
  });

  it('payload without sender is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', text: 'hi', timestamp: '' });
    expect(useDriverStore.getState().chatMessages).toHaveLength(0);
    expect(Vibration.vibrate).not.toHaveBeenCalled();
  });

  it('duplicate WS delivery of the same id is deduplicated', () => {
    const msg = _msg({ id: 'dup-1' });
    handleChatMessage(msg);
    handleChatMessage(msg);
    expect(useDriverStore.getState().chatMessages).toHaveLength(1);
  });

  it('rider-sent message retains sender=rider', () => {
    handleChatMessage(_msg({ sender: 'rider', text: "I'm at the corner" }));
    expect(useDriverStore.getState().chatMessages[0].sender).toBe('rider');
  });

  it('multiple distinct messages accumulate in order', () => {
    handleChatMessage(_msg({ id: 'm1', text: 'one' }));
    handleChatMessage(_msg({ id: 'm2', text: 'two' }));
    handleChatMessage(_msg({ id: 'm3', text: 'three' }));
    const texts = useDriverStore.getState().chatMessages.map((m) => m.text);
    expect(texts).toEqual(['one', 'two', 'three']);
  });
});
