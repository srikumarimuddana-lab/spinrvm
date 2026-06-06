import React, { useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  LayoutChangeEvent,
  StyleSheet,
  View,
} from 'react-native';

const LOGO = require('../assets/images/spinr-logo.png');

// The logo asset is 384×156 (≈2.46:1). Keep the render box on that exact ratio
// so `resizeMode="contain"` fills it with no letterboxing and the full mark is
// always shown — never cropped, never squished.
const LOGO_WIDTH = 260;
const LOGO_HEIGHT = Math.round((LOGO_WIDTH * 156) / 384); // 106

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
  const tagY = useRef(new Animated.Value(10)).current;
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
        <Animated.Image
          source={LOGO}
          resizeMode="contain"
          style={[styles.logo, { opacity: logoOpacity, transform: [{ scale: logoScale }] }]}
        />
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
  logo: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
  tagline: {
    marginTop: 20,
    fontSize: 13,
    letterSpacing: 1.4,
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
