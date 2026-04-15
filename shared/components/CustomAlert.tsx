// @ts-nocheck
import React, { useEffect, useRef, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
  Animated,
  Dimensions,
  TextInput,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BlurView } from 'expo-blur';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

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

const VARIANT_CONFIG: Record<AlertVariant, {
  icon: string;
  iconColor: string;
  iconBg: string;
  buttonColor: string;
}> = {
  info: {
    icon: 'information-circle',
    iconColor: '#3B82F6',
    iconBg: '#EFF6FF',
    buttonColor: '#3B82F6',
  },
  warning: {
    icon: 'alert-circle',
    iconColor: '#F59E0B',
    iconBg: '#FFFBEB',
    buttonColor: '#F59E0B',
  },
  danger: {
    icon: 'warning',
    iconColor: '#EF4444',
    iconBg: '#FEF2F2',
    buttonColor: '#EF4444',
  },
  success: {
    icon: 'checkmark-circle',
    iconColor: '#10B981',
    iconBg: '#ECFDF5',
    buttonColor: '#10B981',
  },
};

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
  const styles = useMemo(() => createStyles(colors), [colors]);

  const scaleAnim = useRef(new Animated.Value(0.85)).current;
  const opacityAnim = useRef(new Animated.Value(0)).current;

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
      ]).start();
    } else {
      scaleAnim.setValue(0.85);
      opacityAnim.setValue(0);
    }
  }, [visible]);

  const config = VARIANT_CONFIG[variant];
  const iconName = (icon || config.icon) as keyof typeof Ionicons.glyphMap;

  const handlePress = (button: AlertButton) => {
    button.onPress?.();
    onClose();
  };

  const cancelButton = buttons.find((b) => b.style === 'cancel');
  const actionButtons = buttons.filter((b) => b.style !== 'cancel');

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.overlay}
      >
        <TouchableOpacity
          style={styles.backdrop}
          activeOpacity={1}
          onPress={cancelButton ? () => handlePress(cancelButton) : onClose}
        />
        <Animated.View
          style={[
            styles.container,
            {
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
              style={styles.input}
              placeholder={inputPlaceholder}
              placeholderTextColor={colors.textDim}
              value={inputValue}
              onChangeText={onInputChange}
              autoCapitalize="characters"
              autoFocus
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
      width: SCREEN_WIDTH - 56,
      maxWidth: 360,
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
