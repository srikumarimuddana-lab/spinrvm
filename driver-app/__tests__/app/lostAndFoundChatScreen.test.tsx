/**
 * app/driver/lost-and-found-chat.tsx — driver-side lost & found. Pins:
 *  - caseId mode: loads the case + messages on mount; a load failure toasts
 *  - rideId mode (no case yet): shows the "Report Found Item" form; a
 *    driver-created case then loads normally
 *  - the 'reported'/'driver_notified' statuses show the Found/Not Found
 *    respond banner; every other status collapses straight to chat
 *  - respond(): confirms via Alert, PUTs found/not_found, then reloads
 *    the case; a failure toasts without crashing
 *  - sendMessage: blocked on empty text; optimistic add replaced by the
 *    server message; a failure removes the optimistic bubble, restores
 *    the typed text, and toasts
 *  - a closed case shows the "closed" banner and hides the input row
 *  - photo attach: camera/library permission-denied toasts and uploads
 *    nothing; a successful pick uploads via multipart POST and replaces
 *    the optimistic bubble; an upload failure drops the optimistic
 *    bubble and toasts
 *  - tapping a message photo opens the full-screen preview modal
 *  - the focus-effect poll (every 10s) refreshes messages only when the
 *    fetched list actually differs
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
    // Depend on `cb` itself (as the real focus-effect hook does), not a
    // fixed [] — this screen's interval callback is memoized on
    // activeCaseId (derived from lostCase state, unset at mount), so a
    // fixed-deps mock would never re-arm the poll once the case loads.
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), [cb]);
    },
  };
});

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiPut = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    post: (...a: any[]) => mockApiPost(...a),
    put: (...a: any[]) => mockApiPut(...a),
  },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

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

import DriverLostAndFoundChatScreen from '../../app/driver/lost-and-found-chat';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CASE_AWAITING = { id: 'case-1', item_description: 'Blue Backpack', status: 'reported', reporter_type: 'rider' };
const CASE_CHAT = { id: 'case-1', item_description: 'Blue Backpack', status: 'in_progress', reporter_type: 'rider' };
const MSG_FROM_RIDER = {
  id: 'm1', lost_and_found_id: 'case-1', sender_id: 'r1', sender_role: 'rider', message: 'Did you find my bag?', created_at: '2026-08-22T10:05:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DriverLostAndFoundChatScreen />);
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
    if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_CHAT } });
    if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: { message: MSG_FROM_RIDER } });
  mockApiPut.mockResolvedValue({ data: {} });
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

describe('DriverLostAndFoundChatScreen', () => {
  it('loads the case and messages on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found/case-1');
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found/case-1/messages');
  });

  it('toasts on a load failure', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Could not load case.', 'Pull to refresh.');
  });

  it('shows the Found/Not Found respond banner for an awaiting-response status', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_AWAITING } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Is this item in your vehicle?');
    expect(allText(r)).toContain('Respond above, then you can chat with the rider.');
  });

  it('hides the respond banner once the case has moved past awaiting-response', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Is this item in your vehicle?');
  });

  it('shows the report-found-item form in rideId mode with no existing case', async () => {
    mockParams = { rideId: 'ride-9' };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found') return Promise.resolve({ data: { cases: [] } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Report Found Item');
    expect(allText(r)).toContain('What did you find?');
  });

  it('submits a driver report, then loads the newly created case', async () => {
    mockParams = { rideId: 'ride-9' };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found') return Promise.resolve({ data: { cases: [] } });
      if (url === '/lost-and-found/case-new') return Promise.resolve({ data: { case: CASE_CHAT } });
      if (url === '/lost-and-found/case-new/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    mockApiPost.mockResolvedValue({ data: { case: { ...CASE_CHAT, id: 'case-new' } } });
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Black wallet under the seat'); });
    const submitBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children).includes('Submit & Open Chat'); } catch { return false; }
      })
    )!;
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/lost-and-found/driver-report', {
      ride_id: 'ride-9', item_description: 'Black wallet under the seat', item_category: 'other',
    });
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found/case-new');
  });

  it('confirms found via Alert, PUTs found=true, then reloads the case', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_AWAITING } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const yesBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children).includes('Yes, I have it'); } catch { return false; }
      })
    )!;
    act(() => { yesBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const yesAction = alertCall[2].find((b: any) => b.text === 'Yes');
    await act(async () => { await yesAction.onPress(); await flush(); });
    expect(mockApiPut).toHaveBeenCalledWith('/lost-and-found/case-1/respond', { found: true });
  });

  it('toasts without crashing when respond() fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_AWAITING } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPut.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    const noBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children).includes('Not in my vehicle'); } catch { return false; }
      })
    )!;
    act(() => { noBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const yesAction = alertCall[2].find((b: any) => b.text === 'Yes');
    await act(async () => { await yesAction.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Update Failed', 'Could not update status. Please try again.');
  });

  it('sends a message optimistically, then replaces it with the server message', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('On my way to drop it off'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/lost-and-found/case-1/messages', { message: 'On my way to drop it off' });
    expect(allText(r)).toContain('Did you find my bag?');
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('removes the optimistic message, restores the input text, and toasts on a send failure', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Still looking'); });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Send Failed', 'Could not send message. Please try again.');
    expect(r.root.findByType(TextInput).props.value).toBe('Still looking');
  });

  it('shows the closed banner and hides the input row when the case is closed', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: { ...CASE_CHAT, status: 'resolved' } } });
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('This case is closed.');
    expect(r.root.findAllByType(TextInput)).toHaveLength(0);
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
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Permission Needed', 'Camera access is needed to take a photo.');
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
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Permission Needed', 'Photo access is needed to attach an image.');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('uploads a photo optimistically and replaces it with the server message', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://item.jpg' }] });
    mockApiPost.mockResolvedValue({ data: { message: { ...MSG_FROM_RIDER, id: 'm2', image_url: 'https://cdn.spinr.ca/item.jpg', message: '' } } });
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
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Upload Failed', 'Could not send the photo. Please try again.');
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });

  it('opens the full-screen photo preview when a message photo is tapped', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1') return Promise.resolve({ data: { case: CASE_CHAT } });
      if (url === '/lost-and-found/case-1/messages') {
        return Promise.resolve({ data: { messages: [{ ...MSG_FROM_RIDER, message: '', image_url: 'https://cdn.spinr.ca/item.jpg' }] } });
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
    const r = await renderScreen();
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/lost-and-found/case-1/messages') return Promise.resolve({ data: { messages: [MSG_FROM_RIDER] } });
      return Promise.reject(new Error('unexpected'));
    });
    await act(async () => {
      jest.advanceTimersByTime(10000);
      await flush();
    });
    expect(allText(r)).toContain('Did you find my bag?');
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});
