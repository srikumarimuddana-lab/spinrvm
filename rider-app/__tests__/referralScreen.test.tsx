/**
 * app/referral.tsx — rider referral hub. Pins:
 *  - GET /users/referral + /users/referrals load in parallel on mount
 *  - a load failure shows the error state (not a blank "zeroed" screen)
 *    with a working Retry button, per the file's own stated rationale
 *  - "Copy" writes the code to the clipboard and toasts
 *  - "Share" opens the native share sheet with the code embedded, falling
 *    back to clipboard-copy when Share.share itself throws
 *  - the $ Earned stat sums referral_earnings + referee_earnings
 *  - the invite list renders qualified ("Earned") vs. in-progress
 *    ("Pending" + rides progress) rows, and the empty state when there
 *    are no invites yet
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Share } from 'react-native';

import RiderReferralScreen from '../app/referral';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const mockSetStringAsync = jest.fn().mockResolvedValue(undefined);
jest.mock('expo-clipboard', () => ({
  setStringAsync: (...a: any[]) => mockSetStringAsync(...a),
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const BASE_INFO = {
  referral_code: 'RIDER123',
  referral_link: 'https://spinr.ca/r/RIDER123',
  total_referrals: 2,
  qualified_referrals: 1,
  pending_referrals: 1,
  referral_earnings: '10.00',
  referee_earnings: '0',
  referrer_reward: '10',
  referee_reward: '5',
  rides_required: 3,
  terms: 'Referral rewards apply after 3 completed rides.',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RiderReferralScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/users/referral') return Promise.resolve({ data: BASE_INFO });
    if (url === '/users/referrals') return Promise.resolve({ data: { referees: [] } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('RiderReferralScreen', () => {
  it('loads referral info and referees in parallel, showing the code', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/users/referral');
    expect(mockApiGet).toHaveBeenCalledWith('/users/referrals');
    expect(allText(r)).toContain('RIDER123');
  });

  it('shows the error state with a working retry when the load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load your referrals");

    mockApiGet.mockImplementation((url: string) => {
      if (url === '/users/referral') return Promise.resolve({ data: BASE_INFO });
      if (url === '/users/referrals') return Promise.resolve({ data: { referees: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const retryBtn = r.root.findByProps({ accessibilityLabel: 'Retry loading referrals' });
    await act(async () => {
      await retryBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('RIDER123');
  });

  it('copies the code and toasts on "Copy"', async () => {
    const r = await renderScreen();
    const copyBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Copy')))!;
    await act(async () => {
      await copyBtn.props.onPress();
      await flush();
    });
    expect(mockSetStringAsync).toHaveBeenCalledWith('RIDER123');
    expect(mockShowToast).toHaveBeenCalledWith('Copied!', 'Referral code copied to clipboard', 'success');
  });

  it('shares the code via the native share sheet', async () => {
    const shareSpy = jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as any);
    const r = await renderScreen();
    const shareBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Share Your Code')))!;
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(shareSpy).toHaveBeenCalledWith({ message: expect.stringContaining('RIDER123') });
    shareSpy.mockRestore();
  });

  it('falls back to clipboard-copy when Share.share throws', async () => {
    const shareSpy = jest.spyOn(Share, 'share').mockRejectedValue(new Error('dismissed'));
    const r = await renderScreen();
    const shareBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Share Your Code')))!;
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(mockSetStringAsync).toHaveBeenCalledWith('RIDER123');
    expect(mockShowToast).toHaveBeenCalledWith('Copied!', 'Referral code copied to clipboard', 'success');
    shareSpy.mockRestore();
  });

  it('sums referral_earnings + referee_earnings for the Earned stat', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/users/referral') {
        return Promise.resolve({ data: { ...BASE_INFO, referral_earnings: '10.00', referee_earnings: '5.00' } });
      }
      return Promise.resolve({ data: { referees: [] } });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('$15.00');
    const text = allText(r);
    expect(text).toContain('Includes your $');
    expect(text).toContain('5.00');
    expect(text).toContain('signup bonus');
  });

  it('shows the empty state when there are no invites yet', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No invites yet');
  });

  it('renders qualified vs. in-progress referee rows', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/users/referral') return Promise.resolve({ data: BASE_INFO });
      return Promise.resolve({
        data: {
          referees: [
            { name: 'Alice', referred_at: '2026-01-01', completed_rides: 3, rides_required: 3, qualified: true, status: 'earned' },
            { name: 'Bob', referred_at: '2026-01-05', completed_rides: 1, rides_required: 3, qualified: false, status: 'in_progress' },
          ],
        },
      });
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Alice');
    expect(text).toContain('Reward earned');
    expect(text).toContain('Bob');
    expect(text).toContain('[1,"/",3," rides"]');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
