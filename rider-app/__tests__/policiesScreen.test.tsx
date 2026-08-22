/**
 * app/policies.tsx — the single entry point listing every rider-facing
 * legal/policy document. Pins: renders one row per
 * legalDocTypesForAudience('rider') entry with its real title, and tapping
 * a row navigates to /legal?type=<slug>.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity } from 'react-native';

import PoliciesScreen from '../app/policies';
import { LEGAL_DOC_TITLES, legalDocTypesForAudience } from '@shared/config/legalDocs';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

describe('PoliciesScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders a row for every rider-audience doc type with its real title', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<PoliciesScreen />);
    });
    const docTypes = legalDocTypesForAudience('rider');
    const text = allText(renderer);
    for (const docType of docTypes) {
      expect(text).toContain(LEGAL_DOC_TITLES[docType]);
    }
    // Driver-only doc must not leak into the rider list.
    expect(text).not.toContain(LEGAL_DOC_TITLES['deactivation-appeals']);
  });

  it('navigates to /legal?type=<slug> when a row is tapped', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<PoliciesScreen />);
    });
    const docTypes = legalDocTypesForAudience('rider');
    // Row 0 is the first doc-type touchable; row index 1 in the tree is
    // the back button, so skip it.
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
