/**
 * Spinr Motion Constants (rider-app + driver-app — not for admin-dashboard)
 *
 * Shared `Animated.timing` duration/easing so functionally identical
 * interactions don't drift apart when reimplemented independently on each
 * screen. Deliberately separate from `./responsive.ts`: that file is scoped
 * to device/window-dimension-driven values (breakpoints, scaled fonts,
 * responsive spacing) that react to `useWindowDimensions()`; TIMING/EASING
 * are static motion tokens with no relationship to screen size, so they get
 * their own file rather than stretching responsive.ts's stated scope.
 *
 * First (and so far only) shared use: the wrong-code "shake" on rider-app's
 * OTP entry (`app/otp.tsx`) and driver-app's PIN entry
 * (`components/dashboard/ActiveRidePanel.tsx`) — see ACTION_ITEMS.md UX4.
 * Both used to hardcode their own step duration (60ms / 50ms respectively)
 * and left easing at RN's implicit default. `shakeHorizontal()` below is the
 * one shared implementation of that sequence; each caller only supplies the
 * amplitude, which is a visual/layout detail of that screen, not a timing
 * convention.
 *
 * Usage:
 *   import { TIMING, EASING, shakeHorizontal } from '@shared/utils/motion';
 *
 *   const shakeAnim = useAnimatedValue(0); // or useRef(new Animated.Value(0)).current
 *   shakeHorizontal(shakeAnim); // default amplitude, or shakeHorizontal(shakeAnim, [10, 6])
 */

import { Animated, Easing } from 'react-native';

// ── Durations (ms) ───────────────────────────────────────────────────────────
export const TIMING = {
  // Single leg of a rapid back-and-forth shake (e.g. wrong-code entry).
  shakeStep: 50,
  fast: 150,
  base: 250,
  slow: 400,
} as const;

// ── Easing curves ─────────────────────────────────────────────────────────────
export const EASING = {
  // Constant velocity per leg — reads as a consistent "buzz" rather than the
  // default ease-in-out, which pauses fractionally at each direction change.
  shake: Easing.linear,
  // RN's own Animated.timing default, named here so a call site can be
  // explicit about using it rather than silently relying on the fallback.
  standard: Easing.inOut(Easing.ease),
} as const;

/**
 * Horizontal wrong-code shake: a decaying back-and-forth sequence that
 * settles back to 0. `amplitudes` is `[primary, secondary]` — the first
 * (larger) swing's magnitude and the second (smaller) one; each is mirrored
 * to its negative before the value eases back to rest. Defaults match
 * rider-app's OTP shake; pass driver-app's `[10, 6]` (or any screen's own
 * values) to keep that screen's existing visual amplitude while sharing the
 * same step duration/easing/shape.
 */
export function shakeHorizontal(
  value: Animated.Value,
  amplitudes: readonly [number, number] = [12, 8],
): void {
  const [primary, secondary] = amplitudes;
  Animated.sequence([
    Animated.timing(value, { toValue: primary, duration: TIMING.shakeStep, easing: EASING.shake, useNativeDriver: true }),
    Animated.timing(value, { toValue: -primary, duration: TIMING.shakeStep, easing: EASING.shake, useNativeDriver: true }),
    Animated.timing(value, { toValue: secondary, duration: TIMING.shakeStep, easing: EASING.shake, useNativeDriver: true }),
    Animated.timing(value, { toValue: -secondary, duration: TIMING.shakeStep, easing: EASING.shake, useNativeDriver: true }),
    Animated.timing(value, { toValue: 0, duration: TIMING.shakeStep, easing: EASING.shake, useNativeDriver: true }),
  ]).start();
}
