/**
 * Driver-app only (ACTION_ITEMS.md B16) — the full "Safety" screen opened by
 * a short tap on SafetyShield.tsx (or a future dedicated bottom-bar entry
 * point). Do not import from rider-app — see SafetyShield.tsx's header for
 * why (react-native-svg is a driver-app-only dependency; SafetyOverlay
 * itself doesn't use svg, but it's designed as SafetyShield's companion and
 * kept alongside it rather than split across app boundaries).
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Modal, Pressable, Linking, Animated } from 'react-native';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { useHoldToConfirm } from '../hooks/useHoldToConfirm';
import { useEmergencyContacts } from '../hooks/useEmergencyContacts';
import { getSOSLocation } from '../utils/sosLocation';
import api from '@shared/api/client';

const HOLD_MS = 3000;

export interface SafetyOverlayTriggerResult {
  contacts: Array<{ id: string; name: string; notified: boolean }>;
}

export interface SafetyOverlayProps {
  visible: boolean;
  onClose: () => void;
  rideId: string;
  onTrigger: (rideId: string, lat?: number, lng?: number) => Promise<SafetyOverlayTriggerResult>;
}

export function SafetyOverlay({ visible, onClose, rideId, onTrigger }: SafetyOverlayProps) {
  const { contacts } = useEmergencyContacts();
  const [coords, setCoords] = useState<{ lat?: number; lng?: number }>({});
  const [areaText, setAreaText] = useState('Locating…');
  const [sentContacts, setSentContacts] = useState<Array<{ id: string; name: string; notified: boolean }> | null>(
    null,
  );
  const [failed, setFailed] = useState(false);
  const pulse = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (!visible) return;
    // Reset per-open so a re-opened overlay after a prior send doesn't show
    // a stale confirmation view.
    setSentContacts(null);
    setFailed(false);

    let cancelled = false;
    (async () => {
      const { lat, lng } = await getSOSLocation();
      if (cancelled) return;
      setCoords({ lat, lng });
      if (lat === undefined || lng === undefined) {
        setAreaText('Location unavailable');
        return;
      }
      try {
        const geocode = await Location.reverseGeocodeAsync({ latitude: lat, longitude: lng });
        if (cancelled) return;
        const place = geocode[0];
        const line = [place?.street, place?.city].filter(Boolean).join(', ');
        setAreaText(line || 'Location shared');
      } catch {
        if (!cancelled) setAreaText('Location shared');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [visible]);

  useEffect(() => {
    if (!visible) return undefined;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 0.4, duration: 900, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1, duration: 900, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [visible, pulse]);

  const handleConfirm = () => {
    (async () => {
      try {
        const result = await onTrigger(rideId, coords.lat, coords.lng);
        setFailed(false);
        setSentContacts(result?.contacts || []);
      } catch {
        setFailed(true);
      }
    })();
  };

  const { progress, onPressIn, onPressOut } = useHoldToConfirm({ durationMs: HOLD_MS, onConfirm: handleConfirm });

  const barWidth = progress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });

  const call911 = () => Linking.openURL('tel:911');

  const shareTripLink = async () => {
    try {
      await api.get(`/rides/${rideId}/share`);
    } catch {
      // Best-effort — a failed share-link fetch isn't a safety-critical
      // failure (911/contacts-alert paths are unaffected); no inline
      // error state needed here, matching this action's low stakes.
    }
  };

  const contactNames = contacts.map((c) => c.name).filter(Boolean);
  const contactsSubtitle = contactNames.length > 0 ? `${contactNames.join(', ')} + Spinr Safety (silent)` : 'Spinr Safety (silent)';

  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        {sentContacts ? (
          <View style={styles.content}>
            <Ionicons name="checkmark-circle" size={48} color="#38A169" />
            <Text style={styles.sentTitle}>Alert Sent Silently</Text>
            <Text style={styles.sentSub}>
              Your contacts and Spinr Safety have been notified. They can see your live location.
            </Text>
            <View style={styles.sentList}>
              {sentContacts.map((c) => (
                <View key={c.id} style={styles.sentRow}>
                  <Text style={styles.sentRowName}>{c.name}</Text>
                  <Text style={styles.sentRowStatus}>{c.notified ? '✓ Notified' : 'Not reached'}</Text>
                </View>
              ))}
              <View style={styles.sentRow}>
                <Text style={styles.sentRowName}>Spinr Safety Team</Text>
                <Text style={styles.sentRowStatus}>✓ Monitoring</Text>
              </View>
            </View>
            <Pressable
              style={styles.continueBtn}
              onPress={onClose}
              accessibilityRole="button"
              accessibilityLabel="Continue my ride"
            >
              <Text style={styles.continueBtnText}>Continue My Ride</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.content}>
            <View style={styles.discreetBanner}>
              <Text style={styles.discreetBannerText}>🔒 Discreet mode on — Alerts send silently, no red screen</Text>
            </View>

            <View style={styles.headerRow}>
              <Text style={styles.title}>🛡 Safety</Text>
              <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close safety screen">
                <Ionicons name="close" size={22} color="#FFFFFF" />
              </Pressable>
            </View>

            <View style={styles.locationRow}>
              <Animated.View style={[styles.locationDot, { opacity: pulse }]} />
              <Text style={styles.locationText} numberOfLines={1}>
                {areaText}
              </Text>
              <Text style={styles.liveLabel}>LIVE</Text>
            </View>

            <Pressable
              onPressIn={onPressIn}
              onPressOut={onPressOut}
              style={[styles.actionBtn, styles.holdBtn, failed && styles.actionBtnFailed]}
              accessibilityRole="button"
              accessibilityLabel="Alert emergency contacts"
              accessibilityHint="Hold for 3 seconds to alert your emergency contacts and Spinr Safety."
            >
              <Animated.View style={[styles.holdBar, { width: barWidth }]} />
              <Text style={styles.actionBtnTitle}>Alert Emergency Contacts</Text>
              <Text style={styles.actionBtnSub}>{failed ? 'Alert failed — hold to retry' : contactsSubtitle}</Text>
              <Text style={styles.holdHint}>Hold 3s</Text>
            </Pressable>

            <Pressable
              onPress={call911}
              style={[styles.actionBtn, styles.call911Btn]}
              accessibilityRole="button"
              accessibilityLabel="Call 911"
              accessibilityHint="Opens your phone dialer. Spinr never auto-calls 911 — you decide."
            >
              <Text style={styles.actionBtnTitle}>Call 911</Text>
              <Text style={styles.actionBtnSub}>Opens your phone dialer — you decide</Text>
            </Pressable>

            <Pressable
              onPress={shareTripLink}
              style={[styles.actionBtn, styles.shareBtn]}
              accessibilityRole="button"
              accessibilityLabel="Share live trip link"
            >
              <Text style={styles.actionBtnTitle}>Share Live Trip Link</Text>
              <Text style={styles.actionBtnSub}>Anyone with the link can track this ride</Text>
            </Pressable>

            <Pressable
              onPress={onClose}
              style={styles.closeBtn}
              accessibilityRole="button"
              accessibilityLabel="I'm safe, close"
            >
              <Text style={styles.closeBtnText}>I'm Safe — Close</Text>
            </Pressable>
          </View>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(4,4,10,0.97)',
    justifyContent: 'center',
    padding: 20,
  },
  content: {
    alignItems: 'stretch',
  },
  discreetBanner: {
    backgroundColor: 'rgba(56,161,105,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(56,161,105,0.25)',
    borderRadius: 8,
    padding: 10,
    marginBottom: 16,
  },
  discreetBannerText: {
    color: '#9AE6B4',
    fontSize: 12,
    fontWeight: '600',
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 20,
    fontWeight: '700',
  },
  locationRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 20,
  },
  locationDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#38A169',
  },
  locationText: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 13,
    flex: 1,
  },
  liveLabel: {
    color: '#38A169',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  actionBtn: {
    borderRadius: 10,
    padding: 14,
    marginBottom: 12,
    overflow: 'hidden',
  },
  actionBtnFailed: {
    borderWidth: 1.5,
    borderColor: '#FCD34D',
  },
  holdBtn: {
    backgroundColor: 'rgba(237,137,54,0.1)',
    borderWidth: 1.5,
    borderColor: 'rgba(237,137,54,0.3)',
  },
  holdBar: {
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    backgroundColor: 'rgba(237,137,54,0.25)',
  },
  call911Btn: {
    backgroundColor: '#E53E3E',
  },
  shareBtn: {
    backgroundColor: 'rgba(66,153,225,0.12)',
    borderWidth: 1.5,
    borderColor: 'rgba(66,153,225,0.3)',
  },
  actionBtnTitle: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
    marginBottom: 2,
  },
  actionBtnSub: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 12,
  },
  holdHint: {
    position: 'absolute',
    top: 14,
    right: 14,
    color: 'rgba(255,255,255,0.6)',
    fontSize: 11,
    fontWeight: '700',
  },
  closeBtn: {
    alignItems: 'center',
    paddingVertical: 14,
    marginTop: 4,
  },
  closeBtnText: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 14,
    fontWeight: '600',
  },
  sentTitle: {
    color: '#FFFFFF',
    fontSize: 20,
    fontWeight: '700',
    textAlign: 'center',
    marginTop: 12,
  },
  sentSub: {
    color: 'rgba(255,255,255,0.75)',
    fontSize: 13,
    textAlign: 'center',
    marginTop: 8,
    marginBottom: 20,
  },
  sentList: {
    marginBottom: 20,
  },
  sentRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255,255,255,0.15)',
  },
  sentRowName: {
    color: '#FFFFFF',
    fontSize: 14,
  },
  sentRowStatus: {
    color: '#38A169',
    fontSize: 13,
    fontWeight: '600',
  },
  continueBtn: {
    backgroundColor: '#38A169',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  continueBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
});

export default SafetyOverlay;
