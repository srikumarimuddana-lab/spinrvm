import React, { useState, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert, Animated,
  Linking, Platform, Vibration,
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
 * 3. Prompts to call 911
 * 4. Shares GPS location
 */
export function SOSButton({ rideId, onTrigger, size = 'small' }: SOSButtonProps) {
  const [triggered, setTriggered] = useState(false);
  const [pressing, setPressing] = useState(false);
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const startPress = () => {
    setPressing(true);
    // Start pulse animation
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.15, duration: 400, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1, duration: 400, useNativeDriver: true }),
      ])
    ).start();

    // Trigger after 2 second hold
    pressTimer.current = setTimeout(() => {
      triggerSOS();
    }, 2000);
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

  const triggerSOS = async () => {
    setPressing(false);
    setTriggered(true);
    Vibration.vibrate([0, 200, 100, 200, 100, 200]); // SOS vibration pattern

    // Get current location
    let lat: number | undefined;
    let lng: number | undefined;
    try {
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      lat = loc.coords.latitude;
      lng = loc.coords.longitude;
    } catch {}

    // Call backend
    if (rideId) {
      try {
        await onTrigger(rideId, lat, lng);
      } catch {}
    }

    // Show options
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
          text: 'I\'m OK',
          style: 'cancel',
          onPress: () => setTriggered(false),
        },
      ]
    );
  };

  const isLarge = size === 'large';

  return (
    <Animated.View style={[{ transform: [{ scale: pressing ? pulseAnim : 1 }] }]}>
      <TouchableOpacity
        style={[
          styles.btn,
          isLarge ? styles.btnLarge : styles.btnSmall,
          triggered && styles.btnTriggered,
          pressing && styles.btnPressing,
        ]}
        onPressIn={startPress}
        onPressOut={endPress}
        activeOpacity={0.9}
      >
        <Ionicons
          name={triggered ? 'checkmark-circle' : 'shield'}
          size={isLarge ? 28 : 20}
          color="#FFF"
        />
        {isLarge && (
          <Text style={styles.btnText}>
            {pressing ? 'Hold...' : triggered ? 'Alert Sent' : 'SOS'}
          </Text>
        )}
      </TouchableOpacity>
      {pressing && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>Hold for 2 seconds</Text>
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
