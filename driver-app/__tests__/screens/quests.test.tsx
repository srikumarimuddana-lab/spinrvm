/**
 * Render smoke tests for the Quests screen.
 *
 * Pins the guarantee the screen kept regressing on: every state (loading /
 * error / empty / list, both tabs) paints visible content, and a malformed
 * quest payload (missing id/fields) never drops the card or crashes.
 */
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import QuestsScreen from '../../app/driver/quests';

jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: ({ children, ...props }: any) => <View {...props}>{children}</View> };
});
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-router', () => ({ useRouter: () => ({ back: jest.fn(), push: jest.fn() }) }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const COLORS = {
  primary: '#FF3B30', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#6B7280', border: '#E5E7EB', overlay: '#FFF',
  error: '#DC2626', success: '#34C759', warning: '#FFCC00', info: '#3B82F6',
  successBg: '#ECFDF5', warningBg: '#FFFBEB', dangerBg: '#FEF2F2', infoBg: '#EFF6FF',
  accent: '#FF3B30', accentDim: '#D32F2F', danger: '#DC2626', orange: '#FF9500', gold: '#FFD700',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS }) }));

let mockState: any;
jest.mock('../../store/questStore', () => ({ useQuestStore: () => mockState }));

const baseState = () => ({
  availableQuests: [], myQuests: [],
  isLoadingAvailable: false, isLoadingMine: false, error: null,
  fetchAvailableQuests: jest.fn(), fetchMyQuests: jest.fn(), joinQuest: jest.fn(), claimReward: jest.fn(),
});
beforeEach(() => { mockState = baseState(); });

const quest = (over: any = {}) => ({
  id: 'q1', title: 'Weekend Warrior', description: 'Complete 20 rides this weekend.',
  type: 'ride_count', target_value: 20, reward_amount: 50, reward_type: 'wallet_credit',
  start_date: '', end_date: '2099-01-01T00:00:00+00:00', current_value: 0, progress_pct: 0,
  status: 'available', progress_id: null, ...over,
});

describe('QuestsScreen', () => {
  it('renders the empty state when no quests are available', () => {
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('No quests right now')).toBeTruthy();
  });

  it('renders an error state when the fetch fails', () => {
    mockState.error = 'Network error';
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('Couldn’t load quests')).toBeTruthy();
  });

  it('renders an available quest card with title, reward and join button', () => {
    mockState.availableQuests = [quest()];
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('Weekend Warrior')).toBeTruthy();
    expect(getByText('$50')).toBeTruthy();
    expect(getByText('Join Quest')).toBeTruthy();
  });

  it('still renders a card when a quest is missing its id', () => {
    mockState.availableQuests = [{ title: 'No-Id Quest', reward_amount: 30, type: 'ride_count', status: 'available' } as any];
    const { getByText } = render(<QuestsScreen />);
    expect(getByText('No-Id Quest')).toBeTruthy();
    expect(getByText('$30')).toBeTruthy();
  });

  it('shows the My Quests empty state after switching tabs', () => {
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    expect(getByText('No quests joined yet')).toBeTruthy();
  });

  it('renders a claim button for a completed quest', () => {
    mockState.myQuests = [{
      progress_id: 'p1', quest: quest({ reward_amount: 25 }),
      current_value: 20, progress_pct: 100, status: 'completed',
      started_at: '', completed_at: '', claimed_at: null,
    }];
    const { getByText } = render(<QuestsScreen />);
    fireEvent.press(getByText(/My Quests/));
    expect(getByText('Claim $25 reward')).toBeTruthy();
  });
});
