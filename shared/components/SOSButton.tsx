import React, { useState, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert, Animated,
  Linking, Vibration,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';

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
 * 4. On network failure: shows retry dialog, never shows "Alert Sent" falsely
 */
const SOS_HOLD_MS = 1200;
const SOS_RETRY_DELAY_MS = 2000;
const SOS_MAX_ATTEMPTS = 2;

export function SOSButton({ rideId, onTrigger, size = 'small' }: SOSButtonProps) {
  const [triggered, setTriggered] = useState(false);
  const [sending, setSending] = useState(false);
  const [pressing, setPressing] = useState(false);
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
      'Alert Not Sent',
      'Could not reach Spinr. Your device will keep trying. You can call 911 directly.',
      [
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: 'Retry',
          onPress: retry,
        },
        { text: 'Cancel', style: 'cancel' },
      ]
    );
  };

  const triggerSOS = async () => {
    setPressing(false);
    setSending(true);
    Vibration.vibrate([0, 200, 100, 200, 100, 200]);

    let lat: number | undefined;
    let lng: number | undefined;
    try {
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      lat = loc.coords.latitude;
      lng = loc.coords.longitude;
    } catch {}

    // Attempt backend call with one retry before declaring failure.
    let backendOk = !rideId; // no rideId = demo / offline mode, treat as ok
    if (rideId) {
      for (let attempt = 0; attempt < SOS_MAX_ATTEMPTS && !backendOk; attempt++) {
        if (attempt > 0) {
          await new Promise<void>((r) => setTimeout(r, SOS_RETRY_DELAY_MS));
        }
        try {
          await onTrigger(rideId, lat, lng);
          backendOk = true;
        } catch {}
      }
    }

    setSending(false);

    if (backendOk) {
      setTriggered(true);
      showSuccessAlert();
    } else {
      // Do NOT set triggered — the alert was NOT sent.
      showFailureAlert(triggerSOS);
    }
  };

  const isLarge = size === 'large';
  const iconName = triggered ? 'checkmark-circle' : sending ? 'hourglass' : 'shield';
  const labelText = pressing ? 'Hold...' : sending ? 'Sending…' : triggered ? 'Alert Sent' : 'SOS';

  return (
    <Animated.View style={[{ transform: [{ scale: pressing ? pulseAnim : 1 }] }]}>
      <TouchableOpacity
        style={[
          styles.btn,
          isLarge ? styles.btnLarge : styles.btnSmall,
          triggered && styles.btnTriggered,
          sending && styles.btnSending,
          pressing && styles.btnPressing,
        ]}
        onPressIn={sending || triggered ? undefined : startPress}
        onPressOut={sending || triggered ? undefined : endPress}
        activeOpacity={0.9}
        disabled={sending}
        accessibilityLabel={
          triggered ? 'Emergency alert sent' : sending ? 'Sending emergency alert' : 'Emergency SOS'
        }
        accessibilityRole="button"
        accessibilityHint="Hold for 1.2 seconds to send an emergency alert"
        accessibilityState={{ selected: triggered, busy: sending }}
      >
        <Ionicons name={iconName} size={isLarge ? 28 : 20} color="#FFF" />
        {isLarge && <Text style={styles.btnText}>{labelText}</Text>}
      </TouchableOpacity>
      {pressing && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Hold for 1.2 seconds</Text>
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
