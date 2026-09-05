import React from 'react';
import { render, act } from '@testing-library/react-native';
import { Ionicons } from '@expo/vector-icons';
import { MapControls } from '../../components/dashboard/MapControls';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const COLORS = {
  primary: '#EF4444',
  textSecondary: '#6B7280',
  text: '#111111',
  overlay: 'rgba(255,255,255,0.8)',
  border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS }) }));

const location = { coords: { latitude: 52.1, longitude: -106.6 } };
const mapRef = { current: { animateToRegion: jest.fn() } };
const currentRegionRef = { current: { latitudeDelta: 0.01, longitudeDelta: 0.01 } };

// The recenter ("locate") icon — matched by its own two possible glyph
// names (mutually exclusive: exactly one renders per state) rather than
// position or size, since the zoom/compass icons in the same control stack
// share the same 24px size.
function findRecenterIcon(root: any) {
  return root
    .findAllByType(Ionicons as any)
    .find((el: any) => el.props.name === 'locate' || el.props.name === 'locate-outline');
}

describe('MapControls — off-follow recenter icon (isFollowing)', () => {
  it('defaults to the filled, accented icon when isFollowing is omitted', () => {
    const { UNSAFE_root } = render(
      <MapControls mapRef={mapRef as any} location={location} currentRegionRef={currentRegionRef as any} />,
    );
    const icon = findRecenterIcon(UNSAFE_root);
    expect(icon.props.name).toBe('locate');
    expect(icon.props.color).toBe(COLORS.primary);
  });

  it('renders the filled, accented icon while following', () => {
    const { UNSAFE_root } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing
      />,
    );
    const icon = findRecenterIcon(UNSAFE_root);
    expect(icon.props.name).toBe('locate');
    expect(icon.props.color).toBe(COLORS.primary);
  });

  it('dims to the outline icon once the driver has panned away (isFollowing=false)', () => {
    const { UNSAFE_root } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing={false}
      />,
    );
    const icon = findRecenterIcon(UNSAFE_root);
    expect(icon.props.name).toBe('locate-outline');
    expect(icon.props.color).toBe(COLORS.textSecondary);
  });

  it('updates the icon live when isFollowing changes via re-render (recenter tapped)', () => {
    const { UNSAFE_root, rerender } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing={false}
      />,
    );
    expect(findRecenterIcon(UNSAFE_root).props.name).toBe('locate-outline');

    rerender(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing
      />,
    );
    expect(findRecenterIcon(UNSAFE_root).props.name).toBe('locate');
  });

  it('reflects the follow state in accessibility props', () => {
    const { UNSAFE_root: following } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing
      />,
    );
    const followingBtn = following.findByProps({ accessibilityLabel: 'Center map on my location' });
    expect(followingBtn.props.accessibilityState).toEqual({ selected: true });

    const { UNSAFE_root: offFollow } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing={false}
      />,
    );
    const offFollowBtn = offFollow.findByProps({ accessibilityLabel: 'Resume following my location' });
    expect(offFollowBtn.props.accessibilityState).toEqual({ selected: false });
  });

  it('still calls onRecenter and does not throw when handleRecenter is pressed, regardless of follow state', () => {
    const onRecenter = jest.fn();
    const { UNSAFE_root } = render(
      <MapControls
        mapRef={mapRef as any}
        location={location}
        currentRegionRef={currentRegionRef as any}
        isFollowing={false}
        onRecenter={onRecenter}
      />,
    );
    const button = UNSAFE_root.findByProps({ accessibilityLabel: 'Resume following my location' });
    act(() => {
      button.props.onPress();
    });
    expect(onRecenter).toHaveBeenCalledTimes(1);
    expect(mapRef.current.animateToRegion).toHaveBeenCalled();
  });
});
