import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
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

function SpinrToast({ text1, text2, type }: ToastConfigParams<unknown>) {
  const config = VARIANT_CONFIG[(type as VariantKey) ?? 'info'] ?? VARIANT_CONFIG.info;
  return (
    <View style={[styles.container, { backgroundColor: config.bg }]}>
      <Ionicons name={config.icon as any} size={20} color="#FFF" style={styles.icon} />
      <View style={styles.textWrap}>
        {text1 ? <Text style={styles.title} numberOfLines={1}>{text1}</Text> : null}
        {text2 ? <Text style={styles.message} numberOfLines={2}>{text2}</Text> : null}
      </View>
    </View>
  );
}

export const toastConfig = {
  success: (props: ToastConfigParams<unknown>) => <SpinrToast {...props} />,
  error:   (props: ToastConfigParams<unknown>) => <SpinrToast {...props} />,
  warning: (props: ToastConfigParams<unknown>) => <SpinrToast {...props} />,
  info:    (props: ToastConfigParams<unknown>) => <SpinrToast {...props} />,
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
