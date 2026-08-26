/**
 * app/policies.tsx (driver-app) — entry point listing every driver-facing
 * legal/policy document, including the driver-only deactivation-appeals
 * policy. Pins: renders one row per legalDocTypesForAudience('driver')
 * entry with its real title, and tapping a row navigates to
 * /legal?type=<slug>.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity } from 'react-native';

import PoliciesScreen from '../../app/policies';
import { LEGAL_DOC_TITLES, legalDocTypesForAudience } from '@shared/config/legalDocs';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

describe('PoliciesScreen (driver-app)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders a row for every driver-audience doc type, including the driver-only appeals policy', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<PoliciesScreen />);
    });
    const docTypes = legalDocTypesForAudience('driver');
    const text = allText(renderer);
    for (const docType of docTypes) {
      expect(text).toContain(LEGAL_DOC_TITLES[docType]);
    }
    expect(docTypes).toContain('deactivation-appeals');
  });

  it('navigates to /legal?type=<slug> when a row is tapped', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<PoliciesScreen />);
    });
    const docTypes = legalDocTypesForAudience('driver');
    const rows = renderer.root.findAllByType(TouchableOpacity);
    const firstDocRow = rows[1]; // rows[0] is the header back button
    act(() => {
      firstDocRow.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith(`/legal?type=${docTypes[0]}`);
  });

  it('navigates back when the back button is pressed', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<PoliciesScreen />);
    });
    const rows = renderer.root.findAllByType(TouchableOpacity);
    act(() => {
      rows[0].props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
