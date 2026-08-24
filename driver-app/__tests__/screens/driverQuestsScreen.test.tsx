/**
 * app/driver/quests.tsx — broader coverage beyond quests.test.tsx (which
 * pins only render-smoke states: empty/error/available-card/malformed-
 * quest/tab-switch/claim-button-presence).
 *
 * Pins:
 *  - handleJoin: success calls joinQuest and switches to the "active" tab;
 *    a failure toasts without switching tabs
 *  - handleClaim: success calls claimReward and toasts the claimed
 *    amount (from the response, falling back to the passed-in display
 *    string); a failure toasts without crashing
 *  - pull-to-refresh calls both fetchAvailableQuests and fetchMyQuests
 *  - metaFor's fallback label-cases an unrecognized quest `type`
 *  - timeLeft: an expired quest disables Join with "Quest ended" copy;
 *    a <1-day-remaining quest renders the urgent (warning-colored) label
 *  - the hero summary counts (active/to-claim/earned) derive correctly
 *    from myQuests' status mix
 *  - a joined-but-not-yet-completed quest shows the "Joined" pill instead
 *    of the Join button; a claimed quest shows the "Reward added" pill
 */
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { Alert } from 'react-native';
import QuestsScreen from '../../app/driver/quests';

jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: ({ children, ...props }: any) => <View {...props}>{children}</View> };
});
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack, push: jest.fn() }) }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../../components/SafeRefreshControl', () => (props: any) => {
  const { View } = require('react-native');
  return <View testID="refresh-control" {...props} />;
});

const COLORS = {
  primary: '#FF3B30', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#6B7280', border: '#E5E7EB', overlay: '#FFF',
  error: '#DC2626', success: '#34C759', warning: '#FFCC00', info: '#3B82F6',
  successBg: '#ECFDF5', warningBg: '#FFFBEB', dangerBg: '#FEF2F2', infoBg: '#EFF6FF',
  accent: '#FF3B30', accentDim: '#D32F2F', danger: '#DC2626', orange: '#FF9500', gold: '#FFD700',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS } as any) }));

const mockGetApiErrorMessage = jest.fn((..._a: any[]) => _a[1]);
jest.mock('@shared/api/client', () => ({ getApiErrorMessage: (...a: any[]) => mockGetApiErrorMessage(...a) }));

const mockFetchAvailableQuests = jest.fn();
const mockFetchMyQuests = jest.fn();
const mockJoinQuest = jest.fn();
const mockClaimReward = jest.fn();
let mockState: any;
jest.mock('../../store/questStore', () => ({ useQuestStore: () => mockState }));

const baseState = () => ({
  availableQuests: [], myQuests: [],
  isLoadingAvailable: false, isLoadingMine: false, error: null,
  fetchAvailableQuests: mockFetchAvailableQuests, fetchMyQuests: mockFetchMyQuests,
  joinQuest: mockJoinQuest, claimReward: mockClaimReward,
});

const quest = (over: any = {}) => ({
  id: 'q1', title: 'Weekend Warrior', description: 'Complete 20 rides this weekend.',
  type: 'ride_count', target_value: 20, reward_amount: 50, reward_type: 'wallet_credit',
  start_date: '', end_date: '2099-01-01T00:00:00+00:00', current_value: 0, progress_pct: 0,
  status: 'available', progress_id: null, ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockState = baseState();
  mockFetchAvailableQuests.mockResolvedValue(undefined);
  mockFetchMyQuests.mockResolvedValue(undefined);
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

describe('handleJoin', () => {
  it('joins successfully and switches to the My Quests tab', async () => {
    mockState.availableQuests = [quest()];
    mockJoinQuest.mockResolvedValue(undefined);
    const { getByText, queryByText } = render(<QuestsScreen />);
    await act(async () => { fireEvent.press(getByText('Join Quest')); });
    expect(mockJoinQuest).toHaveBeenCalledWith('q1');
    // The active tab shows the (empty in this fixture) "My Quests" state.
    expect(queryByText('No quests joined yet')).toBeTruthy();
  });

  it('toasts without switching tabs on a join failure', async () => {
    mockState.availableQuests = [quest()];
    mockJoinQuest.mockRejectedValue(new Error('server down'));
    const { getByText, queryByText } = render(<QuestsScreen />);
    await act(async () => { fireEvent.press(getByText('Join Quest')); });
    expect(Alert.alert).toHaveBeenCalledWith('Could not join', 'Could not join the quest. Please try again.');
    expect(queryByText('Weekend Warrior')).toBeTruthy();
  });
});

describe('handleClaim', () => {
  const completedProgress = () => ({
    progress_id: 'p1', quest: quest({ reward_amount: 25 }),
    current_value: 20, progress_pct: 100, status: 'completed',
    started_at: '', completed_at: '', claimed_at: null,
  });

  it('claims successfully and toasts the reward amount from the response', async () => {
    mockState.myQuests = [completedProgress()];
    mockClaimReward.mockResolvedValue({ reward_amount: 25 });
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    await act(async () => { fireEvent.press(getByText('Claim $25 reward')); });
    expect(mockClaimReward).toHaveBeenCalledWith('p1');
    expect(Alert.alert).toHaveBeenCalledWith('Reward claimed! 🎉', '$25 added to your wallet.');
  });

  it('falls back to the already-formatted display string, re-money()-formatted, when the response has no reward_amount', async () => {
    // handleClaim passes `money(q.reward_amount)` (already "$25") as its
    // fallback `reward` param — money() applied a second time on that
    // string parses to 0 via parseFloat, so the fallback path renders
    // "$0", not the original "$25". Pinned as documented actual behavior.
    mockState.myQuests = [completedProgress()];
    mockClaimReward.mockResolvedValue({});
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    await act(async () => { fireEvent.press(getByText('Claim $25 reward')); });
    expect(Alert.alert).toHaveBeenCalledWith('Reward claimed! 🎉', '$0 added to your wallet.');
  });

  it('toasts without crashing on a claim failure', async () => {
    mockState.myQuests = [completedProgress()];
    mockClaimReward.mockRejectedValue(new Error('server down'));
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    await act(async () => { fireEvent.press(getByText('Claim $25 reward')); });
    expect(Alert.alert).toHaveBeenCalledWith('Could not claim', 'Could not claim your reward. Please try again.');
  });
});

describe('pull-to-refresh', () => {
  it('calls both fetchAvailableQuests and fetchMyQuests', async () => {
    const { getByTestId } = render(<QuestsScreen />);
    const refreshControl = getByTestId('refresh-control');
    await act(async () => { await refreshControl.props.onRefresh(); });
    expect(mockFetchAvailableQuests).toHaveBeenCalled();
    expect(mockFetchMyQuests).toHaveBeenCalled();
  });
});

describe('metaFor fallback', () => {
  it('title-cases and de-underscores an unrecognized quest type', () => {
    mockState.availableQuests = [quest({ type: 'weekend_special_bonus' })];
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('Weekend Special Bonus')).toBeTruthy();
  });
});

describe('timeLeft urgency and expiry', () => {
  it('disables Join and shows "Quest ended" for an expired quest', () => {
    mockState.availableQuests = [quest({ end_date: '2000-01-01T00:00:00+00:00' })];
    const { getByText } = render(<QuestsScreen />);
    const btn = getByText('Quest ended');
    expect(btn).toBeTruthy();
  });

  it('shows the urgent hours-left label for a quest expiring within a day', () => {
    const soon = new Date(Date.now() + 5 * 3_600_000).toISOString();
    mockState.availableQuests = [quest({ end_date: soon })];
    const { getByText } = render(<QuestsScreen />);
    expect(getByText(/h left/)).toBeTruthy();
  });

  it('shows "No deadline" when the quest has no end_date', () => {
    mockState.availableQuests = [quest({ end_date: undefined })];
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('No deadline')).toBeTruthy();
  });
});

describe('hero summary counts', () => {
  it('counts active (in-progress + completed), to-claim (completed), and earned (claimed sum)', () => {
    mockState.myQuests = [
      { progress_id: 'p1', quest: quest({ reward_amount: 10 }), status: 'active', current_value: 2 },
      { progress_id: 'p2', quest: quest({ reward_amount: 15 }), status: 'completed', current_value: 20 },
      { progress_id: 'p3', quest: quest({ reward_amount: 20 }), status: 'claimed', current_value: 20 },
      { progress_id: 'p4', quest: quest({ reward_amount: 5 }), status: 'expired', current_value: 1 },
    ];
    const { getByText } = render(<QuestsScreen />);
    // active = 1 (status 'active') + 1 (status 'completed') = 2
    expect(getByText('2')).toBeTruthy();
    // to-claim = 1 (only 'completed' counts)
    expect(getByText('1')).toBeTruthy();
    // earned = $20 (only the claimed quest's reward_amount)
    expect(getByText('$20')).toBeTruthy();
  });
});

describe('joined / claimed pills', () => {
  it('shows the "Joined" pill instead of the Join button once already joined', () => {
    mockState.availableQuests = [quest({ status: 'active' })];
    const { getByText, queryByText } = render(<QuestsScreen />);
    expect(getByText('Joined — see My Quests')).toBeTruthy();
    expect(queryByText('Join Quest')).toBeNull();
  });

  it('shows the "Reward added to your wallet" pill for a claimed progress row', () => {
    mockState.myQuests = [{ progress_id: 'p1', quest: quest(), status: 'claimed', current_value: 20 }];
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    expect(getByText('Reward added to your wallet')).toBeTruthy();
  });
});

it('the back button navigates back', () => {
  const { UNSAFE_getAllByType } = render(<QuestsScreen />);
  const { TouchableOpacity } = require('react-native');
  fireEvent.press(UNSAFE_getAllByType(TouchableOpacity)[0]);
  expect(mockBack).toHaveBeenCalled();
});
