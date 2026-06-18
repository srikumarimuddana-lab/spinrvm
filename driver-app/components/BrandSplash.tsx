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
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const logoScale = useRef(new Animated.Value(0.92)).current;
  const tagOpacity = useRef(new Animated.Value(0)).current;
  const tagY = useRef(new Animated.Value(-8)).current;
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
  }, [logoOpacity, logoScale, tagOpacity, tagY, footerOpacity]);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <View style={styles.center}>
        <View style={styles.logoWrap}>
          <Animated.Image
            source={LOGO}
            resizeMode="contain"
            style={[styles.logo, { opacity: logoOpacity, transform: [{ scale: logoScale }] }]}
          />
          {/* Role label — right-aligned under the wordmark so it reads as
              "spinr Driver", distinguishing this build from the rider app. */}
          <Animated.Text
            allowFontScaling={false}
            style={[styles.appRole, { opacity: logoOpacity }]}
          >
            Driver
          </Animated.Text>
        </View>
        <Animated.Text
          allowFontScaling={false}
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
  logoWrap: {
    width: LOGO_WIDTH,
    // Right-align the "Driver" label to the wordmark's right edge.
    alignItems: 'flex-end',
  },
  logo: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
  appRole: {
    marginTop: 2,
    marginRight: 4,
    fontSize: Math.round(LOGO_HEIGHT * 0.22),
    letterSpacing: 3,
    color: '#FF3B30',
    fontFamily: 'PlusJakartaSans_700Bold',
    textTransform: 'uppercase',
    includeFontPadding: false,
  },
  tagline: {
    marginTop: 18,
    paddingBottom: 12,
    fontSize: TAGLINE_SIZE,
    // Explicit lineHeight + padding so the descenders of "pp" in "Support" are
    // never clipped by the text box (RN's intrinsic line height is too tight
    // for PlusJakartaSans here). includeFontPadding keeps Android consistent.
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
