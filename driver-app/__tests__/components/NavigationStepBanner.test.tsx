import React from 'react';
import { render } from '@testing-library/react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  NavigationStepBanner,
  formatManeuverDistance,
  iconForManeuver,
  sosTopOffset,
  SOS_DEFAULT_OFFSET,
} from '../../components/dashboard/NavigationStepBanner';
import type { NavigationStep } from '@shared/utils/navigationSteps';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const COLORS = {
  primary: '#EF4444',
  text: '#111111',
  textDim: '#666666',
  overlay: 'rgba(255,255,255,0.95)',
  border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS }) }));

const STEP: NavigationStep = {
  instruction: 'Turn right onto Albert St',
  maneuver: 'turn-right',
  distanceMeters: 400,
  startLocation: [52.1, -106.6],
  endLocation: [52.11, -106.6],
};

describe('formatManeuverDistance', () => {
  it('rounds sub-km distances to the nearest 10m', () => {
    expect(formatManeuverDistance(83)).toBe('80 m');
    expect(formatManeuverDistance(0)).toBe('0 m');
  });

  it('switches to km with one decimal at or above 1000m', () => {
    expect(formatManeuverDistance(1000)).toBe('1.0 km');
    expect(formatManeuverDistance(1700)).toBe('1.7 km');
  });

  it('clamps a negative distance to zero rather than showing a negative', () => {
    expect(formatManeuverDistance(-40)).toBe('0 m');
  });
});

describe('iconForManeuver', () => {
  it('maps a known Google maneuver string to its icon', () => {
    expect(iconForManeuver('turn-right')).toBe('arrow-forward');
    expect(iconForManeuver('roundabout-left')).toBe('sync');
  });

  it('falls back to a generic navigate icon for null or unknown maneuvers', () => {
    expect(iconForManeuver(null)).toBe('navigate');
    expect(iconForManeuver('some-future-google-maneuver')).toBe('navigate');
  });
});

describe('NavigationStepBanner', () => {
  it('renders the instruction text, distance, and an accessible label', () => {
    const { getByText, UNSAFE_root } = render(
      <NavigationStepBanner step={STEP} distanceToManeuverMeters={420} topOffset={12} />,
    );
    expect(getByText('Turn right onto Albert St')).toBeTruthy();
    expect(getByText('420 m')).toBeTruthy();
    const banner = UNSAFE_root.findByProps({ accessibilityRole: 'text' });
    expect(banner.props.accessibilityLabel).toBe('Turn right onto Albert St, in 420 m');
  });

  it('is non-interactive (pointerEvents none) so it never blocks map gestures underneath', () => {
    const { UNSAFE_root } = render(
      <NavigationStepBanner step={STEP} distanceToManeuverMeters={420} topOffset={12} />,
    );
    const banner = UNSAFE_root.findByProps({ accessibilityRole: 'text' });
    expect(banner.props.pointerEvents).toBe('none');
  });
});

describe('sosTopOffset (SOS stays visible below the turn banner)', () => {
  const insetsTop = 44;

  it('uses the default slot when no turn banner is shown', () => {
    expect(sosTopOffset(insetsTop, false, 60)).toBe(insetsTop + SOS_DEFAULT_OFFSET);
  });

  it('uses the default slot until the banner has reported its height', () => {
    expect(sosTopOffset(insetsTop, true, 0)).toBe(insetsTop + SOS_DEFAULT_OFFSET);
  });

  it('moves just below the banner while a step shows', () => {
    // banner top 8 + height 54 + gap 8 = 70
    expect(sosTopOffset(insetsTop, true, 54)).toBe(insetsTop + 70);
  });

  it('follows a taller (OS-scaled) banner', () => {
    expect(sosTopOffset(insetsTop, true, 71)).toBe(insetsTop + 87);
  });

  it('never moves above its default slot', () => {
    expect(sosTopOffset(insetsTop, true, 20)).toBe(insetsTop + SOS_DEFAULT_OFFSET);
  });
});

describe('NavigationStepBanner height reporting', () => {
  it('reports its rendered height through onHeightChange', () => {
    const onHeightChange = jest.fn();
    const { UNSAFE_root } = render(
      <NavigationStepBanner
        step={{ instruction: 'Turn left onto Main St', maneuver: 'turn-left' } as any}
        distanceToManeuverMeters={120}
        topOffset={52}
        onHeightChange={onHeightChange}
      />,
    );
    const banner = UNSAFE_root.findByProps({ accessibilityRole: 'text' });
    banner.props.onLayout({ nativeEvent: { layout: { height: 58 } } });
    expect(onHeightChange).toHaveBeenCalledWith(58);
  });
});
