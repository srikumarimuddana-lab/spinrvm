import React, { useState, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert, Animated,
  Linking, Vibration,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getSOSLocation } from '../utils/sosLocation';
import { deriveContactOutcome, type SOSContactOutcome, type SOSTriggerResult } from '../types/safety';

// This component is shared by rider-app and driver-app, which each ship
// their own, independent i18n instance (different translation JSON, two
// separate zustand stores — see rider-app/i18n/index.ts's useTranslation()
// vs driver-app/store/languageStore.ts's useLanguageStore()). Rather than
// import either app's store from `shared/` (which would hard-couple this
// component to one app and break the other's build), the caller injects
// its own `t`. Both apps carry an identical `sos.*` key set (plus
// `common.cancel`) in their locale files specifically so this one set of
// t() calls resolves correctly regardless of which app renders it.
//
// `t` is optional and defaults to the pre-localization English copy below
// — any caller that hasn't been updated to pass `t` yet keeps rendering
// byte-identical English text.
const DEFAULT_STRINGS: Record<string, string> = {
  'sos.button_label': 'Emergency SOS',
  'sos.alert_title': '🚨 Emergency Alert Sent',
  // Four outcomes for the contact leg. The alert itself reached our safety
  // team in all four -- only what happened to the rider's emergency contacts
  // differs, and we must never claim more than the response supports.
  'sos.alert_msg': 'Your location has been shared with Spinr support and your emergency contacts.\n\nDo you want to call 911?',
  'sos.alert_msg_contacts_failed':
    'Your location has been shared with Spinr support.\n\nWe could NOT reach your emergency contacts — please call them directly.\n\nDo you want to call 911?',
  'sos.alert_msg_no_contacts':
    'Your location has been shared with Spinr support.\n\nYou have no emergency contacts saved, so nobody else was alerted.\n\nDo you want to call 911?',
  'sos.alert_msg_contacts_unknown':
    'Your location has been shared with Spinr support.\n\nWe could not confirm whether your emergency contacts were reached — consider calling them directly.\n\nDo you want to call 911?',
  'sos.call_911': 'Call 911',
  'sos.im_ok': "I'm OK",
  'sos.failure_title': '⚠️ Alert Not Sent',
  'sos.failure_msg': 'Could not reach Spinr. The button will stay red — tap it to retry.\n\nYou can call 911 directly right now.',
  'sos.retry_now': 'Retry Now',
  'sos.dismiss': 'Dismiss',
  'sos.no_active_ride_title': 'No Active Ride',
  'sos.no_active_ride_msg': 'Emergency alert requires an active ride. Call 911 directly for immediate help.',
  'sos.label_hold': 'Hold...',
  'sos.label_sending': 'Sending…',
  'sos.label_alert_sent': 'Alert Sent',
  'sos.label_failed': 'FAILED',
  'sos.label_default': 'SOS',
  'sos.hold_hint': 'Hold for 1.2 seconds',
  'sos.retry_hint': 'Tap to retry',
  'sos.alert_sent_label': 'Emergency alert sent',
  'sos.a11y_label_sending': 'Sending emergency alert',
  'sos.a11y_label_failed': 'Emergency alert failed — tap to retry',
  'sos.a11y_hint_failed': 'Alert failed — double tap to retry',
  'sos.a11y_hint_sent': 'Alert sent',
  'sos.a11y_hint_default': 'Double tap to send SOS alert',
  'common.cancel': 'Cancel',
};

function defaultT(key: string): string {
  return DEFAULT_STRINGS[key] ?? key;
}

interface SOSButtonProps {
  rideId?: string;
  /**
   * Fires the backend alert. May resolve with the endpoint's body so the
   * success dialog can report what actually happened to the emergency
   * contacts; `void` is still accepted for callers that don't surface it
   * (deriveContactOutcome maps a void result to 'unknown' — never to
   * "notified", and never to "no contacts saved", since a caller that
   * returns nothing tells us nothing either way). See shared/types/safety.ts.
   */
  onTrigger: (
    rideId: string,
    lat?: number,
    lng?: number,
    idempotencyKey?: string,
  ) => Promise<SOSTriggerResult | void>;
  size?: 'small' | 'large';
  /**
   * Translation function — pass `useTranslation().t` (rider-app) or
   * `useLanguageStore().t` (driver-app). Omit to keep English strings.
   */
  t?: (key: string) => string;
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

export function SOSButton({ rideId, onTrigger, size = 'small', t }: SOSButtonProps) {
  const translate = t ?? defaultT;
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

  // The success dialog used to assert unconditionally that emergency contacts
  // had been notified. That was untrue whenever every SMS failed, and untrue
  // for a rider with no contacts saved. The endpoint has always returned the
  // per-contact outcome; we now branch the copy on it. The title stays the
  // same in every case because the part it claims -- the alert reached our
  // safety team -- is what actually did happen on any 200.
  const showSuccessAlert = (outcome: SOSContactOutcome) => {
    const msgKey =
      outcome === 'contacts_notified'
        ? 'sos.alert_msg'
        : outcome === 'contacts_failed'
        ? 'sos.alert_msg_contacts_failed'
        : outcome === 'no_contacts'
        ? 'sos.alert_msg_no_contacts'
        : 'sos.alert_msg_contacts_unknown';
    Alert.alert(
      translate('sos.alert_title'),
      translate(msgKey),
      [
        {
          text: translate('sos.call_911'),
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: translate('sos.im_ok'),
          style: 'cancel',
          onPress: () => setTriggered(false),
        },
      ]
    );
  };

  const showFailureAlert = (retry: () => void) => {
    Alert.alert(
      translate('sos.failure_title'),
      translate('sos.failure_msg'),
      [
        {
          text: translate('sos.call_911'),
          style: 'destructive',
          onPress: () => Linking.openURL('tel:911'),
        },
        {
          text: translate('sos.retry_now'),
          onPress: retry,
        },
        // "Dismiss" intentionally does NOT reset the button — failed state
        // persists so the driver always knows the alert was not sent.
        { text: translate('sos.dismiss'), style: 'cancel' },
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

    // One idempotency key per PRESS, deliberately generated outside the retry
    // loop so every attempt below carries the same value. The backend uses it
    // to return the original incident instead of inserting a duplicate and
    // re-sending emergency-contact SMS when a retry follows a response that
    // was lost in transit (migration 315). Not security-sensitive — it only
    // has to be unique per user — so this avoids pulling in a UUID dependency.
    const idempotencyKey = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

    // Attempt backend call with two retries (3 total attempts) using
    // 1 s / 2 s backoff before declaring failure. Never treat missing
    // rideId as success — that would show "Alert Sent" without any
    // backend notification going out.
    let backendOk = false;
    let contactOutcome: SOSContactOutcome = 'unknown';
    if (rideId) {
      for (let attempt = 0; attempt < SOS_MAX_ATTEMPTS && !backendOk; attempt++) {
        if (attempt > 0) {
          const delay = SOS_RETRY_DELAYS_MS[attempt - 1] ?? SOS_RETRY_DELAYS_MS[SOS_RETRY_DELAYS_MS.length - 1];
          await new Promise<void>((r) => setTimeout(r, delay));
        }
        try {
          const result = await onTrigger(rideId, lat, lng, idempotencyKey);
          // Derived, never assumed: deriveContactOutcome falls back to
          // 'unknown' for a void-returning caller or a deduped replay, so a
          // caller that can't report contact status never causes us to claim
          // contacts were reached.
          contactOutcome = deriveContactOutcome(result ?? null);
          backendOk = true;
        } catch {}
      }
    }

    setSending(false);

    if (!rideId) {
      // No active ride context — direct user to call 911 immediately.
      Alert.alert(
        translate('sos.no_active_ride_title'),
        translate('sos.no_active_ride_msg'),
        [
          { text: translate('common.cancel'), style: 'cancel' },
          { text: translate('sos.call_911'), style: 'destructive', onPress: () => Linking.openURL('tel:911') },
        ],
      );
    } else if (backendOk) {
      setTriggered(true);
      showSuccessAlert(contactOutcome);
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
    ? translate('sos.label_hold')
    : sending
    ? translate('sos.label_sending')
    : triggered
    ? translate('sos.label_alert_sent')
    : failed
    ? translate('sos.label_failed')
    : translate('sos.label_default');

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
            ? translate('sos.alert_sent_label')
            : sending
            ? translate('sos.a11y_label_sending')
            : failed
            ? translate('sos.a11y_label_failed')
            : translate('sos.button_label')
        }
        accessibilityRole="button"
        accessibilityHint={
          failed
            ? translate('sos.a11y_hint_failed')
            : triggered
            ? translate('sos.a11y_hint_sent')
            : translate('sos.a11y_hint_default')
        }
        accessibilityState={{
          selected: triggered,
          busy: sending,
          disabled: sending,
        }}
      >
        {/* The label used to render only at size="large". Every map placement
            uses "small", so in its most-visible spot this button was a bare
            shield glyph — the same symbol a non-emergency safety screen would
            use, giving a rider in trouble nothing that reads as "emergency".
            Small now shows the SOS wordmark when idle. The transient states
            (sending / sent / failed) keep the icon: it conveys progress better
            than a word, is short-lived, and the persistent FAILED caption
            below already spells out what happened. Geometry is unchanged —
            still a 44px circle — so no map layout shifts. */}
        {!isLarge && !sending && !triggered && !failed ? (
          // allowFontScaling={false}: the circle is a fixed 44px, so an OS
          // text-scale setting would clip or overflow the wordmark. The icon
          // this replaced used a fixed numeric size and was immune, so the
          // swap would otherwise have quietly introduced an a11y-setting
          // dependency. numberOfLines guards the same edge on any locale
          // whose word is longer than "SOS".
          <Text style={styles.btnTextSmall} allowFontScaling={false} numberOfLines={1}>
            {translate('sos.label_default')}
          </Text>
        ) : (
          <Ionicons name={iconName} size={isLarge ? 28 : 20} color="#FFF" />
        )}
        {isLarge && <Text style={styles.btnText}>{labelText}</Text>}
      </TouchableOpacity>
      {pressing && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>{translate('sos.hold_hint')}</Text>
        </View>
      )}
      {failed && !sending && (
        <View style={[styles.holdHint, isLarge && { bottom: -24 }]}>
          <Text style={styles.holdHintText}>{translate('sos.retry_hint')}</Text>
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
  // Sized to sit inside the 44px small circle without changing its geometry.
  btnTextSmall: {
    color: '#FFF', fontSize: 13, fontWeight: '800', letterSpacing: 0.3,
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
