/**
 * app/driver/lost-and-found.tsx — the driver's list of lost-and-found
 * cases. Pins:
 *  - GET /lost-and-found fires on focus
 *  - a load failure silently keeps stale data (no error UI)
 *  - the empty state renders when there are no cases
 *  - `reported`/`driver_notified` statuses render as "Action Needed" with
 *    the urgent-border/dot styling instead of their normal status label —
 *    every other status shows its normal label with no urgency marker
 *  - unknown status values fall back to the raw string
 *  - tapping a case navigates to /driver/lost-and-found-chat with its
 *    case id
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import DriverLostAndFoundScreen from '../../app/driver/lost-and-found';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

jest.mock('../../components/ScreenHeader', () => ({
  ScreenHeader: ({ title }: any) => {
    const { Text: RNText } = require('react-native');
    return <RNText>{title}</RNText>;
  },
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const CASE_1 = {
  id: 'case-1', ride_id: 'ride-1', item_description: 'Black wallet',
  item_category: 'other', status: 'reported', reporter_type: 'rider',
  created_at: '2026-01-01T00:00:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DriverLostAndFoundScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: { cases: [] } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('DriverLostAndFoundScreen', () => {
  it('loads cases on focus', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [CASE_1] } });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found');
    expect(allText(r)).toContain('Black wallet');
  });

  it('shows the empty state when there are no cases', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No cases yet');
  });

  it('does not crash and shows no error UI when the load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    // No error state exists on this screen -- a failed fetch just leaves
    // `cases` empty, rendering the same empty state a genuinely-empty
    // inbox would show.
    expect(allText(r)).toContain('No cases yet');
  });

  it('shows "Action Needed" for a reported case, not its normal status label', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [{ ...CASE_1, status: 'reported' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Action Needed');
    expect(allText(r)).not.toContain('Rider Filed');
  });

  it('shows the normal status label for a resolved case, no urgency marker', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [{ ...CASE_1, status: 'resolved' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Resolved');
    expect(allText(r)).not.toContain('Action Needed');
  });

  it('falls back to the raw status string for an unrecognised status', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [{ ...CASE_1, status: 'weird_status' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('weird_status');
  });

  it('navigates to /driver/lost-and-found-chat with the case id when tapped', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [CASE_1] } });
    const r = await renderScreen();
    const card = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Black wallet')))!;
    act(() => {
      card.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/driver/lost-and-found-chat',
      params: { caseId: 'case-1' },
    });
  });
});
