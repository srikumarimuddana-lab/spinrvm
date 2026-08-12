import { useRef } from 'react';
import { Animated } from 'react-native';

/**
 * Returns a stable value, built once per component instance by `factory`
 * and never reassigned — the general "create once via useRef, never mutate"
 * idiom (an animation driver, a gesture responder, any instance that
 * intentionally lives outside React's render/state model).
 *
 * This is the one place in the app that reads `ref.current` during render
 * and suppresses `react-hooks/refs` for it — every caller below just gets
 * a plain value back, with no further `.current` access for the linter (or
 * a future reader) to reason about. `factory` still runs on every render
 * (useRef only uses the FIRST call's result — same as the pre-existing
 * `useRef(new Animated.Value(x)).current` idiom this replaces), so keep it
 * cheap and side-effect-free.
 */
export function useStableRef<T>(factory: () => T): T {
  // The instance is created once via useRef and never reassigned anywhere
  // (no `.current = ...` in this hook or, per each call site's own
  // audit, in the caller) — the render-time dereference is inert because
  // the ref's identity never changes after initial setup.
  // eslint-disable-next-line react-hooks/refs
  return useRef(factory()).current;
}

/**
 * Returns a stable `Animated.Value`, created once per component instance and
 * never reassigned — the idiomatic RN pattern for driving native-thread
 * animations (`useNativeDriver: true`) without triggering React re-renders.
 *
 * `Animated.Value` is intentionally NOT React state: calling `.setValue()` or
 * running an `Animated.timing`/`Animated.spring` against it must NOT cause a
 * re-render (that is the entire point of the native-driver animation model —
 * the value is mutated outside React's render cycle). So `useState` is not a
 * correct substitute here, unlike a plain reactive primitive.
 */
export function useAnimatedValue(initial: number): Animated.Value {
  return useStableRef(() => new Animated.Value(initial));
}

/**
 * Array form of {@link useAnimatedValue} — `count` independent
 * `Animated.Value`s (same initial value each), created once and never
 * reassigned. Used for per-item animations (e.g. staggered section
 * fade-ins, per-digit OTP dots) where the count is fixed for the
 * component's lifetime.
 */
export function useAnimatedValues(count: number, initial: number): Animated.Value[] {
  return useStableRef(() => Array.from({ length: count }, () => new Animated.Value(initial)));
}
