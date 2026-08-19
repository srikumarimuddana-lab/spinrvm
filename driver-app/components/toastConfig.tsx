import React, { useEffect, useRef } from 'react';
import { AccessibilityInfo, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { ToastConfigParams } from 'react-native-toast-message';

// Shared color palette — must stay in sync with rider-app/components/Toast.tsx VARIANT_CONFIG
const VARIANT_CONFIG = {
  success: { bg: '#0d9f6e', icon: 'checkmark-circle' },
  error:   { bg: '#dc2626', icon: 'alert-circle' },
  warning: { bg: '#d97706', icon: 'warning' },
  info:    { bg: '#1a73e8', icon: 'information-circle' },
} as const;

type VariantKey = keyof typeof VARIANT_CONFIG;

// error toasts are urgent/failure content — interrupt whatever the screen
// reader is currently announcing. success/warning/info are informational and
// should queue politely behind other speech.
function liveRegionFor(type: VariantKey): 'assertive' | 'polite' {
  return type === 'error' ? 'assertive' : 'polite';
}

function SpinrToast({ text1, text2, type }: ToastConfigParams<unknown>) {
  const variant = (type as VariantKey) ?? 'info';
  const config = VARIANT_CONFIG[variant] ?? VARIANT_CONFIG.info;

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
    marginHorizontal: 16,
    borderRadius: 12,
    paddingHorizontal: 16,
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
  title: { color: '#FFF', fontSize: 15, fontWeight: '600' },
  message: { color: 'rgba(255,255,255,0.9)', fontSize: 13, marginTop: 2 },
});
