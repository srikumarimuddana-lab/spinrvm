import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;

interface FreeCancelTimerProps {
  /** ISO timestamp when driver accepted the ride (null if not yet accepted). */
  driverAcceptedAt: string | null | undefined;
  /** Seconds within which cancellation is free. Default 120 (2 minutes). */
  freeCancelWindowSeconds?: number;
  /** Flat cancellation fee applied once the window expires. Default $3.00. */
  cancellationFee?: number;
  /** Compact single-line layout for use inside dialogs. Default false. */
  compact?: boolean;
}

/**
 * FreeCancelTimer — UX-001
 *
 * Shows the remaining free-cancel window with a countdown, then transitions
 * to a "Cancellation fee: $X" label once the window closes.
 *
 * This component replaces the hardcoded `Math.min(5, fare * 0.2)` fee formula
 * used in ride-status, driver-arriving and driver-arrived screens, which did
 * not match the server-side fee stored in app_settings.
 */
export function FreeCancelTimer({
  driverAcceptedAt,
  freeCancelWindowSeconds = 120,
  cancellationFee = 3.0,
  compact = false,
}: FreeCancelTimerProps) {
  const [secondsLeft, setSecondsLeft] = useState<number>(() => {
    if (!driverAcceptedAt) return freeCancelWindowSeconds;
    const elapsed = Math.floor((Date.now() - new Date(driverAcceptedAt).getTime()) / 1000);
    return Math.max(0, freeCancelWindowSeconds - elapsed);
  });

  useEffect(() => {
    if (!driverAcceptedAt || secondsLeft <= 0) return;

    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - new Date(driverAcceptedAt).getTime()) / 1000);
      const remaining = Math.max(0, freeCancelWindowSeconds - elapsed);
      setSecondsLeft(remaining);
      if (remaining === 0) clearInterval(interval);
    }, 1000);

    return () => clearInterval(interval);
  }, [driverAcceptedAt, freeCancelWindowSeconds]);

  const isWindowOpen = secondsLeft > 0 && !!driverAcceptedAt;
  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  const timerLabel = `${minutes}:${String(seconds).padStart(2, '0')}`;

  if (compact) {
    return (
      <Text style={isWindowOpen ? styles.compactFree : styles.compactFee}>
        {isWindowOpen
          ? `Free cancel — ${timerLabel} left`
          : `Cancel fee: $${cancellationFee.toFixed(2)}`}
      </Text>
    );
  }

  return (
    <View style={[styles.container, isWindowOpen ? styles.containerFree : styles.containerFee]}>
      <Ionicons
        name={isWindowOpen ? 'checkmark-circle-outline' : 'alert-circle-outline'}
        size={16}
        color={isWindowOpen ? '#059669' : '#DC2626'}
      />
      <View style={styles.textBlock}>
        {isWindowOpen ? (
          <>
            <Text style={styles.freeLabel}>Free cancellation</Text>
            <Text style={styles.freeTimer}>{timerLabel} remaining</Text>
          </>
        ) : (
          <>
            <Text style={styles.feeLabel}>Cancellation fee applies</Text>
            <Text style={styles.feeAmount}>${cancellationFee.toFixed(2)}</Text>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    borderWidth: 1,
  },
  containerFree: {
    backgroundColor: '#F0FFF4',
    borderColor: '#D1FAE5',
  },
  containerFee: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
  },
  textBlock: { flex: 1 },
  freeLabel: { fontSize: 13, fontWeight: '600', color: '#059669' },
  freeTimer: { fontSize: 18, fontWeight: '800', color: '#059669', letterSpacing: -0.5 },
  feeLabel: { fontSize: 13, fontWeight: '600', color: '#DC2626' },
  feeAmount: { fontSize: 18, fontWeight: '800', color: '#DC2626', letterSpacing: -0.5 },

  // Compact variants (for use inside alert dialogs)
  compactFree: { fontSize: 13, fontWeight: '600', color: '#059669' },
  compactFee:  { fontSize: 13, fontWeight: '600', color: '#DC2626' },
});

export default FreeCancelTimer;
