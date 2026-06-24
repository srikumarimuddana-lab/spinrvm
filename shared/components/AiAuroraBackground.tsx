/**
 * AiAuroraBackground — slow-drifting gradient "aurora" behind the AI chat.
 *
 * Gemini-style ambient motion on Spinr's red/orange/white brand palette:
 * several soft colour blobs scattered across the screen drift and breathe on
 * independent loops, lightly softened by a blur layer so they read as glows
 * rather than hard circles. Purely decorative and pointer-transparent — chat
 * content renders above it untouched.
 *
 * Shared by the rider AI assistant screen and the support chat tab (both
 * apps). Motion runs on the UI thread via reanimated worklets so it never
 * competes with streaming/list work on the JS thread, and honours
 * reduce-motion (renders the blobs static — still a gradient, no drift).
 */
import React, { useEffect } from 'react';
import { AccessibilityInfo, Dimensions, StyleSheet, View } from 'react-native';
import { BlurView } from 'expo-blur';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
  Easing,
} from 'react-native-reanimated';

import { useTheme } from '@shared/theme/ThemeContext';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
// Smaller than the screen so the blobs read as distinct, scattered glows
// rather than three big washes.
const BLOB = Math.max(SCREEN_W, SCREEN_H) * 0.62;

type BlobSpec = {
  color: string;
  /** Resting centre as a fraction of the screen. */
  x: number;
  y: number;
  /** Drift amplitude in px and loop duration in ms. */
  dx: number;
  dy: number;
  duration: number;
};

function DriftingBlob({ spec, animate }: { spec: BlobSpec; animate: boolean }) {
  const t = useSharedValue(0);

  useEffect(() => {
    if (!animate) return;
    t.value = withRepeat(
      withTiming(1, { duration: spec.duration, easing: Easing.inOut(Easing.sin) }),
      -1,
      true,
    );
  }, [animate, spec.duration, t]);

  const animatedStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: spec.dx * t.value },
      { translateY: spec.dy * t.value },
      { scale: 1 + 0.1 * t.value },
    ],
  }));

  return (
    <Animated.View
      pointerEvents="none"
      style={[
        styles.blob,
        {
          backgroundColor: spec.color,
          left: spec.x * SCREEN_W - BLOB / 2,
          top: spec.y * SCREEN_H - BLOB / 2,
        },
        animatedStyle,
      ]}
    />
  );
}

export default function AiAuroraBackground({ visible = true }: { visible?: boolean }) {
  const { colors, isDark } = useTheme();
  const [reduceMotion, setReduceMotion] = React.useState(false);
  const fade = useSharedValue(visible ? 1 : 0);

  useEffect(() => {
    fade.value = withTiming(visible ? 1 : 0, {
      duration: 450,
      easing: Easing.inOut(Easing.ease),
    });
  }, [visible, fade]);

  const fadeStyle = useAnimatedStyle(() => ({ opacity: fade.value }));

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled().then((v) => {
      if (mounted) setReduceMotion(v);
    });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => {
      mounted = false;
      sub.remove();
    };
  }, []);

  // Vivid, on-brand aurora — red, orange, and white. Higher alpha than a
  // typical wash so the colour stays saturated ("less transparent"); the
  // blur intensity is kept low so it doesn't bleach the hues back to white.
  const red = isDark ? 'F2' : '9E';
  const orange = isDark ? 'D9' : '85';
  const white = isDark ? '#FFFFFF7A' : '#FFFFFFCC';
  const blobs: BlobSpec[] = [
    { color: `${colors.primary}${red}`, x: 0.12, y: 0.16, dx: 64, dy: 84, duration: 9000 },
    { color: `${colors.orange}${orange}`, x: 0.86, y: 0.24, dx: -78, dy: 60, duration: 11000 },
    { color: white, x: 0.5, y: 0.46, dx: 54, dy: -48, duration: 10000 },
    { color: `${colors.orange}${orange}`, x: 0.16, y: 0.8, dx: 70, dy: -70, duration: 12500 },
    { color: `${colors.primary}${red}`, x: 0.88, y: 0.84, dx: -60, dy: -82, duration: 13500 },
  ];

  return (
    <Animated.View style={[styles.container, fadeStyle]} pointerEvents="none">
      {blobs.map((spec, i) => (
        <DriftingBlob key={i} spec={spec} animate={visible && !reduceMotion} />
      ))}
      {/* Light blur to soften the blob edges without washing out the colour. */}
      <BlurView
        intensity={isDark ? 55 : 42}
        tint={isDark ? 'dark' : 'light'}
        style={StyleSheet.absoluteFill}
      />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, overflow: 'hidden' },
  blob: {
    position: 'absolute',
    width: BLOB,
    height: BLOB,
    borderRadius: BLOB / 2,
  },
});
