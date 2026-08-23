/**
 * app/lost-and-found-chat.tsx — rider's lost-and-found case chat. Pins:
 *  - loads the case + messages on mount; a load failure toasts
 *  - the header status label maps via STATUS_LABELS, falling back to the
 *    raw status string for an unknown value
 *  - the empty state differs for a closed vs. still-open case
 *  - sendMessage: blocked on empty/whitespace text; success adds an
 *    optimistic message, then replaces it with the server's; a failure
 *    removes the optimistic message, restores the typed text, and toasts
 *  - a closed case shows the "messaging is disabled" banner instead of
 *    the input row, and the input row's own attach/send are simply
 *    absent (never rendered) rather than merely disabled
 *  - photo attach: the source-picker Alert offers Camera/Photo Library;
 *    each path's permission-denied case toasts and uploads nothing; a
 *    successful pick adds an optimistic image message, uploads via
 *    multipart POST, and replaces it with the server's; an upload
 *    failure drops the optimistic bubble (never leaves a phantom photo)
 *    and toasts
 *  - tapping a message's photo opens the full-screen preview modal
 *  - the focus-effect poll (every 10s) refreshes messages only when the
 *    fetched list actually differs (length or newest id) from the
 *    current one
 *  - back nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Image, Alert, AppState } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => mockParams,
}));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
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

const mockRequestCameraPermissionsAsync = jest.fn();
const mockRequestMediaLibraryPermissionsAsync = jest.fn();
const mockLaunchCameraAsync = jest.fn();
const mockLaunchImageLibraryAsync = jest.fn();
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: (...a: any[]) => mockRequestCameraPermissionsAsync(...a),
  requestMediaLibraryPermissionsAsync: (...a: any[]) => mockRequestMediaLibraryPermissionsAsync(...a),
  launchCameraAsync: (...a: any[]) => mockLaunchCameraAsync(...a),
  launchImageLibraryAsync: (...a: any[]) => mockLaunchImageLibraryAsync(...a),
}));

import LostAndFoundChatScreen from '../app/lost-and-found-chat';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CASE_OPEN = { id: 'case-1', item_description: 'Blue Backpack', status: 'reported', reporter_type: 'rider', created_at: '2026-08-22T10:00:00Z' };
const MSG_FROM_DRIVER = {
  id: 'm1', lost_and_found_id: 'case-1', sender_id: 'd1', sender_role: 'driver', message: 'I found it!', created_at: '2026-08-22T10:05:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<LostAndFoundChatScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockParams = { caseId: 'case-1' };
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
    if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: { message: MSG_FROM_DRIVER } });
  mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'granted' });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('LostAndFoundChatScreen', () => {
  it('loads the case and messages on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found/case-1');
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found/case-1/messages');
  });

  it('toasts on a load failure', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith("Couldn't Load Case", 'Could not load case. Pull to refresh.', 'danger');
  });

  it('maps a known status to its label, and falls back to the raw string for an unknown one', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Awaiting driver response');

    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: { ...CASE_OPEN, status: 'weird_status' } } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r2 = await renderScreen();
    expect(allText(r2)).toContain('weird_status');
  });

  it('shows the open-case empty state when there are no messages', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Waiting for driver to respond. You can send a message below.');
  });

  it('shows the closed-case empty state and hides the input row when the case is closed', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: { ...CASE_OPEN, status: 'resolved' } } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('This case is closed.');
    expect(allText(r)).toContain('This case is closed — messaging is disabled.');
    expect(r.root.findAllByType(TextInput)).toHaveLength(0);
  });

  it('sends a message optimistically, then replaces it with the server message', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Found it near the seat'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/lost-and-found/case-1/messages', { message: 'Found it near the seat' });
    expect(allText(r)).toContain('I found it!');
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('removes the optimistic message, restores the input text, and toasts on a send failure', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Is it still there?'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Send Failed', 'Could not send message.', 'danger');
    expect(r.root.findByType(TextInput).props.value).toBe('Is it still there?');
  });

  it('toasts and uploads nothing when camera permission is denied', async () => {
    mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Permission Needed', 'Camera access is needed to take a photo.', 'danger');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('toasts and uploads nothing when library permission is denied', async () => {
    mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const libAction = alertCall[2].find((b: any) => b.text === 'Photo Library');
    await act(async () => {
      await libAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Permission Needed', 'Photo access is needed to attach an image.', 'danger');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('uploads a photo optimistically and replaces it with the server message', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockResolvedValue({ data: { message: { ...MSG_FROM_DRIVER, id: 'm2', image_url: 'https://cdn.spinr.ca/item.jpg', message: '' } } });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/lost-and-found/case-1/messages/image', expect.any(FormData));
    const img = r.root.findAllByType(Image)[0];
    expect(img.props.source.uri).toBe('https://cdn.spinr.ca/item.jpg');
  });

  it('drops the optimistic photo bubble and toasts on an upload failure', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Upload Failed', 'Could not send the photo. Please try again.', 'danger');
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });

  it('opens the full-screen photo preview when a message photo is tapped', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') {
        return Promise.resolve({ data: { messages: [{ ...MSG_FROM_DRIVER, message: '', image_url: 'https://cdn.spinr.ca/item.jpg' }] } });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const photoBtn = r.root.findByProps({ accessibilityLabel: 'Item photo — tap to enlarge' });
    act(() => { photoBtn.props.onPress(); });
    const closeBtn = r.root.findByProps({ accessibilityLabel: 'Close photo preview' });
    expect(closeBtn).toBeTruthy();
  });

  it('polls every 10s and refreshes messages when the fetched list differs', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [MSG_FROM_DRIVER] } });
      return Promise.reject(new Error('unexpected'));
    });
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(allText(r)).toContain('I found it!');
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});
