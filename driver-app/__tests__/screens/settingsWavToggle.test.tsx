/**
 * Ranked blocker #22 / audit finding N6: the WAV (wheelchair-accessible
 * vehicle) declaration toggle in Settings → Vehicle Accessibility must
 * announce as a switch and report its checked state to a screen reader,
 * not just render a colored pill a sighted user can see is on/off.
 *
 * Mirrors screens/notifications.test.tsx's mocking convention.
 */
import React from 'react';
import { act, render } from '@testing-library/react-native';
import SettingsScreen from '../../app/driver/settings';

const mockUpdateDriverMeMutate = jest.fn();
let mockDriverMeData: { is_wav?: boolean | null } | undefined = { is_wav: false };

jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: undefined }),
  useUpdateNotificationPreferences: () => ({ mutate: jest.fn() }),
  useDriverMe: () => ({ data: mockDriverMeData }),
  useUpdateDriverMe: () => ({ mutate: mockUpdateDriverMeMutate }),
}));

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: () => ({ logout: jest.fn() }),
}));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444',
      primaryDark: '#D32F2F',
      background: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceLight: '#F3F4F6',
      text: '#111827',
      textDim: '#6B7280',
      textSecondary: '#9CA3AF',
      border: '#E5E7EB',
      orange: '#F97316',
      gold: '#D4AF37',
      danger: '#DC2626',
    },
    colorScheme: 'light',
    setTheme: jest.fn(),
  }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({
    language: 'en',
    setLanguage: jest.fn(),
    loadLanguage: jest.fn(),
    t: (key: string) => key,
  }),
}));

jest.mock('../../store/navStore', () => ({
  useNavStore: () => ({ navApp: 'default', setNavApp: jest.fn(), loadNavApp: jest.fn() }),
}));

jest.mock('../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: () => ({
    soundEffects: true,
    vibration: true,
    setSoundEffects: jest.fn(),
    setVibration: jest.fn(),
    loadAlertPrefs: jest.fn(),
  }),
}));

jest.mock('../../hooks/useToast', () => ({ showToast: jest.fn() }));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn().mockResolvedValue({ data: { email_opt_in: false, sms_opt_in: false } }),
    put: jest.fn().mockResolvedValue({}),
    post: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue({}),
  },
  getApiErrorMessage: () => 'error',
}));

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn() }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));
jest.mock('../../components/ScreenHeader', () => ({ ScreenHeader: () => null }));

// SettingsScreen fetches notification preferences via the mocked
// @shared/api/client on mount. Without flushing that pending promise here,
// its `.then` continuation can resolve after RTL's implicit afterEach
// unmount, calling setState on an already-unmounted tree outside act() -- a
// leaked update that intermittently corrupted a later test file in the
// same Jest worker (observed: driverProfileScreen.test.tsx timing out
// under CI's leaner scheduling). Flushing the pending microtask queue
// inside act() while the component is still mounted discharges it here
// instead.
async function flushPendingEffects() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('Driver settings — WAV accessibility toggle', () => {
  beforeEach(() => {
    mockUpdateDriverMeMutate.mockReset();
    mockDriverMeData = { is_wav: false };
  });

  it('announces as a switch and reports checked:false when WAV is off', async () => {
    mockDriverMeData = { is_wav: false };
    const screen = render(<SettingsScreen />);
    await flushPendingEffects();

    const toggle = screen.getByLabelText('settings.wavTitle');
    expect(toggle.props.accessibilityRole).toBe('switch');
    expect(toggle.props.accessibilityState).toEqual(
      expect.objectContaining({ checked: false }),
    );
  });

  it('announces as a switch and reports checked:true when WAV is on', async () => {
    mockDriverMeData = { is_wav: true };
    const screen = render(<SettingsScreen />);
    await flushPendingEffects();

    const toggle = screen.getByLabelText('settings.wavTitle');
    expect(toggle.props.accessibilityRole).toBe('switch');
    expect(toggle.props.accessibilityState).toEqual(
      expect.objectContaining({ checked: true }),
    );
  });
});
