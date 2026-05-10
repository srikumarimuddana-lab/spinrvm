import React, { useEffect, useRef, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
  Animated,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';
export type AlertVariant = 'info' | 'warning' | 'danger' | 'success';

export interface AlertButton {
  text: string;
  style?: 'default' | 'cancel' | 'destructive';
  onPress?: () => void;
}

interface CustomAlertProps {
  visible: boolean;
  title: string;
  message?: string;
  variant?: AlertVariant;
  icon?: keyof typeof Ionicons.glyphMap;
  buttons?: AlertButton[];
  onClose: () => void;
  // For text input prompts
  showInput?: boolean;
  inputPlaceholder?: string;
  inputValue?: string;
  onInputChange?: (text: string) => void;
}

export default function CustomAlert({
  visible,
  title,
  message,
  variant = 'info',
  icon,
  buttons = [{ text: 'OK', style: 'default' }],
  onClose,
  showInput,
  inputPlaceholder,
  inputValue,
  onInputChange,
}: CustomAlertProps) {
  const { colors } = useTheme();
  const { width: windowWidth } = useWindowDimensions();
  const containerWidth = Math.min(windowWidth - 56, 360);
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [isPending, setIsPending] = useState(false);

  // Variant config reads from the active theme, so dark mode renders dark
  // tinted backgrounds instead of the legacy light-only hex literals.
  const variantConfig = useMemo(
    () => ({
      info: {
        icon: 'information-circle',
        iconColor: colors.info,
        iconBg: colors.infoBg,
        buttonColor: colors.info,
      },
      warning: {
        icon: 'alert-circle',
        iconColor: colors.warning,
        iconBg: colors.warningBg,
        buttonColor: colors.warning,
      },
      danger: {
        icon: 'warning',
        iconColor: colors.error,        // theme aliases `danger` to error
        iconBg: colors.dangerBg,
        buttonColor: colors.error,
      },
      success: {
        icon: 'checkmark-circle',
        iconColor: colors.success,
        iconBg: colors.successBg,
        buttonColor: colors.success,
      },
    }),
    [colors],
  );

  const scaleAnim = useRef(new Animated.Value(0.85)).current;
  const opacityAnim = useRef(new Animated.Value(0)).current;
  const inputRef = useRef<TextInput>(null);

  // Focus the input after the open animation completes — firing earlier
  // (e.g. autoFocus, or a fixed delay) races against Modal native-window
  // readiness on Android and the keyboard request is silently dropped.
  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.spring(scaleAnim, {
          toValue: 1,
          friction: 8,
          tension: 80,
          useNativeDriver: true,
        }),
        Animated.timing(opacityAnim, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start(() => {
        if (showInput) inputRef.current?.focus();
      });
    } else {
      scaleAnim.setValue(0.85);
      opacityAnim.setValue(0);
    }
  }, [visible, showInput]);

  const config = variantConfig[variant];
  const iconName = (icon || config.icon) as keyof typeof Ionicons.glyphMap;

  const handlePress = async (button: AlertButton) => {
    if (isPending) return;
    setIsPending(true);
    try {
      await button.onPress?.();
    } finally {
      setIsPending(false);
      onClose();
    }
  };

  const cancelButton = buttons.find((b) => b.style === 'cancel');
  const actionButtons = buttons.filter((b) => b.style !== 'cancel');

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior="padding"
        style={styles.overlay}
      >
        <TouchableOpacity
          style={styles.backdrop}
          activeOpacity={1}
          accessible={false}
          onPress={cancelButton ? () => handlePress(cancelButton) : onClose}
        />
        <Animated.View
          style={[
            styles.container,
            {
              width: containerWidth,
              transform: [{ scale: scaleAnim }],
              opacity: opacityAnim,
            },
          ]}
        >
          {/* Icon */}
          <View style={[styles.iconCircle, { backgroundColor: config.iconBg }]}>
            <Ionicons name={iconName} size={32} color={config.iconColor} />
          </View>

          {/* Title */}
          <Text style={styles.title}>{title}</Text>

          {/* Message */}
          {message && <Text style={styles.message}>{message}</Text>}

          {/* Input field */}
          {showInput && (
            <TextInput
              ref={inputRef}
              style={styles.input}
              placeholder={inputPlaceholder}
              placeholderTextColor={colors.textDim}
              value={inputValue}
              onChangeText={onInputChange}
              autoCapitalize="characters"
              accessibilityLabel={inputPlaceholder || 'Input field'}
            />
          )}

          {/* Buttons */}
          <View style={styles.buttonContainer}>
            {/* Action buttons first (stacked if multiple) */}
            {actionButtons.map((button, i) => {
              const isDestructive = button.style === 'destructive';
              return (
                <TouchableOpacity
                  key={i}
                  style={[
                    styles.button,
                    isDestructive
                      ? styles.destructiveButton
                      : { backgroundColor: config.buttonColor },
                  ]}
                  onPress={() => handlePress(button)}
                  activeOpacity={0.8}
                  disabled={isPending}
                >
                  <Text
                    style={[
                      styles.buttonText,
                      { color: '#fff' },
                    ]}
                  >
                    {button.text}
                  </Text>
                </TouchableOpacity>
              );
            })}

            {/* Cancel button (ghost style) */}
            {cancelButton && (
              <TouchableOpacity
                style={[styles.button, styles.cancelButton]}
                onPress={() => handlePress(cancelButton)}
                activeOpacity={0.7}
                disabled={isPending}
              >
                <Text style={[styles.buttonText, styles.cancelButtonText]}>
                  {cancelButton.text}
                </Text>
              </TouchableOpacity>
            )}
          </View>
        </Animated.View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    overlay: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
    },
    backdrop: {
      ...StyleSheet.absoluteFillObject,
      backgroundColor: 'rgba(0, 0, 0, 0.5)',
    },
    container: {
      backgroundColor: colors.surface,
      borderRadius: 24,
      padding: 28,
      alignItems: 'center',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 20 },
      shadowOpacity: 0.15,
      shadowRadius: 40,
      elevation: 25,
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
    input: {
      width: '100%',
      borderWidth: 1.5,
      borderColor: colors.border,
      borderRadius: 14,
      paddingHorizontal: 16,
      paddingVertical: 12,
      fontSize: 16,
      color: colors.text,
      textAlign: 'center',
      letterSpacing: 2,
      fontWeight: '600',
      marginBottom: 20,
    },
    buttonContainer: {
      width: '100%',
      gap: 10,
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
