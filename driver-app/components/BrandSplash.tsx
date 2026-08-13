import React, { useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  Easing,
  LayoutChangeEvent,
  StyleSheet,
  View,
} from 'react-native';

const LOGO = require('../assets/images/spinr-logo.png');

// Scale logo to 68% of screen width, capped at 260dp so it never looks
// oversized on large phones and never overflows on small ones (320dp → 218dp).
const SCREEN_W = Dimensions.get('window').width;
const LOGO_WIDTH = Math.min(260, Math.round(SCREEN_W * 0.68));
const LOGO_HEIGHT = Math.round((LOGO_WIDTH * 156) / 384);
// Tagline font scales with screen width: 12dp min on 320dp, 14dp on 390dp+.
const TAGLINE_SIZE = Math.min(14, Math.max(12, Math.round(SCREEN_W * 0.036)));

type Props = { onLayout?: (e: LayoutChangeEvent) => void };

/**
 * BrandSplash — the in-app splash shown while the app initializes (fonts, auth,
 * location). Rendered by the loading gate in app/_layout.tsx, which also holds
 * it for a minimum duration so the logo + tagline are actually seen. Plain core
 * RN Animated (no Reanimated) so it can mount before the rest of the app is up.
 */
export default function BrandSplash({ onLayout }: Props) {
  // react-hooks/refs ("Cannot access refs during render") flags every
  // `.current` read below, including these 5 declarations, the deps array,
  // and the JSX style bindings. All are the standard Animated.Value driver
  // idiom, not runtime state read for rendering — React guarantees useRef()
  // returns the same ref object every render, and nothing in this file ever
  // reassigns `.current` after creation (verified: grepped this file for
  // `.current =`, no matches), so a discarded/replayed concurrent render
  // reading `.current` would get the identical Animated.Value instance
  // every time. Animated mutates these values imperatively outside the
  // render cycle (Animated.timing/.spring), which is exactly why they're
  // refs and not useState — suppressed at each read site below.
  // eslint-disable-next-line react-hooks/refs
  const logoOpacity = useRef(new Animated.Value(0)).current;
  // eslint-disable-next-line react-hooks/refs
  const logoScale = useRef(new Animated.Value(0.92)).current;
  // eslint-disable-next-line react-hooks/refs
  const tagOpacity = useRef(new Animated.Value(0)).current;
  // eslint-disable-next-line react-hooks/refs
  const tagY = useRef(new Animated.Value(-8)).current;
  // eslint-disable-next-line react-hooks/refs
  const footerOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Logo: fade in with a gentle settle.
    Animated.parallel([
      Animated.timing(logoOpacity, {
        toValue: 1,
        duration: 500,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.spring(logoScale, {
        toValue: 1,
        friction: 7,
        tension: 60,
        useNativeDriver: true,
      }),
    ]).start();

    // Tagline: fade up shortly after the logo lands.
    Animated.parallel([
      Animated.timing(tagOpacity, {
        toValue: 1,
        duration: 600,
        delay: 350,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(tagY, {
        toValue: 0,
        duration: 600,
        delay: 350,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();

    // Footer loader: fades in last so the screen reads brand-first.
    Animated.timing(footerOpacity, {
      toValue: 1,
      duration: 400,
      delay: 700,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
    // eslint-disable-next-line react-hooks/refs -- stable Animated.Value driver refs, see note above declarations
  }, [logoOpacity, logoScale, tagOpacity, tagY, footerOpacity]);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <View style={styles.center}>
        <Animated.Image
          source={LOGO}
          resizeMode="contain"
          // eslint-disable-next-line react-hooks/refs -- stable Animated.Value driver refs, see note above declarations
          style={[styles.logo, { opacity: logoOpacity, transform: [{ scale: logoScale }] }]}
        />
        <Animated.Text
          allowFontScaling={false}
          // eslint-disable-next-line react-hooks/refs -- stable Animated.Value driver refs, see note above declarations
          style={[styles.tagline, { opacity: tagOpacity, transform: [{ translateY: tagY }] }]}
        >
          Ride Local · Support Local
        </Animated.Text>
      </View>

      <Animated.View style={[styles.footer, { opacity: footerOpacity }]}>
        <ActivityIndicator size="small" color="#C7CBD1" />
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  logo: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
  tagline: {
    marginTop: 16,
    paddingBottom: 12,
    fontSize: TAGLINE_SIZE,
    // Explicit lineHeight + bottom padding so the descenders of the "pp" in
    // "Support" aren't clipped by the text box (RN's intrinsic line height is
    // too tight for PlusJakartaSans). includeFontPadding keeps Android
    // consistent. This is a pure text-style change — no timing/layout effect.
    lineHeight: Math.round(TAGLINE_SIZE * 1.5),
    includeFontPadding: true,
    letterSpacing: 0.8,
    color: '#8A8F98',
    fontFamily: 'PlusJakartaSans_600SemiBold',
    textAlign: 'center',
    alignSelf: 'stretch',
  },
  footer: {
    position: 'absolute',
    bottom: 56,
    left: 0,
    right: 0,
    alignItems: 'center',
  },
});
