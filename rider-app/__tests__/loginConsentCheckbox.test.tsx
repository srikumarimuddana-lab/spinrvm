/**
 * docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md
 *
 * Pins app/login.tsx's new explicit, unchecked-by-default consent
 * checkbox — the fix for the passive "by continuing you agree" text that
 * had no tappable action or opt-in gesture behind it (see the legal
 * fact-finding audit's §8):
 *  - the checkbox starts unchecked and the "Send Verification Code" button
 *    is disabled (accessibilityState.disabled) even with a valid phone
 *    number until it's checked
 *  - checking it (accessibilityRole="checkbox", accessibilityState.checked
 *    toggles) enables the button
 *  - a successful continue navigates to /otp carrying consentAccepted
 *    as a route param, so otp.tsx's POST /auth/verify-otp call can send it
 *  - the checkbox is never re-enabled while a send is in flight
 *
 * Uses react-test-renderer directly, matching verifyEmailScreen.test.tsx's
 * conventions.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import LoginScreen from '../app/login';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace, back: jest.fn() }),
  // login.tsx only uses this to bounce away when already authenticated
  // (useAuthStore.getState().token truthy) — irrelevant to this screen's
  // signed-out consent-gating behaviour, so a no-op keeps these tests
  // focused without needing to fake navigation focus events.
  useFocusEffect: () => {},
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
  act(() => {
    input.props.onChangeText(digits);
  });
}

function getContinueButton(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findByProps({ accessibilityLabel: 'Send verification code' });
}

function getConsentCheckbox(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findByProps({ accessibilityRole: 'checkbox' });
}

function toggleConsent(renderer: TestRenderer.ReactTestRenderer) {
  act(() => {
    getConsentCheckbox(renderer).props.onPress();
  });
}

async function tapContinue(renderer: TestRenderer.ReactTestRenderer) {
  await act(async () => {
    await getContinueButton(renderer).props.onPress();
    await Promise.resolve();
  });
}

afterEach(() => {
  act(() => {
    mountedRenderer?.unmount();
  });
  mountedRenderer = null;
  jest.clearAllMocks();
});

beforeEach(() => {
  mockApiPost.mockResolvedValue({ data: { success: true } });
});

describe('LoginScreen — explicit consent checkbox', () => {
  it('starts unchecked, with the continue button disabled even for a valid phone number', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');

    const checkbox = getConsentCheckbox(renderer);
    expect(checkbox.props.accessibilityState).toMatchObject({ checked: false });

    const button = getContinueButton(renderer);
    expect(button.props.accessibilityState).toMatchObject({ disabled: true });
  });

  it('checking the box (with a valid phone) enables the continue button', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer);

    expect(getConsentCheckbox(renderer).props.accessibilityState).toMatchObject({ checked: true });
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: false });
  });

  it('an invalid phone number keeps continue disabled even once consent is checked', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '123'); // too short
    toggleConsent(renderer);

    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: true });
  });

  it('tapping continue while unchecked never calls send-otp (defence in depth behind the disabled button)', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    await tapContinue(renderer); // consent still unchecked
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('navigates to /otp with consentAccepted carried as a route param once checked', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer);
    await tapContinue(renderer);

    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'true' }),
      }),
    );
  });

  it('unchecking after checking disables continue again', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer); // check
    toggleConsent(renderer); // uncheck
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: true });
  });
});
