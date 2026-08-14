import { useCallback, useRef, useState } from 'react';
import { Animated } from 'react-native';

export interface UseHoldToConfirmOptions {
  /** How long the press must be held before onConfirm fires, in ms. */
  durationMs: number;
  /** Fires exactly once, when a hold completes the full duration. */
  onConfirm: () => void;
}

export interface UseHoldToConfirmResult {
  /** Animated 0 -> 1 value over durationMs while held; resets to 0 on release/complete. */
  progress: Animated.Value;
  /** True while the press is being held (not yet released or completed). */
  pressing: boolean;
  onPressIn: () => void;
  /** No-op if the hold already completed (onConfirm already fired) or wasn't pressing. */
  onPressOut: () => void;
}

/**
 * Press-and-hold-for-N-ms confirmation gesture, shared by the driver SOS
 * Safety shield's own hold and the Safety overlay's "Alert Emergency
 * Contacts" hold button (ACTION_ITEMS.md B16) — both need the identical
 * "hold N ms -> animate progress -> fire once" timing, so it's factored out
 * here instead of duplicated.
 *
 * Deliberately implemented with plain `Animated` + `setTimeout`, mirroring
 * `SOSButton.tsx`'s existing hold-gesture style, rather than
 * react-native-gesture-handler — this keeps the same simple, proven pattern
 * intact on a safety control instead of introducing a new gesture library
 * dependency for it.
 */
export function useHoldToConfirm({ durationMs, onConfirm }: UseHoldToConfirmOptions): UseHoldToConfirmResult {
  const [pressing, setPressing] = useState(false);
  const progress = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animation = useRef<Animated.CompositeAnimation | null>(null);

  const reset = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (animation.current) {
      animation.current.stop();
      animation.current = null;
    }
    progress.setValue(0);
    setPressing(false);
  }, [progress]);

  const onPressIn = useCallback(() => {
    setPressing(true);
    progress.setValue(0);
    animation.current = Animated.timing(progress, {
      toValue: 1,
      duration: durationMs,
      useNativeDriver: false, // driven into stroke/width values, not transform/opacity
    });
    animation.current.start();
    timer.current = setTimeout(() => {
      timer.current = null;
      // Full hold completed: fire once, then reset so a subsequent press
      // starts clean rather than replaying the fired-at-1.0 progress state.
      onConfirm();
      reset();
    }, durationMs);
  }, [durationMs, onConfirm, progress, reset]);

  const onPressOut = useCallback(() => {
    // Released before completion (or already fired/reset) -- cancel and
    // reset without calling onConfirm.
    reset();
  }, [reset]);

  return { progress, pressing, onPressIn, onPressOut };
}
