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
 *  - a successful continue navigates to /otp carrying consentAccepted as a
 *    route param, so otp.tsx's POST /auth/verify-otp call can send it
 *
 * Mirrors screens/settingsWavToggle.test.tsx's mocking conventions
 * (@testing-library/react-native, `t: (key) => key`).
 */
import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import LoginScreen from '../../app/login';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: mockPush, replace: jest.fn() }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({
    language: 'en',
    setLanguage: jest.fn(),
    loadLanguage: jest.fn(),
    t: (key: string) => key,
  }),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFFFFF', surface: '#FFFFFF',
      surfaceLight: '#F3F4F6', text: '#111827', textDim: '#6B7280', border: '#E5E7EB',
      success: '#22C55E',
    },
    isDark: false,
  }),
}));

const mockApiPost = jest.fn().mockResolvedValue({ data: { success: true } });
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: (...args: any[]) => mockApiPost(...args), put: jest.fn(), delete: jest.fn() },
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

function findCheckbox(screen: ReturnType<typeof render>) {
  return screen.UNSAFE_getByProps({ accessibilityRole: 'checkbox' });
}

function findContinueButton(screen: ReturnType<typeof render>) {
  return screen.getByLabelText('Send verification code');
}

afterEach(() => jest.clearAllMocks());

describe('Driver LoginScreen — explicit consent checkbox', () => {
  it('starts unchecked, with continue disabled even for a valid phone number', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');

    expect(findCheckbox(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ checked: false }),
    );
    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: true }),
    );
  });

  it('checking the box (with a valid phone) enables the continue button', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    fireEvent.press(findCheckbox(screen));

    expect(findCheckbox(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ checked: true }),
    );
    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: false }),
    );
  });

  it('tapping continue while unchecked never calls send-otp (RN ignores press on a disabled Touchable)', async () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    await act(async () => {
      fireEvent.press(findContinueButton(screen));
    });
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockShowToast).not.toHaveBeenCalled();
  });

  it('navigates to /otp with consentAccepted carried as a route param once checked', async () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    fireEvent.press(findCheckbox(screen));
    await act(async () => {
      fireEvent.press(findContinueButton(screen));
    });

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' }));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'true' }),
      }),
    );
  });
});
