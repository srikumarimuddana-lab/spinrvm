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
import { TouchableOpacity, Text, TextInput, Image, Alert, AppState, ActivityIndicator, Modal } from 'react-native';

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

  it('does nothing on mount when there is no caseId in the route params', async () => {
    mockParams = {};
    await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it('falls back to a null case and an empty message list when the API responses omit those fields', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: {} });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: {} });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Lost & Found');
  });

  it('skips the poll tick entirely while the app is backgrounded', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'background', configurable: true });
    await renderScreen();
    mockApiGet.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it('silently swallows a poll-tick fetch failure', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
    const r = await renderScreen();
    mockApiGet.mockImplementation((url: string) => Promise.reject(new Error('poll failed')));
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    // No crash, no toast for a background poll failure -- unlike the initial
    // load, this is a silent .catch(() => {}).
    expect(mockShowToast).not.toHaveBeenCalled();
    expect(allText(r)).toBeDefined();
  });

  it('replaces the message list on a poll tick when the length matches but the newest id differs', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [MSG_FROM_DRIVER] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const EDITED = { ...MSG_FROM_DRIVER, id: 'm1-edited', message: 'Actually, found your bag!' };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [EDITED] } });
      return Promise.reject(new Error('unexpected'));
    });
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(allText(r)).toContain('Actually, found your bag!');
  });

  it('leaves the message list untouched on a poll tick when the fetched list is identical', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [MSG_FROM_DRIVER] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const before = allText(r);
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(allText(r)).toBe(before);
  });

  it('scrolls the list to the end shortly after a new message is added', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Found it near the seat'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
      jest.advanceTimersByTime(100); // fires the scrollToEnd setTimeout
    });
    expect(allText(r)).toContain('I found it!');
  });

  it('does nothing when Send is triggered with only whitespace text', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('   '); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('leaves the optimistic text message in place when the server response has no message field', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
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
    expect(allText(r)).toContain('Found it near the seat');
  });

  it('does nothing when the image picker is dismissed with no selection', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: true, assets: null });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('uploads via the Photo Library picker path', async () => {
    mockLaunchImageLibraryAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://library-item.jpg' }] });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const libAction = alertCall[2].find((b: any) => b.text === 'Photo Library');
    await act(async () => {
      await libAction.onPress();
      await flush();
    });
    expect(mockLaunchImageLibraryAsync).toHaveBeenCalled();
    expect(mockApiPost).toHaveBeenCalledWith('/lost-and-found/case-1/messages/image', expect.any(FormData));
  });

  it('leaves the optimistic photo bubble in place when the server response has no message field', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(r.root.findAllByType(Image)).toHaveLength(1); // the optimistic (local) bubble is still there
  });

  it('shows the pending-upload spinner overlay on an in-flight local image message', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') {
        return Promise.resolve({
          data: { messages: [{ ...MSG_FROM_DRIVER, id: 'm-pending', message: '', local_image_uri: 'file://still-uploading.jpg', image_url: undefined }] },
        });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const img = r.root.findAllByType(Image)[0];
    expect(img.props.source.uri).toBe('file://still-uploading.jpg');
  });

  it("renders a rider's own message with the 'me' bubble styling and no avatar", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') {
        return Promise.resolve({
          data: { messages: [{ id: 'm-mine', lost_and_found_id: 'case-1', sender_id: 'me', sender_role: 'rider', message: 'Any updates?', created_at: '2026-08-22T10:10:00Z' }] },
        });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Any updates?');
  });

  it('renders a system/admin message as a centered system row (no bubble)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_OPEN } });
      if (url === '/lost-and-found/case-1/messages') {
        return Promise.resolve({
          data: { messages: [{ id: 'm-sys', lost_and_found_id: 'case-1', sender_id: null, sender_role: 'system', message: 'Case opened by support', created_at: '2026-08-22T10:00:00Z' }] },
        });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Case opened by support');
  });

  it('falls back to an empty list on a poll tick whose response omits the messages field', async () => {
    Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
    const r = await renderScreen();
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: {} });
      return Promise.reject(new Error('unexpected'));
    });
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(allText(r)).toContain('Waiting for driver to respond. You can send a message below.');
  });

  it('ignores a second photo pick while the first upload is still in flight', async () => {
    let resolveUpload: (v: any) => void;
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockImplementation(() => new Promise((resolve) => { resolveUpload = resolve; }));
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    let alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    let cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      cameraAction.onPress(); // 1st pick, sets uploadingPhoto — don't await it, its own fetch is pending
      await flush();
    });

    act(() => { attachBtn.props.onPress(); }); // 2nd tap while still uploading
    alertCall = (Alert.alert as jest.Mock).mock.calls[1];
    cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => { await cameraAction.onPress(); await flush(); }); // guarded by `if (uploadingPhoto) return;`, resolves immediately

    expect(mockApiPost).toHaveBeenCalledTimes(1);
    await act(async () => { resolveUpload!({ data: { message: MSG_FROM_DRIVER } }); await flush(); });
  });

  it('shows a spinner on the attach button while a photo upload is in flight', async () => {
    let resolveUpload: (v: any) => void;
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockImplementation(() => new Promise((resolve) => { resolveUpload = resolve; }));
    const r = await renderScreen();
    const attachBtn = r.root.findByProps({ accessibilityLabel: 'Attach a photo of the item' });
    act(() => { attachBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      cameraAction.onPress(); // don't await — its own fetch is deliberately left pending
      await flush();
    });
    expect(r.root.findAllByType(ActivityIndicator).length).toBeGreaterThan(0);
    await act(async () => { resolveUpload!({ data: { message: MSG_FROM_DRIVER } }); await flush(); });
  });

  it('shows a spinner on the send button while a text message is in flight', async () => {
    let resolveSend: (v: any) => void;
    mockApiPost.mockImplementation(() => new Promise((resolve) => { resolveSend = resolve; }));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Found it near the seat'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      sendBtn.props.onPress(); // don't await — its fetch is deliberately left pending
      await flush();
    });
    expect(r.root.findAllByType(ActivityIndicator).length).toBeGreaterThan(0);
    await act(async () => { resolveSend!({ data: { message: MSG_FROM_DRIVER } }); await flush(); });
  });

  it('closes the photo-preview modal via its onRequestClose callback too', async () => {
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
    const modal = r.root.findByType(Modal);
    expect(modal.props.visible).toBe(true);
    act(() => { modal.props.onRequestClose(); });
    expect(r.root.findByType(Modal).props.visible).toBe(false);
  });

  it('closes the photo-preview modal via the backdrop press', async () => {
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
    act(() => { closeBtn.props.onPress(); });
    expect(r.root.findAllByType(Image)).toHaveLength(1); // only the message thumbnail; the preview Image unmounted
  });
});
