import React, { forwardRef, useMemo } from 'react';
import {
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  TextStyle,
  View,
  ViewStyle,
} from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';
import { FONT, SPACING } from '../utils/responsive';

/**
 * Input — the shared labeled-text-field primitive.
 *
 * Extracted from the consistent (borderWidth:~1-1.5, borderColor:colors.border,
 * borderRadius:12, fontSize:16, color:colors.text) field styling repeated
 * across rider-app/app/emergency-contacts.tsx ("formInput", label above via
 * a sibling Text), become-driver.tsx ("input") and report-safety.tsx
 * ("input") — same shape, same radius, unlike the CTA-button trio the design
 * audit flagged. The optional `label`/`error` props extract
 * emergency-contacts.tsx's label-above-field layout and the
 * colors.danger error-text convention used app-wide (e.g. ride-options.tsx's
 * `errorText`).
 *
 * Not migrated anywhere in this PR — see Card.tsx's note; same future scope.
 */
export interface InputProps extends Omit<TextInputProps, 'style'> {
  label?: string;
  error?: string;
  containerStyle?: StyleProp<ViewStyle>;
  style?: StyleProp<TextStyle>;
}

export const Input = forwardRef<TextInput, InputProps>(function Input(
  { label, error, containerStyle, style, editable = true, ...rest },
  ref,
) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  return (
    <View style={containerStyle}>
      {label ? <Text style={styles.label}>{label}</Text> : null}
      <TextInput
        ref={ref}
        style={[
          styles.input,
          !!error && styles.inputError,
          !editable && styles.inputDisabled,
          style,
        ]}
        placeholderTextColor={colors.textDim}
        editable={editable}
        {...rest}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </View>
  );
});

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    label: {
      fontSize: FONT.bodySm,
      fontWeight: '600',
      color: colors.textDim,
      marginBottom: SPACING.xs + 2,
    },
    input: {
      backgroundColor: colors.surface,
      borderWidth: 1.5,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: SPACING.md,
      paddingVertical: SPACING.md - 2,
      fontSize: FONT.bodyLg,
      color: colors.text,
    },
    inputError: {
      borderColor: colors.danger,
    },
    inputDisabled: {
      opacity: 0.6,
    },
    error: {
      fontSize: FONT.bodySm,
      color: colors.danger,
      marginTop: SPACING.xs,
    },
  });
}

export default Input;
