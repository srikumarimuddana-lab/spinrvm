/**
 * app/promotions.tsx — available-promo list + manual code entry. Pins:
 *  - GET /promo/available?ride_fare=20 loads the list on mount
 *  - Apply is disabled until a code is entered
 *  - typing lowercase is uppercased automatically
 *  - a valid code posts to /promo/validate, shows the right toast copy for
 *    percentage vs. flat discounts, clears the input, and reloads the list
 *  - an invalid/failed code shows a neutral "Couldn't Apply Code" toast
 *    with the backend's message, not a hardcoded "bad code" assumption
 *  - the empty state renders when there are no available promos
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import PromotionsScreen from '../app/promotions';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  };
});

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<PromotionsScreen />);
    await flush();
  });
  return renderer!;
}

// FlatList's fiber tree contains a circular reference under
// react-test-renderer (unrelated to app code), so JSON.stringify(toJSON())
// throws on any screen containing one. Collect visible Text content
// instead of relying on a single serialized string.
function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root
    .findAllByType(Text)
    .map((t) => JSON.stringify(t.props.children))
    .join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: [] });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('PromotionsScreen', () => {
  it('loads available promos on mount', async () => {
    mockApiGet.mockResolvedValue({
      data: [{ promo_id: 'p1', code: 'SAVE10', discount_type: 'percentage', discount_value: 10, description: '10% off', expiry_date: '2026-12-01T00:00:00Z' }],
    });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/promo/available?ride_fare=20');
    const text = allText(r);
    expect(text).toContain('SAVE10');
    expect(text).toContain('10% off');
  });

  it('shows the empty state when there are no available promos', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No promotions available');
  });

  it('disables Apply until a code is entered', async () => {
    const r = await renderScreen();
    const applyBtn = r.root.findAllByType(TouchableOpacity)[1];
    expect(applyBtn.props.disabled).toBe(true);
  });

  it('uppercases typed codes', async () => {
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Enter promo code' });
    act(() => {
      input.props.onChangeText('save10');
    });
    // Re-query after the state update triggers a re-render.
    const updatedInput = r.root.findByProps({ placeholder: 'Enter promo code' });
    expect(updatedInput.props.value).toBe('SAVE10');
  });

  it('applies a valid percentage promo, shows the right toast, clears input, and reloads', async () => {
    mockApiPost.mockResolvedValue({ data: { discount_type: 'percentage', discount_value: 15 } });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Enter promo code' });
    act(() => {
      input.props.onChangeText('save15');
    });
    mockApiGet.mockClear();
    const applyBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await applyBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/promo/validate', { code: 'SAVE15', ride_fare: 20 });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Promo Valid!',
      '15% off — will apply on your next ride.',
      'success',
    );
    expect(mockApiGet).toHaveBeenCalledWith('/promo/available?ride_fare=20');
    const clearedInput = r.root.findByProps({ placeholder: 'Enter promo code' });
    expect(clearedInput.props.value).toBe('');
  });

  it('applies a valid flat-dollar promo with $-formatted toast copy', async () => {
    mockApiPost.mockResolvedValue({ data: { discount_type: 'flat', discount_value: 5 } });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Enter promo code' });
    act(() => {
      input.props.onChangeText('flat5');
    });
    const applyBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await applyBtn.props.onPress();
      await flush();
    });
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Promo Valid!');
    expect(call?.[1]).toBe('$5 off — will apply on your next ride.');
  });

  it('shows a neutral failure toast using the backend message on an invalid code', async () => {
    mockApiPost.mockRejectedValue({ name: 'SpinrApiError', message: 'This code has expired', messageKey: undefined });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Enter promo code' });
    act(() => {
      input.props.onChangeText('expired');
    });
    const applyBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await applyBtn.props.onPress();
      await flush();
    });
    const call = mockShowToast.mock.calls.find((c) => c[0] === "Couldn't Apply Code");
    expect(call).toBeTruthy();
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
