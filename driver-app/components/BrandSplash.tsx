import React, { useEffect } from 'react';
import { View, Text, StyleSheet, LayoutChangeEvent } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withRepeat,
  withDelay,
  Easing,
  cancelAnimation,
} from 'react-native-reanimated';

/**
 * BrandSplash — the animated launch screen shown while fonts / auth / location
 * initialise. Designed to hand off seamlessly from the white native splash
 * (white background + the same red Spinr mark), then come alive:
 *   - the lockup fades + scales in
 *   - the red mark (the dotted "i") spins continuously — the literal "spinr"
 *   - "ride local · support local" fades up
 *
 * Pure vector + Reanimated (no extra deps). The mark is the exact splash-mark
 * asset used by the native splash, so the two layers match.
 */

const INK = '#2D3038'; // charcoal wordmark
const TAG = '#8A8F98'; // dim tagline
const MARK = require('../assets/images/splash-mark.png');
const WORD_FONT = 'PlusJakartaSans_700Bold';

type Props = { onLayout?: (e: LayoutChangeEvent) => void };

export default function BrandSplash({ onLayout }: Props) {
  const enter = useSharedValue(0); // 0 -> 1 entrance
  const spin = useSharedValue(0); // 0 -> 360 continuous
  const tag = useSharedValue(0); // tagline reveal

  useEffect(() => {
    enter.value = withTiming(1, { duration: 480, easing: Easing.out(Easing.cubic) });
    spin.value = withRepeat(
      withTiming(360, { duration: 5200, easing: Easing.linear }),
      -1,
      false,
    );
    tag.value = withDelay(380, withTiming(1, { duration: 620, easing: Easing.out(Easing.cubic) }));
    return () => {
      cancelAnimation(spin);
    };
  }, [enter, spin, tag]);

  const lockupStyle = useAnimatedStyle(() => ({
    opacity: enter.value,
    transform: [{ scale: 0.92 + 0.08 * enter.value }],
  }));
  const markStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${spin.value}deg` }],
  }));
  const tagStyle = useAnimatedStyle(() => ({
    opacity: tag.value,
    transform: [{ translateY: 10 * (1 - tag.value) }],
  }));

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Animated.View style={[styles.lockup, lockupStyle]}>
        <View style={styles.word}>
          <Text style={styles.letters} allowFontScaling={false}>
            sp
          </Text>
          {/* the "i": charcoal tittle + spinning red mark */}
          <View style={styles.iWrap}>
            <View style={styles.tittle} />
            <Animated.Image source={MARK} style={[styles.mark, markStyle]} resizeMode="contain" />
          </View>
          <Text style={styles.letters} allowFontScaling={false}>
            nr
          </Text>
        </View>
        <Animated.Text style={[styles.tagline, tagStyle]} allowFontScaling={false}>
          ride local · support local
        </Animated.Text>
      </Animated.View>
    </View>
  );
}

const MARK_SIZE = 44;

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
  },
  lockup: {
    alignItems: 'center',
  },
  word: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  letters: {
    fontFamily: WORD_FONT,
    fontSize: 46,
    color: INK,
    letterSpacing: -1.5,
    includeFontPadding: false,
  },
  iWrap: {
    width: MARK_SIZE,
    height: MARK_SIZE,
    marginHorizontal: 1,
    marginBottom: 2, // sit the mark like an "i" body
    alignItems: 'center',
    justifyContent: 'center',
  },
  tittle: {
    position: 'absolute',
    top: -13,
    width: 9,
    height: 9,
    borderRadius: 4.5,
    backgroundColor: INK,
  },
  mark: {
    width: MARK_SIZE,
    height: MARK_SIZE,
  },
  tagline: {
    marginTop: 20,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    fontSize: 13,
    letterSpacing: 1.4,
    color: TAG,
  },
});
