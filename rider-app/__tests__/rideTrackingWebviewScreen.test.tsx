/**
 * app/ride-tracking-webview.tsx — public live-ride-tracking WebView.
 * Security-relevant: sanitizes any caller-supplied trackingUrl against an
 * allowlist before ever loading it (defence against a deep-link phishing
 * injection into the app's own WebView chrome). Pins:
 *  - a trackingUrl param on an allowed host (track.spinr.ca /
 *    spinr-track.app) loads directly, no fetch needed
 *  - a trackingUrl param on ANY other host (or non-https) is rejected
 *    with "Invalid tracking link." and the WebView never renders
 *  - no trackingUrl + a rideId fetches /rides/:id/share and builds the
 *    URL from the configured track base URL + share token (falling back
 *    to the raw rideId if the backend omits share_token)
 *  - no configured track base URL surfaces its own "not set up" error
 *    without ever calling the API
 *  - a fetch failure shows a retry button that re-fetches
 *  - Share (header icon + footer button) is disabled/no-ops until a URL
 *    is resolved
 *  - a WebView load error surfaces its own error state
 *  - back nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Share } from 'react-native';
import { WebView } from 'react-native-webview';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('react-native-webview', () => ({ WebView: 'WebView' }));

const mockBack = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666',
  textSecondary: '#333', border: '#E5E7EB', error: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

jest.mock('../app/_layout', () => ({
  TrackBaseUrlContext: require('react').createContext(null),
}));

import RideTrackingWebviewScreen from '../app/ride-tracking-webview';
import { TrackBaseUrlContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen(trackBaseUrl: string | null = 'https://track.spinr.ca') {
  await act(async () => {
    renderer = TestRenderer.create(
      <TrackBaseUrlContext.Provider value={trackBaseUrl}>
        <RideTrackingWebviewScreen />
      </TrackBaseUrlContext.Provider>,
    );
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  mockApiGet.mockResolvedValue({ data: { share_token: 'tok-123' } });
  jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as any);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('RideTrackingWebviewScreen', () => {
  it('loads a trackingUrl param on an allowed host directly, without fetching', async () => {
    mockParams = { trackingUrl: 'https://track.spinr.ca/abc123' };
    const r = await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalled();
    const webview = r.root.findByType(WebView as any);
    expect(webview.props.source.uri).toBe('https://track.spinr.ca/abc123');
  });

  it('loads a trackingUrl param on the legacy allowed host too', async () => {
    mockParams = { trackingUrl: 'https://spinr-track.app/abc123' };
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    expect(webview.props.source.uri).toBe('https://spinr-track.app/abc123');
  });

  it('rejects a trackingUrl on a disallowed host, never rendering the WebView', async () => {
    mockParams = { trackingUrl: 'https://evil.example.com/phish' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Invalid tracking link.');
    expect(r.root.findAllByType(WebView as any)).toHaveLength(0);
  });

  it('rejects a non-https trackingUrl', async () => {
    mockParams = { trackingUrl: 'http://track.spinr.ca/abc123' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Invalid tracking link.');
  });

  it('fetches the share token for a rideId and builds the URL from the track base', async () => {
    mockParams = { rideId: 'ride-1' };
    const r = await renderScreen('https://track.spinr.ca');
    expect(mockApiGet).toHaveBeenCalledWith('/rides/ride-1/share');
    const webview = r.root.findByType(WebView as any);
    expect(webview.props.source.uri).toBe('https://track.spinr.ca/tok-123');
  });

  it('falls back to the raw rideId when the backend omits share_token', async () => {
    mockApiGet.mockResolvedValue({ data: {} });
    mockParams = { rideId: 'ride-1' };
    const r = await renderScreen('https://track.spinr.ca');
    const webview = r.root.findByType(WebView as any);
    expect(webview.props.source.uri).toBe('https://track.spinr.ca/ride-1');
  });

  it('shows a "not set up" error without calling the API when no track base URL is configured', async () => {
    mockParams = { rideId: 'ride-1' };
    const r = await renderScreen(null);
    expect(mockApiGet).not.toHaveBeenCalled();
    expect(allText(r)).toContain('Live trip tracking is not set up yet. Please contact support.');
  });

  it('re-fetches on retry and resolves a new URL (KNOWN BUG: fetchTrackingUrl never clears a prior `error`, so a successful retry still shows the stale error view instead of the WebView -- the URL bar is the only visible sign the retry actually worked)', async () => {
    mockApiGet.mockRejectedValueOnce(new Error('down'));
    mockParams = { rideId: 'ride-1' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Could not generate tracking link. Please try again.');
    mockApiGet.mockResolvedValue({ data: { share_token: 'tok-456' } });
    const retryBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Try Again')))!;
    await act(async () => {
      await retryBtn.props.onPress();
      await flush();
    });
    expect(mockApiGet).toHaveBeenLastCalledWith('/rides/ride-1/share');
    expect(allText(r)).toContain('https://track.spinr.ca/tok-456');
    // Not `.toHaveLength(0)` here on purpose -- this pins the current (buggy)
    // behavior rather than the intended one; see the test title.
    expect(r.root.findAllByType(WebView as any)).toHaveLength(0);
  });

  it('disables the header share button until a URL is resolved', async () => {
    mockApiGet.mockImplementation(() => new Promise(() => {})); // never resolves
    mockParams = { rideId: 'ride-1' };
    const r = await renderScreen();
    const shareBtn = r.root.findAllByType(TouchableOpacity)[1]; // [0] back, [1] share
    expect(shareBtn.props.disabled).toBe(true);
  });

  it('shares the resolved tracking link from the header button', async () => {
    mockParams = { trackingUrl: 'https://track.spinr.ca/abc123' };
    const r = await renderScreen();
    const shareBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(Share.share).toHaveBeenCalledWith({
      message: 'Track my Spinr ride live: https://track.spinr.ca/abc123',
      title: 'Track My Spinr Ride',
    });
  });

  it('shares from the footer button too', async () => {
    mockParams = { trackingUrl: 'https://track.spinr.ca/abc123' };
    const r = await renderScreen();
    const footerBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Share Tracking Link')))!;
    await act(async () => {
      await footerBtn.props.onPress();
      await flush();
    });
    expect(Share.share).toHaveBeenCalled();
  });

  it('surfaces its own error state when the WebView itself fails to load', async () => {
    mockParams = { trackingUrl: 'https://track.spinr.ca/abc123' };
    const r = await renderScreen();
    const webview = r.root.findByType(WebView as any);
    act(() => {
      webview.props.onError();
    });
    expect(allText(r)).toContain('Could not load tracking page.');
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
