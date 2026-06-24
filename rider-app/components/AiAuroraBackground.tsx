/**
 * AiAuroraBackground — slow-drifting gradient "aurora" behind the AI chat.
 *
 * Gemini-style ambient motion, on Spinr's red brand palette: three large soft
 * colour blobs drift and breathe on independent loops, smeared into a single
 * continuous gradient by a blur layer on top. Purely decorative and
 * pointer-transparent — chat content renders above it untouched.
 *
 * Motion is driven by reanimated worklets (UI thread) so it never competes
 * with streaming/list work on the JS thread. Honours reduce-motion: when the
 * OS flag is set we render the blobs static (still a pretty gradient, no drift).
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
const BLOB = Math.max(SCREEN_W, SCREEN_H) * 0.95;

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
      { scale: 1 + 0.08 * t.value },
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

  // Warm, on-brand aurora — red, orange, and a bright white glow so the
  // gradient shimmers rather than reading as one flat wash. Higher opacity on
  // dark so the glow registers against true-black; gentler on white.
  const a = isDark ? 'CC' : '5C';
  const b = isDark ? 'B3' : '47';
  const white = isDark ? '#FFFFFF66' : '#FFFFFFAA';
  const blobs: BlobSpec[] = [
    { color: `${colors.primary}${a}`, x: 0.18, y: 0.22, dx: 70, dy: 90, duration: 9000 },
    { color: `${colors.orange}${b}`, x: 0.85, y: 0.38, dx: -90, dy: 60, duration: 11000 },
    { color: white, x: 0.5, y: 0.9, dx: 60, dy: -80, duration: 13000 },
  ];

  return (
    <Animated.View style={[styles.container, fadeStyle]} pointerEvents="none">
      {blobs.map((spec, i) => (
        <DriftingBlob key={i} spec={spec} animate={visible && !reduceMotion} />
      ))}
      {/* Smear the hard circles into a single continuous gradient. */}
      <BlurView
        intensity={isDark ? 70 : 60}
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
