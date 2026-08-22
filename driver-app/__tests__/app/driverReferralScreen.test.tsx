/**
 * app/driver/referral.tsx — driver-app "Refer & Earn" screen. Pins:
 *  - summary (/drivers/referral) failure shows a full error state with
 *    retry; the secondary referrals-list fetch failing on its own leaves
 *    the summary visible and just empties the invites list
 *  - copy-to-clipboard and share (native Share.share, falling back to
 *    clipboard-copy on a Share failure)
 *  - stats: Earned = referral_earnings + referee_earnings; a positive
 *    referee_earnings shows its own "signup bonus" note
 *  - reward cards: "New driver earns" vs. "No signup bonus" label
 *    depending on referee_reward
 *  - referred-drivers list: qualified drivers show the earned-reward
 *    line; in-progress drivers show a rides-remaining note + progress
 *    bar; the empty state when there are no invites yet
 *  - the referred_by note only renders when present
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Share } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockSetStringAsync = jest.fn();
jest.mock('expo-clipboard', () => ({ setStringAsync: (...a: any[]) => mockSetStringAsync(...a) }));

import ReferralScreen from '../../app/driver/referral';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const REFERRAL_INFO = {
  referral_code: 'DRV-ABC123',
  total_referrals: 3,
  qualified_referrals: 1,
  referral_earnings: '25.00',
  referee_earnings: '0',
  referrer_reward: '25.00',
  referee_reward: '0',
  referred_by: null,
  referral_link: 'https://spinr.ca/r/DRV-ABC123',
  terms: 'Reward paid after 10 qualifying rides.',
};

const DRIVER_QUALIFIED = {
  name: 'Alex Chen', email: 'alex@example.com', referred_at: '2026-01-01', total_trips: 12,
  reward_amount: '25.00', qualified: true, status: 'earned',
};

const DRIVER_IN_PROGRESS = {
  name: 'Bo Singh', email: 'bo@example.com', referred_at: '2026-02-01', total_trips: 4,
  rides_required: 10, rides_remaining: 6, qualified: false, status: 'in_progress',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ReferralScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/drivers/referral') return Promise.resolve({ data: REFERRAL_INFO });
    if (url.startsWith('/drivers/referrals')) return Promise.resolve({ data: { referred_drivers: [] } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as any);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('ReferralScreen (driver-app)', () => {
  it('loads the summary and referrals list on mount', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/referral');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/referrals?limit=50');
    expect(allText(r)).toContain('DRV-ABC123');
  });

  it('shows a full error state with retry when the summary fetch fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.reject(new Error('down'));
      return Promise.resolve({ data: { referred_drivers: [] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load your referrals");
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.resolve({ data: REFERRAL_INFO });
      return Promise.resolve({ data: { referred_drivers: [] } });
    });
    const retryBtn = r.root.findByProps({ accessibilityLabel: 'Retry loading referrals' });
    await act(async () => {
      await retryBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('DRV-ABC123');
  });

  it('keeps the summary visible and just empties the invites list when the referrals-list fetch fails on its own', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.resolve({ data: REFERRAL_INFO });
      if (url.startsWith('/drivers/referrals')) return Promise.reject(new Error('down'));
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('DRV-ABC123');
    expect(allText(r)).toContain('No invites yet');
  });

  it('copies the referral code to the clipboard', async () => {
    const r = await renderScreen();
    const copyBtn = findButtonByText(r, 'Copy');
    await act(async () => {
      await copyBtn.props.onPress();
      await flush();
    });
    expect(mockSetStringAsync).toHaveBeenCalledWith('DRV-ABC123');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Copied!', 'Referral code copied to clipboard');
  });

  it('shares the referral code via native Share', async () => {
    const r = await renderScreen();
    const shareBtn = findButtonByText(r, 'Share Your Code');
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(Share.share).toHaveBeenCalledWith({
      message: 'Join me on Spinr! Download the driver app, then paste my referral code DRV-ABC123 during signup to start driving.',
    });
  });

  it('falls back to clipboard-copy when Share.share fails', async () => {
    (Share.share as jest.Mock).mockRejectedValue(new Error('share failed'));
    const r = await renderScreen();
    const shareBtn = findButtonByText(r, 'Share Your Code');
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(mockSetStringAsync).toHaveBeenCalledWith('DRV-ABC123');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Copied!', 'Referral code copied to clipboard');
  });

  it('shows Earned as the sum of referral_earnings + referee_earnings, and a signup-bonus note when referee_earnings > 0', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') {
        return Promise.resolve({ data: { ...REFERRAL_INFO, referral_earnings: '25.00', referee_earnings: '10.00' } });
      }
      return Promise.resolve({ data: { referred_drivers: [] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","35.00"]');
    expect(allText(r)).toContain('["Includes your $","10.00"," signup bonus"]');
  });

  it('shows "No signup bonus" when referee_reward is 0, and "New driver earns" when positive', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No signup bonus');
  });

  it('shows "New driver earns" when referee_reward is positive', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.resolve({ data: { ...REFERRAL_INFO, referee_reward: '5.00' } });
      return Promise.resolve({ data: { referred_drivers: [] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('New driver earns');
  });

  it('shows a qualified driver\'s earned-reward line', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.resolve({ data: REFERRAL_INFO });
      return Promise.resolve({ data: { referred_drivers: [DRIVER_QUALIFIED] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Alex Chen');
    expect(allText(r)).toContain('["Reward earned · $","25.00"]');
    expect(allText(r)).toContain('Earned');
  });

  it('shows an in-progress driver\'s rides-remaining note and Pending badge', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') return Promise.resolve({ data: REFERRAL_INFO });
      return Promise.resolve({ data: { referred_drivers: [DRIVER_IN_PROGRESS] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Bo Singh');
    expect(allText(r)).toContain('Pending');
    expect(allText(r)).toContain('6 more to unlock');
  });

  it('shows the empty invites state when there are no referred drivers', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No invites yet');
    expect(allText(r)).toContain('Share your code to start earning!');
  });

  it('shows the referred_by note only when present', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Referred by');

    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/referral') {
        return Promise.resolve({ data: { ...REFERRAL_INFO, referred_by: { name: 'Jordan', code: 'JOR1' } } });
      }
      return Promise.resolve({ data: { referred_drivers: [] } });
    });
    const r2 = await renderScreen();
    expect(allText(r2)).toContain('["Referred by ","Jordan"," (","JOR1",")"]');
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
