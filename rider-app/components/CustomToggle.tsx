import React from 'react';
import { TouchableOpacity, View, StyleSheet } from 'react-native';

interface CustomToggleProps {
  value?: boolean;
  onValueChange?: (value: boolean) => void;
  trackColor?: { false: string; true: string };
  thumbColor?: string;
  disabled?: boolean;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  accessibilityRole?: string;
}

export default function CustomToggle({
  value = false,
  onValueChange,
  trackColor = { false: '#D1D5DB', true: '#EF444460' },
  thumbColor,
  disabled = false,
  accessibilityLabel,
  accessibilityHint,
}: CustomToggleProps) {
  const resolvedThumbColor = thumbColor ?? (value ? '#EF4444' : '#FFF');

  return (
    <TouchableOpacity
      activeOpacity={0.8}
      onPress={() => onValueChange?.(!value)}
      disabled={disabled}
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled }}
      style={[
        styles.track,
        { backgroundColor: value ? trackColor.true : trackColor.false },
        disabled && styles.disabled,
      ]}
    >
      <View
        style={[
          styles.thumb,
          { backgroundColor: resolvedThumbColor },
          value ? styles.thumbOn : styles.thumbOff,
        ]}
      />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  track: {
    width: 48,
    height: 28,
    borderRadius: 14,
    padding: 3,
    justifyContent: 'center',
  },
  thumb: {
    width: 22,
    height: 22,
    borderRadius: 11,
  },
  thumbOn: {
    alignSelf: 'flex-end',
  },
  thumbOff: {
    alignSelf: 'flex-start',
  },
  disabled: {
    opacity: 0.5,
  },
});
