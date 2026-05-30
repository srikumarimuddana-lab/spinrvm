import React from 'react';
import { render, waitFor } from '@testing-library/react-native';
import ActivityView from '../../components/activity/ActivityView';
import api from '@shared/api/client';
import { useDriverStore } from '../../store/driverStore';

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#FF3B30',
      primaryDark: '#D32F2F',
      background: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceLight: '#F5F5F5',
      text: '#1A1A1A',
      textDim: '#666666',
      border: '#E5E7EB',
      success: '#10B981',
      danger: '#DC2626',
    },
  }),
}));

jest.mock('@expo/vector-icons', () => ({
  Ionicons: () => null,
  MaterialCommunityIcons: () => null,
  FontAwesome5: () => null,
}));

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock('@react-navigation/native', () => {
  const React = require('react');
  return {
    useFocusEffect: (effect: () => void | (() => void)) => {
      React.useEffect(() => effect(), [effect]);
    },
  };
});

jest.mock('../../store/driverStore', () => ({
  useDriverStore: jest.fn(),
}));

const mockUseDriverStore = useDriverStore as unknown as jest.Mock;
const mockApiGet = api.get as jest.Mock;

const makeRide = () => ({
  id: 'ride-001',
  status: 'completed',
  ride_completed_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
  distance_km: 5.2,
  duration_minutes: 18,
  total_earned: 12,
});

const mockStore = {
  earnings: {
    total_earnings: 12,
    total_tips: 0,
    total_incentives: 0,
    total_tax: 0,
    total_rides: 1,
    total_distance_km: 5.2,
    total_duration_minutes: 18,
    average_per_ride: 12,
  },
  fetchEarnings: jest.fn(),
  fetchDriverBalance: jest.fn(),
};

describe('ActivityView', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseDriverStore.mockReturnValue(mockStore);
    mockStore.fetchEarnings.mockResolvedValue(undefined);
    mockStore.fetchDriverBalance.mockResolvedValue(undefined);
  });

  it('keeps ride history visible when earnings loading fails', async () => {
    mockStore.fetchEarnings.mockRejectedValueOnce(new Error('earnings unavailable'));
    mockApiGet.mockResolvedValueOnce({ data: { rides: [makeRide()], total: 1 } });

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    expect(getByText('456 Elm Ave')).toBeTruthy();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/rides/history?limit=20');
  });

  it('renders rider-style cursor history responses during backend/client skew', async () => {
    mockApiGet.mockResolvedValueOnce({ data: { rides: [makeRide()], next_cursor: 'ride-001' } });

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    expect(getByText('1 rides')).toBeTruthy();
  });

  it('renders legacy array history responses during backend/client skew', async () => {
    mockApiGet.mockResolvedValueOnce({ data: [makeRide()] });

    const { getByText } = render(<ActivityView />);

    await waitFor(() => expect(getByText('123 Main St')).toBeTruthy());
    expect(getByText('1 rides')).toBeTruthy();
  });
});
