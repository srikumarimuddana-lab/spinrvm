import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { ActivityIndicator, Text } from 'react-native';
import { FallbackRefreshControl } from '../../components/SafeRefreshControl';

/**
 * Regression test for the Android-only "Notifications screen is blank" bug
 * (2026-09-14). On Android, ScrollView renders its `refreshControl` element
 * as the PARENT of the native scroll view — `React.cloneElement(refreshControl,
 * {style}, <NativeScrollView/>)` — so a refresh-control fallback that does not
 * render `children` swallows the entire FlatList. iOS renders the element as
 * a sibling (no children), which is why the bug never showed there.
 *
 * The fallback component is tested directly: the native-vs-fallback decision
 * happens at module load via a guarded require() of RN-internal native
 * component paths, which is not something a Jest module registry can
 * reproduce faithfully for both platforms in one file.
 */

function render(el: React.ReactElement) {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(el);
  });
  return renderer;
}

describe('FallbackRefreshControl', () => {
  describe('Android shape — ScrollView passes the scroll view as children', () => {
    it('renders the wrapped scroll view when idle (this is the bug case)', () => {
      const renderer = render(
        <FallbackRefreshControl refreshing={false} onRefresh={() => {}}>
          <Text>list content</Text>
        </FallbackRefreshControl>,
      );
      expect(renderer.root.findAllByType(Text).map((t) => t.props.children)).toEqual(['list content']);
      expect(renderer.root.findAllByType(ActivityIndicator)).toHaveLength(0);
    });

    it('renders both the spinner and the wrapped scroll view while refreshing', () => {
      const renderer = render(
        <FallbackRefreshControl refreshing onRefresh={() => {}} tintColor="#123456">
          <Text>list content</Text>
        </FallbackRefreshControl>,
      );
      expect(renderer.root.findAllByType(Text).map((t) => t.props.children)).toEqual(['list content']);
      const spinners = renderer.root.findAllByType(ActivityIndicator);
      expect(spinners).toHaveLength(1);
      expect(spinners[0].props.color).toBe('#123456');
    });

    it('applies the layout style ScrollView hands the wrapper, plus flex: 1', () => {
      const renderer = render(
        <FallbackRefreshControl refreshing={false} onRefresh={() => {}} style={{ backgroundColor: 'red' }}>
          <Text>list content</Text>
        </FallbackRefreshControl>,
      );
      const json = renderer.toJSON() as { props: { style: unknown } };
      expect(json.props.style).toEqual([{ flex: 1 }, { backgroundColor: 'red' }]);
    });
  });

  describe('iOS shape — element is a sibling inside the scroll view, no children', () => {
    it('renders nothing when idle', () => {
      const renderer = render(<FallbackRefreshControl refreshing={false} onRefresh={() => {}} />);
      expect(renderer.toJSON()).toBeNull();
    });

    it('renders only a spinner while refreshing', () => {
      const renderer = render(
        <FallbackRefreshControl refreshing onRefresh={() => {}} colors={['#abcdef']} />,
      );
      const spinners = renderer.root.findAllByType(ActivityIndicator);
      expect(spinners).toHaveLength(1);
      expect(spinners[0].props.color).toBe('#abcdef');
      expect(renderer.root.findAllByType(Text)).toHaveLength(0);
    });
  });
});
