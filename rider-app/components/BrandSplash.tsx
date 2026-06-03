import React, { useEffect, useRef } from 'react';
import { Animated, Easing, View, Text, StyleSheet, LayoutChangeEvent } from 'react-native';

/**
 * BrandSplash — the animated launch screen shown while fonts / auth / location
 * initialise. Hands off seamlessly from the white native splash (white
 * background + the same red Spinr mark), then comes alive:
 *   - the lockup fades + scales in
 *   - the red mark (the dotted "i") spins continuously — the literal "spinr"
 *   - "ride local · support local" fades up
 *
 * Uses React Native's core Animated (native-driven) rather than Reanimated:
 * these apps run the old architecture (newArchEnabled: false) and the bundled
 * Reanimated 4 only supports the New Architecture. No extra deps, no babel
 * plugin requirement, works on both arches. The mark is the exact splash-mark
 * asset used by the native splash, so the two layers match.
 */

const INK = '#2D3038'; // charcoal wordmark
const TAG = '#8A8F98'; // dim tagline
const MARK = require('../assets/images/splash-mark.png');
const WORD_FONT = 'PlusJakartaSans_700Bold';

type Props = { onLayout?: (e: LayoutChangeEvent) => void };

export default function BrandSplash({ onLayout }: Props) {
  const enter = useRef(new Animated.Value(0)).current; // 0 -> 1 entrance
  const spin = useRef(new Animated.Value(0)).current; // 0 -> 1 continuous
  const tag = useRef(new Animated.Value(0)).current; // tagline reveal

  useEffect(() => {
    const entrance = Animated.timing(enter, {
      toValue: 1,
      duration: 480,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    });
    const tagline = Animated.timing(tag, {
      toValue: 1,
      duration: 620,
      delay: 380,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    });
    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1,
        duration: 5200,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    entrance.start();
    tagline.start();
    loop.start();
    return () => {
      entrance.stop();
      tagline.stop();
      loop.stop();
    };
  }, [enter, spin, tag]);

  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  const scale = enter.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1] });
  const tagY = tag.interpolate({ inputRange: [0, 1], outputRange: [10, 0] });

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Animated.View style={[styles.lockup, { opacity: enter, transform: [{ scale }] }]}>
        <View style={styles.word}>
          <Text style={styles.letters} allowFontScaling={false}>
            sp
          </Text>
          {/* the "i": charcoal tittle + spinning red mark */}
          <View style={styles.iWrap}>
            <View style={styles.tittle} />
            <Animated.Image
              source={MARK}
              style={[styles.mark, { transform: [{ rotate }] }]}
              resizeMode="contain"
            />
          </View>
          <Text style={styles.letters} allowFontScaling={false}>
            nr
          </Text>
        </View>
        <Animated.Text
          style={[styles.tagline, { opacity: tag, transform: [{ translateY: tagY }] }]}
          allowFontScaling={false}
        >
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
