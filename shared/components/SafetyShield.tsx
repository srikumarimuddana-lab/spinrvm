/**
 * Driver-app only (ACTION_ITEMS.md B16). Pulls in react-native-svg, which is
 * a driver-app dependency but NOT a rider-app one — do not import this from
 * rider-app or its build breaks on a missing native module. Rider-app keeps
 * its own, unmodified SOS UX (SOSButton.tsx).
 *
 * The "Discreet Hold Shield": a floating shield icon with two gestures --
 * hold 3s fires a SILENT alert (no screen change beyond a badge + a brief
 * auto-dismissing toast, never an interruptive Alert.alert -- the entire
 * point is that a threatening in-vehicle passenger sees nothing change on
 * the driver's screen); a short tap opens the full Safety overlay
 * (SafetyOverlay.tsx) instead.
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Circle } from 'react-native-svg';
import { useHoldToConfirm } from '../hooks/useHoldToConfirm';
import { useEmergencyContacts } from '../hooks/useEmergencyContacts';

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

const HOLD_MS = 3000;
const TOAST_VISIBLE_MS = 3500;
const FAILURE_VISIBLE_MS = 3000;

const SIZE = 46;
const RING_SIZE = 54;
const RING_RADIUS = (RING_SIZE - 4) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

export interface SafetyShieldTriggerResult {
  contacts: Array<{ id: string; name: string; notified: boolean }>;
}

export interface SafetyShieldProps {
  rideId: string;
  onTrigger: (rideId: string, lat?: number, lng?: number) => Promise<SafetyShieldTriggerResult>;
  /** Fires on a short tap (release before the 3s hold completes). */
  onOpenOverlay: () => void;
  lat?: number;
  lng?: number;
}

export function SafetyShield({ rideId, onTrigger, onOpenOverlay, lat, lng }: SafetyShieldProps) {
  const [sent, setSent] = useState(false);
  const [failed, setFailed] = useState(false);
  const [toastVisible, setToastVisible] = useState(false);
  const [toastContactNames, setToastContactNames] = useState<string[]>([]);
  const { contacts } = useEmergencyContacts();
  const releasedEarly = useRef(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const failureTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleConfirm = () => {
    if (releasedEarly.current) return;
    (async () => {
      try {
        const result = await onTrigger(rideId, lat, lng);
        setFailed(false);
        setSent(true);
        const names = (result?.contacts || []).filter((c) => c.notified).map((c) => c.name).filter(Boolean);
        setToastContactNames(names);
        setToastVisible(true);
        if (toastTimer.current) clearTimeout(toastTimer.current);
        toastTimer.current = setTimeout(() => setToastVisible(false), TOAST_VISIBLE_MS);
      } catch {
        // A failed SILENT alert must never itself become interruptive --
        // no Alert.alert, no red flash. Brief inline amber state only.
        setFailed(true);
        if (failureTimer.current) clearTimeout(failureTimer.current);
        failureTimer.current = setTimeout(() => setFailed(false), FAILURE_VISIBLE_MS);
      }
    })();
  };

  const { progress, pressing, onPressIn, onPressOut } = useHoldToConfirm({
    durationMs: HOLD_MS,
    onConfirm: handleConfirm,
  });

  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
      if (failureTimer.current) clearTimeout(failureTimer.current);
    },
    [],
  );

  const handlePressIn = () => {
    releasedEarly.current = false;
    onPressIn();
  };

  const handlePressOut = () => {
    // A release strictly before the hold fires counts as a tap -> open the
    // overlay. useHoldToConfirm's own onPressOut always resets progress;
    // releasedEarly just tells handleConfirm (already scheduled via
    // setTimeout inside the hook) not to also fire if it somehow races.
    if (!pressing) return; // already fired/reset; nothing to open
    releasedEarly.current = true;
    onPressOut();
    onOpenOverlay();
  };

  const strokeDashoffset = progress.interpolate({
    inputRange: [0, 1],
    outputRange: [RING_CIRCUMFERENCE, 0],
  });

  return (
    <View style={styles.wrap}>
      {pressing && (
        <Svg width={RING_SIZE} height={RING_SIZE} style={styles.ring} pointerEvents="none">
          <Circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            stroke="rgba(229,62,62,0.25)"
            strokeWidth={3}
            fill="none"
          />
          <AnimatedCircle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            stroke="#E53E3E"
            strokeWidth={3}
            fill="none"
            strokeDasharray={`${RING_CIRCUMFERENCE}, ${RING_CIRCUMFERENCE}`}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            rotation={-90}
            originX={RING_SIZE / 2}
            originY={RING_SIZE / 2}
          />
        </Svg>
      )}
      <Pressable
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        style={[styles.shield, failed && styles.shieldFailed]}
        accessibilityRole="button"
        accessibilityLabel="Safety shield"
        accessibilityHint="Hold for 3 seconds to send a silent alert. Tap to open safety options."
        accessibilityState={{ busy: pressing }}
      >
        <Ionicons name={failed ? 'alert-circle' : 'shield'} size={22} color={failed ? '#FCD34D' : '#FFFFFF'} />
        {sent && (
          <View style={styles.badge}>
            <Ionicons name="shield-checkmark" size={10} color="#FFFFFF" />
          </View>
        )}
      </Pressable>
      {toastVisible && (
        <View style={styles.toast}>
          <View style={styles.toastDot} />
          {/* Names appear only for contacts the backend confirmed as notified.
              With none confirmed (no contacts saved, or a deduplicated replay
              whose original send isn't re-derivable) the toast claims only
              what is certain: Spinr Safety was alerted. */}
          <Text style={styles.toastText} numberOfLines={2}>
            Silent alert sent
            {toastContactNames.length > 0 ? ` — ${toastContactNames.join(', ')} + Spinr Safety notified` : ' — Spinr Safety notified'}
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    width: SIZE,
    height: SIZE,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ring: {
    position: 'absolute',
  },
  shield: {
    width: SIZE,
    height: SIZE,
    borderRadius: SIZE / 2,
    backgroundColor: 'rgba(10,10,20,0.8)',
    borderWidth: 1.5,
    borderColor: 'rgba(229,62,62,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  shieldFailed: {
    borderColor: '#FCD34D',
    borderWidth: 2,
  },
  badge: {
    position: 'absolute',
    top: -2,
    right: -2,
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: '#38A169',
    borderWidth: 2,
    borderColor: '#000',
    alignItems: 'center',
    justifyContent: 'center',
  },
  toast: {
    position: 'absolute',
    top: SIZE + 8,
    right: 0,
    minWidth: 200,
    maxWidth: 260,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(15,20,15,0.92)',
    borderWidth: 1,
    borderColor: 'rgba(56,161,105,0.35)',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  toastDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#38A169',
  },
  toastText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '600',
    flexShrink: 1,
  },
});

export default SafetyShield;
