import React, { useCallback, useEffect, useRef } from 'react';
import { AccessibilityInfo, Animated, PanResponder, StyleSheet, View } from 'react-native';
import { Text } from '@shared/components/Text';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT, MAX_FONT_SCALE } from '@shared/utils/responsive';
import { useUnifiedToastStore, type UnifiedToastVariant } from '../store/unifiedToastStore';
import { useAnimatedValue, useStableRef } from '../hooks/useAnimatedValue';

/**
 * The unified driver toast host, mounted once in app/_layout.tsx. It renders
 * nothing until hooks/useToast.ts routes a toast here, which only happens
 * while settings.driver_unified_toast_enabled is on (migration 490).
 *
 * Modelled on rider-app/components/Toast.tsx. Differences, chosen to keep the
 * driver app's current placement and timing: a fixed 60 px top offset (the
 * old Toast.show topOffset) instead of the safe-area inset, and no
 * de-duplication (see store/unifiedToastStore.ts).
 */

// Same top offset react-native-toast-message used (hooks/useToast.ts).
export const UNIFIED_TOAST_TOP_OFFSET = 60;

const HIDDEN_Y = -120;

// Icon glyphs only; colors come from the live theme, as in toastConfig.tsx.
const ICON_NAMES: Record<UnifiedToastVariant, string> = {
  info: 'information-circle',
  success: 'checkmark-circle',
  warning: 'warning',
  danger: 'alert-circle',
};

function variantConfig(colors: ThemeColors, variant: UnifiedToastVariant) {
  const bgByVariant: Record<UnifiedToastVariant, string> = {
    info: colors.info,
    success: colors.success,
    warning: colors.warning,
    danger: colors.danger,
  };
  return { bg: bgByVariant[variant], icon: ICON_NAMES[variant] };
}

// danger toasts are failures: interrupt the screen reader. Everything else
// queues politely behind other speech.
function liveRegionFor(variant: UnifiedToastVariant): 'assertive' | 'polite' {
  return variant === 'danger' ? 'assertive' : 'polite';
}

export default function UnifiedToast() {
  const { colors } = useTheme();
  const current = useUnifiedToastStore((s) => s.current);
  const dismiss = useUnifiedToastStore((s) => s.dismiss);

  const translateY = useAnimatedValue(HIDDEN_Y);
  const opacity = useAnimatedValue(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The id showing now, for the stable pan responder below.
  const currentId = useRef<number | null>(null);
  useEffect(() => {
    currentId.current = current?.id ?? null;
  }, [current?.id]);

  // Slide out, then clear that toast only (a newer one may have replaced it).
  const hide = useCallback((id: number | null) => {
    if (timer.current) clearTimeout(timer.current);
    if (id == null) return;
    Animated.parallel([
      Animated.timing(translateY, { toValue: HIDDEN_Y, duration: 200, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 0, duration: 200, useNativeDriver: true }),
    ]).start(() => dismiss(id));
  }, [translateY, opacity, dismiss]);

  // Swipe up to dismiss. Created once; reads only stable values and refs.
  const panResponder = useStableRef(() =>
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) => g.dy < -5,
      onPanResponderMove: (_, g) => {
        if (g.dy < 0) translateY.setValue(g.dy);
      },
      onPanResponderRelease: (_, g) => {
        if (g.dy < -30) {
          hide(currentId.current);
        } else {
          Animated.spring(translateY, { toValue: 0, useNativeDriver: true }).start();
        }
      },
    }),
  );

  // Enter animation, once per toast. There is no de-dupe, so a repeat of the
  // same text is a new id and slides in again, like Toast.show() did.
  useEffect(() => {
    if (current?.id == null) return;
    translateY.setValue(HIDDEN_Y);
    opacity.setValue(0);
    Animated.parallel([
      Animated.spring(translateY, { toValue: 0, friction: 8, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 1, duration: 200, useNativeDriver: true }),
    ]).start();
  }, [current?.id, translateY, opacity]);

  // Screen-reader announcement, once per toast. Fired imperatively because a
  // short-lived view is a weak spot for live regions on both platforms.
  useEffect(() => {
    if (current?.id == null) return;
    AccessibilityInfo.announceForAccessibility(
      current.message ? `${current.title}. ${current.message}` : current.title,
    );
    // id is the trigger: title/message never change under one id, and a
    // repeat is a new id, so this announces exactly once per toast.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id]);

  // Auto-dismiss after the toast's own duration (3.5 s from useToast).
  useEffect(() => {
    if (current?.id == null) return;
    const id = current.id;
    timer.current = setTimeout(() => hide(id), current.duration);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [current?.id, current?.duration, hide]);

  if (!current) return null;

  const config = variantConfig(colors, current.variant);
  const label = current.message ? `${current.title}. ${current.message}` : current.title;

  return (
    <Animated.View
      style={[
        styles.container,
        { top: UNIFIED_TOAST_TOP_OFFSET, backgroundColor: config.bg, transform: [{ translateY }], opacity },
      ]}
      accessible
      accessibilityRole="alert"
      accessibilityLabel={label}
      accessibilityLiveRegion={liveRegionFor(current.variant)}
      {...panResponder.panHandlers}
    >
      <Ionicons name={config.icon as any} size={20} color="#FFF" style={styles.icon} />
      <View style={styles.textWrap}>
        <Text maxFontSizeMultiplier={MAX_FONT_SCALE} style={styles.title} numberOfLines={1}>
          {current.title}
        </Text>
        {current.message ? (
          <Text maxFontSizeMultiplier={MAX_FONT_SCALE} style={styles.message} numberOfLines={2}>
            {current.message}
          </Text>
        ) : null}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    left: SPACING.md,
    right: SPACING.md,
    borderRadius: 12,
    paddingHorizontal: SPACING.md,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    zIndex: 9999,
    elevation: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
  },
  icon: { marginRight: 12 },
  textWrap: { flex: 1 },
  title: { color: '#FFF', fontSize: FONT.bodyMd, fontWeight: '600' },
  message: { color: 'rgba(255,255,255,0.9)', fontSize: FONT.bodySm, marginTop: 2 },
});
