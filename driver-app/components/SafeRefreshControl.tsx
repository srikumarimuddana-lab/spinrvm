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

// Check at module load if the native Android RefreshControl component is valid.
// The patched RefreshControl.js has a fallback, but in case it doesn't fire
// (stale cache, etc.), this is the last line of defense.
let useNative = true;
if (Platform.OS === 'android') {
  try {
    const nativeModule = require('react-native/Libraries/Components/RefreshControl/AndroidSwipeRefreshLayoutNativeComponent');
    const component = nativeModule?.default;
    if (component && typeof component !== 'function' && typeof component !== 'string') {
      useNative = false;
    }
  } catch {
    useNative = false;
  }
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
