/**
 * SupportScreen — Contact tab contact details.
 *
 * Code under test: shared/components/SupportScreen.tsx (rider-app's
 * app/support.tsx and driver-app's app/driver/help.tsx both render this one
 * component, so these pins cover the Help screen in both apps).
 *
 * Pins the fix for the reported bug and its follow-up: the Contact tab showed
 * a hardcoded `1-800-SPINR` placeholder even when admin Settings → Company
 * Info had no phone configured, so Help advertised a number that dials
 * nowhere. Every company detail on this screen — name, address, email, phone,
 * website — now comes from `/company-info` only, with NO hardcoded fallback,
 * and the hardcoded "Mon–Fri 9am–6pm CST" line (never an admin-configurable
 * setting, so never something the app should assert) is gone.
 *
 * The rule these tests enforce: a field the operator left blank renders
 * nothing. The unconditional ticket form above is what keeps Contact from
 * becoming a dead end, not a placeholder nobody can correct.
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
let mockAiEnabled = false;
let mockAiConfigPromise: Promise<{ data: { enabled: boolean; mode: string } }> | null = null;
const mockApiPost = jest.fn((..._args: any[]) => Promise.resolve({ data: {} as any }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (url?: string) => {
      if (url === '/company-info') return Promise.resolve({ data: mockCompanyInfo });
      if (url === '/ai/config') {
        if (mockAiConfigPromise) return mockAiConfigPromise;
        return Promise.resolve({
          data: { enabled: mockAiEnabled, mode: mockAiEnabled ? 'enabled' : 'hidden' },
        });
      }
      return Promise.resolve({ data: [] });
    },
    post: (...args: any[]) => mockApiPost(...args),
  },
}));

/** Every rendered string, flattened depth-first in render order.
 *
 * `getAllByText` answers "is it there", not "in what order" — this is what
 * lets the row-order test below assert sequence rather than presence. */
function textsInRenderOrder(node: any, out: string[] = []): string[] {
  if (node == null) return out;
  if (typeof node === 'string') {
    out.push(node);
  } else if (Array.isArray(node)) {
    node.forEach((child) => textsInRenderOrder(child, out));
  } else if (node.children) {
    node.children.forEach((child: any) => textsInRenderOrder(child, out));
  }
  return out;
}

async function renderTab(initialTab: 'contact' | 'chat') {
  // `render()` must stay OUTSIDE `act()`. On its first call RNTL runs
  // `detectHostComponentNames`, which renders its own probe tree and reads
  // `.root`; nesting that inside an outer `act()` tears the probe down before
  // it is read, and every test in this file dies with
  // "Can't access .root on unmounted test renderer" (all 12 did — see
  // rider-app-test on 8ca66e69).
  const utils = render(<SupportScreen role="rider" initialTab={initialTab} />);
  // Then flush the mount effects' promises (/faqs, /company-info, /ai/config)
  // so the screen has its settings-driven data before any assertion.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return utils;
}

const renderContactTab = () => renderTab('contact');

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
    const { queryByText, queryAllByText, queryByLabelText } = await renderContactTab();

    // The specific regression: the removed placeholder must never appear.
    expect(queryByText('1-800-SPINR')).toBeNull();
    // ...and no phone chip or company-card phone row is rendered in its place.
    expect(queryAllByText('call-outline')).toHaveLength(0);
    // Nothing is left behind in the accessibility tree either — a screen
    // reader must not find a call affordance that sighted users can't see.
    expect(queryByLabelText(/^Call support/)).toBeNull();
  });

  it('treats an empty-string phone from /company-info the same as unset', async () => {
    // What the endpoint actually returns for a blank settings column —
    // `settings.get("company_phone", "") or ""` in routes/settings.py.
    mockCompanyInfo = { phone: '', email: '', address: '', website: '' };
    const { queryAllByText } = await renderContactTab();

    expect(queryAllByText('call-outline')).toHaveLength(0);
  });

  it('renders no company details at all when nothing is configured', async () => {
    const { queryByText, queryAllByText, queryByLabelText } = await renderContactTab();

    // No email chip or card row either — every detail is settings-driven now,
    // so an unconfigured Company Info section shows nothing rather than a
    // placeholder the operator never entered and cannot correct.
    expect(queryByText('support@spinr.ca')).toBeNull();
    expect(queryByLabelText(/^Email support/)).toBeNull();
    // The Contact tab itself always has this icon; no email action chip or
    // company-card row should add another one when the email is unset.
    expect(queryAllByText('mail-outline')).toHaveLength(1);
    // ...and the former hardcoded identity/address/website placeholders.
    expect(queryByText('SPINR MOBILITY INC.')).toBeNull();
    expect(queryByText('Saskatoon, SK, Canada')).toBeNull();
    expect(queryByText('www.spinr.ca')).toBeNull();
    expect(queryAllByText('location-outline')).toHaveLength(0);
    expect(queryAllByText('globe-outline')).toHaveLength(0);

    // The ticket form is unconditional, so Contact is still not a dead end.
    expect(queryByText('Submit Report')).toBeTruthy();
  });

  it('never renders the removed hardcoded business hours', async () => {
    // Support hours are not an admin-configurable setting, so the app must
    // not assert them. Checked with details populated, since the line used to
    // sit at the foot of the company card.
    mockCompanyInfo = { name: 'Spinr Mobility Inc.', phone: '+1 306 555 0100' };
    const { queryByText } = await renderContactTab();

    expect(queryByText(/Mon.*Fri/)).toBeNull();
    expect(queryByText(/9am/)).toBeNull();
    expect(queryByText(/CST/)).toBeNull();
  });

  it('renders the configured company name, and no placeholder when unset', async () => {
    mockCompanyInfo = { name: 'Acme Rides Ltd.', address: '1 Main St, Saskatoon SK' };
    const withName = await renderContactTab();
    expect(withName.getByText('Acme Rides Ltd.')).toBeTruthy();
    withName.unmount();

    // Address alone still renders — it just gets no invented title above it.
    mockCompanyInfo = { address: '1 Main St, Saskatoon SK' };
    const withoutName = await renderContactTab();
    expect(withoutName.getByText('1 Main St, Saskatoon SK')).toBeTruthy();
    expect(withoutName.queryByText('SPINR MOBILITY INC.')).toBeNull();
  });

  it('omits the card entirely when a name is the only thing configured', async () => {
    // A name with no details is a caption with nothing to caption. Rendering
    // the card for it leaves an elevated, padded panel holding one line,
    // which reads as broken rather than minimal.
    mockCompanyInfo = { name: 'Acme Rides Ltd.' };
    const { queryByText, queryAllByText } = await renderContactTab();

    expect(queryByText('Acme Rides Ltd.')).toBeNull();
    expect(queryAllByText('location-outline')).toHaveLength(0);
    expect(queryByText('Submit Report')).toBeTruthy();
  });

  it('orders the company rows address → email → phone → website', async () => {
    // Both company blocks share one `companyRows` array so they cannot drift
    // apart. Pinned because unifying them changed the FAQ footer's previous
    // address/phone/email/website order, and nothing else guards it.
    mockCompanyInfo = {
      address: '1 Main St',
      email: 'help@spinr.ca',
      phone: '+1 306 555 0100',
      website: 'https://spinr.ca',
    };
    const utils = await renderContactTab();
    const texts = textsInRenderOrder(utils.toJSON());

    // Phone and email also appear in the chip row ABOVE the card, so compare
    // last occurrences — those are the card's own rows.
    const card = (value: string) => texts.lastIndexOf(value);
    expect(card('1 Main St')).toBeGreaterThan(-1);
    expect(card('1 Main St')).toBeLessThan(card('help@spinr.ca'));
    expect(card('help@spinr.ca')).toBeLessThan(card('+1 306 555 0100'));
    expect(card('+1 306 555 0100')).toBeLessThan(card('https://spinr.ca'));
  });

  it('renders the configured phone in both the chip and the company card', async () => {
    mockCompanyInfo = { phone: '+1 306 555 0100' };
    const { getAllByText, getByLabelText } = await renderContactTab();

    expect(getAllByText('call-outline')).toHaveLength(2);
    expect(getAllByText('+1 306 555 0100')).toHaveLength(2);
    // The chip is reachable by its accessible name, not just its visible text.
    expect(getByLabelText('Call support at +1 306 555 0100')).toBeTruthy();
  });

  it('dials the configured number with separators stripped and the + kept', async () => {
    mockCompanyInfo = { phone: '+1 (306) 555-0100' };
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as any);

    const { getByLabelText } = await renderContactTab();
    fireEvent.press(getByLabelText('Call support at +1 (306) 555-0100'));

    expect(openURL).toHaveBeenCalledWith('tel:+13065550100');
  });

  it('renders the configured support email in both the chip and the card', async () => {
    mockCompanyInfo = { email: 'help@spinr.ca' };
    const { getAllByText, getByLabelText, queryByText } = await renderContactTab();

    expect(getAllByText('help@spinr.ca')).toHaveLength(2);
    expect(getByLabelText('Email support at help@spinr.ca')).toBeTruthy();
    // The old built-in address must not appear alongside the configured one.
    expect(queryByText('support@spinr.ca')).toBeNull();
  });
});

describe('SupportScreen AI chat — failure copy quotes the configured email', () => {
  beforeEach(() => {
    mockCompanyInfo = {};
    mockAiEnabled = true;
    mockAiConfigPromise = null;
    jest.clearAllMocks();
  });

  afterEach(() => {
    mockAiEnabled = false;
    mockAiConfigPromise = null;
  });

  it('keeps chat out of view while config is pending, then shows it when enabled', async () => {
    let resolveConfig!: (value: { data: { enabled: boolean; mode: string } }) => void;
    mockAiConfigPromise = new Promise((resolve) => { resolveConfig = resolve; });

    const utils = render(<SupportScreen role="rider" initialTab="chat" />);
    expect(utils.queryByPlaceholderText('Ask a question...')).toBeNull();
    expect(utils.queryByText('AI Chat')).toBeNull();
    expect(utils.queryByText('AI Assistant coming soon')).toBeNull();
    expect(utils.getByLabelText('Loading support chat availability')).toBeTruthy();

    await act(async () => {
      resolveConfig({ data: { enabled: true, mode: 'enabled' } });
      await mockAiConfigPromise;
    });
    expect(utils.getByPlaceholderText('Ask a question...')).toBeTruthy();
  });

  it('falls back to FAQ without exposing chat when config rejects', async () => {
    let rejectConfig!: (reason: Error) => void;
    mockAiConfigPromise = new Promise((_resolve, reject) => { rejectConfig = reject; });

    const utils = render(<SupportScreen role="rider" initialTab="chat" />);
    expect(utils.queryByPlaceholderText('Ask a question...')).toBeNull();

    await act(async () => {
      rejectConfig(new Error('configuration unavailable'));
      await mockAiConfigPromise?.catch(() => undefined);
    });
    expect(utils.queryByText('AI Chat')).toBeNull();
    expect(utils.getByPlaceholderText('Search questions...')).toBeTruthy();
  });

  /** Type a message and send it, letting the rejected POST settle. */
  async function sendChat(utils: ReturnType<typeof render>) {
    await act(async () => {
      fireEvent.changeText(utils.getByPlaceholderText('Ask a question...'), 'hello');
    });
    await act(async () => {
      // The send button's only child is the mocked Ionicons name.
      fireEvent.press(utils.getByText('send'));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('omits the "or contact ..." clause entirely when no email is configured', async () => {
    // This copy used to hardcode support@spinr.ca. With nothing configured it
    // must name no address at all rather than one the operator cannot change.
    mockApiPost.mockRejectedValueOnce(new Error('network down'));
    const utils = await renderTab('chat');
    await sendChat(utils);

    expect(
      utils.getByText("I'm having trouble connecting right now. Please try again."),
    ).toBeTruthy();
    expect(utils.queryByText(/support@spinr\.ca/)).toBeNull();
  });

  it('names the configured email when one is set', async () => {
    mockCompanyInfo = { email: 'help@spinr.ca' };
    mockApiPost.mockRejectedValueOnce(new Error('network down'));
    const utils = await renderTab('chat');
    await sendChat(utils);

    expect(
      utils.getByText(
        "I'm having trouble connecting right now. Please try again or contact help@spinr.ca.",
      ),
    ).toBeTruthy();
  });
});
