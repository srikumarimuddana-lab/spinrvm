import React, { useEffect, useRef } from 'react';
import { Animated, Easing, StyleSheet, View, LayoutChangeEvent } from 'react-native';

const LOGO = require('../assets/images/spinr-logo.png');

type Props = { onLayout?: (e: LayoutChangeEvent) => void };

export default function BrandSplash({ onLayout }: Props) {
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const tagOpacity = useRef(new Animated.Value(0)).current;
  const tagY = useRef(new Animated.Value(8)).current;

  useEffect(() => {
    Animated.sequence([
      Animated.timing(logoOpacity, {
        toValue: 1,
        duration: 400,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.parallel([
        Animated.timing(tagOpacity, {
          toValue: 1,
          duration: 500,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
        Animated.timing(tagY, {
          toValue: 0,
          duration: 500,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
      ]),
    ]).start();
  }, [logoOpacity, tagOpacity, tagY]);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Animated.Image
        source={LOGO}
        style={[styles.logo, { opacity: logoOpacity }]}
        resizeMode="contain"
      />
      <Animated.Text
        style={[styles.subtitle, { opacity: tagOpacity, transform: [{ translateY: tagY }] }]}
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
  subtitle: {
    marginTop: 18,
    fontSize: 13,
    letterSpacing: 1.2,
    color: '#8A8F98',
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
});
