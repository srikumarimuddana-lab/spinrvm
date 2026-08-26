/**
 * app/legacy-consent-notice.tsx — one-time re-consent notice for accounts
 * with no recorded consent. Pins:
 *  - re-checks GET /consent/status on mount and redirects straight to the
 *    app when needs_notice is false (self-heals if reached unnecessarily)
 *  - fails open to the app (never traps the rider) when the status check
 *    itself throws
 *  - shows the notice and lets the rider accept via POST /consent/accept,
 *    then redirects
 *  - a failed accept surfaces a toast and does not navigate
 *  - "Read the full policy" navigates to /legal
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import LegacyConsentNoticeScreen from '../app/legacy-consent-notice';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockReplace = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace, push: mockPush }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666',
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

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<LegacyConsentNoticeScreen />);
    await flush();
  });
  return renderer;
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('LegacyConsentNoticeScreen', () => {
  it('redirects straight to the app when needs_notice is false', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('fails open to the app when the status check throws', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('shows the notice and does not redirect when needs_notice is true', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    const renderer = await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
    const text = JSON.stringify(renderer.toJSON());
    expect(text).toContain('An update on how we handle your information');
  });

  it('accepts and redirects on "I understand, continue"', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    mockApiPost.mockResolvedValue({ data: { success: true } });
    const renderer = await renderScreen();
    const acceptBtn = renderer.root.findByProps({ accessibilityLabel: 'I understand, continue to Spinr' });
    await act(async () => {
      await acceptBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/consent/accept');
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('shows a toast and does not navigate when accept fails', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const renderer = await renderScreen();
    const acceptBtn = renderer.root.findByProps({ accessibilityLabel: 'I understand, continue to Spinr' });
    await act(async () => {
      await acceptBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Something went wrong', 'Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalledWith('/(tabs)');
  });

  it('navigates to /legal when "Read the full policy" is tapped', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    const renderer = await renderScreen();
    const readBtn = renderer.root.findByProps({
      accessibilityLabel: 'Read the full Privacy Policy and Terms of Service',
    });
    act(() => {
      readBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/legal');
  });
});
