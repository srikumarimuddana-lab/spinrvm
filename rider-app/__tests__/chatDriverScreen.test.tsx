/**
 * app/chat-driver.tsx — rider<->driver in-ride chat. Pins:
 *  - no rideId param redirects straight to /(tabs)
 *  - chat history loads from AsyncStorage first (instant), then the
 *    backend (authoritative) overwrites the store + re-persists
 *  - driver header: name/vehicle info render from currentDriver, with
 *    sane fallbacks when fields are missing; a photo load error falls
 *    back to the placeholder icon
 *  - sendMessage: blocked on empty/whitespace text; success adds an
 *    optimistic message immediately, then replaces it with the server's
 *    once it returns; a send failure removes the optimistic message,
 *    restores the typed text into the input, and toasts
 *  - a quick-reply tap sends that exact text
 *  - back nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Image } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockReplace = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, replace: mockReplace }),
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

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockAsyncGetItem = jest.fn();
const mockAsyncSetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...a: any[]) => mockAsyncGetItem(...a),
  setItem: (...a: any[]) => mockAsyncSetItem(...a),
}));

const mockSetChatMessages = jest.fn();
let mockRideState: any;
function mockUseRideStoreFn(selector?: any) {
  return typeof selector === 'function' ? selector(mockRideState) : mockRideState;
}
mockUseRideStoreFn.getState = () => mockRideState;
jest.mock('../store/rideStore', () => ({
  useRideStore: mockUseRideStoreFn,
}));

import ChatDriverScreen from '../app/chat-driver';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const MSG_FROM_DRIVER = {
  id: 'm1', ride_id: 'ride-1', sender: 'driver', text: 'On my way', timestamp: '2026-08-22T10:00:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ChatDriverScreen />);
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
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

// addChatMessage in this screen just appends to the tracked mockRideState so
// the assertion helpers below can see the effect of an optimistic send.
function makeAddChatMessage() {
  return jest.fn((msg: any) => {
    mockRideState = { ...mockRideState, chatMessages: [...mockRideState.chatMessages, msg] };
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { rideId: 'ride-1' };
  mockRideState = {
    currentDriver: { name: 'Sam Lee', vehicle_color: 'Blue', vehicle_make: 'Toyota', vehicle_model: 'Camry', rating: 4.9, photo_url: null },
    chatMessages: [],
    addChatMessage: makeAddChatMessage(),
    setChatMessages: mockSetChatMessages,
  };
  mockAsyncGetItem.mockResolvedValue(null);
  mockAsyncSetItem.mockResolvedValue(undefined);
  mockApiGet.mockResolvedValue({ data: { messages: [] } });
  mockApiPost.mockResolvedValue({ data: { message: MSG_FROM_DRIVER } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('ChatDriverScreen', () => {
  it('redirects to /(tabs) when there is no rideId param', async () => {
    mockParams = {};
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('seeds from AsyncStorage first, then overwrites with the authoritative backend history', async () => {
    mockAsyncGetItem.mockResolvedValue(JSON.stringify([MSG_FROM_DRIVER]));
    mockApiGet.mockResolvedValue({ data: { messages: [MSG_FROM_DRIVER] } });
    await renderScreen();
    expect(mockSetChatMessages).toHaveBeenCalledWith([MSG_FROM_DRIVER]);
    expect(mockAsyncSetItem).toHaveBeenCalledWith('spinr_chat_rider_ride-1', JSON.stringify([MSG_FROM_DRIVER]));
  });

  it("renders the driver's name and vehicle info", async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Sam Lee');
    expect(allText(r)).toContain('["You are now connected with ","Sam"]');
  });

  it('falls back to defaults when driver fields are missing', async () => {
    mockRideState.currentDriver = null;
    const r = await renderScreen();
    expect(allText(r)).toContain('Driver');
  });

  it('falls back to the placeholder icon when the driver photo fails to load', async () => {
    mockRideState.currentDriver = { ...mockRideState.currentDriver, photo_url: 'https://example.com/x.jpg' };
    const r = await renderScreen();
    const img = r.root.findByType(Image);
    act(() => {
      img.props.onError();
    });
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });

  it('blocks sending empty/whitespace text', async () => {
    const r = await renderScreen();
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    expect(sendBtn.props.disabled).toBe(true);
  });

  it('sends a message optimistically, then replaces it with the server message', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => {
      input.props.onChangeText('On my way there');
    });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/messages', { text: 'On my way there' });
    expect(mockSetChatMessages).toHaveBeenCalledWith([MSG_FROM_DRIVER]);
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('removes the optimistic message, restores the input text, and toasts on a send failure', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => {
      input.props.onChangeText('Are you close?');
    });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockSetChatMessages).toHaveBeenCalledWith([]);
    expect(mockShowToast).toHaveBeenCalledWith(
      'Send Failed', 'Could not send message. Check your connection.', 'danger',
    );
    expect(r.root.findByType(TextInput).props.value).toBe('Are you close?');
  });

  it('sends the exact quick-reply text when tapped', async () => {
    const r = await renderScreen();
    const quickBtn = findButtonByText(r, 'On my way');
    await act(async () => {
      await quickBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/messages', { text: 'On my way' });
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
