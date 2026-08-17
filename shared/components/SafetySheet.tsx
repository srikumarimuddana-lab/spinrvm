/**
 * The Safety panel opened by a short TAP on the SOS button.
 *
 * Rider-safe by construction: no react-native-svg import, unlike its driver
 * sibling SafetyShield.tsx, so both apps can render this. (SafetyOverlay.tsx
 * remains the driver's discreet-mode screen; this is the rider-facing one and
 * they are deliberately separate — the driver's threat model needs silence,
 * the rider's needs clarity.)
 *
 * The hold gesture is NOT replaced. Holding SOS for 1.2s still fires the alert
 * exactly as before; this panel is purely additive, so nothing a rider already
 * relies on changes.
 *
 * Row rules, all data-driven — every row appears only if it can actually do
 * something:
 *   - Call <emergency>  always. Hard-defaults to 911 even if every fetch failed.
 *   - Alert contacts    always. Hold 2s, matching the panic-button guard.
 *   - Share trip        only with an active ride and the tile enabled.
 *   - Local authority   only where an admin configured one (Calgary 311, SGI...).
 *                       Renders a call button only when a phone exists — in SK
 *                       there is no municipal hotline, so it is a link.
 *   - Email Spinr Safety only when an address is configured.
 *   - Report an issue   only when the tile is enabled.
 *
 * Never claims to replace emergency services, and never auto-dials.
 */
import React, { useRef, useState } from 'react';
import { View, Text, StyleSheet, Modal, Pressable, Linking, Animated, Share, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useHoldToConfirm } from '../hooks/useHoldToConfirm';
import { useSafetyPanelConfig } from '../hooks/useSafetyPanelConfig';
import { getSOSLocation } from '../utils/sosLocation';
import { deriveContactOutcome, type SOSTriggerResult } from '../types/safety';
import api from '../api/client';

const ALERT_HOLD_MS = 2000;

export interface SafetySheetProps {
  visible: boolean;
  onClose: () => void;
  /** Absent when there's no active ride — share-trip is hidden, everything else works. */
  rideId?: string;
  onTrigger: (
    rideId: string,
    lat?: number,
    lng?: number,
    idempotencyKey?: string,
  ) => Promise<SOSTriggerResult | void>;
  onReportIssue?: () => void;
  lat?: number;
  lng?: number;
}

type AlertState = 'idle' | 'sending' | 'sent' | 'failed';

export function SafetySheet({
  visible,
  onClose,
  rideId,
  onTrigger,
  onReportIssue,
  lat,
  lng,
}: SafetySheetProps) {
  const cfg = useSafetyPanelConfig(lat, lng, visible);
  const [alertState, setAlertState] = useState<AlertState>('idle');
  const [outcome, setOutcome] = useState<string | null>(null);
  const [shareState, setShareState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const shareTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const callEmergency = () => Linking.openURL(`tel:${cfg.emergencyNumber}`);

  const sendAlert = () => {
    if (!rideId) {
      // No ride to attach the alert to. Say so plainly rather than showing a
      // success state that notified nobody. (The rideless SOS path is
      // ACTION_ITEMS.md B15(c) and still open.)
      setAlertState('failed');
      setOutcome('no_ride');
      return;
    }
    setAlertState('sending');
    (async () => {
      const coords = await getSOSLocation();
      const key = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      try {
        const res = await onTrigger(rideId, coords.lat ?? lat, coords.lng ?? lng, key);
        setOutcome(deriveContactOutcome(res ?? null));
        setAlertState('sent');
      } catch {
        setAlertState('failed');
        setOutcome(null);
      }
    })();
  };

  const { progress, onPressIn, onPressOut } = useHoldToConfirm({
    durationMs: ALERT_HOLD_MS,
    onConfirm: sendAlert,
  });
  const barWidth = progress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });

  const shareTrip = async () => {
    if (!rideId) return;
    setShareState('loading');
    try {
      const res = await api.get<{ share_url?: string }>(`/rides/${rideId}/share`);
      const url = res.data?.share_url;
      if (!url) throw new Error('no share_url');
      await Share.share({ message: `Follow my Spinr ride live:\n${url}` });
      setShareState('idle');
    } catch {
      setShareState('failed');
      if (shareTimer.current) clearTimeout(shareTimer.current);
      shareTimer.current = setTimeout(() => setShareState('idle'), 3000);
    }
  };

  const alertSubtitle = () => {
    if (alertState === 'sending') return 'Sending…';
    if (alertState === 'failed' && outcome === 'no_ride')
      return 'Needs an active ride — call for help directly';
    if (alertState === 'failed') return 'Not sent — hold to retry, or call for help';
    if (alertState === 'sent') {
      if (outcome === 'contacts_notified') return 'Sent — contacts and Spinr Safety notified';
      if (outcome === 'contacts_failed') return 'Spinr Safety notified — contacts NOT reached, call them';
      if (outcome === 'no_contacts') return 'Spinr Safety notified — no emergency contacts saved';
      return 'Spinr Safety notified — contact delivery unconfirmed';
    }
    return 'Hold 2s — alerts your contacts and our safety team';
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.grabber} />
          <View style={styles.headerRow}>
            <Text style={styles.title}>Safety</Text>
            <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close safety options" hitSlop={12}>
              <Ionicons name="close" size={22} color="#8E8E93" />
            </Pressable>
          </View>

          <ScrollView showsVerticalScrollIndicator={false}>
            <Pressable
              onPress={callEmergency}
              style={[styles.row, styles.rowEmergency]}
              accessibilityRole="button"
              accessibilityLabel={`Call ${cfg.emergencyNumber}`}
              accessibilityHint="Opens your phone dialer. Spinr never calls for you."
            >
              <Ionicons name="call" size={20} color="#FFFFFF" />
              <View style={styles.rowText}>
                <Text style={styles.rowTitleOnRed}>Call {cfg.emergencyNumber}</Text>
                <Text style={styles.rowSubOnRed}>Opens your dialer — you decide</Text>
              </View>
            </Pressable>

            <Pressable
              onPressIn={alertState === 'sending' ? undefined : onPressIn}
              onPressOut={alertState === 'sending' ? undefined : onPressOut}
              style={[styles.row, styles.rowAlert, alertState === 'failed' && styles.rowFailed]}
              accessibilityRole="button"
              accessibilityLabel="Alert my emergency contacts and Spinr Safety"
              accessibilityHint="Hold for 2 seconds to send"
              accessibilityState={{ busy: alertState === 'sending' }}
            >
              <Animated.View style={[styles.holdBar, { width: barWidth }]} />
              <Ionicons
                name={alertState === 'sent' ? 'checkmark-circle' : 'shield'}
                size={20}
                color={alertState === 'sent' ? '#34C759' : '#FF9500'}
              />
              <View style={styles.rowText}>
                <Text style={styles.rowTitle}>Alert my contacts</Text>
                <Text style={styles.rowSub}>{alertSubtitle()}</Text>
              </View>
            </Pressable>

            {cfg.showShareTrip && !!rideId && (
              <Pressable
                onPress={shareTrip}
                disabled={shareState === 'loading'}
                style={[styles.row, shareState === 'failed' && styles.rowFailed]}
                accessibilityRole="button"
                accessibilityLabel="Share my live trip"
              >
                <Ionicons name="share-outline" size={20} color="#3B82F6" />
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>Share my live trip</Text>
                  <Text style={styles.rowSub}>
                    {shareState === 'loading'
                      ? 'Getting your link…'
                      : shareState === 'failed'
                      ? "Couldn't get the link — tap to retry"
                      : 'Send a live tracking link to anyone'}
                  </Text>
                </View>
              </Pressable>
            )}

            {!!cfg.authority && (
              <Pressable
                onPress={() =>
                  cfg.authority?.phone
                    ? Linking.openURL(`tel:${cfg.authority.phone}`)
                    : cfg.authority?.url
                    ? Linking.openURL(cfg.authority.url)
                    : undefined
                }
                style={styles.row}
                accessibilityRole="button"
                accessibilityLabel={
                  cfg.authority.phone
                    ? `Call ${cfg.authority.name}`
                    : `Open ${cfg.authority.name}`
                }
              >
                <Ionicons name="business-outline" size={20} color="#3B82F6" />
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>
                    {cfg.authority.phone ? `Call ${cfg.authority.name}` : cfg.authority.name}
                  </Text>
                  <Text style={styles.rowSub}>
                    Report a ride concern to the local authority — not for emergencies
                    {cfg.authority.hours ? ` · ${cfg.authority.hours}` : ''}
                  </Text>
                </View>
              </Pressable>
            )}

            {!!cfg.safetyTeamEmail && (
              <Pressable
                onPress={() =>
                  Linking.openURL(
                    `mailto:${cfg.safetyTeamEmail}?subject=${encodeURIComponent(
                      rideId ? `Safety concern — ride ${rideId}` : 'Safety concern',
                    )}`,
                  )
                }
                style={styles.row}
                accessibilityRole="button"
                accessibilityLabel="Email Spinr Safety"
              >
                <Ionicons name="mail-outline" size={20} color="#3B82F6" />
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>Email Spinr Safety</Text>
                  <Text style={styles.rowSub}>{cfg.safetyTeamEmail}</Text>
                </View>
              </Pressable>
            )}

            {cfg.showReportIssue && !!onReportIssue && (
              <Pressable
                onPress={() => {
                  onClose();
                  onReportIssue();
                }}
                style={styles.row}
                accessibilityRole="button"
                accessibilityLabel="Report a safety issue"
              >
                <Ionicons name="alert-circle-outline" size={20} color="#3B82F6" />
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>Report a safety issue</Text>
                  <Text style={styles.rowSub}>Tell our team what happened</Text>
                </View>
              </Pressable>
            )}

            <Text style={styles.footnote}>
              Spinr alerts your emergency contacts and our safety team. It is not a replacement for
              emergency services.
            </Text>

            <Pressable onPress={onClose} style={styles.closeBtn} accessibilityRole="button" accessibilityLabel="I'm safe, close">
              <Text style={styles.closeBtnText}>I&rsquo;m safe — close</Text>
            </Pressable>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 24,
    maxHeight: '88%',
  },
  grabber: { alignSelf: 'center', width: 36, height: 4, borderRadius: 2, backgroundColor: '#E5E7EB', marginBottom: 10 },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  title: { fontSize: 20, fontWeight: '700', color: '#1A1A1A' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 13,
    marginBottom: 10,
    overflow: 'hidden',
  },
  rowEmergency: { backgroundColor: '#DC2626', borderColor: '#DC2626' },
  rowAlert: { borderColor: 'rgba(255,149,0,0.45)', backgroundColor: 'rgba(255,149,0,0.06)' },
  rowFailed: { borderColor: '#d97706', borderWidth: 1.5 },
  holdBar: { position: 'absolute', left: 0, top: 0, bottom: 0, backgroundColor: 'rgba(255,149,0,0.22)' },
  rowText: { flex: 1, minWidth: 0 },
  rowTitle: { fontSize: 15, fontWeight: '600', color: '#1A1A1A' },
  rowSub: { fontSize: 12, color: '#6B7280', marginTop: 2 },
  rowTitleOnRed: { fontSize: 15, fontWeight: '700', color: '#FFFFFF' },
  rowSubOnRed: { fontSize: 12, color: 'rgba(255,255,255,0.85)', marginTop: 2 },
  footnote: { fontSize: 11, color: '#9A9294', textAlign: 'center', marginTop: 6, marginBottom: 4, lineHeight: 15 },
  closeBtn: { alignItems: 'center', paddingVertical: 14 },
  closeBtnText: { fontSize: 15, fontWeight: '600', color: '#6B7280' },
});

export default SafetySheet;
