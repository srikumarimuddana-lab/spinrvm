/**
 * app/login.tsx — broader coverage beyond loginConsentCheckbox.test.tsx
 * (which pins only the explicit unchecked-by-default consent checkbox's
 * gating of the continue button).
 *
 * Pins:
 *  - the focus-effect redirect: already-authenticated (truthy
 *    authStore token) bounces to /(tabs); signed-out stays on this screen
 *  - handleSendCode's three response/error branches: `success:false` →
 *    generic "Code Not Sent" toast; a thrown error with a server message
 *    → "Sign-in Unavailable" with that message; a thrown error with
 *    nothing extractable → generic "Connection Error"
 *  - the loading state resets in `finally` regardless of outcome
 *  - the Terms of Service / Privacy Policy links each navigate to
 *    /legal with their own `type` param
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import LoginScreen from '../app/login';
import { useAuthStore } from '@shared/store/authStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockFocusEffectEnabled = true;
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace, back: jest.fn() }),
  useFocusEffect: (cb: () => void) => {
    const ReactActual = require('react');
    ReactActual.useEffect(() => {
      if (mockFocusEffectEnabled) cb();
    }, []);
  },
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', success: '#22C55E',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: { post: (...args: any[]) => mockApiPost(...args) },
  };
});

jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({ token: null }));
  return { useAuthStore };
});

let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<LoginScreen />);
    await Promise.resolve();
  });
  mountedRenderer = renderer;
  return renderer;
}

function enterPhone(renderer: TestRenderer.ReactTestRenderer, digits: string) {
  const input = renderer.root.findByProps({ accessibilityLabel: 'Phone number' });
  act(() => { input.props.onChangeText(digits); });
}

function getContinueButton(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findByProps({ accessibilityLabel: 'Send verification code' });
}

function toggleConsent(renderer: TestRenderer.ReactTestRenderer) {
  act(() => { renderer.root.findByProps({ accessibilityRole: 'checkbox' }).props.onPress(); });
}

async function tapContinue(renderer: TestRenderer.ReactTestRenderer) {
  await act(async () => {
    await getContinueButton(renderer).props.onPress();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFocusEffectEnabled = true;
  useAuthStore.setState({ token: null });
  mockApiPost.mockResolvedValue({ data: { success: true } });
});

afterEach(() => {
  act(() => { mountedRenderer?.unmount(); });
  mountedRenderer = null;
});

describe('already-authenticated redirect', () => {
  it('bounces to /(tabs) when the focus-effect fires with a truthy token', async () => {
    useAuthStore.setState({ token: 'valid-token' });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('stays on this screen when signed out', async () => {
    useAuthStore.setState({ token: null });
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
  });
});

describe('handleSendCode branches', () => {
  it('toasts "Code Not Sent" on a success:false response', async () => {
    mockApiPost.mockResolvedValue({ data: { success: false } });
    const r = await renderScreen();
    enterPhone(r, '3065550199');
    toggleConsent(r);
    await tapContinue(r);
    expect(mockShowToast).toHaveBeenCalledWith('Code Not Sent', 'Could not send verification code. Please try again.', 'danger');
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('toasts "Sign-in Unavailable" with the server-provided message on a thrown error', async () => {
    const err: any = new Error('boom');
    err.response = { data: { detail: 'Verification is temporarily unavailable.' } };
    mockApiPost.mockRejectedValue(err);
    const r = await renderScreen();
    enterPhone(r, '3065550199');
    toggleConsent(r);
    await tapContinue(r);
    expect(mockShowToast).toHaveBeenCalledWith('Sign-in Unavailable', 'Verification is temporarily unavailable.', 'danger');
  });

  it('toasts a generic "Connection Error" when nothing extractable is on the thrown error', async () => {
    mockApiPost.mockRejectedValue(new Error());
    const r = await renderScreen();
    enterPhone(r, '3065550199');
    toggleConsent(r);
    await tapContinue(r);
    expect(mockShowToast).toHaveBeenCalledWith('Connection Error', 'Unable to reach server. Please check your connection.', 'danger');
  });

  it('resets the loading state in `finally` after a failure (button re-enables)', async () => {
    mockApiPost.mockRejectedValue(new Error());
    const r = await renderScreen();
    enterPhone(r, '3065550199');
    toggleConsent(r);
    await tapContinue(r);
    expect(getContinueButton(r).props.accessibilityState).toMatchObject({ disabled: false });
  });

  it('shows the loading spinner while the request is in flight', async () => {
    let resolveFn!: (v: any) => void;
    mockApiPost.mockReturnValue(new Promise((resolve) => { resolveFn = resolve; }));
    const r = await renderScreen();
    enterPhone(r, '3065550199');
    toggleConsent(r);
    act(() => { getContinueButton(r).props.onPress(); });
    expect(getContinueButton(r).props.accessibilityState).toMatchObject({ disabled: true });
    await act(async () => { resolveFn({ data: { success: true } }); await Promise.resolve(); });
  });
});

describe('legal links', () => {
  it('the Terms of Service link navigates to /legal?type=tos', async () => {
    const r = await renderScreen();
    const tosLink = r.root.findByProps({ accessibilityLabel: 'Terms of Service' });
    act(() => { tosLink.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/legal', params: { type: 'tos' } });
  });

  it('the Privacy Policy link navigates to /legal?type=privacy', async () => {
    const r = await renderScreen();
    const privacyLink = r.root.findByProps({ accessibilityLabel: 'Privacy Policy' });
    act(() => { privacyLink.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/legal', params: { type: 'privacy' } });
  });
});
