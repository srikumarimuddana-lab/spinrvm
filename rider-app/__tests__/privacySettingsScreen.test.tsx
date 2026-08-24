/**
 * app/privacy-settings.tsx — broader coverage beyond
 * privacySettingsToggles.test.tsx (covers the push-notification toggle's
 * hydrate/save/revert path and confirms the two dead rows stay removed).
 *
 * Pins:
 *  - turning the push toggle ON with permission already granted just saves;
 *    with permission denied, requests it — if the request is denied too, it
 *    toasts, opens device Settings, and leaves the toggle off (never calls
 *    the preferences mutation)
 *  - the three marketing-consent toggles hydrate from GET
 *    /marketing/preferences and each independently PUTs its own field with
 *    optimistic revert-on-failure
 *  - handleDownloadData: success toasts the translated confirmation;
 *    failure toasts the backend's message
 *  - handleDeleteAccount: opens a danger confirm sheet; confirming DELETEs
 *    the account, logs out, and navigates to /login; a delete failure
 *    toasts instead of navigating
 *  - the Privacy Policy / Terms of Service rows navigate to /legal with the
 *    right `type` query param
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import PrivacySettingsScreen from '../app/privacy-settings';
import CustomToggle from '../components/CustomToggle';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace }) }));
jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaView: ({ children }: any) => <View>{children}</View> };
});
jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      <RNText>{props.message}</RNText>
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});
const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));
jest.mock('../i18n', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
const mockLogout = jest.fn();
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => ({ logout: mockLogout }) }));

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockApiPost = jest.fn();
const mockApiDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    put: (...a: any[]) => mockApiPut(...a),
    post: (...a: any[]) => mockApiPost(...a),
    delete: (...a: any[]) => mockApiDelete(...a),
  },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockMutate = jest.fn();
let mockPrefsData: any = null;
jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: mockPrefsData }),
  useUpdateNotificationPreferences: () => ({ mutate: mockMutate }),
}));

const mockCheckNotificationPermission = jest.fn();
const mockRequestNotificationPermission = jest.fn();
const mockOpenNotificationSettings = jest.fn();
jest.mock('@shared/services/firebase', () => ({
  checkNotificationPermission: (...a: any[]) => mockCheckNotificationPermission(...a),
  requestNotificationPermission: (...a: any[]) => mockRequestNotificationPermission(...a),
  openNotificationSettings: (...a: any[]) => mockOpenNotificationSettings(...a),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<PrivacySettingsScreen />);
    await flush();
    await flush();
  });
  mountedRenderer = renderer;
  return renderer;
}

afterEach(() => {
  act(() => { mountedRenderer?.unmount(); });
  mountedRenderer = null;
});

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

function toggles(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findAllByType(CustomToggle);
}

function rowByText(renderer: TestRenderer.ReactTestRenderer, text: string) {
  return renderer.root.findAllByType(TouchableOpacity).find((n) =>
    n.findAllByType(Text).some((t) => t.props.children === text)
  )!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPrefsData = { push_enabled: false };
  mockApiGet.mockResolvedValue({ data: { email_opt_in: false, sms_opt_in: false, push_opt_in: false } });
  mockApiPut.mockResolvedValue({ data: {} });
  mockApiPost.mockResolvedValue({ data: {} });
  mockApiDelete.mockResolvedValue({ data: {} });
});

describe('push notification toggle — turning ON', () => {
  it('saves directly when permission is already granted', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: true });
    const renderer = await renderScreen();
    await act(async () => {
      toggles(renderer)[0].props.onValueChange(true);
      await flush();
    });
    expect(mockMutate).toHaveBeenCalledWith({ push_enabled: true }, expect.anything());
    expect(mockRequestNotificationPermission).not.toHaveBeenCalled();
  });

  it('requests permission when not granted, and saves if the request succeeds', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    mockRequestNotificationPermission.mockResolvedValue(true);
    const renderer = await renderScreen();
    await act(async () => {
      toggles(renderer)[0].props.onValueChange(true);
      await flush();
    });
    expect(mockMutate).toHaveBeenCalledWith({ push_enabled: true }, expect.anything());
  });

  it('toasts and opens device Settings, never saving, when the permission request is denied', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    mockRequestNotificationPermission.mockResolvedValue(false);
    const renderer = await renderScreen();
    await act(async () => {
      toggles(renderer)[0].props.onValueChange(true);
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Permissions Required',
      'Push notifications are disabled in device settings. Tap to open Settings.',
      'warning',
    );
    expect(mockOpenNotificationSettings).toHaveBeenCalled();
    expect(mockMutate).not.toHaveBeenCalled();
  });
});

describe('marketing consent toggles', () => {
  it('hydrate from GET /marketing/preferences', async () => {
    mockApiGet.mockResolvedValue({ data: { email_opt_in: true, sms_opt_in: false, push_opt_in: true } });
    const renderer = await renderScreen();
    const [, email, sms, push] = toggles(renderer);
    expect(email.props.value).toBe(true);
    expect(sms.props.value).toBe(false);
    expect(push.props.value).toBe(true);
  });

  it('saves the email channel and reverts on failure', async () => {
    const renderer = await renderScreen();
    const email = toggles(renderer)[1];
    await act(async () => { await email.props.onValueChange(true); await flush(); });
    expect(mockApiPut).toHaveBeenCalledWith('/marketing/preferences', { email_opt_in: true, source: 'rider_app' });
    expect(toggles(renderer)[1].props.value).toBe(true);

    mockApiPut.mockRejectedValueOnce(new Error('down'));
    await act(async () => { await toggles(renderer)[1].props.onValueChange(false); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Update Failed', 'Could not save your preference. Please try again.', 'danger');
    // Reverted back to true (the value before this failed attempt).
    expect(toggles(renderer)[1].props.value).toBe(true);
  });
});

describe('data section', () => {
  it('handleDownloadData toasts success', async () => {
    const renderer = await renderScreen();
    const row = rowByText(renderer, 'privacy.download_data');
    await act(async () => { await row.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/users/data-export');
    expect(mockShowToast).toHaveBeenCalledWith('privacy.download_requested', 'privacy.download_requested_msg', 'success');
  });

  it('handleDownloadData toasts the backend failure message', async () => {
    mockApiPost.mockRejectedValue(new Error('rate limited'));
    const renderer = await renderScreen();
    const row = rowByText(renderer, 'privacy.download_data');
    await act(async () => { await row.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Export Failed', 'Could not request your data export. Please try again.', 'danger');
  });

  it('handleDeleteAccount opens a confirm sheet; confirming deletes, logs out, and navigates to /login', async () => {
    const renderer = await renderScreen();
    const row = rowByText(renderer, 'privacy.delete_account');
    act(() => { row.props.onPress(); });
    expect(allText(renderer)).toContain('privacy.delete_confirm_title');
    const confirmBtn = renderer.root.findAllByProps({ accessibilityLabel: 'confirm-privacy.delete_my_account' })[0];
    await act(async () => { await confirmBtn.props.onPress(); await flush(); });
    expect(mockApiDelete).toHaveBeenCalledWith('/users/account');
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('a delete-account failure toasts instead of navigating', async () => {
    mockApiDelete.mockRejectedValue(new Error('server error'));
    const renderer = await renderScreen();
    const row = rowByText(renderer, 'privacy.delete_account');
    act(() => { row.props.onPress(); });
    const confirmBtn = renderer.root.findAllByProps({ accessibilityLabel: 'confirm-privacy.delete_my_account' })[0];
    await act(async () => { await confirmBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Delete Failed', 'Could not delete account. Please contact support.', 'danger');
    expect(mockLogout).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('Privacy Policy and Terms of Service rows navigate to /legal with the right type', async () => {
    const renderer = await renderScreen();
    act(() => { rowByText(renderer, 'privacy.privacy_policy').props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/legal?type=privacy');
    act(() => { rowByText(renderer, 'privacy.terms_of_service').props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/legal?type=tos');
  });
});

it('the back button navigates back', async () => {
  const renderer = await renderScreen();
  const backBtn = renderer.root.findAllByType(TouchableOpacity)[0];
  act(() => { backBtn.props.onPress(); });
  expect(mockBack).toHaveBeenCalled();
});
