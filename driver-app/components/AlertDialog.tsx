import React, { useMemo } from 'react';
import {
  Modal, View, Text, TouchableOpacity, StyleSheet, Dimensions,
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
  useAlertStore.setState({
    visible: true,
    title,
    message,
    buttons: buttons || [{ text: 'OK' }],
  });
}

export function hideAlert() {
  useAlertStore.setState({ visible: false });
}

export const AlertDialog: React.FC = () => {
  const { visible, title, message, buttons } = useAlertStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  if (!visible) return null;

  const handlePress = (btn: AlertButton) => {
    hideAlert();
    btn.onPress?.();
  };

  const hasDestructive = buttons.some((b) => b.style === 'destructive');

  return (
    <Modal transparent animationType="fade" visible={visible} statusBarTranslucent>
      <View style={styles.overlay}>
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
    </Modal>
  );
};

const { width } = Dimensions.get('window');

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    overlay: {
      flex: 1,
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
      backgroundColor: '#EF4444',
    },
    buttonDestructive: {
      backgroundColor: '#EF4444',
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
