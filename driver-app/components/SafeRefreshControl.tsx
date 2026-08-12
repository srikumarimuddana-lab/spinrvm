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

function SafeRefreshControl(props: RefreshControlProps) {
  if (useNative) {
    return <RefreshControl {...props} />;
  }

  const { refreshing, tintColor, colors } = props;
  if (!refreshing) return null;
  const color = tintColor ?? (colors && colors.length > 0 ? String(colors[0]) : '#999');
  return (
    <View style={{ alignItems: 'center', justifyContent: 'center', paddingVertical: 12 }}>
      <ActivityIndicator size="small" color={color} />
    </View>
  );
}

export default SafeRefreshControl;
