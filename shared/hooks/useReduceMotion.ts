/**
 * Live read of the OS "Reduce Motion" accessibility setting.
 *
 * Single shared source for rider-app/driver-app components that run
 * decorative looping animations (pulses, shimmer, presence rings). Unlike a
 * one-shot `AccessibilityInfo.isReduceMotionEnabled()` call, this also
 * subscribes to `reduceMotionChanged`, so a user who flips the setting while
 * a screen is mounted gets the change without restarting the app — the same
 * behaviour `shared/components/AiAuroraBackground.tsx` already had inline.
 *
 * Returns `false` until the async read resolves, so the first frame renders
 * exactly as it did before this hook existed; a component that gates a loop
 * on it must stop the loop (and reset to its static resting value) when the
 * value flips to `true`.
 *
 * Usage:
 *   const reduceMotion = useReduceMotion();
 *   useEffect(() => {
 *     if (reduceMotion) return;
 *     const loop = Animated.loop(...); loop.start();
 *     return () => loop.stop();
 *   }, [reduceMotion]);
 */
import { useEffect, useState } from 'react';
import { AccessibilityInfo } from 'react-native';

export function useReduceMotion(): boolean {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((enabled) => {
        if (mounted) setReduceMotion(enabled);
      })
      // A failed read leaves the default (animations on) — the pre-existing
      // behaviour for every caller — rather than throwing into render.
      .catch(() => {});
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', (enabled) => {
      if (mounted) setReduceMotion(enabled);
    });
    return () => {
      mounted = false;
      sub.remove();
    };
  }, []);

  return reduceMotion;
}
