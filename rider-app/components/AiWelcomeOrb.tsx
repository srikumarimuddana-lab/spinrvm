/**
 * AiWelcomeOrb — the glowing sparkle that anchors the AI welcome state.
 *
 * Gemini's spinning four-point star, rendered on Spinr's palette: a gradient
 * diamond that slowly rotates and breathes inside a soft halo. Decorative
 * only. Respects reduce-motion (renders a static, still-pretty orb).
 */
import React, { useEffect } from 'react';
import { AccessibilityInfo, StyleSheet, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
  Easing,
} from 'react-native-reanimated';

import { useTheme } from '@shared/theme/ThemeContext';

export default function AiWelcomeOrb() {
  const { colors } = useTheme();
  const [reduceMotion, setReduceMotion] = React.useState(false);
  const spin = useSharedValue(0);
  const pulse = useSharedValue(0);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion);
  }, []);

  useEffect(() => {
    if (reduceMotion) return;
    spin.value = withRepeat(withTiming(1, { duration: 14000, easing: Easing.linear }), -1, false);
    pulse.value = withRepeat(
      withTiming(1, { duration: 2600, easing: Easing.inOut(Easing.ease) }),
      -1,
      true,
    );
  }, [reduceMotion, spin, pulse]);

  const diamondStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${spin.value * 360}deg` }, { scale: 1 + 0.06 * pulse.value }],
  }));
  const haloStyle = useAnimatedStyle(() => ({
    opacity: 0.4 + 0.35 * pulse.value,
    transform: [{ scale: 1 + 0.12 * pulse.value }],
  }));

  return (
    <View style={styles.wrap}>
      <Animated.View style={[styles.halo, { backgroundColor: colors.primary }, haloStyle]} />
      <Animated.View style={diamondStyle}>
        <LinearGradient
          colors={[colors.primary, colors.orange, '#FFFFFF']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.diamond}
        >
          <Ionicons name="sparkles" size={26} color="#fff" />
        </LinearGradient>
      </Animated.View>
    </View>
  );
}

const SIZE = 72;
const styles = StyleSheet.create({
  wrap: { width: SIZE * 1.6, height: SIZE * 1.6, alignItems: 'center', justifyContent: 'center' },
  halo: {
    position: 'absolute',
    width: SIZE * 1.5,
    height: SIZE * 1.5,
    borderRadius: SIZE,
    opacity: 0.35,
  },
  diamond: {
    width: SIZE,
    height: SIZE,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
