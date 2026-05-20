import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { Text, TouchableOpacity, View, StyleSheet } from 'react-native';
import BottomSheet, { BottomSheetView } from '@gorhom/bottom-sheet';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export type ConfirmVariant = 'info' | 'warning' | 'danger' | 'success';

export interface ConfirmSheetButton {
  text: string;
  style?: 'default' | 'cancel' | 'destructive';
  onPress?: () => void;
}

interface ConfirmSheetProps {
  visible: boolean;
  title: string;
  message?: string;
  variant?: ConfirmVariant;
  buttons?: ConfirmSheetButton[];
  onClose: () => void;
}

const VARIANT_CONFIG: Record<ConfirmVariant, { icon: string; color: string }> = {
  info: { icon: 'information-circle', color: '#1a73e8' },
  warning: { icon: 'alert-circle', color: '#d97706' },
  danger: { icon: 'warning', color: '#dc2626' },
  success: { icon: 'checkmark-circle', color: '#0d9f6e' },
};

export default function ConfirmSheet({
  visible,
  title,
  message,
  variant = 'info',
  buttons = [{ text: 'OK', style: 'default' }],
  onClose,
}: ConfirmSheetProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const sheetRef = useRef<BottomSheet>(null);
  const config = VARIANT_CONFIG[variant];

  useEffect(() => {
    if (visible) {
      sheetRef.current?.snapToIndex(0);
    } else {
      sheetRef.current?.close();
    }
  }, [visible]);

  const handleSheetChange = useCallback(
    (index: number) => {
      if (index === -1) onClose();
    },
    [onClose],
  );

  const cancelButton = buttons.find((b) => b.style === 'cancel');
  const actionButtons = buttons.filter((b) => b.style !== 'cancel');

  if (!visible) return null;

  return (
    <BottomSheet
      ref={sheetRef}
      index={0}
      enableDynamicSizing
      enablePanDownToClose
      onChange={handleSheetChange}
      backgroundStyle={styles.sheetBg}
      handleIndicatorStyle={styles.handle}
      style={styles.sheet}
    >
      <BottomSheetView style={styles.content}>
        <View style={[styles.iconCircle, { backgroundColor: config.color + '18' }]}>
          <Ionicons name={config.icon as any} size={32} color={config.color} />
        </View>

        <Text style={styles.title}>{title}</Text>
        {message ? <Text style={styles.message}>{message}</Text> : null}

        <View style={styles.buttonContainer}>
          {actionButtons.map((button, i) => {
            const isDestructive = button.style === 'destructive';
            return (
              <TouchableOpacity
                key={i}
                style={[
                  styles.button,
                  isDestructive
                    ? styles.destructiveButton
                    : { backgroundColor: config.color },
                ]}
                onPress={() => {
                  button.onPress?.();
                  onClose();
                }}
                activeOpacity={0.8}
              >
                <Text style={styles.buttonText}>{button.text}</Text>
              </TouchableOpacity>
            );
          })}

          {cancelButton && (
            <TouchableOpacity
              style={[styles.button, styles.cancelButton]}
              onPress={() => {
                cancelButton.onPress?.();
                onClose();
              }}
              activeOpacity={0.7}
            >
              <Text style={[styles.buttonText, styles.cancelButtonText]}>
                {cancelButton.text}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      </BottomSheetView>
    </BottomSheet>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    sheet: {
      zIndex: 9998,
    },
    sheetBg: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 24,
      borderTopRightRadius: 24,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -4 },
      shadowOpacity: 0.12,
      shadowRadius: 16,
      elevation: 20,
    },
    handle: {
      backgroundColor: colors.border,
      width: 40,
    },
    content: {
      paddingHorizontal: 28,
      paddingTop: 8,
      paddingBottom: 40,
      alignItems: 'center',
    },
    iconCircle: {
      width: 64,
      height: 64,
      borderRadius: 32,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 16,
    },
    title: {
      fontSize: 20,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
      marginBottom: 8,
    },
    message: {
      fontSize: 14,
      color: colors.textSecondary,
      textAlign: 'center',
      lineHeight: 20,
      marginBottom: 20,
    },
    buttonContainer: {
      width: '100%',
      gap: 10,
      marginTop: 8,
    },
    button: {
      width: '100%',
      paddingVertical: 14,
      borderRadius: 14,
      alignItems: 'center',
      justifyContent: 'center',
    },
    buttonText: {
      fontSize: 16,
      fontWeight: '600',
      color: '#fff',
    },
    destructiveButton: {
      backgroundColor: '#EF4444',
    },
    cancelButton: {
      backgroundColor: colors.surfaceLight,
    },
    cancelButtonText: {
      color: colors.textSecondary,
    },
  });
}
