import React, { useEffect } from 'react';
import { Animated, StyleSheet, ViewStyle } from 'react-native';
import { useAnimatedValue } from '../hooks/useAnimatedValue';
import { useReduceMotion } from '@shared/hooks/useReduceMotion';

interface Props {
  width?: number | string;
  height?: number;
  borderRadius?: number;
  style?: ViewStyle;
}

export default function SkeletonBox({ width = '100%', height = 16, borderRadius = 6, style }: Props) {
  const opacity = useAnimatedValue(0.4);
  const reduceMotion = useReduceMotion();

  useEffect(() => {
    // Reduce Motion: hold a static mid-tone placeholder instead of pulsing.
    if (reduceMotion) {
      opacity.setValue(0.7);
      return;
    }
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.4, duration: 700, useNativeDriver: true }),
      ])
    );
    pulse.start();
    return () => pulse.stop();
  }, [opacity, reduceMotion]);

  return (
    <Animated.View
      style={[
        styles.box,
        { width: width as any, height, borderRadius, opacity },
        style,
      ]}
    />
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: '#E0E0E0',
  },
});
