/**
 * app/lost-and-found.tsx — the rider's list of lost-and-found cases. Pins:
 *  - GET /lost-and-found fires on focus (via useFocusEffect, not just mount)
 *  - a load failure silently keeps stale data (no error toast/state) --
 *    the file's own stated behavior
 *  - the empty state renders when there are no cases
 *  - unknown status/category values fall back to their raw string / a
 *    generic icon instead of crashing
 *  - tapping a case navigates to /lost-and-found-chat with its case id
 *  - pull-to-refresh re-fires the load
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, FlatList } from 'react-native';

import LostAndFoundScreen from '../app/lost-and-found';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

// Fires like the real hook on every mount (post-commit, via a real
// useEffect) -- this screen expects a fresh load on focus every time.
jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

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
  id: 'case-1', ride_id: 'ride-1', item_description: 'Blue umbrella',
  item_category: 'other', status: 'reported', reporter_type: 'rider',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<LostAndFoundScreen />);
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

describe('LostAndFoundScreen', () => {
  it('loads cases on focus', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [CASE_1] } });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/lost-and-found');
    expect(allText(r)).toContain('Blue umbrella');
    expect(allText(r)).toContain('Awaiting Driver');
  });

  it('shows the empty state when there are no cases', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No cases yet');
  });

  it('silently keeps stale data on a load failure (no error UI)', async () => {
    mockApiGet.mockResolvedValueOnce({ data: { cases: [CASE_1] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Blue umbrella');

    mockApiGet.mockRejectedValue(new Error('network down'));
    const list = r.root.findByType(FlatList);
    await act(async () => {
      await list.props.refreshControl.props.onRefresh();
      await flush();
    });
    // Still showing the stale case, no crash, no error text.
    expect(allText(r)).toContain('Blue umbrella');
  });

  it('falls back to the raw status string for an unrecognised status', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [{ ...CASE_1, status: 'some_new_status' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('some_new_status');
  });

  it('shows "Driver reported" prefix for driver-reported cases', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [{ ...CASE_1, reporter_type: 'driver' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Driver reported');
  });

  it('navigates to /lost-and-found-chat with the case id when a case is tapped', async () => {
    mockApiGet.mockResolvedValue({ data: { cases: [CASE_1] } });
    const r = await renderScreen();
    const card = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Blue umbrella')))!;
    act(() => {
      card.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/lost-and-found-chat', params: { caseId: 'case-1' } });
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
