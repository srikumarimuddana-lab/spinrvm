import React, { useEffect, useRef } from 'react';
import {
  Animated,
  PanResponder,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useToastStore, type ToastVariant } from '../store/toastStore';
import { useAnimatedValue, useStableRef } from '../hooks/useAnimatedValue';

const VARIANT_CONFIG: Record<ToastVariant, { bg: string; icon: string }> = {
  info: { bg: '#1a73e8', icon: 'information-circle' },
  success: { bg: '#0d9f6e', icon: 'checkmark-circle' },
  warning: { bg: '#d97706', icon: 'warning' },
  danger: { bg: '#dc2626', icon: 'alert-circle' },
};

const DEFAULT_DURATION = 4000;

export default function Toast() {
  const insets = useSafeAreaInsets();
  const current = useToastStore((s) => s.current);
  const dismiss = useToastStore((s) => s.dismiss);

  const translateY = useAnimatedValue(-120);
  const opacity = useAnimatedValue(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hide = () => {
    if (timer.current) clearTimeout(timer.current);
    Animated.parallel([
      Animated.timing(translateY, { toValue: -120, duration: 200, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 0, duration: 200, useNativeDriver: true }),
    ]).start(() => dismiss());
  };

  // Stable gesture responder — created once, never reassigned. Handlers
  // close over `translateY` (a stable Animated.Value) and `hide` (a stable
  // function reference for this component), so the closure stays valid for
  // the component's lifetime. Declared after `hide` so nothing here reads
  // it before its declaration.
  const panResponder = useStableRef(() =>
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) => g.dy < -5,
      onPanResponderMove: (_, g) => {
        if (g.dy < 0) translateY.setValue(g.dy);
      },
      onPanResponderRelease: (_, g) => {
        if (g.dy < -30) {
          hide();
        } else {
          Animated.spring(translateY, {
            toValue: 0,
            useNativeDriver: true,
          }).start();
        }
      },
    }),
  );

  // Enter animation — runs only when a NEW toast appears (id changes). A
  // deduped repeat keeps the same id, so the banner does not slide back off
  // and in again (the flicker).
  useEffect(() => {
    if (!current) return;
    translateY.setValue(-120);
    opacity.setValue(0);
    Animated.parallel([
      Animated.spring(translateY, { toValue: 0, friction: 8, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 1, duration: 200, useNativeDriver: true }),
    ]).start();
  }, [current?.id]);

  // Auto-dismiss timer — (re)armed for a new toast OR when a deduped repeat
  // extends it (shownAt bumps). Kept separate from the enter animation so
  // refreshing the timer never restarts the slide-in.
  useEffect(() => {
    if (!current) return;
    const duration = current.duration ?? DEFAULT_DURATION;
    timer.current = setTimeout(hide, duration);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [current?.id, current?.shownAt]);

  if (!current) return null;

  const config = VARIANT_CONFIG[current.variant];

  return (
    <Animated.View
      style={[
        styles.container,
        { top: insets.top + 8, backgroundColor: config.bg, transform: [{ translateY }], opacity },
      ]}
      {...panResponder.panHandlers}
    >
      <Ionicons name={config.icon as any} size={20} color="#FFF" style={styles.icon} />
      <View style={styles.textWrap}>
        <Text style={styles.title} numberOfLines={1}>{current.title}</Text>
        {current.message ? (
          <Text style={styles.message} numberOfLines={2}>{current.message}</Text>
        ) : null}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    left: 16,
    right: 16,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    zIndex: 9999,
    elevation: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
  },
  icon: { marginRight: 12 },
  textWrap: { flex: 1 },
  title: { color: '#FFF', fontSize: 15, fontWeight: '600' },
  message: { color: 'rgba(255,255,255,0.9)', fontSize: 13, marginTop: 2 },
});
