import React, { useState, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert, Animated,
  Linking, Vibration,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getSOSLocation } from '../utils/sosLocation';

interface SOSButtonProps {
  rideId?: string;
  onTrigger: (rideId: string, lat?: number, lng?: number) => Promise<void>;
  size?: 'small' | 'large';
}

/**
 * SOS Emergency Button — long press to activate.
 * 1. Vibrates device
 * 2. Calls backend emergency endpoint (notifies admin + emergency contacts)
 * 3. Only shows success + 911 prompt AFTER backend confirms (fail-safe)
 * 4. On network failure: shows retry dialog AND button stays in FAILED state
 *    until the alert is confirmed by the backend — a dismissed alert does NOT
 *    reset the button to idle so the driver always knows the alert was not sent.
 * 5. While in FAILED state a single tap (no hold) retries immediately.
 */
const SOS_HOLD_MS = 1200;
// R-P0-1 spec: 3 total attempts with 1 s / 2 s backoff between them
// before declaring failure. The button stays amber until a backend 200.
const SOS_MAX_ATTEMPTS = 3;
const SOS_RETRY_DELAYS_MS = [1000, 2000];

export function SOSButton({ rideId, onTrigger, size = 'small' }: SOSButtonProps) {
  const [triggered, setTriggered] = useState(false);
  const [sending, setSending] = useState(false);
  const [pressing, setPressing] = useState(false);
  // Persistent failure flag: stays true until the backend confirms the alert.
  // Dismissing the failure dialog does NOT clear it — the button stays amber
  // so the driver always has a visible reminder that the alert was NOT sent.
  const [failed, setFailed] = useState(false);
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const startPress = () => {
    setPressing(true);
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.15, duration: 400, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1, duration: 400, useNativeDriver: true }),
      ])
    ).start();
    pressTimer.current = setTimeout(() => {
      triggerSOS();
    }, SOS_HOLD_MS);
  };

  const endPress = () => {
    setPressing(false);
    pulseAnim.stopAnimation();
    pulseAnim.setValue(1);
    if (pressTimer.current) {
      clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
  };

  const showSuccessAlert = () => {
    Alert.alert(
      '🚨 Emergency Alert Sent',
      'Your location has been shared with Spinr support and your emergency contacts.\n\nDo you want to call 911?',
      [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: "I'm OK",
          style: 'cancel',
          onPress: () => setTriggered(false),
        },
      ]
    );
  };

  const showFailureAlert = (retry: () => void) => {
    Alert.alert(
      '⚠️ Alert Not Sent',
      'Could not reach Spinr. The button will stay red — tap it to retry.\n\nYou can call 911 directly right now.',
      [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: 'Retry Now',
          onPress: retry,
        },
        // "Dismiss" intentionally does NOT reset the button — failed state
        // persists so the driver always knows the alert was not sent.
        { text: 'Dismiss', style: 'cancel' },
      ]
    );
  };

  const triggerSOS = async () => {
    // Clear any prior failure so the button transitions to "Sending…" visually.
    setFailed(false);
    setPressing(false);
    setSending(true);
    Vibration.vibrate([0, 200, 100, 200, 100, 200]);

    // Hard-bounded lookup: fresh fix raced against a short deadline, then
    // last-known, then no coords — a GPS dead zone must never delay the alert.
    const { lat, lng } = await getSOSLocation();
    if (lat === undefined) {
      console.warn('[SOS] No location fix within deadline — sending alert without coordinates');
    }

    // Attempt backend call with two retries (3 total attempts) using
    // 1 s / 2 s backoff before declaring failure. Never treat missing
    // rideId as success — that would show "Alert Sent" without any
    // backend notification going out.
    let backendOk = false;
    if (rideId) {
      for (let attempt = 0; attempt < SOS_MAX_ATTEMPTS && !backendOk; attempt++) {
        if (attempt > 0) {
          const delay = SOS_RETRY_DELAYS_MS[attempt - 1] ?? SOS_RETRY_DELAYS_MS[SOS_RETRY_DELAYS_MS.length - 1];
          await new Promise<void>((r) => setTimeout(r, delay));
        }
        try {
          await onTrigger(rideId, lat, lng);
          backendOk = true;
        } catch {}
      }
    }

    setSending(false);

    if (!rideId) {
      // No active ride context — direct user to call 911 immediately.
      Alert.alert(
        'No Active Ride',
        'Emergency alert requires an active ride. Call 911 directly for immediate help.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Call 911', style: 'destructive', onPress: () => Linking.openURL('tel:911') },
        ],
      );
    } else if (backendOk) {
      setTriggered(true);
      showSuccessAlert();
    } else {
      // Mark failed BEFORE showing the alert so the button is already amber
      // when the dialog appears. Dismissing the dialog leaves failed=true.
      setFailed(true);
      showFailureAlert(triggerSOS);
    }
  };

  const isLarge = size === 'large';

  const iconName = triggered
    ? 'checkmark-circle'
    : sending
    ? 'hourglass'
    : failed
    ? 'alert-circle'
    : 'shield';

  const labelText = pressing
    ? 'Hold...'
    : sending
    ? 'Sending…'
    : triggered
    ? 'Alert Sent'
    : failed
    ? 'FAILED'
    : 'SOS';

  return (
    <Animated.View style={[{ transform: [{ scale: pressing ? pulseAnim : 1 }] }]}>
      <TouchableOpacity
        style={[
          styles.btn,
          isLarge ? styles.btnLarge : styles.btnSmall,
          triggered && styles.btnTriggered,
          sending && styles.btnSending,
          pressing && styles.btnPressing,
          failed && styles.btnFailed,
        ]}
        // In failed state a single tap retries immediately — no hold required.
        onPressIn={sending || triggered ? undefined : (failed ? triggerSOS : startPress)}
        onPressOut={sending || triggered || failed ? undefined : endPress}
        activeOpacity={0.9}
        disabled={sending}
        accessibilityLabel={
          triggered
            ? 'Emergency alert sent'
            : sending
            ? 'Sending emergency alert'
            : failed
            ? 'Emergency alert failed — tap to retry'
            : 'Emergency SOS'
        }
        accessibilityRole="button"
        accessibilityHint={failed ? "Alert failed — double tap to retry" : triggered ? "Alert sent" : "Double tap to send SOS alert"}
        accessibilityState={{
          selected: triggered,
          busy: sending,
          disabled: sending,
        }}
      >
        <Ionicons name={iconName} size={isLarge ? 28 : 20} color="#FFF" />
        {isLarge && <Text style={styles.btnText}>{labelText}</Text>}
      </TouchableOpacity>
      {pressing && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Hold for 1.2 seconds</Text>
        </View>
      )}
      {failed && !sending && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Tap to retry</Text>
        </View>
      )}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  btn: {
    backgroundColor: '#DC2626',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 6,
    shadowColor: '#DC2626',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
  },
  btnSmall: {
    width: 44, height: 44, borderRadius: 22,
  },
  btnLarge: {
    width: 80, height: 80, borderRadius: 40,
    flexDirection: 'column', gap: 2,
  },
  btnPressing: {
    backgroundColor: '#B91C1C',
  },
  btnSending: {
    backgroundColor: '#D97706',
  },
  btnTriggered: {
    backgroundColor: '#10B981',
  },
  // Distinct amber style for failed state — clearly different from normal red.
  // Border draws attention even on dark backgrounds.
  btnFailed: {
    backgroundColor: '#92400E',
    borderWidth: 2,
    borderColor: '#FCD34D',
  },
  btnText: {
    color: '#FFF', fontSize: 12, fontWeight: '800', letterSpacing: 0.5,
  },
  holdHint: {
    position: 'absolute', bottom: -20, alignSelf: 'center',
    backgroundColor: 'rgba(0,0,0,0.7)', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  holdHintText: {
    color: '#FFF', fontSize: 10, fontWeight: '600',
  },
});

export default SOSButton;
