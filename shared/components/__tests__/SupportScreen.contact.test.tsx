/**
 * SupportScreen — Contact tab contact details.
 *
 * Code under test: shared/components/SupportScreen.tsx (rider-app's
 * app/support.tsx and driver-app's app/driver/help.tsx both render this one
 * component, so these pins cover the Help screen in both apps).
 *
 * Pins the fix for the reported bug: the Contact tab showed a hardcoded
 * `1-800-SPINR` placeholder even when admin Settings → Company Info had no
 * phone configured, so Help advertised a number that dials nowhere. The
 * phone now comes from `/company-info` only, and is omitted entirely when
 * unset — matching what the FAQ tab, the rider Account screen and the driver
 * Profile screen already did.
 *
 * `call-outline` is the structural marker used below: on the Contact tab it
 * appears exactly twice when a phone is configured (the quick-action chip and
 * the company-card row) and never otherwise, so counting it asserts the row's
 * presence rather than just the absence of one placeholder string.
 */
import React from 'react';
import { Linking } from 'react-native';
import { act, fireEvent, render } from '@testing-library/react-native';

import SupportScreen from '../SupportScreen';

jest.mock('@expo/vector-icons', () => {
  const { Text } = require('react-native');
  // Render the icon name as text so tests can assert which rows exist.
  return { Ionicons: ({ name }: { name: string }) => <Text>{name}</Text> };
});

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
}));

jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ granted: false })),
  getLastKnownPositionAsync: jest.fn(() => Promise.resolve(null)),
}));

jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaView: ({ children }: any) => <View>{children}</View> };
});

jest.mock('@shared/components/CustomAlert', () => () => null);
jest.mock('@shared/components/AiAuroraBackground', () => () => null);
jest.mock('@shared/hooks/useLogRocketPrivacyScreen', () => ({
  useLogRocketPrivacyScreen: () => undefined,
}));

const COLORS = {
  primary: '#EF4444',
  background: '#FFF',
  surface: '#FFF',
  surfaceLight: '#F5F5F5',
  text: '#111',
  textSecondary: '#444',
  textDim: '#666',
  border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: COLORS, isDark: false }),
}));

let mockCompanyInfo: Record<string, string> = {};
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (url?: string) => {
      if (url === '/company-info') return Promise.resolve({ data: mockCompanyInfo });
      if (url === '/ai/config') return Promise.resolve({ data: { enabled: false, mode: 'hidden' } });
      return Promise.resolve({ data: [] });
    },
    post: jest.fn(() => Promise.resolve({ data: {} })),
  },
}));

async function renderContactTab() {
  let utils!: ReturnType<typeof render>;
  await act(async () => {
    utils = render(<SupportScreen role="rider" initialTab="contact" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return utils;
}

describe('SupportScreen Contact tab — phone comes from admin settings', () => {
  beforeEach(() => {
    mockCompanyInfo = {};
    jest.clearAllMocks();
  });

  // The Linking.openURL spy below is a real spy on a shared module — restore
  // it so it cannot leak into the tests that follow.
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('renders no phone at all when company_phone is unset in admin settings', async () => {
    const { queryByText, queryAllByText } = await renderContactTab();

    // The specific regression: the removed placeholder must never appear.
    expect(queryByText('1-800-SPINR')).toBeNull();
    // ...and no phone chip or company-card phone row is rendered in its place.
    expect(queryAllByText('call-outline')).toHaveLength(0);
  });

  it('treats an empty-string phone from /company-info the same as unset', async () => {
    // What the endpoint actually returns for a blank settings column —
    // `settings.get("company_phone", "") or ""` in routes/settings.py.
    mockCompanyInfo = { phone: '', email: '', address: '', website: '' };
    const { queryAllByText } = await renderContactTab();

    expect(queryAllByText('call-outline')).toHaveLength(0);
  });

  it('still offers the support email so Contact is never a dead end', async () => {
    const { getAllByText } = await renderContactTab();

    // Chip + company-card row both fall back to the real support address.
    expect(getAllByText('support@spinr.ca').length).toBeGreaterThan(0);
  });

  it('renders the configured phone in both the chip and the company card', async () => {
    mockCompanyInfo = { phone: '+1 306 555 0100' };
    const { getAllByText } = await renderContactTab();

    expect(getAllByText('call-outline')).toHaveLength(2);
    expect(getAllByText('+1 306 555 0100')).toHaveLength(2);
  });

  it('dials the configured number with separators stripped and the + kept', async () => {
    mockCompanyInfo = { phone: '+1 (306) 555-0100' };
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as any);

    const { getAllByText } = await renderContactTab();
    fireEvent.press(getAllByText('+1 (306) 555-0100')[0]);

    expect(openURL).toHaveBeenCalledWith('tel:+13065550100');
  });

  it('prefers the admin-configured support email over the built-in fallback', async () => {
    mockCompanyInfo = { email: 'help@spinr.ca' };
    const { getAllByText, queryByText } = await renderContactTab();

    expect(getAllByText('help@spinr.ca').length).toBeGreaterThan(0);
    expect(queryByText('support@spinr.ca')).toBeNull();
  });
});
