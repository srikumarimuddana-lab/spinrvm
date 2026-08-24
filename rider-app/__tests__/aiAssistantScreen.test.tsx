/**
 * app/ai-assistant.tsx — AI chat screen. Pins:
 *  - loadConfig + loadHistory fire on mount
 *  - the welcome state (no messages) shows quick-prompt chips; tapping one
 *    sends it immediately
 *  - handleSend trims + clears input, then calls sendMessage; a blank/
 *    whitespace-only send is a no-op
 *  - the header back button and hardware back both route through
 *    activeRideRouteFor: an active ride replaces to its owning screen
 *    with rideId; no active ride falls through to router.back()
 *  - support-action bubbles: "cancel_ride" routes to the ride's own
 *    screen (or /ride-status with no live ride); "lost-and-found" routes
 *    there; anything else goes to /support
 *  - map_picker bubbles push to /pick-on-map with the field + optional
 *    approx coords, and are disabled once a newer turn has started
 *    (stale-card gating)
 *  - fare_quote/location_suggestions selections call handleSend with the
 *    self-contained follow-up message
 *  - RideStatusBanner: hidden with no active/trackable ride; shows
 *    "Searching…" / driver-found copy / arrived copy per status, and its
 *    Share/Track buttons
 *  - the disclaimer footer falls back to a default when the store has none
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Share, BackHandler } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
// expo-speech-recognition is an installed native module (not stubbed by the
// jest-expo preset) — the screen's own guarded `require()` only degrades
// gracefully when the module is genuinely absent from the build. Under
// Jest it IS present as a real package, and importing its real code
// touches expo's winter-runtime fetch polyfill at module-eval time, which
// throws `ReferenceError: You are trying to 'import' a file outside of
// the scope of the test code` before any test body even runs. Mock it so
// the screen's own require() resolves to this instead of the real native
// module.
const mockSpeechListeners: Record<string, (e: any) => void> = {};
const mockRequestPermissionsAsync = jest.fn();
const mockSpeechStart = jest.fn();
const mockSpeechStop = jest.fn();
jest.mock('expo-speech-recognition', () => ({
  ExpoSpeechRecognitionModule: {
    addListener: (event: string, cb: (e: any) => void) => {
      mockSpeechListeners[event] = cb;
      return { remove: jest.fn() };
    },
    requestPermissionsAsync: (...a: any[]) => mockRequestPermissionsAsync(...a),
    start: (...a: any[]) => mockSpeechStart(...a),
    stop: (...a: any[]) => mockSpeechStop(...a),
  },
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace }),
}));

const COLORS = {
  primary: '#EF4444', orange: '#F97316', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', background: '#FFF',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

let mockAuthUser: any = { first_name: 'Jamie' };
jest.mock('@shared/store/authStore', () => ({ useAuthStore: (sel: any) => sel({ user: mockAuthUser }) }));

let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((sel: any) => sel(mockRideState), { getState: () => mockRideState }),
}));

const mockSendMessage = jest.fn();
const mockStopStreaming = jest.fn();
const mockStartNewConversation = jest.fn();
const mockLoadHistory = jest.fn();
const mockLoadConfig = jest.fn();
let mockAiChatState: any;
jest.mock('../store/aiChatStore', () => ({
  useAiChatStore: (sel: any) => sel(mockAiChatState),
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

jest.mock('../components/BookingProposalCard', () => (props: any) => {
  const { Text: RNText } = require('react-native');
  return <RNText>{`BookingProposalCard:${props.proposal?.id ?? ''}`}</RNText>;
});
jest.mock('../components/bookingProposal', () => ({
  buildQuoteBookingMessage: (_quote: any, option: any) => `Book the ${option} option`,
}));
jest.mock('../components/FareQuoteCard', () => (props: any) => {
  const { TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
  return (
    <RNTouchableOpacity disabled={props.disabled} onPress={() => props.onSelect('economy')} accessibilityLabel="fare-quote-select">
      <RNText>Fare Quote</RNText>
    </RNTouchableOpacity>
  );
});
jest.mock('../components/AiAuroraBackground', () => () => null);
jest.mock('../components/AiWelcomeOrb', () => () => null);

import AiAssistantScreen from '../app/ai-assistant';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<AiAssistantScreen />);
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
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthUser = { first_name: 'Jamie' };
  mockRideState = { currentRide: null, currentDriver: null };
  mockAiChatState = {
    messages: [],
    isStreaming: false,
    toolStatus: null,
    disclaimer: null,
    sendMessage: mockSendMessage,
    stopStreaming: mockStopStreaming,
    startNewConversation: mockStartNewConversation,
    loadHistory: mockLoadHistory,
    loadConfig: mockLoadConfig,
  };
  mockApiGet.mockResolvedValue({ data: { share_url: 'https://spinr-track.app/ride-1' } });
  jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as any);
  mockRequestPermissionsAsync.mockResolvedValue({ granted: true });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('AiAssistantScreen', () => {
  it('loads config and history on mount', async () => {
    await renderScreen();
    expect(mockLoadConfig).toHaveBeenCalled();
    expect(mockLoadHistory).toHaveBeenCalled();
  });

  it('shows quick-prompt chips on the welcome state and sends one on tap', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain("Hi Jamie, let's get going");
    const chip = findButtonByText(r, "Where's my driver?");
    act(() => { chip.props.onPress(); });
    expect(mockSendMessage).toHaveBeenCalledWith("Where's my driver?");
  });

  it('trims and clears the input on send, and is a no-op for blank input', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('  hello there  '); });
    act(() => { input.props.onSubmitEditing(); });
    expect(mockSendMessage).toHaveBeenCalledWith('hello there');
    expect(r.root.findByType(TextInput).props.value).toBe('');

    mockSendMessage.mockClear();
    act(() => { r.root.findByType(TextInput).props.onChangeText('   '); });
    act(() => { r.root.findByType(TextInput).props.onSubmitEditing(); });
    expect(mockSendMessage).not.toHaveBeenCalled();
  });

  it('routes back via activeRideRouteFor when a ride is active (header back)', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'searching' }, currentDriver: null };
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
    expect(mockBack).not.toHaveBeenCalled();
  });

  it('falls through to router.back() when there is no active ride', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('triggers the same route-through-active-ride logic on hardware back', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'driver_arrived' }, currentDriver: null };
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    const handled = handler();
    expect(handled).toBe(true);
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-1' } });
  });

  it('shows the searching status in the ride banner', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'searching' }, currentDriver: null };
    const r = await renderScreen();
    expect(allText(r)).toContain('Searching for a driver…');
  });

  it('shows the driver-found status with driver details', async () => {
    mockRideState = {
      currentRide: { id: 'ride-1', status: 'driver_accepted' },
      currentDriver: { name: 'Sam Lee', vehicle_color: 'Blue', vehicle_make: 'Toyota', vehicle_model: 'Camry', license_plate: 'ABC 123' },
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Driver found: Sam Lee — Blue Toyota Camry, plate ABC 123');
  });

  it('shows the arrived status', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'driver_arrived' }, currentDriver: null };
    const r = await renderScreen();
    expect(allText(r)).toContain('Your driver has arrived!');
  });

  it('shares the trip via the ride banner', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'driver_arrived' }, currentDriver: null };
    const r = await renderScreen();
    const shareBtn = r.root.findByProps({ accessibilityLabel: 'Share trip' });
    await act(async () => { await shareBtn.props.onPress(); await flush(); });
    expect(mockApiGet).toHaveBeenCalledWith('/rides/ride-1/share');
    expect(Share.share).toHaveBeenCalledWith({ message: 'Follow my Spinr trip live: https://spinr-track.app/ride-1' });
  });

  it('routes the "Track ride" button to the owning screen', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'driver_arrived' }, currentDriver: null };
    const r = await renderScreen();
    const trackBtn = r.root.findByProps({ accessibilityLabel: 'Track ride' });
    act(() => { trackBtn.props.onPress(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-1' } });
  });

  it('routes a support-action cancel_ride bubble to the ride-owning screen', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'searching' }, currentDriver: null };
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'support_action', content: '', action: { type: 'open_support', category: 'cancel_ride', link: '/support' } },
    ];
    const r = await renderScreen();
    const cancelBtn = findButtonByText(r, 'Go to your ride');
    act(() => { cancelBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('routes a support-action cancel_ride bubble to /ride-status when there is no live ride', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'support_action', content: '', action: { type: 'open_support', category: 'cancel_ride', link: '/support' } },
    ];
    const r = await renderScreen();
    const cancelBtn = findButtonByText(r, 'Go to your ride');
    act(() => { cancelBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/ride-status');
  });

  it('routes a lost-and-found support-action bubble to /lost-and-found', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'support_action', content: '', action: { type: 'open_support', category: 'other', link: '/lost-and-found' } },
    ];
    const r = await renderScreen();
    const btn = findButtonByText(r, 'Open Lost & Found');
    act(() => { btn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/lost-and-found');
  });

  it('routes a generic support-action bubble to /support', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'support_action', content: '', action: { type: 'open_support', category: 'other', link: '/help' } },
    ];
    const r = await renderScreen();
    const btn = findButtonByText(r, 'Contact support');
    act(() => { btn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/support');
  });

  it('pushes to /pick-on-map with approx coords for a map_picker bubble', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'user', kind: 'text', content: 'set pickup' },
      { id: 'm2', role: 'assistant', kind: 'map_picker', content: '', action: { type: 'open_map_picker', location_role: 'pickup', label: 'Home', approx_lat: 52.1, approx_lng: -106.6 } },
    ];
    const r = await renderScreen();
    const btn = findButtonByText(r, 'Drop a pin');
    act(() => { btn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/pick-on-map',
      params: { field: 'pickup', ai: '1', aiLat: '52.1', aiLng: '-106.6' },
    });
  });

  it('disables a map_picker bubble once a newer conversation turn has started (stale card)', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'map_picker', content: '', action: { type: 'open_map_picker', location_role: 'pickup' } },
      { id: 'm2', role: 'user', kind: 'text', content: 'never mind' },
    ];
    const r = await renderScreen();
    const btn = findButtonByText(r, 'Drop a pin');
    expect(btn.props.disabled).toBe(true);
  });

  it('renders a booking_proposal message via BookingProposalCard', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'booking_proposal', content: '', action: { type: 'booking_proposal', proposal: { id: 'prop-1' } } },
    ];
    const r = await renderScreen();
    expect(allText(r)).toContain('BookingProposalCard:prop-1');
  });

  it('sends the follow-up message when a fare_quote option is selected', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'assistant', kind: 'fare_quote', content: '', action: { type: 'fare_quote' } },
    ];
    const r = await renderScreen();
    const selectBtn = r.root.findByProps({ accessibilityLabel: 'fare-quote-select' });
    act(() => { selectBtn.props.onPress(); });
    expect(mockSendMessage).toHaveBeenCalledWith('Book the economy option');
  });

  it('renders a plain user/assistant text bubble', async () => {
    mockAiChatState.messages = [
      { id: 'm1', role: 'user', kind: 'text', content: 'Hi there' },
      { id: 'm2', role: 'assistant', kind: 'text', content: 'Hello! How can I help?' },
    ];
    const r = await renderScreen();
    expect(allText(r)).toContain('Hi there');
    expect(allText(r)).toContain('Hello! How can I help?');
  });

  it('shows the default disclaimer when the store has none', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('AI answers can be inaccurate. For emergencies, call 911 or use the SOS button.');
  });

  it('starts a new conversation on the header create button', async () => {
    const r = await renderScreen();
    const newConvoBtn = r.root.findAllByType(TouchableOpacity)[1];
    act(() => { newConvoBtn.props.onPress(); });
    expect(mockStartNewConversation).toHaveBeenCalled();
  });

  it('sends via the Send button (not just onSubmitEditing)', async () => {
    const r = await renderScreen();
    act(() => { r.root.findByType(TextInput).props.onChangeText('hello'); });
    const sendBtn = r.root.findByProps({ accessibilityLabel: 'Send' });
    act(() => { sendBtn.props.onPress(); });
    expect(mockSendMessage).toHaveBeenCalledWith('hello');
  });

  it('logs (and does not crash) when the trip-share fetch fails', async () => {
    mockRideState = { currentRide: { id: 'ride-1', status: 'driver_arrived' }, currentDriver: null };
    mockApiGet.mockRejectedValue(new Error('down'));
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    const r = await renderScreen();
    const shareBtn = r.root.findByProps({ accessibilityLabel: 'Share trip' });
    await act(async () => { await shareBtn.props.onPress(); await flush(); });
    expect(Share.share).not.toHaveBeenCalled();
    expect(consoleSpy).toHaveBeenCalled();
  });

  describe('location_suggestions card', () => {
    const CANDIDATE = { name: 'Home', address: '655 Albert St, Regina', lat: 50.44079, lng: -104.61802 };

    it('renders a "Choose your pickup" card for a pickup role and sends the bracketed-coords message on select', async () => {
      mockAiChatState.messages = [
        {
          id: 'm1', role: 'assistant', kind: 'location_suggestions', content: '',
          action: { type: 'location_suggestions', location_role: 'pickup', candidates: [CANDIDATE] },
        },
      ];
      const r = await renderScreen();
      expect(allText(r)).toContain('Choose your pickup');
      const optionBtn = r.root.findByProps({ accessibilityLabel: 'Use Home' });
      act(() => { optionBtn.props.onPress(); });
      expect(mockSendMessage).toHaveBeenCalledWith('Use 655 Albert St, Regina [50.44079,-104.61802] as my pickup.');
    });

    it('renders a "Choose your dropoff" card for a dropoff role', async () => {
      mockAiChatState.messages = [
        {
          id: 'm1', role: 'assistant', kind: 'location_suggestions', content: '',
          action: { type: 'location_suggestions', location_role: 'dropoff', candidates: [CANDIDATE] },
        },
      ];
      const r = await renderScreen();
      expect(allText(r)).toContain('Choose your dropoff');
    });

    it('is disabled once a newer conversation turn has started (stale card)', async () => {
      mockAiChatState.messages = [
        {
          id: 'm1', role: 'assistant', kind: 'location_suggestions', content: '',
          action: { type: 'location_suggestions', location_role: 'pickup', candidates: [CANDIDATE] },
        },
        { id: 'm2', role: 'user', kind: 'text', content: 'never mind' },
      ];
      const r = await renderScreen();
      const optionBtn = r.root.findByProps({ accessibilityLabel: 'Use Home' });
      expect(optionBtn.props.disabled).toBe(true);
      expect(allText(r)).toContain('The conversation has moved on');
    });
  });

  describe('voice input (mic button)', () => {
    it('requests permission and starts listening on tap when not yet listening', async () => {
      const r = await renderScreen();
      const micBtn = r.root.findByProps({ accessibilityLabel: 'Start voice input' });
      await act(async () => { await micBtn.props.onPress(); await flush(); });
      expect(mockRequestPermissionsAsync).toHaveBeenCalled();
      expect(mockSpeechStart).toHaveBeenCalledWith({ lang: 'en-CA', interimResults: true, continuous: false });
      expect(r.root.findByProps({ accessibilityLabel: 'Stop voice input' })).toBeTruthy();
    });

    it('toasts and does not start when microphone permission is denied', async () => {
      mockRequestPermissionsAsync.mockResolvedValue({ granted: false });
      const r = await renderScreen();
      const micBtn = r.root.findByProps({ accessibilityLabel: 'Start voice input' });
      await act(async () => { await micBtn.props.onPress(); await flush(); });
      expect(mockShowToast).toHaveBeenCalledWith('Microphone needed', 'Allow microphone access in Settings to use voice input.', 'warning');
      expect(mockSpeechStart).not.toHaveBeenCalled();
    });

    it('toasts a generic failure when requestPermissionsAsync throws', async () => {
      mockRequestPermissionsAsync.mockRejectedValue(new Error('native error'));
      const r = await renderScreen();
      const micBtn = r.root.findByProps({ accessibilityLabel: 'Start voice input' });
      await act(async () => { await micBtn.props.onPress(); await flush(); });
      expect(mockShowToast).toHaveBeenCalledWith('Voice input failed', 'Could not start voice capture — please type instead.', 'warning');
    });

    it('tapping again while listening stops recognition', async () => {
      const r = await renderScreen();
      await act(async () => { await r.root.findByProps({ accessibilityLabel: 'Start voice input' }).props.onPress(); await flush(); });
      await act(async () => { await r.root.findByProps({ accessibilityLabel: 'Stop voice input' }).props.onPress(); await flush(); });
      expect(mockSpeechStop).toHaveBeenCalled();
    });

    it('a "result" event streams the transcript into the input field', async () => {
      const r = await renderScreen();
      act(() => { mockSpeechListeners.result({ results: [{ transcript: 'book me a ride' }] }); });
      expect(r.root.findByType(TextInput).props.value).toBe('book me a ride');
    });

    it('an "end" event stops the listening indicator', async () => {
      const r = await renderScreen();
      await act(async () => { await r.root.findByProps({ accessibilityLabel: 'Start voice input' }).props.onPress(); await flush(); });
      act(() => { mockSpeechListeners.end({}); });
      expect(r.root.findByProps({ accessibilityLabel: 'Start voice input' })).toBeTruthy();
    });

    it('an "error" event for aborted/no-speech does not toast', async () => {
      await renderScreen();
      act(() => { mockSpeechListeners.error({ error: 'aborted' }); });
      act(() => { mockSpeechListeners.error({ error: 'no-speech' }); });
      expect(mockShowToast).not.toHaveBeenCalledWith('Voice input failed', expect.anything(), expect.anything());
    });

    it('any other "error" event toasts a capture failure', async () => {
      await renderScreen();
      act(() => { mockSpeechListeners.error({ error: 'network' }); });
      expect(mockShowToast).toHaveBeenCalledWith('Voice input failed', 'Could not capture audio — please try again or type instead.', 'warning');
    });
  });
});
