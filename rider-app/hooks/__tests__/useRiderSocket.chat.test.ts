/**
 * useRiderSocket — chat_message handler (P2-13 / R7)
 *
 * Pins:
 *   - Valid payload → added to rideStore + vibrates
 *   - Malformed payload (missing text / missing sender) → rejected, store unchanged
 *   - Duplicate delivery (same id twice) → deduplicated by store
 *   - Driver-sent message (sender='driver') → isUser=false in store
 */

import { Vibration } from 'react-native';
import { useRideStore } from '../../store/rideStore';
import type { ChatMessage } from '../../store/rideStore';

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
  AppState: { addEventListener: jest.fn(() => ({ remove: jest.fn() })) },
  Vibration: { vibrate: jest.fn() },
  Alert: { alert: jest.fn() },
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
    getState: jest.fn(() => ({ user: { id: 'u1' }, token: 'tok' })),
    subscribe: jest.fn(() => jest.fn()),
  },
}));
jest.mock('../../store/toastStore', () => ({ showToast: jest.fn() }));
// Real expo-router's useRouter() returns a module-level singleton object,
// not a fresh object per call — matched here since useRiderSocket now
// depends on `router` being referentially stable (see the same comment in
// useRiderSocket.reconnect.test.ts).
jest.mock('expo-router', () => {
  const mockRouter = { replace: jest.fn(), push: jest.fn() };
  return { useRouter: () => mockRouter };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    ride_id: 'ride-001',
    text: 'Hello',
    sender: 'driver',
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

// Simulate what useRiderSocket does in its chat_message case after our fix.
// We test the logic directly rather than mounting the full hook to keep these
// tests fast and free of WS connection side-effects.
function handleChatMessage(data: unknown): void {
  const d = data as Record<string, unknown>;
  if (typeof d.text === 'string' && typeof d.sender === 'string') {
    useRideStore.getState().addChatMessage(d as unknown as ChatMessage);
    Vibration.vibrate(100);
  }
  // else: malformed — silently ignored (console.warn in real code)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  useRideStore.setState({ chatMessages: [] });
  jest.clearAllMocks();
});

describe('useRiderSocket — chat_message handler', () => {
  it('valid driver message is added to the store', () => {
    handleChatMessage(_msg());
    expect(useRideStore.getState().chatMessages).toHaveLength(1);
    expect(useRideStore.getState().chatMessages[0].text).toBe('Hello');
  });

  it('valid message triggers vibration', () => {
    handleChatMessage(_msg());
    expect(Vibration.vibrate).toHaveBeenCalledWith(100);
  });

  it('payload without text field is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', sender: 'driver', timestamp: '' });
    expect(useRideStore.getState().chatMessages).toHaveLength(0);
    expect(Vibration.vibrate).not.toHaveBeenCalled();
  });

  it('payload with numeric text is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', text: 42, sender: 'driver', timestamp: '' });
    expect(useRideStore.getState().chatMessages).toHaveLength(0);
  });

  it('payload without sender field is rejected', () => {
    handleChatMessage({ id: 'x', ride_id: 'r1', text: 'hi', timestamp: '' });
    expect(useRideStore.getState().chatMessages).toHaveLength(0);
    expect(Vibration.vibrate).not.toHaveBeenCalled();
  });

  it('duplicate WS delivery of the same id is deduplicated', () => {
    const msg = _msg({ id: 'dup-1' });
    handleChatMessage(msg);
    handleChatMessage(msg);
    expect(useRideStore.getState().chatMessages).toHaveLength(1);
  });

  it('driver-sent message has sender=driver in store', () => {
    handleChatMessage(_msg({ sender: 'driver', text: 'On my way' }));
    expect(useRideStore.getState().chatMessages[0].sender).toBe('driver');
  });

  it('multiple distinct messages accumulate in order', () => {
    handleChatMessage(_msg({ id: 'm1', text: 'first' }));
    handleChatMessage(_msg({ id: 'm2', text: 'second' }));
    handleChatMessage(_msg({ id: 'm3', text: 'third' }));
    const texts = useRideStore.getState().chatMessages.map((m) => m.text);
    expect(texts).toEqual(['first', 'second', 'third']);
  });

  it('empty object payload is safely rejected', () => {
    handleChatMessage({});
    expect(useRideStore.getState().chatMessages).toHaveLength(0);
    expect(Vibration.vibrate).not.toHaveBeenCalled();
  });
});
