import React from 'react';
import { RefreshControl, Platform, View, ActivityIndicator, type RefreshControlProps } from 'react-native';

/**
 * Drop-in RefreshControl replacement that works around RN 0.85.2's
 * Bridgeless/New Architecture codegen bug on Android.
 *
 * On Android, codegenNativeComponent('AndroidSwipeRefreshLayout') can return
 * a non-renderable object instead of a valid component. When that happens,
 * React throws "Element type is invalid: got object" from ScrollView's render.
 *
 * This component detects the broken native component at import time and
 * renders a simple ActivityIndicator fallback. The pull-to-refresh gesture
 * is lost but the UI doesn't crash.
 */

// Check at module load whether the platform's native RefreshControl component
// is renderable. RN 0.85.2 under the New Architecture (Bridgeless) declares
// these via codegenNativeComponent(interfaceOnly), which can resolve to a
// non-renderable object — rendering <RefreshControl> then throws "Element type
// is invalid: got object" from ScrollView. This happens on BOTH platforms
// (Android: AndroidSwipeRefreshLayout, iOS: PullToRefreshView — the latter
// crashed NotificationsScreen), so guard both and fall back to a plain spinner.
function _isRenderable(c: unknown): boolean {
  return (
    typeof c === 'function' ||
    typeof c === 'string' ||
    (c != null && typeof c === 'object' && (c as { $$typeof?: unknown }).$$typeof != null)
  );
}

let useNative = true;
try {
  // Guarded, platform-conditional requires of internal RN native-component
  // paths — must stay runtime require(), not a static import, so only the
  // platform actually running loads its own native component module (a
  // static import of both would defeat the try/catch below and load the
  // wrong platform's codegen path unconditionally).
  /* eslint-disable @typescript-eslint/no-require-imports */
  const native =
    Platform.OS === 'android'
      ? require('react-native/Libraries/Components/RefreshControl/AndroidSwipeRefreshLayoutNativeComponent')?.default
      : require('react-native/Libraries/Components/RefreshControl/PullToRefreshViewNativeComponent')?.default;
  /* eslint-enable @typescript-eslint/no-require-imports */
  if (!_isRenderable(native)) {
    useNative = false;
  }
} catch {
  useNative = false;
}

/**
 * Spinner-only stand-in used when the native RefreshControl is unusable.
 *
 * MUST render `children`. ScrollView (and therefore FlatList) treats its
 * `refreshControl` element differently per platform:
 *   - iOS: the element is rendered as a SIBLING inside the scroll view, so
 *     `children` is undefined and returning just a spinner (or null) is fine.
 *   - Android: ScrollView.js wraps the scroll view IN the refresh control —
 *     `React.cloneElement(refreshControl, {style}, <NativeScrollView…>)` — so
 *     the refresh control is the PARENT of the entire list. A fallback that
 *     ignores `children` (as this one did until 2026-09-14) drops the whole
 *     FlatList on Android: the Notifications screen rendered nothing below the
 *     header (or nothing at all once the header moved inside the FlatList),
 *     pull-to-refresh had no touch surface, and iOS was unaffected. The same
 *     fallback is shared by quests, lost-and-found, tax-documents and
 *     payout-history.
 *
 * Exported for the regression test; screens should keep using the default
 * export so the native/fallback choice stays in one place.
 */
export function FallbackRefreshControl(props: RefreshControlProps) {
  const { refreshing, tintColor, colors, children, style } = props;
  const hasChildren = children != null;
  if (!refreshing && !hasChildren) return null;
  const color = tintColor ?? (colors && colors.length > 0 ? String(colors[0]) : '#999');
  return (
    // `style` is the layout style ScrollView hands the wrapper on Android
    // (its own flex/size props); `flex: 1` is what AndroidSwipeRefreshLayout
    // would otherwise contribute so the wrapped list fills the screen.
    <View style={[hasChildren ? { flex: 1 } : null, style]}>
      {refreshing ? (
        <View style={{ alignItems: 'center', justifyContent: 'center', paddingVertical: 12 }}>
          <ActivityIndicator size="small" color={color} />
        </View>
      ) : null}
      {children}
    </View>
  );
}

function SafeRefreshControl(props: RefreshControlProps) {
  if (useNative) {
    return <RefreshControl {...props} />;
  }
  return <FallbackRefreshControl {...props} />;
}

export default SafeRefreshControl;
