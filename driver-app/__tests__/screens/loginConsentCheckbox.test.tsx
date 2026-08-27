/**
 * docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md,
 * revised 2026-08-27 (docs/migration/2026-08-27-legacy-data-full-migration-approach.md §6a).
 *
 * Pins app/login.tsx's explicit consent checkbox, updated for the
 * 2026-08-27 fix: the checkbox no longer gates "Send Verification Code" —
 * only phone validity does. The backend only ever reads consent_accepted
 * for a brand-new account (routes/auth.py's verify_otp); gating this
 * screen on it forced every returning driver (session expired, logged
 * back in) to re-tick a box with zero effect for them. The checkbox stays
 * visible/toggleable — a proactive new signup can still pre-accept it —
 * and its state is still carried to /otp as a route param either way.
 * otp.tsx now handles the genuine-new-account case inline if the backend
 * comes back with consent_required.
 *  - the checkbox starts unchecked; the "Send Verification Code" button is
 *    enabled once the phone number is valid, regardless of checkbox state
 *  - toggling it (accessibilityRole="checkbox", accessibilityState.checked)
 *    still works, but never itself disables/enables the button
 *  - a successful continue navigates to /otp carrying consentAccepted as a
 *    route param reflecting whatever the checkbox's state was
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
  it('starts unchecked, with continue already enabled for a valid phone number', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');

    expect(findCheckbox(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ checked: false }),
    );
    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: false }),
    );
  });

  it('checking the box toggles its own state without changing the continue button', () => {
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

  it('tapping continue while unchecked still calls send-otp and carries consentAccepted:false to /otp', async () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    await act(async () => {
      fireEvent.press(findContinueButton(screen));
    });

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' }));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'false' }),
      }),
    );
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

  it('an invalid phone number keeps continue disabled regardless of consent state', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '123'); // too short
    fireEvent.press(findCheckbox(screen));

    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: true }),
    );
  });
});
