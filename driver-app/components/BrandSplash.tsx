import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Dimensions,
  Easing,
  StyleSheet,
} from 'react-native';
import { useAnimatedValue } from '../hooks/useAnimatedValue';
import {
  DESCRIPTOR_GAP_FRACTION,
  DESCRIPTOR_HEIGHT_FRACTION,
  DESCRIPTOR_MARGIN_FRACTION,
  DESCRIPTOR_WIDTH_FRACTION,
  GLOW_DP,
  GLOW_SETTLED_OPACITY,
  GLOW_SETTLED_SCALE,
  LOCKUP_CENTER_Y_FRACTION,
  MARK_CROP_PX,
  MARK_DP,
  O_CENTER_X_FRACTION,
  O_CENTER_Y_FRACTION,
  SPLASH_BACKGROUND,
  SPLASH_PROVENANCE,
  SPLASH_TAGLINE,
  SPLASH_TIMING,
  WORDMARK_MAX_WIDTH,
  WORDMARK_SRC_H,
  WORDMARK_SRC_W,
  WORDMARK_WIDTH_FRACTION,
} from '../constants/splash';

const GLOW = require('../assets/images/splash/glow.png');
const LETTERS = require('../assets/images/splash/wordmark-letters.png');
const MARK = require('../assets/images/splash/mark.png');
const DESCRIPTOR = require('../assets/images/splash/descriptor-driver.png');

// --- Geometry ---------------------------------------------------------------
// All derived from the measured fractions in constants/splash.ts, so the mark
// lands pixel-exactly inside the wordmark's "o" at every screen size.
const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const WORDMARK_W = Math.min(WORDMARK_MAX_WIDTH, Math.round(SCREEN_W * WORDMARK_WIDTH_FRACTION));
const WORDMARK_H = Math.round((WORDMARK_W * WORDMARK_SRC_H) / WORDMARK_SRC_W);
const STAGE_LEFT = (SCREEN_W - WORDMARK_W) / 2;
// "Driver" hangs off the wordmark's right edge, exactly as it does in the app
// icon this art was lifted from. The wordmark and the descriptor are centred as
// one block, so the lockup sits optically where the rider app's does.
const DESCRIPTOR_W = DESCRIPTOR_WIDTH_FRACTION * WORDMARK_W;
const DESCRIPTOR_H = DESCRIPTOR_HEIGHT_FRACTION * WORDMARK_W;
const DESCRIPTOR_GAP = DESCRIPTOR_GAP_FRACTION * WORDMARK_W;
// descriptor-driver.png carries a transparent margin around the glyphs; add it
// back so the image box lines up with the measured ink box.
const DESCRIPTOR_MARGIN = DESCRIPTOR_MARGIN_FRACTION * DESCRIPTOR_W;
const LOCKUP_H = WORDMARK_H + DESCRIPTOR_GAP + DESCRIPTOR_H;
const STAGE_TOP = SCREEN_H * LOCKUP_CENTER_Y_FRACTION - LOCKUP_H / 2;
// Where the "o" ends up, and therefore where the flying mark has to land.
const O_CENTER_X = STAGE_LEFT + O_CENTER_X_FRACTION * WORDMARK_W;
const O_CENTER_Y = STAGE_TOP + O_CENTER_Y_FRACTION * WORDMARK_H;
// mark.png is a MARK_CROP_PX-wide cut of the WORDMARK_SRC_W-wide artwork, so at
// rest it must be exactly that many source pixels wide on screen.
const MARK_END_SCALE = ((WORDMARK_W / WORDMARK_SRC_W) * MARK_CROP_PX) / MARK_DP;
const MARK_DX = O_CENTER_X - SCREEN_W / 2;
const MARK_DY = O_CENTER_Y - SCREEN_H / 2;
const TAGLINE_TOP = STAGE_TOP + LOCKUP_H + 22;
// 11dp on a 390dp screen, never below 10dp on a small one.
const TAGLINE_SIZE = Math.min(12, Math.max(10, Math.round(SCREEN_W * 0.029)));
const PROVENANCE_SIZE = Math.max(9, TAGLINE_SIZE - 1);

const DESCRIPTOR_LEFT = STAGE_LEFT + WORDMARK_W - DESCRIPTOR_W - DESCRIPTOR_MARGIN;
const DESCRIPTOR_TOP = STAGE_TOP + WORDMARK_H + DESCRIPTOR_GAP - DESCRIPTOR_MARGIN;

export type BrandSplashPhase = 'intro' | 'exit';

type Props = {
  /** 'exit' fades the splash out over the app mounted beneath it. */
  phase?: BrandSplashPhase;
  /** Hold the text back until the brand font is real, so it never swaps mid-fade. */
  fontsReady?: boolean;
  /** Fired once the mark is on screen — the caller hides the native splash then. */
  onNativeHideReady?: () => void;
  /** Fired once the exit fade has finished and the splash can unmount. */
  onExitComplete?: () => void;
};

/**
 * BrandSplash — the launch frame.
 *
 * The native splash (configured in app.config.ts) shows the halo and the mark
 * rotated half a turn. This component's first frame is that same picture, so
 * the handoff from native to JS is invisible; the mark then unwinds those 180
 * degrees, shrinking and travelling until it *is* the "o" in "spinr", and the
 * rest of the wordmark rises in around it.
 *
 * Deliberately plain core RN Animated rather than Reanimated: this mounts
 * before the rest of the app is up, and every property animated here (opacity,
 * translate, scale, rotate) runs on the native driver anyway.
 *
 * Rendered outside ThemeProvider, so its colours are literals — see
 * constants/splash.ts for why the ground is brand-locked white.
 */
export default function BrandSplash({
  phase = 'intro',
  fontsReady = true,
  onNativeHideReady,
  onExitComplete,
}: Props) {
  const markProgress = useAnimatedValue(0);
  const glowProgress = useAnimatedValue(0);
  const lettersOpacity = useAnimatedValue(0);
  const lettersY = useAnimatedValue(6);
  const descriptorOpacity = useAnimatedValue(0);
  const descriptorY = useAnimatedValue(4);
  const taglineOpacity = useAnimatedValue(0);
  const taglineY = useAnimatedValue(4);
  const provenanceOpacity = useAnimatedValue(0);
  const provenanceY = useAnimatedValue(4);
  const traceOpacity = useAnimatedValue(0);
  const traceProgress = useAnimatedValue(0);
  const rootOpacity = useAnimatedValue(1);
  const contentScale = useAnimatedValue(1);

  // null until we know — starting the intro before then would animate at users
  // who asked for no animation.
  const [reduceMotion, setReduceMotion] = useState<boolean | null>(null);
  const [slowBoot, setSlowBoot] = useState(false);
  const mountedAt = useRef(Date.now());
  const nativeHidden = useRef(false);
  const exitDone = useRef(false);
  const traceLoop = useRef<Animated.CompositeAnimation | null>(null);

  useEffect(() => {
    let active = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then(value => {
        if (active) setReduceMotion(value);
      })
      .catch(() => {
        if (active) setReduceMotion(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const handleMarkLoad = useCallback(() => {
    if (nativeHidden.current) return;
    nativeHidden.current = true;
    onNativeHideReady?.();
  }, [onNativeHideReady]);

  // --- Intro ---------------------------------------------------------------
  useEffect(() => {
    if (reduceMotion === null || phase === 'exit') return;

    if (reduceMotion) {
      // No travel, no spin: the lockup is already in place and simply resolves.
      markProgress.setValue(1);
      lettersY.setValue(0);
      descriptorY.setValue(0);
      glowProgress.setValue(1);
      Animated.parallel([
        Animated.timing(lettersOpacity, {
          toValue: 1,
          duration: 320,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
        Animated.timing(descriptorOpacity, {
          toValue: 1,
          duration: 320,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
      ]).start();
      return;
    }

    Animated.timing(markProgress, {
      toValue: 1,
      duration: SPLASH_TIMING.markMs,
      // Fast out of the gate, then a long settle. Deliberately not a spring:
      // premium motion decelerates, it does not bounce.
      easing: Easing.out(Easing.exp),
      useNativeDriver: true,
    }).start();

    Animated.timing(glowProgress, {
      toValue: 1,
      duration: SPLASH_TIMING.glowMs,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();

    Animated.parallel([
      Animated.timing(lettersOpacity, {
        toValue: 1,
        duration: SPLASH_TIMING.lettersMs,
        delay: SPLASH_TIMING.lettersDelayMs,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(lettersY, {
        toValue: 0,
        duration: SPLASH_TIMING.lettersMs,
        delay: SPLASH_TIMING.lettersDelayMs,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(descriptorOpacity, {
        toValue: 1,
        duration: SPLASH_TIMING.descriptorMs,
        delay: SPLASH_TIMING.descriptorDelayMs,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(descriptorY, {
        toValue: 0,
        duration: SPLASH_TIMING.descriptorMs,
        delay: SPLASH_TIMING.descriptorDelayMs,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [
    reduceMotion,
    phase,
    markProgress,
    glowProgress,
    lettersOpacity,
    lettersY,
    descriptorOpacity,
    descriptorY,
  ]);

  // --- Type ----------------------------------------------------------------
  // Split from the intro because it also waits on the font: showing the tagline
  // in the system face and swapping it a frame later looks broken.
  useEffect(() => {
    if (reduceMotion === null || !fontsReady || phase === 'exit') return;
    const elapsed = Date.now() - mountedAt.current;
    const at = (delay: number) => (reduceMotion ? 0 : Math.max(0, delay - elapsed));

    Animated.parallel([
      Animated.timing(taglineOpacity, {
        toValue: 1,
        duration: SPLASH_TIMING.taglineMs,
        delay: at(SPLASH_TIMING.taglineDelayMs),
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(taglineY, {
        toValue: 0,
        duration: SPLASH_TIMING.taglineMs,
        delay: at(SPLASH_TIMING.taglineDelayMs),
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(provenanceOpacity, {
        toValue: 1,
        duration: SPLASH_TIMING.provenanceMs,
        delay: at(SPLASH_TIMING.provenanceDelayMs),
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(provenanceY, {
        toValue: 0,
        duration: SPLASH_TIMING.provenanceMs,
        delay: at(SPLASH_TIMING.provenanceDelayMs),
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [
    reduceMotion,
    fontsReady,
    phase,
    taglineOpacity,
    taglineY,
    provenanceOpacity,
    provenanceY,
  ]);

  // --- Slow boot -----------------------------------------------------------
  // No spinner up front: a loading affordance on a launch screen is what makes
  // an app feel homemade. Only if boot genuinely drags do we admit to waiting.
  useEffect(() => {
    if (phase === 'exit') return;
    const timer = setTimeout(() => setSlowBoot(true), SPLASH_TIMING.slowBootMs);
    return () => clearTimeout(timer);
  }, [phase]);

  useEffect(() => {
    if (!slowBoot || reduceMotion === null) return;
    Animated.timing(traceOpacity, {
      toValue: 1,
      duration: 300,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
    if (reduceMotion) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(traceProgress, {
          toValue: 1,
          duration: 1100,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(traceProgress, {
          toValue: 0,
          duration: 1100,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ]),
    );
    traceLoop.current = loop;
    loop.start();
    return () => {
      loop.stop();
      traceLoop.current = null;
    };
  }, [slowBoot, reduceMotion, traceOpacity, traceProgress]);

  // --- Exit ----------------------------------------------------------------
  useEffect(() => {
    if (phase !== 'exit') return;
    traceLoop.current?.stop();

    const finish = () => {
      if (exitDone.current) return;
      exitDone.current = true;
      onExitComplete?.();
    };

    Animated.parallel([
      Animated.timing(rootOpacity, {
        toValue: 0,
        duration: SPLASH_TIMING.exitMs,
        easing: Easing.in(Easing.quad),
        useNativeDriver: true,
      }),
      Animated.timing(contentScale, {
        toValue: 0.98,
        duration: SPLASH_TIMING.exitMs,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
    ]).start(finish);

    // The completion callback is the fast path; this timer is the contract.
    // A native-driver callback can be dropped (unmount, backgrounding), and we
    // must never leave a fully-transparent splash pinned over a live app.
    const fallback = setTimeout(finish, SPLASH_TIMING.exitMs + SPLASH_TIMING.exitFallbackMs);
    return () => clearTimeout(fallback);
  }, [phase, rootOpacity, contentScale, onExitComplete]);

  const markStyle = {
    transform: [
      {
        translateX: markProgress.interpolate({ inputRange: [0, 1], outputRange: [0, MARK_DX] }),
      },
      {
        translateY: markProgress.interpolate({ inputRange: [0, 1], outputRange: [0, MARK_DY] }),
      },
      {
        rotate: markProgress.interpolate({
          inputRange: [0, 1],
          outputRange: ['-180deg', '0deg'],
        }),
      },
      {
        scale: markProgress.interpolate({ inputRange: [0, 1], outputRange: [1, MARK_END_SCALE] }),
      },
    ],
  };

  return (
    <Animated.View
      testID="brand-splash"
      style={[styles.root, { opacity: rootOpacity }]}
      pointerEvents="none"
      needsOffscreenAlphaCompositing={phase === 'exit'}
      accessibilityElementsHidden={phase === 'exit'}
      importantForAccessibility={phase === 'exit' ? 'no-hide-descendants' : 'auto'}
    >
      <Animated.View
        style={[StyleSheet.absoluteFill, { transform: [{ scale: contentScale }] }]}
        accessible
        accessibilityRole="image"
        accessibilityLabel="Spinr Driver"
      >
        <Animated.Image
          testID="brand-splash-glow"
          source={GLOW}
          resizeMode="contain"
          accessible={false}
          style={[
            styles.glow,
            {
              opacity: glowProgress.interpolate({
                inputRange: [0, 1],
                outputRange: [1, GLOW_SETTLED_OPACITY],
              }),
              transform: [
                {
                  scale: glowProgress.interpolate({
                    inputRange: [0, 1],
                    outputRange: [1, GLOW_SETTLED_SCALE],
                  }),
                },
              ],
            },
          ]}
        />

        <Animated.Image
          testID="brand-splash-letters"
          source={LETTERS}
          resizeMode="contain"
          accessible={false}
          style={[
            styles.letters,
            { opacity: lettersOpacity, transform: [{ translateY: lettersY }] },
          ]}
        />

        <Animated.Image
          testID="brand-splash-descriptor"
          source={DESCRIPTOR}
          resizeMode="contain"
          accessible={false}
          style={[
            styles.descriptor,
            { opacity: descriptorOpacity, transform: [{ translateY: descriptorY }] },
          ]}
        />

        <Animated.Image
          testID="brand-splash-mark"
          source={MARK}
          resizeMode="contain"
          accessible={false}
          onLoad={handleMarkLoad}
          style={[styles.mark, markStyle]}
        />

        <Animated.Text
          testID="brand-splash-tagline"
          allowFontScaling={false}
          style={[
            styles.tagline,
            { opacity: taglineOpacity, transform: [{ translateY: taglineY }] },
          ]}
        >
          {SPLASH_TAGLINE}
        </Animated.Text>

        <Animated.Text
          testID="brand-splash-provenance"
          allowFontScaling={false}
          style={[
            styles.provenance,
            { opacity: provenanceOpacity, transform: [{ translateY: provenanceY }] },
          ]}
        >
          {SPLASH_PROVENANCE}
        </Animated.Text>

        {slowBoot ? (
          <Animated.View
            testID="brand-splash-trace"
            style={[
              styles.trace,
              {
                opacity: traceOpacity,
                transform: [
                  {
                    translateX: traceProgress.interpolate({
                      inputRange: [0, 1],
                      outputRange: [-Math.round(SCREEN_W * 0.16), Math.round(SCREEN_W * 0.16)],
                    }),
                  },
                ],
              },
            ]}
          />
        ) : null}
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: SPLASH_BACKGROUND,
  },
  glow: {
    position: 'absolute',
    left: SCREEN_W / 2 - GLOW_DP / 2,
    top: SCREEN_H / 2 - GLOW_DP / 2,
    width: GLOW_DP,
    height: GLOW_DP,
  },
  letters: {
    position: 'absolute',
    left: STAGE_LEFT,
    top: STAGE_TOP,
    width: WORDMARK_W,
    height: WORDMARK_H,
  },
  descriptor: {
    position: 'absolute',
    left: DESCRIPTOR_LEFT,
    top: DESCRIPTOR_TOP,
    width: DESCRIPTOR_W + 2 * DESCRIPTOR_MARGIN,
    height: DESCRIPTOR_H + 2 * DESCRIPTOR_MARGIN,
  },
  mark: {
    position: 'absolute',
    left: SCREEN_W / 2 - MARK_DP / 2,
    top: SCREEN_H / 2 - MARK_DP / 2,
    width: MARK_DP,
    height: MARK_DP,
  },
  tagline: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: TAGLINE_TOP,
    textAlign: 'center',
    fontSize: TAGLINE_SIZE,
    // Explicit lineHeight + padding so the descenders of "pp" in "Support" are
    // never clipped by the text box (RN's intrinsic line height is too tight
    // for PlusJakartaSans here). includeFontPadding keeps Android consistent.
    lineHeight: Math.round(TAGLINE_SIZE * 1.6),
    includeFontPadding: true,
    letterSpacing: 2.4,
    textTransform: 'uppercase',
    color: '#6B7280',
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  provenance: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 64,
    textAlign: 'center',
    fontSize: PROVENANCE_SIZE,
    lineHeight: Math.round(PROVENANCE_SIZE * 1.6),
    includeFontPadding: true,
    letterSpacing: 2.6,
    textTransform: 'uppercase',
    color: 'rgba(26, 26, 26, 0.35)',
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  trace: {
    position: 'absolute',
    left: SCREEN_W / 2 - 44,
    bottom: 112,
    width: 88,
    height: 2,
    borderRadius: 1,
    backgroundColor: '#E5E7EB',
  },
});
