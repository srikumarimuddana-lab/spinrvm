import React, { useEffect, useRef } from 'react';
import { Animated, Easing, StyleSheet, View, LayoutChangeEvent } from 'react-native';

const LOGO = require('../assets/images/spinr-logo.png');

type Props = { onLayout?: (e: LayoutChangeEvent) => void };

export default function BrandSplash({ onLayout }: Props) {
  const tagOpacity = useRef(new Animated.Value(0)).current;
  const tagY       = useRef(new Animated.Value(8)).current;

  useEffect(() => {
    // Logo is visible immediately — no fade-in — so the moment the native
    // splash cross-fades out, the Spinr logo is already there underneath.
    // Only the tagline animates in after a brief pause.
    Animated.parallel([
      Animated.timing(tagOpacity, {
        toValue: 1,
        duration: 500,
        delay: 300,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(tagY, {
        toValue: 0,
        duration: 500,
        delay: 300,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [tagOpacity, tagY]);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Animated.Image
        source={LOGO}
        style={styles.logo}
        resizeMode="contain"
      />
      <Animated.Text
        style={[styles.tagline, { opacity: tagOpacity, transform: [{ translateY: tagY }] }]}
        allowFontScaling={false}
      >
        Ride Local · Support Local
      </Animated.Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
  },
  logo: {
    width: 220,
    height: 80,
  },
  tagline: {
    marginTop: 18,
    fontSize: 13,
    letterSpacing: 1.2,
    color: '#8A8F98',
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
});
