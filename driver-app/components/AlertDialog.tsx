import React, { useEffect, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Dimensions, BackHandler, Platform, Alert,
} from 'react-native';
import { create } from 'zustand';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface AlertButton {
  text: string;
  style?: 'default' | 'cancel' | 'destructive';
  onPress?: () => void;
}

interface AlertState {
  visible: boolean;
  title: string;
  message: string;
  buttons: AlertButton[];
}

export const useAlertStore = create<AlertState>(() => ({
  visible: false,
  title: '',
  message: '',
  buttons: [],
}));

export function showAlert(
  title: string,
  message: string,
  buttons?: AlertButton[],
) {
  const btns = buttons || [{ text: 'OK' }];
  // iOS: use the OS-native alert. It's rendered by UIKit, not React Native,
  // so it sidesteps every RN Modal / overlay quirk on iOS (the split-screen
  // presentation, New-Architecture host-view issues, etc.) and always floats
  // correctly over whatever is on screen. Android keeps the custom overlay
  // below, which behaves correctly there.
  if (Platform.OS === 'ios') {
    Alert.alert(
      title,
      message,
      btns.map((b) => ({ text: b.text, onPress: b.onPress, style: b.style })),
    );
    return;
  }
  useAlertStore.setState({
    visible: true,
    title,
    message,
    buttons: btns,
  });
}

export function hideAlert() {
  useAlertStore.setState({ visible: false });
}

export const AlertDialog: React.FC = () => {
  const { visible, title, message, buttons } = useAlertStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Consume the Android hardware back button while the alert is up so it
  // dismisses the dialog instead of popping the underlying screen — this is
  // the behaviour a native modal gave us before we moved to an inline overlay.
  useEffect(() => {
    if (!visible || Platform.OS !== 'android') return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      hideAlert();
      return true;
    });
    return () => sub.remove();
  }, [visible]);

  if (!visible) return null;

  const handlePress = (btn: AlertButton) => {
    hideAlert();
    btn.onPress?.();
  };

  // Rendered as a plain absolute-fill overlay (NOT a native <Modal>). RN
  // 0.85.2's RCTModalHostView misbehaves under the New Architecture on iOS —
  // presenting it resizes the app's root view, collapsing the dashboard into
  // the top half and dropping the dialog into a black bottom half. Every other
  // overlay in this app (ride-offer / idle / active-ride panels) is an inline
  // absolute View for the same reason; this keeps the dialog on top of the
  // whole screen with no native modal presentation involved. Mounted once at
  // the SafeAreaProvider root in app/_layout.tsx.
  return (
    <View style={styles.overlay} pointerEvents="auto">
      <View style={styles.dialog}>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.message}>{message}</Text>
        <View style={[styles.buttonRow, buttons.length === 1 && styles.buttonRowSingle]}>
          {buttons.map((btn, i) => {
            const isDestructive = btn.style === 'destructive';
            const isCancel = btn.style === 'cancel';
            return (
              <TouchableOpacity
                key={i}
                style={[
                  styles.button,
                  isDestructive && styles.buttonDestructive,
                  isCancel && styles.buttonCancel,
                  !isDestructive && !isCancel && styles.buttonPrimary,
                  buttons.length === 1 && styles.buttonFull,
                ]}
                onPress={() => handlePress(btn)}
                activeOpacity={0.8}
              >
                <Text
                  style={[
                    styles.buttonText,
                    isDestructive && styles.buttonTextDestructive,
                    isCancel && styles.buttonTextCancel,
                    !isDestructive && !isCancel && styles.buttonTextPrimary,
                  ]}
                >
                  {btn.text}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>
    </View>
  );
};

const { width } = Dimensions.get('window');

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    overlay: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      // Sit above the navigator (and thus the map / dashboard) on both
      // platforms. iOS honours zIndex from paint order; Android needs
      // elevation to lift the overlay above native surfaces.
      zIndex: 9999,
      elevation: 9999,
      backgroundColor: 'rgba(0,0,0,0.5)',
      justifyContent: 'center',
      alignItems: 'center',
      paddingHorizontal: 32,
    },
    dialog: {
      width: Math.min(width - 64, 340),
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.15,
      shadowRadius: 24,
      elevation: 12,
    },
    title: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      textAlign: 'center',
      marginBottom: 8,
    },
    message: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textSecondary,
      textAlign: 'center',
      lineHeight: 20,
      marginBottom: 24,
    },
    buttonRow: {
      flexDirection: 'row',
      gap: 10,
    },
    buttonRowSingle: {
      justifyContent: 'center',
    },
    button: {
      flex: 1,
      paddingVertical: 14,
      borderRadius: 12,
      alignItems: 'center',
    },
    buttonFull: {
      flex: undefined,
      paddingHorizontal: 48,
    },
    buttonPrimary: {
      backgroundColor: colors.primary,
    },
    buttonDestructive: {
      backgroundColor: colors.danger,
    },
    buttonCancel: {
      backgroundColor: colors.border,
    },
    buttonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
    },
    buttonTextPrimary: {
      color: '#FFF',
    },
    buttonTextDestructive: {
      color: '#FFF',
    },
    buttonTextCancel: {
      color: colors.text,
    },
  });
