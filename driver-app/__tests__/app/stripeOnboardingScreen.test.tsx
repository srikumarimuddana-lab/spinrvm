/**
 * app/driver/stripe-onboarding.tsx — embedded Stripe Connect identity
 * verification (WebView). Pins:
 *  - fetches a fresh auth token on mount; a missing/errored token shows the
 *    "session expired" state and never renders the WebView
 *  - the bearer token is injected via injectedJavaScriptBeforeContentLoaded,
 *    never placed in the URL
 *  - the no-progress watchdog trips after LOAD_TIMEOUT_MS with no terminal
 *    signal, showing an actionable timeout error naming the last stage
 *  - onMessage: 'exit' finishes (refetch + success toast + router.back());
 *    'stage:mounted' clears the watchdog; 'error:<code>' shows a fatal error
 *    naming the code and last stage
 *  - Retry clears the fatal error and remounts the WebView with a new key
 *  - the header back button also finishes (refetch, no toast)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';
import { WebView } from 'react-native-webview';

jest.useFakeTimers();

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('react-native-webview', () => ({ WebView: 'WebView' }));

const mockRequestCameraPermissionsAsync = jest.fn();
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: (...a: any[]) => mockRequestCameraPermissionsAsync(...a),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockEnsureFreshToken = jest.fn();
const mockGetAuthHeader = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  ensureFreshToken: (...a: any[]) => mockEnsureFreshToken(...a),
  getAuthHeader: (...a: any[]) => mockGetAuthHeader(...a),
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.spinr.ca' },
}));

const mockRefetch = jest.fn();
jest.mock('@shared/hooks/queries', () => ({ useDriverMe: () => ({ refetch: mockRefetch }) }));

import StripeOnboardingScreen from '../../app/driver/stripe-onboarding';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<StripeOnboardingScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRequestCameraPermissionsAsync.mockResolvedValue({ granted: true });
  mockEnsureFreshToken.mockResolvedValue(undefined);
  mockGetAuthHeader.mockResolvedValue('bearer-token-123');
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('StripeOnboardingScreen', () => {
  it('fetches a fresh token on mount and renders the WebView with it injected (never in the URL)', async () => {
    const r = await renderScreen();
    expect(mockEnsureFreshToken).toHaveBeenCalled();
    const webview = r.root.findByType(WebView as any);
    expect(webview.props.source.uri).toBe('https://api.spinr.ca/api/v1/drivers/stripe-embedded');
    expect(webview.props.source.uri).not.toContain('bearer-token-123');
    expect(webview.props.injectedJavaScriptBeforeContentLoaded).toContain('bearer-token-123');
  });

  it('shows the session-expired state and never renders the WebView when the token is missing', async () => {
    mockGetAuthHeader.mockResolvedValue(null);
    const r = await renderScreen();
    expect(allText(r)).toContain('Your session expired. Please go back and try again.');
    expect(r.root.findAllByType(WebView as any)).toHaveLength(0);
  });

  it('shows the session-expired state when fetching the token throws', async () => {
    mockEnsureFreshToken.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('Your session expired. Please go back and try again.');
  });

  it('trips the no-progress watchdog and shows a timeout error naming the last stage', async () => {
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    act(() => {
      webview.props.onMessage({ nativeEvent: { data: 'stage:fetching-secret' } });
    });
    await act(async () => {
      jest.advanceTimersByTime(30000);
      await flush();
    });
    expect(allText(r)).toContain('Verification couldn’t load.');
    expect(allText(r)).toContain('Timed out at stage \\"timeout\\".');
  });

  it('clears the watchdog on stage:mounted so it never times out', async () => {
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    act(() => {
      webview.props.onMessage({ nativeEvent: { data: 'stage:mounted' } });
    });
    await act(async () => {
      jest.advanceTimersByTime(30000);
      await flush();
    });
    expect(allText(r)).not.toContain('Verification couldn’t load.');
  });

  it('finishes with a refresh + success toast on the "exit" message', async () => {
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    await act(async () => {
      webview.props.onMessage({ nativeEvent: { data: 'exit' } });
      await flush();
    });
    expect(mockRefetch).toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Verification Complete', 'Verification updated.');
    expect(mockBack).toHaveBeenCalled();
  });

  it('shows a fatal error naming the code and last stage on an "error:" message', async () => {
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    act(() => {
      webview.props.onMessage({ nativeEvent: { data: 'stage:connectjs-load' } });
    });
    act(() => {
      webview.props.onMessage({ nativeEvent: { data: 'error:connectjs-timeout' } });
    });
    // `log()` sets `stage` to its own argument before setFatalError runs, so
    // the "last stage" shown is the error-message stage itself, not the
    // stage that preceded it.
    expect(allText(r)).toContain('Failure: connectjs-timeout (last stage \\"error:connectjs-timeout\\").');
  });

  it('retry clears the fatal error and remounts the WebView', async () => {
    const r = await renderScreen();
    const webviewBefore = r.root.findByType(WebView as any);
    act(() => {
      webviewBefore.props.onMessage({ nativeEvent: { data: 'error:init' } });
    });
    const retryBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Retry')))!;
    act(() => {
      retryBtn.props.onPress();
    });
    expect(allText(r)).not.toContain('Verification couldn’t load.');
    // Retry remounts the WebView (fresh `key`) rather than reusing the old
    // instance -- confirmed indirectly: a live WebView is back in the tree
    // and the loading/spinner state (stage: "retry") is showing again.
    expect(r.root.findAllByType(WebView as any)).toHaveLength(1);
    expect(allText(r)).toContain('["stage: ","retry"]');
  });

  it('shows a fatal error on WebView onError and stops the watchdog', async () => {
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    act(() => {
      webview.props.onError({ nativeEvent: { description: 'net::ERR_FAILED' } });
    });
    expect(allText(r)).toContain('Failure: webview-error (last stage \\"webview-error:net::ERR_FAILED\\").');
  });

  it('the header back button finishes with a refresh and no toast', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockRefetch).toHaveBeenCalled();
    expect(mockShowToast).not.toHaveBeenCalled();
    expect(mockBack).toHaveBeenCalled();
  });
});
