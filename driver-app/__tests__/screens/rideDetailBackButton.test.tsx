/**
 * Ranked blocker #22: the map overlay back button on the driver ride-detail
 * screen was an icon-only TouchableOpacity with no accessibilityLabel.
 * Only rendered once pickup+dropoff coords resolve (it overlays the map),
 * so the fixture ride below supplies both.
 */
import React from 'react';
import { render, waitFor } from '@testing-library/react-native';
import RideDetailScreen from '../../app/driver/ride-detail';

const mockGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: unknown[]) => mockGet(...a) },
}));

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn() }),
  useLocalSearchParams: () => ({ id: 'ride-1' }),
}));

jest.mock('react-native-maps', () => {
  const ReactLib = require('react');
  const RN = require('react-native');
  const MapView = ReactLib.forwardRef((props: any, ref: any) =>
    ReactLib.createElement(RN.View, { ref }, props.children),
  );
  return {
    __esModule: true,
    default: MapView,
    PROVIDER_GOOGLE: 'google',
    Polyline: () => null,
    Marker: () => null,
  };
});

jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
jest.mock('@shared/hooks/useCompletedRouteRefresh', () => ({ useCompletedRouteRefresh: () => {} }));
jest.mock('@shared/utils/routeSegments', () => ({
  routeQualityLabel: () => 'Good',
  toReactNativeRouteSections: () => [],
  toReactNativeSegments: () => [],
}));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F3F4F6',
      text: '#111', textDim: '#666', border: '#E5E7EB',
    },
  }),
}));
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

const RIDE_WITH_COORDS = {
  id: 'ride-1',
  status: 'completed',
  pickup_lat: 52.13,
  pickup_lng: -106.67,
  dropoff_lat: 52.14,
  dropoff_lng: -106.68,
};

describe('Driver RideDetailScreen — map back button accessibility', () => {
  it('has a non-empty accessibilityLabel announcing itself as a button', async () => {
    mockGet.mockResolvedValue({ data: RIDE_WITH_COORDS });
    const screen = render(<RideDetailScreen />);

    const backButton = await waitFor(() => screen.getByLabelText('Go back'));
    expect(backButton.props.accessibilityRole).toBe('button');
  });
});
