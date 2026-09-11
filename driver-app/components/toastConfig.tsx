import React, { useEffect, useRef } from 'react';
import { AccessibilityInfo, StyleSheet, View } from 'react-native';
import { Text } from '@shared/components/Text';
import { Ionicons } from '@expo/vector-icons';
import type { ToastConfigParams } from 'react-native-toast-message';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';

// Icon glyphs only — colors come from the live theme (see ICON_NAMES usage
// below), so this stays in sync with `shared/theme/index.ts` automatically
// instead of needing a second hardcoded palette kept in sync by hand.
const ICON_NAMES = {
  success: 'checkmark-circle',
  error:   'alert-circle',
  warning: 'warning',
  info:    'information-circle',
} as const;

type VariantKey = keyof typeof ICON_NAMES;

function variantConfig(colors: ThemeColors, variant: VariantKey) {
  const bgByVariant: Record<VariantKey, string> = {
    success: colors.success,
    error: colors.error,
    warning: colors.warning,
    info: colors.info,
  };
  return { bg: bgByVariant[variant], icon: ICON_NAMES[variant] };
}

// error toasts are urgent/failure content — interrupt whatever the screen
// reader is currently announcing. success/warning/info are informational and
// should queue politely behind other speech.
function liveRegionFor(type: VariantKey): 'assertive' | 'polite' {
  return type === 'error' ? 'assertive' : 'polite';
}

function SpinrToast({ text1, text2, type }: ToastConfigParams<unknown>) {
  const { colors } = useTheme();
  const variant = (type as VariantKey) in ICON_NAMES ? (type as VariantKey) : 'info';
  const config = variantConfig(colors, variant);

  // Screen-reader announcement — fired imperatively (rather than relying
  // solely on the declarative accessibilityLiveRegion prop below) because a
  // transient view that mounts and unmounts quickly is a known weak spot for
  // live-region announcements on both iOS/Android. react-native-toast-message
  // mounts a fresh SpinrToast instance per shown toast, so a mount-time effect
  // fires exactly once per toast.
  const announced = useRef(false);
  useEffect(() => {
    if (announced.current) return;
    announced.current = true;
    const message = text1 ? (text2 ? `${text1}. ${text2}` : text1) : text2;
    if (message) AccessibilityInfo.announceForAccessibility(message);
  }, [text1, text2]);

  return (
    <View
      style={[styles.container, { backgroundColor: config.bg }]}
      accessible
      accessibilityRole="alert"
      accessibilityLiveRegion={liveRegionFor(variant)}
    >
      <Ionicons name={config.icon as any} size={20} color="#FFF" style={styles.icon} />
      <View style={styles.textWrap}>
        {text1 ? <Text style={styles.title} numberOfLines={1}>{text1}</Text> : null}
        {text2 ? <Text style={styles.message} numberOfLines={2}>{text2}</Text> : null}
      </View>
    </View>
  );
}

// Each entry pins its own `type` rather than trusting props.type — the
// react-native-toast-message renderer keys off the map key already, so this
// just makes the mapping explicit (and keeps liveRegionFor deterministic
// under direct/test invocation, not only when routed through Toast.show()).
export const toastConfig = {
  success: (props: ToastConfigParams<unknown>) => <SpinrToast {...props} type="success" />,
  error:   (props: ToastConfigParams<unknown>) => <SpinrToast {...props} type="error" />,
  warning: (props: ToastConfigParams<unknown>) => <SpinrToast {...props} type="warning" />,
  info:    (props: ToastConfigParams<unknown>) => <SpinrToast {...props} type="info" />,
};

const styles = StyleSheet.create({
  container: {
    marginHorizontal: SPACING.md,
    borderRadius: 12,
    paddingHorizontal: SPACING.md,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 10,
  },
  icon: { marginRight: 12 },
  textWrap: { flex: 1 },
  title: { color: '#FFF', fontSize: FONT.bodyMd, fontWeight: '600' },
  message: { color: 'rgba(255,255,255,0.9)', fontSize: FONT.bodySm, marginTop: 2 },
});
