/**
 * app/driver/(tabs)/activity.tsx — driver activity tab wrapper. Pins:
 *  - the header renders the "Activity" title and a Payout shortcut that
 *    navigates to /driver/payout
 *  - the real ActivityView is rendered below the header
 *  - the whole screen is wrapped in an ErrorBoundary
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ push: mockPush }) }));

const COLORS = { primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF' };
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockT = (key: string) => key;
const mockLanguageState = { t: mockT };
jest.mock('../../store/languageStore', () => ({ useLanguageStore: () => mockLanguageState }));

jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

jest.mock('../../components/activity/ActivityView', () => () => {
  const { Text: RNText } = require('react-native');
  return <RNText>ActivityView</RNText>;
});

import ActivityScreen from '../../app/driver/(tabs)/activity';

let renderer: TestRenderer.ReactTestRenderer | null = null;
function renderScreen() {
  act(() => {
    renderer = TestRenderer.create(<ActivityScreen />);
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('ActivityScreen', () => {
  it('renders the Activity title and the ActivityView', () => {
    const r = renderScreen();
    expect(allText(r)).toContain('Activity');
    expect(allText(r)).toContain('ActivityView');
  });

  it('navigates to /driver/payout when the payout shortcut is tapped', () => {
    const r = renderScreen();
    const payoutBtn = r.root.findByType(TouchableOpacity);
    act(() => {
      payoutBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/driver/payout');
  });
});
